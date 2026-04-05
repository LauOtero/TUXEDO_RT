/*
 * canbus.c — CAN 2.0 / FD / XL wrapper layer
 *
 * ISO 11898-1:2003 / 2015 / 2024 compliant.
 * Integrates auto-negotiation engine for all three frame generations.
 *
 * Copyright (C) 2022  Kevin O'Connor <kevin@koconnor.net>
 * Copyright (C) 2024  Extended for CAN XL
 * This file may be distributed under the terms of the GNU GPLv3 license.
 */

#include "autoconf.h"
#include "core/generic/canbus.h"
#include "core/generic/canbus_autoneg.h"
#include "core/generic/canserial.h"
#include "core/base/command.h"
#include "core/base/sched.h"
#include "core/generic/irq.h"
#include "core/generic/misc.h"
#include "core/common/compiler.h"

#pragma GCC optimize("O3")

DECL_CONSTANT("CANBUS_FREQUENCY",    CONFIG_CANBUS_FREQUENCY);
DECL_CONSTANT("CANBUS_SUPPORTS_FD",  CONFIG_CANBUS_SUPPORTS_FD);
DECL_CONSTANT("CANBUS_FD_DATA_FREQ", CONFIG_CANBUS_FD_DATA_FREQUENCY);
DECL_CONSTANT("CANBUS_SUPPORTS_XL",  CONFIG_CANBUS_SUPPORTS_XL);

/* =========================================================================
 * Module state
 * ========================================================================= */
static struct canbus_state {
    canbus_mode_t         active_mode;
    uint32_t              max_dlen;
    struct canbus_autoneg autoneg;
    struct canbus_hw_caps caps;
    uint32_t              tx_lat_min_us;
    uint32_t              tx_lat_max_us;
    uint64_t              tx_lat_sum_us;
    uint32_t              tx_lat_count;
    uint32_t              sw_overflow;
} CanState __attribute__((aligned(64)));

/* =========================================================================
 * ms counter — task context only
 * ========================================================================= */
static inline uint32_t
canbus_now_ms(void)
{
#ifdef CONFIG_CLOCK_FREQ
    return (uint32_t)((uint64_t)timer_read_time() * 1000u / CONFIG_CLOCK_FREQ);
#else
    return (uint32_t)timer_read_time();
#endif
}

/* =========================================================================
 * Mode-locked callback
 * ========================================================================= */
static void
on_mode_locked(canbus_mode_t mode, void *ctx)
{
    (void)ctx;
    CanState.active_mode = mode;
    switch (mode) {
    case CANBUS_MODE_XL:        CanState.max_dlen = CANXL_MAX_DLEN; break;
    case CANBUS_MODE_FD_BRS:
    case CANBUS_MODE_FD_NO_BRS: CanState.max_dlen = CANFD_MAX_DLEN; break;
    default:                    CanState.max_dlen = CAN20_MAX_DLEN;  break;
    }
    canserial_notify_tx();
}

/* =========================================================================
 * canbus_init
 * ========================================================================= */
void
canbus_init(canbus_mode_t requested_mode)
{
    canhw_query_caps(&CanState.caps);
    if (requested_mode > CanState.caps.max_mode)
        requested_mode = CanState.caps.max_mode;

    canhw_set_mode(CANBUS_MODE_CLASSIC);
    CanState.active_mode = CANBUS_MODE_CLASSIC;
    CanState.max_dlen    = CAN20_MAX_DLEN;

    uint32_t now = canbus_now_ms();
    canbus_autoneg_init(&CanState.autoneg, &CanState.caps, on_mode_locked, NULL);
    canbus_autoneg_force_renegotiate(&CanState.autoneg, now);

    CanState.tx_lat_min_us = UINT32_MAX;
}

/* =========================================================================
 * Auto-negotiation task — fast-exit when LOCKED and stable
 * ========================================================================= */
static autoneg_state_t prev_state = (autoneg_state_t)0xFFu;

void
canbus_autoneg_task(void)
{
    /* Fast path: LOCKED with no pending IRQ events */
    if (likely(CanState.autoneg.state == AUTONEG_STATE_LOCKED)) {
        if (!__atomic_load_n(&CanState.autoneg.irq_flags.busoff_pending,
                             __ATOMIC_RELAXED) &&
            __atomic_load_n(&CanState.autoneg.consecutive_errors,
                            __ATOMIC_RELAXED) < AUTONEG_RENEGOTIATE_ERRORS)
            return;
    }

    uint32_t now = canbus_now_ms();
    autoneg_state_t st = canbus_autoneg_run(&CanState.autoneg, now);

    if (unlikely(st != prev_state)) {
        prev_state = st;
        sendf("canbus_autoneg_status state=%u mode=%u locked=%u neg=%u reneg=%u",
              (uint32_t)st,
              (uint32_t)CanState.autoneg.detected_mode,
              (uint32_t)(st == AUTONEG_STATE_LOCKED),
              CanState.autoneg.negotiation_count,
              CanState.autoneg.renegotiation_count);
    }
}
DECL_TASK(canbus_autoneg_task);

