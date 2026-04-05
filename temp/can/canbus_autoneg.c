/*
 * canbus_autoneg.c — CAN 2.0 / FD / XL bus mode auto-detection
 *
 * ISO 11898-1:2003/2015/2024 compliant state machine.
 * All RT and ISO defects from previous audit are preserved and extended
 * for the XL phase.
 *
 * Copyright (C) 2024  CAN XL auto-negotiation implementation
 * This file may be distributed under the terms of the GNU GPLv3 license.
 */

#include <string.h>
#include "autoconf.h"
#include "core/generic/canbus.h"
#include "core/generic/canbus_autoneg.h"
#include "core/generic/canserial.h"
#include "core/generic/irq.h"
#include "core/common/compiler.h"

#pragma GCC optimize("O2")

/* =========================================================================
 * Atomic helpers
 * ========================================================================= */
#define AN_LOAD_RLX(p)     __atomic_load_n((p),  __ATOMIC_RELAXED)
#define AN_LOAD_ACQ(p)     __atomic_load_n((p),  __ATOMIC_ACQUIRE)
#define AN_STORE_RLX(p,v)  __atomic_store_n((p), (v), __ATOMIC_RELAXED)
#define AN_STORE_REL(p,v)  __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define AN_FADD_RLX(p,v)   __atomic_fetch_add((p), (v), __ATOMIC_RELAXED)
#define AN_CAS(ptr,exp,des) \
    __atomic_compare_exchange_n((ptr),(exp),(des),0,__ATOMIC_RELEASE,__ATOMIC_RELAXED)

/* =========================================================================
 * Internal helpers — task context only
 * ========================================================================= */
static void
an_enter(struct canbus_autoneg *an, autoneg_state_t s, uint32_t now_ms)
{
    an->state          = s;
    an->state_entry_ms = now_ms;
    an->probe_sent     = 0;
    if (s != AUTONEG_STATE_FD_PROBE  &&
        s != AUTONEG_STATE_BRS_PROBE &&
        s != AUTONEG_STATE_XL_PROBE)
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
}

static inline uint32_t
an_elapsed(const struct canbus_autoneg *an, uint32_t now_ms)
{
    return now_ms - an->state_entry_ms;
}

/* =========================================================================
 * Probe frame builders
 * ========================================================================= */

/* FD probe — BRS controlled by caller */
static int
an_send_fd_probe(int with_brs)
{
    struct canbus_msg p;
    memset(&p, 0, sizeof(p));
    p.id    = AUTONEG_PROBE_ID;
    p.dlc   = 0;
    p.flags = CANMSG_FLAG_FD;
    if (with_brs)
        p.flags |= CANMSG_FLAG_BRS;
    return canbus_send_prio(&p, CANBUS_PRIO_HIGH);
}

/*
 * XL probe — ISO 11898-1:2024 §10.5
 *
 * Uses SDT=CANXL_SDT_ADMIN, VCID=0, AF=0, DLC=0 (1 byte payload).
 * 1-byte minimum payload per §10.5.2.4.
 * Payload byte = 0x00 (no significance for probe).
 *
 * A CAN FD-only node will see the XLDF bit and generate a form error.
 * A CAN XL node will ACK the frame normally.
 */
static int
an_send_xl_probe(void)
{
    struct canbus_msg p;
    memset(&p, 0, sizeof(p));
    p.id       = AUTONEG_XL_PROBE_ID;
    p.flags    = CANMSG_FLAG_XL;
    p.dlc      = canxl_len_to_dlc(1u);   /* DLC=0 → 1 byte payload            */
    p.xl.sdt   = CANXL_SDT_ADMIN;
    p.xl.vcid  = 0;
    p.xl.af    = 0;
    p.data[0]  = 0x00;
    return canbus_send_prio(&p, CANBUS_PRIO_HIGH);
}

/* =========================================================================
 * Mode commit — task context
 * ========================================================================= */
static void
an_commit(struct canbus_autoneg *an, canbus_mode_t mode, uint32_t now_ms)
{
    canbus_mode_t prev = an->detected_mode;
    an->detected_mode  = mode;

    if (canhw_set_mode(mode) != 0) {
        an->detected_mode = CANBUS_MODE_CLASSIC;
        canhw_set_mode(CANBUS_MODE_CLASSIC);
    }

    canserial_set_mode(an->detected_mode);

    if (an->on_mode_change && an->detected_mode != prev)
        an->on_mode_change(an->detected_mode, an->on_mode_change_ctx);

    AN_STORE_RLX(&an->consecutive_errors, 0u);
    an->negotiation_count++;
    an_enter(an, AUTONEG_STATE_LOCKED, now_ms);
}