/* =========================================================================
 * Accessors
 * ========================================================================= */
canbus_mode_t canbus_get_mode(void)         { return CanState.active_mode; }
uint32_t      canbus_max_dlen(void)         { return CanState.max_dlen; }
int           canbus_autoneg_is_ready(void) {
    return canbus_autoneg_is_locked(&CanState.autoneg);
}

/* =========================================================================
 * Frame validation — mutates *msg in-place
 *
 * Rules per generation:
 *   CAN 2.0 : DLC ≤ 8; RTR forces DLC=0; no FD/XL flags
 *   CAN FD  : DLC ≤ 15 (FD table); no RTR; BRS only if mode=FD_BRS
 *   CAN XL  : DLC ≤ 2047; SDT/VCID/AF passed through; BRS always on bus
 *             (but software sets CANMSG_FLAG_XL only)
 * ========================================================================= */
static int
canbus_validate_msg(struct canbus_msg *msg)
{
    if (unlikely(!msg)) return -1;

    int is_xl = (msg->flags & CANMSG_FLAG_XL) != 0;
    int is_fd = (msg->flags & CANMSG_FLAG_FD) != 0;

    /* --- XL frame on non-XL bus ---------------------------------------- */
    if (is_xl && CanState.active_mode != CANBUS_MODE_XL) {
        /* Downgrade: try to send as FD or classic */
        msg->flags &= ~CANMSG_FLAG_XL;
        /* Strip XL header */
        msg->xl.sdt  = 0;
        msg->xl.vcid = 0;
        msg->xl.af   = 0;
        is_xl = 0;
        /* Payload may be > 64 bytes; truncate */
        if (msg->dlc > 15u) msg->dlc = 15u;
        if (CanState.active_mode >= CANBUS_MODE_FD_NO_BRS) {
            msg->flags |= CANMSG_FLAG_FD;
            is_fd = 1;
        }
    }

    /* --- FD frame on classic bus --------------------------------------- */
    if (!is_xl && is_fd && CanState.active_mode == CANBUS_MODE_CLASSIC) {
        msg->flags &= ~(CANMSG_FLAG_FD | CANMSG_FLAG_BRS | CANMSG_FLAG_ESI);
        if (msg->dlc > CAN20_MAX_DLEN) msg->dlc = CAN20_MAX_DLEN;
        is_fd = 0;
    }

    /* --- BRS without FD/XL context -------------------------------------- */
    if (!is_fd && !is_xl)
        msg->flags &= ~(CANMSG_FLAG_BRS | CANMSG_FLAG_ESI);

    /* --- Strip BRS if current mode doesn't support it ------------------- */
    if (is_fd && !is_xl && CanState.active_mode == CANBUS_MODE_FD_NO_BRS)
        msg->flags &= ~CANMSG_FLAG_BRS;

    /* --- DLC clamp ------------------------------------------------------ */
    if (is_xl) {
        if (msg->dlc > 2047u) msg->dlc = 2047u;
        /* XL SDT=0x00 is reserved — default to admin if caller forgot */
        if (msg->xl.sdt == 0x00u) msg->xl.sdt = CANXL_SDT_ADMIN;
    } else if (is_fd) {
        if (msg->dlc > 15u) msg->dlc = 15u;
    } else {
        if (msg->dlc > CAN20_MAX_DLEN) msg->dlc = CAN20_MAX_DLEN;
        if (msg->flags & CANMSG_FLAG_RTR) msg->dlc = 0u;
    }

    /* 29-bit IDs require EFF (XL uses 11-bit only per §10.5 — clear EFF) */
    if (is_xl)
        msg->flags &= ~CANMSG_FLAG_EFF;
    else if (msg->id > 0x7FFu)
        msg->flags |= CANMSG_FLAG_EFF;

    return 0;
}

/* =========================================================================
 * TX path — hot path
 * ========================================================================= */
static int
canbus_send_internal(struct canbus_msg *msg)
{
    uint32_t t0  = timer_read_time();
    int      ret = canhw_send(msg);

    if (likely(ret > 0)) {
#ifdef CONFIG_CLOCK_FREQ
        uint32_t us = (uint32_t)(
            (uint64_t)(timer_read_time() - t0) * 1000000u / CONFIG_CLOCK_FREQ);
#else
        uint32_t us = timer_read_time() - t0;
#endif
        if (us < CanState.tx_lat_min_us) CanState.tx_lat_min_us = us;
        if (us > CanState.tx_lat_max_us) CanState.tx_lat_max_us = us;
        CanState.tx_lat_sum_us += us;
        CanState.tx_lat_count++;
    } else if (ret == 0) {
        CanState.sw_overflow++;
    }
    return ret;
}

int canbus_send(struct canbus_msg *msg) {
    if (unlikely(canbus_validate_msg(msg) < 0)) return -1;
    return canbus_send_internal(msg);
}

int canbus_send_prio(struct canbus_msg *msg, canbus_prio_t prio) {
    (void)prio;
    return canbus_send(msg);
}

/* =========================================================================
 * RX / filter path — IRQ context
 * ========================================================================= */
void canbus_set_filter(uint32_t id) { canhw_set_filter(id); }

void
canbus_process_data(struct canbus_msg *msg)
{
    canbus_autoneg_rx_frame(&CanState.autoneg, msg);
    canserial_process_data(msg);
}

void
canbus_notify_tx(void)
{
    canbus_autoneg_probe_ack(&CanState.autoneg);
    canserial_notify_tx();
}

void
canbus_notify_bus_error(uint8_t hw_error_type)
{
    canbus_autoneg_bus_error(&CanState.autoneg, hw_error_type);
}

/* =========================================================================
 * Host commands
 * ========================================================================= */
void command_get_canbus_mode(uint32_t *args) {
    sendf("canbus_mode mode=%u max_dlen=%u locked=%u autoneg_state=%u",
          (uint32_t)canbus_get_mode(), canbus_max_dlen(),
          (uint32_t)canbus_autoneg_is_locked(&CanState.autoneg),
          (uint32_t)CanState.autoneg.state);
}
DECL_COMMAND_FLAGS(command_get_canbus_mode, HF_IN_SHUTDOWN, "get_canbus_mode");

void command_force_canbus_renegotiate(uint32_t *args) {
    uint32_t now = canbus_now_ms();
    canhw_set_mode(CANBUS_MODE_CLASSIC);
    CanState.active_mode = CANBUS_MODE_CLASSIC;
    CanState.max_dlen    = CAN20_MAX_DLEN;
    canserial_set_mode(CANBUS_MODE_CLASSIC);
    canbus_autoneg_force_renegotiate(&CanState.autoneg, now);
}
DECL_COMMAND(command_force_canbus_renegotiate, "force_canbus_renegotiate");

void canbus_query_stats(struct canbus_stats *out) {
    canhw_query_stats(out);
    out->tx_latency_min_us = (CanState.tx_lat_min_us == UINT32_MAX)
                             ? 0u : CanState.tx_lat_min_us;
    out->tx_latency_max_us = CanState.tx_lat_max_us;
    out->tx_latency_avg_us = CanState.tx_lat_count
        ? (uint32_t)(CanState.tx_lat_sum_us / CanState.tx_lat_count) : 0u;
    out->fifo_overflows += CanState.sw_overflow;
}

void command_get_canbus_stats(uint32_t *args) {
    struct canbus_stats s;
    canbus_query_stats(&s);
    sendf("canbus_stats rx=%u tx=%u rx_fd=%u tx_fd=%u rx_xl=%u tx_xl=%u"
          " overflow=%u bus_err=%u crc=%u xl_crc32=%u"
          " lat_min=%u lat_max=%u lat_avg=%u"
          " tec=%u rec=%u err_state=%u neg=%u reneg=%u",
          s.rx_packets, s.tx_packets,
          s.rx_fd_packets, s.tx_fd_packets,
          s.rx_xl_packets, s.tx_xl_packets,
          s.fifo_overflows, s.bus_errors, s.crc_errors, s.xl_crc32_errors,
          s.tx_latency_min_us, s.tx_latency_max_us, s.tx_latency_avg_us,
          (uint32_t)s.tec, (uint32_t)s.rec, s.error_state,
          CanState.autoneg.negotiation_count,
          CanState.autoneg.renegotiation_count);
}
DECL_COMMAND_FLAGS(command_get_canbus_stats, HF_IN_SHUTDOWN, "get_canbus_stats");