/* =========================================================================
 * canbus_autoneg_init
 * ========================================================================= */
void
canbus_autoneg_init(struct canbus_autoneg *an,
                    const struct canbus_hw_caps *caps,
                    void (*on_mode_change)(canbus_mode_t, void *),
                    void *ctx)
{
    memset(an, 0, sizeof(*an));

    an->hw_supports_fd   = (caps->max_mode >= CANBUS_MODE_FD_NO_BRS) ? 1u : 0u;
    an->hw_supports_brs  = (caps->max_mode >= CANBUS_MODE_FD_BRS)    ? 1u : 0u;
    an->hw_supports_xl   = (caps->max_mode >= CANBUS_MODE_XL)        ? 1u : 0u;
    an->hw_supports_tdc  = caps->supports_tdc;
    an->hw_is_iso_fd     = caps->supports_iso_fd;
    an->hw_supports_vcid = caps->supports_vcid;
    an->nom_bitrate      = caps->nom_bitrate ? caps->nom_bitrate : 500000u;

    an->on_mode_change     = on_mode_change;
    an->on_mode_change_ctx = ctx;
    an->detected_mode      = CANBUS_MODE_CLASSIC;

    an_enter(an, AUTONEG_STATE_IDLE_WAIT, 0u);
}

/* =========================================================================
 * PHASE 0 — IDLE_WAIT  (ISO 11898-1 §9.3.3 / ISO 11898-2 §6.4)
 * ========================================================================= */
static autoneg_state_t
phase_idle_wait(struct canbus_autoneg *an, uint32_t now_ms)
{
    int      hw_idle    = canhw_is_bus_idle();
    uint32_t frames     = AN_LOAD_RLX(&an->obs.frames_total);
    int      timed_out  = (an_elapsed(an, now_ms) >= 130u);
    int      confirmed  = (hw_idle == 1) || (frames > 0) ||
                          (timed_out && hw_idle != 0);

    if (!confirmed)
        return AUTONEG_STATE_IDLE_WAIT;

    if (frames > 0) {
        an_enter(an, AUTONEG_STATE_OBSERVING, now_ms);
        return AUTONEG_STATE_OBSERVING;
    }

    /* Silent bus: probe in order XL → FD → Classic */
    if (an->hw_supports_xl) {
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
        an->probe_retries = AUTONEG_XL_PROBE_RETRIES;
        an_enter(an, AUTONEG_STATE_XL_PROBE, now_ms);
        return AUTONEG_STATE_XL_PROBE;
    }
    if (an->hw_supports_fd) {
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
        an->probe_retries = AUTONEG_FD_PROBE_RETRIES;
        an_enter(an, AUTONEG_STATE_FD_PROBE, now_ms);
        return AUTONEG_STATE_FD_PROBE;
    }
    an_commit(an, CANBUS_MODE_CLASSIC, now_ms);
    return AUTONEG_STATE_LOCKED;
}

/* =========================================================================
 * PHASE 1 — OBSERVING  (ISO §10.4.2.4 FDF, §10.5.2.1 XLDF)
 *
 * Priority: XL > FD > Classic (escalate to highest seen)
 * ========================================================================= */
static autoneg_state_t
phase_observing(struct canbus_autoneg *an, uint32_t now_ms)
{
    uint32_t xl_frames  = AN_LOAD_ACQ(&an->obs.xl_frames);
    uint32_t fd_frames  = AN_LOAD_ACQ(&an->obs.fd_frames);
    uint32_t total      = AN_LOAD_RLX(&an->obs.frames_total);

    /* XL frame seen — escalate immediately to XL probe */
    if (xl_frames > 0 && an->hw_supports_xl) {
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
        an->probe_retries = AUTONEG_XL_PROBE_RETRIES;
        an_enter(an, AUTONEG_STATE_XL_PROBE, now_ms);
        return AUTONEG_STATE_XL_PROBE;
    }

    /* FD frame seen (but no XL) — probe for FD */
    if (fd_frames > 0 && an->hw_supports_fd) {
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
        an->probe_retries = AUTONEG_FD_PROBE_RETRIES;
        an_enter(an, AUTONEG_STATE_FD_PROBE, now_ms);
        return AUTONEG_STATE_FD_PROBE;
    }

    int done = (total >= AUTONEG_OBS_FRAMES) ||
               (an_elapsed(an, now_ms) >= AUTONEG_OBS_TIMEOUT_MS);
    if (!done)
        return AUTONEG_STATE_OBSERVING;

    /* Window closed: only classic frames → lock as 2.0 */
    an_commit(an, CANBUS_MODE_CLASSIC, now_ms);
    return AUTONEG_STATE_LOCKED;
}

/* =========================================================================
 * Generic probe phase executor
 * Used by FD, BRS, and XL probe phases — same retry/timeout logic.
 * ========================================================================= */
typedef int (*probe_send_fn)(void);

static autoneg_state_t
phase_probe_generic(struct canbus_autoneg *an, uint32_t now_ms,
                    probe_send_fn send_fn,
                    autoneg_state_t this_state,
                    autoneg_state_t next_state_on_ack,
                    canbus_mode_t   mode_on_ack,
                    autoneg_state_t next_state_on_fail,
                    canbus_mode_t   mode_on_fail)
{
    /* Transmit exactly once per state entry */
    if (!an->probe_sent) {
        AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
        if (send_fn() > 0)
            an->probe_sent = 1;
        return this_state;
    }

    probe_result_t result = (probe_result_t)AN_LOAD_ACQ(&an->probe_result);
    if (result == PROBE_RESULT_PENDING &&
        an_elapsed(an, now_ms) >= AUTONEG_PROBE_TIMEOUT_MS)
        result = PROBE_RESULT_TIMEOUT;

    switch (result) {
    case PROBE_RESULT_PENDING:
        return this_state;

    case PROBE_RESULT_ACK:
        if (next_state_on_ack != AUTONEG_STATE_LOCKED) {
            /* Escalate to next probe phase */
            AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
            an->probe_retries = AUTONEG_FD_PROBE_RETRIES;
            an_enter(an, next_state_on_ack, now_ms);
            return next_state_on_ack;
        }
        an_commit(an, mode_on_ack, now_ms);
        return AUTONEG_STATE_LOCKED;

    default: /* ERROR or TIMEOUT */
        if (an->probe_retries > 0) {
            an->probe_retries--;
            an_enter(an, this_state, now_ms);
            return this_state;
        }
        if (next_state_on_fail != AUTONEG_STATE_LOCKED) {
            /* Downgrade to next probe phase */
            AN_STORE_RLX(&an->probe_result, (uint8_t)PROBE_RESULT_PENDING);
            an->probe_retries = AUTONEG_FD_PROBE_RETRIES;
            an_enter(an, next_state_on_fail, now_ms);
            return next_state_on_fail;
        }
        an_commit(an, mode_on_fail, now_ms);
        return AUTONEG_STATE_LOCKED;
    }
}

/* Wrappers — needed because probe_send_fn takes no args */
static int send_fd_no_brs(void) { return an_send_fd_probe(0); }
static int send_fd_brs(void)    { return an_send_fd_probe(1); }
static int send_xl(void)        { return an_send_xl_probe();  }

/* =========================================================================
 * PHASE 2 — FD_PROBE  (CiA 601 §5.3.4)
 *   ACK  → go to BRS_PROBE (if hw supports BRS) or lock FD_NO_BRS
 *   FAIL → lock CLASSIC
 * ========================================================================= */
static autoneg_state_t
phase_fd_probe(struct canbus_autoneg *an, uint32_t now_ms)
{
    autoneg_state_t next_ack = an->hw_supports_brs
        ? AUTONEG_STATE_BRS_PROBE : AUTONEG_STATE_LOCKED;
    canbus_mode_t   mode_ack = CANBUS_MODE_FD_NO_BRS;

    return phase_probe_generic(an, now_ms,
        send_fd_no_brs,
        AUTONEG_STATE_FD_PROBE,
        next_ack,       mode_ack,
        AUTONEG_STATE_LOCKED, CANBUS_MODE_CLASSIC);
}

/* =========================================================================
 * PHASE 3 — BRS_PROBE
 *   ACK  → lock FD_BRS
 *   FAIL → lock FD_NO_BRS (BRS not supported on this bus)
 * ========================================================================= */
static autoneg_state_t
phase_brs_probe(struct canbus_autoneg *an, uint32_t now_ms)
{
    return phase_probe_generic(an, now_ms,
        send_fd_brs,
        AUTONEG_STATE_BRS_PROBE,
        AUTONEG_STATE_LOCKED, CANBUS_MODE_FD_BRS,
        AUTONEG_STATE_LOCKED, CANBUS_MODE_FD_NO_BRS);
}

/* =========================================================================
 * PHASE 4 — XL_PROBE  (ISO 11898-1:2024 §10.5 / §12.2)
 *
 *   ACK  → lock XL
 *   FAIL → downgrade: try BRS_PROBE or FD_PROBE
 *
 * XL probe failure means either:
 *   (a) No XL node on bus: downgrade to FD/BRS probing.
 *   (b) FD nodes on bus generate form error on XLDF bit.
 *
 * After XL probe failure we restart FD detection from FD_PROBE so the
 * FD/BRS determination is made on the actual bus.
 * ========================================================================= */
static autoneg_state_t
phase_xl_probe(struct canbus_autoneg *an, uint32_t now_ms)
{
    autoneg_state_t next_fail = an->hw_supports_brs
        ? AUTONEG_STATE_BRS_PROBE
        : (an->hw_supports_fd ? AUTONEG_STATE_FD_PROBE : AUTONEG_STATE_LOCKED);
    canbus_mode_t mode_fail = CANBUS_MODE_CLASSIC;

    return phase_probe_generic(an, now_ms,
        send_xl,
        AUTONEG_STATE_XL_PROBE,
        AUTONEG_STATE_LOCKED, CANBUS_MODE_XL,
        next_fail, mode_fail);
}

/* =========================================================================
 * PHASE 5 — LOCKED
 * ========================================================================= */
static autoneg_state_t
phase_locked(struct canbus_autoneg *an, uint32_t now_ms)
{
    /* Consume bus-off flag set by IRQ */
    if (AN_LOAD_ACQ(&an->irq_flags.busoff_pending)) {
        AN_STORE_REL(&an->irq_flags.busoff_pending, 0u);
        an->renegotiation_count++;
        memset((void*)&an->obs, 0, sizeof(an->obs));
        AN_STORE_RLX(&an->consecutive_errors, 0u);
        an_enter(an, AUTONEG_STATE_BUSOFF_RECOVERY, now_ms);
        return AUTONEG_STATE_BUSOFF_RECOVERY;
    }

    if (AN_LOAD_RLX(&an->consecutive_errors) >= AUTONEG_RENEGOTIATE_ERRORS) {
        an->renegotiation_count++;
        AN_STORE_RLX(&an->consecutive_errors, 0u);
        memset((void*)&an->obs, 0, sizeof(an->obs));
        an_enter(an, AUTONEG_STATE_IDLE_WAIT, now_ms);
        return AUTONEG_STATE_IDLE_WAIT;
    }

    return AUTONEG_STATE_LOCKED;
}

/* =========================================================================
 * PHASE 6 — BUSOFF_RECOVERY  (ISO §12.1.7)
 * ========================================================================= */
static autoneg_state_t
phase_busoff_recovery(struct canbus_autoneg *an, uint32_t now_ms)
{
    uint32_t holdoff = AUTONEG_BUSOFF_HOLDOFF_MS(an->nom_bitrate);
    if (an_elapsed(an, now_ms) >= holdoff) {
        memset((void*)&an->obs, 0, sizeof(an->obs));
        an_enter(an, AUTONEG_STATE_IDLE_WAIT, now_ms);
        return AUTONEG_STATE_IDLE_WAIT;
    }
    return AUTONEG_STATE_BUSOFF_RECOVERY;
}

/* =========================================================================
 * canbus_autoneg_run — task tick
 * ========================================================================= */
autoneg_state_t
canbus_autoneg_run(struct canbus_autoneg *an, uint32_t now_ms)
{
    switch (an->state) {
    case AUTONEG_STATE_IDLE_WAIT:       return phase_idle_wait(an, now_ms);
    case AUTONEG_STATE_OBSERVING:       return phase_observing(an, now_ms);
    case AUTONEG_STATE_FD_PROBE:        return phase_fd_probe(an, now_ms);
    case AUTONEG_STATE_BRS_PROBE:       return phase_brs_probe(an, now_ms);
    case AUTONEG_STATE_XL_PROBE:        return phase_xl_probe(an, now_ms);
    case AUTONEG_STATE_LOCKED:          return phase_locked(an, now_ms);
    case AUTONEG_STATE_BUSOFF_RECOVERY: return phase_busoff_recovery(an, now_ms);
    default:                            return an->state;
    }
}

/* =========================================================================
 * IRQ-safe entry points — lock-free atomics only, no state mutation
 * ========================================================================= */
void
canbus_autoneg_rx_frame(struct canbus_autoneg *an, const struct canbus_msg *msg)
{
    if (msg->flags & CANMSG_FLAG_XL)
        AN_FADD_RLX(&an->obs.xl_frames, 1u);
    else if (msg->flags & CANMSG_FLAG_FD)
        AN_FADD_RLX(&an->obs.fd_frames, 1u);
    else
        AN_FADD_RLX(&an->obs.classic_frames, 1u);

    AN_FADD_RLX(&an->obs.frames_total, 1u);
    AN_STORE_RLX(&an->consecutive_errors, 0u);
}

void
canbus_autoneg_bus_error(struct canbus_autoneg *an, uint8_t error_type)
{
    if (error_type == AUTONEG_ERR_BUSOFF) {
        AN_STORE_REL(&an->irq_flags.busoff_pending, 1u);
        return;
    }
    if (error_type == AUTONEG_ERR_FORM  ||
        error_type == AUTONEG_ERR_CRC   ||
        error_type == AUTONEG_ERR_STUFF)
        AN_FADD_RLX(&an->consecutive_errors, 1u);

    autoneg_state_t st = (autoneg_state_t)AN_LOAD_ACQ(&an->state);
    if (st == AUTONEG_STATE_FD_PROBE  ||
        st == AUTONEG_STATE_BRS_PROBE ||
        st == AUTONEG_STATE_XL_PROBE) {
        uint8_t exp = (uint8_t)PROBE_RESULT_PENDING;
        uint8_t des = (uint8_t)PROBE_RESULT_ERROR;
        AN_CAS(&an->probe_result, &exp, des);
    }
}

void
canbus_autoneg_probe_ack(struct canbus_autoneg *an)
{
    autoneg_state_t st = (autoneg_state_t)AN_LOAD_ACQ(&an->state);
    if (st == AUTONEG_STATE_FD_PROBE  ||
        st == AUTONEG_STATE_BRS_PROBE ||
        st == AUTONEG_STATE_XL_PROBE) {
        uint8_t exp = (uint8_t)PROBE_RESULT_PENDING;
        uint8_t des = (uint8_t)PROBE_RESULT_ACK;
        AN_CAS(&an->probe_result, &exp, des);
    }
}

void
canbus_autoneg_force_renegotiate(struct canbus_autoneg *an, uint32_t now_ms)
{
    irqstatus_t flags = irq_save();
    memset((void*)&an->obs, 0, sizeof(an->obs));
    AN_STORE_RLX(&an->consecutive_errors, 0u);
    AN_STORE_RLX(&an->irq_flags.busoff_pending,    0u);
    AN_STORE_RLX(&an->irq_flags.form_error_pending, 0u);
    an->renegotiation_count++;
    an_enter(an, AUTONEG_STATE_IDLE_WAIT, now_ms);
    irq_restore(flags);
}

/* =========================================================================
 * Diagnostic helpers
 * ========================================================================= */
const char *
canbus_autoneg_state_name(autoneg_state_t s)
{
    switch (s) {
    case AUTONEG_STATE_IDLE_WAIT:       return "IDLE_WAIT";
    case AUTONEG_STATE_OBSERVING:       return "OBSERVING";
    case AUTONEG_STATE_FD_PROBE:        return "FD_PROBE";
    case AUTONEG_STATE_BRS_PROBE:       return "BRS_PROBE";
    case AUTONEG_STATE_XL_PROBE:        return "XL_PROBE";
    case AUTONEG_STATE_LOCKED:          return "LOCKED";
    case AUTONEG_STATE_BUSOFF_RECOVERY: return "BUSOFF_RECOVERY";
    default:                            return "UNKNOWN";
    }
}

const char *
canbus_autoneg_mode_name(canbus_mode_t m)
{
    switch (m) {
    case CANBUS_MODE_CLASSIC:   return "CAN_2.0";
    case CANBUS_MODE_FD_NO_BRS: return "CAN_FD";
    case CANBUS_MODE_FD_BRS:    return "CAN_FD_BRS";
    case CANBUS_MODE_XL:        return "CAN_XL";
    default:                    return "UNKNOWN";
    }
}
