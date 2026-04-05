/*
 * canserial.c — serial-over-CAN: CAN 2.0 / CAN FD / CAN XL
 *
 * ISO 11898-1:2003 / 2015 / 2024 compliant.
 *
 * CAN XL additions (ISO 11898-1:2024 §10.5):
 *   • TX ring payload expanded to 4096 bytes to accommodate XL burst traffic.
 *   • RX ring expanded to 4096 bytes (max XL frame = 2048 bytes payload).
 *   • tx_drain_one() builds XL frames with correct SDT/VCID/AF header.
 *   • CANMSG_DATA_LEN macro already handles XL DLC table via canxl_dlc_to_len.
 *   • Admin commands sent as CAN 2.0 (SDT/VCID irrelevant for admin).
 *
 * All RT/concurrency properties from FD implementation are preserved:
 *   • Shadow double-buffer TX: no memmove in IRQ context.
 *   • encode+enqueue in one critical section (SAFETY-3).
 *   • Retained parse_pos across rx_task invocations (PERF-3).
 *   • ADMIN_QUEUE_SZ power-of-two enforced at compile time (PERF-2).
 *
 * Copyright (C) 2019 Eug Krashtan <eug.krashtan@gmail.com>
 * Copyright (C) 2020 Pontus Borg <glpontus@gmail.com>
 * Copyright (C) 2021  Kevin O'Connor <kevin@koconnor.net>
 * Copyright (C) 2024  Extended for CAN XL
 * This file may be distributed under the terms of the GNU GPLv3 license.
 */

#include <string.h>
#include "autoconf.h"
#include "core/generic/io.h"
#include "core/generic/irq.h"
#include "core/generic/misc.h"
#include "core/generic/canbus.h"
#include "core/generic/canserial.h"
#include "core/base/command.h"
#include "lib/fast-hash/fasthash.h"
#include "core/base/sched.h"
#include "core/common/compiler.h"

#pragma GCC optimize("O3")

#define CANBUS_UUID_LEN  6u

/* =========================================================================
 * Buffer sizing
 *
 * TX/RX rings are 4096 bytes to accommodate CAN XL frames (≤2048 payload).
 * At ≤1 Mbit/s nominal + 10 Mbit/s data (XL), one 2048-byte frame ≈ 1.7 ms.
 * Two frames in-flight → 4096 bytes minimum ring size.
 * ========================================================================= */
#define TX_BUF_SIZE    4096u   /* per shadow half                             */
#define RX_BUF_SIZE    4096u   /* data ring                                   */
#define ADMIN_QUEUE_SZ    8u   /* must be power-of-two                        */

STATIC_ASSERT((ADMIN_QUEUE_SZ & (ADMIN_QUEUE_SZ - 1u)) == 0u,
              ADMIN_QUEUE_SZ_must_be_power_of_two);

#define CACHE_LINE  64u
#define CACHE_ALIGN __attribute__((aligned(CACHE_LINE)))

/* =========================================================================
 * Shadow double-buffer TX  (prevents memmove in IRQ context)
 *
 * Two physical halves per priority.
 * TX task drains the "active" half; console_sendf() fills the other.
 * Swap is atomic (byte index flip).
 * ========================================================================= */
struct tx_half {
    uint32_t pos;
    uint32_t max;
    uint8_t  buf[TX_BUF_SIZE];
} CACHE_ALIGN;

struct tx_shadow {
    struct tx_half   half[2];
    volatile uint8_t active;   /* index of half being drained by TX task      */
    uint8_t          _pad[3];
} CACHE_ALIGN;

/* =========================================================================
 * Main state block
 * ========================================================================= */
static struct candata {
    /* Identity — cold */
    uint32_t          assigned_id;
    uint32_t          max_frame_dlen;
    canbus_mode_t     active_mode;
    uint8_t           uuid[CANBUS_UUID_LEN];
    uint8_t           _pad_id[2];

    /* TX — shadow double-buffers per priority level */
    struct task_wake  tx_wake;
    struct tx_shadow  tx[CANBUS_PRIO_COUNT];

    /* RX — data ring + admin queue */
    struct {
        struct task_wake wake;
        volatile uint32_t pos;       /* IRQ write head                        */
        uint32_t          parse_pos; /* task read head (retained)             */
        volatile uint32_t admin_push;
        uint32_t          admin_pull;
        struct canbus_msg admin_queue[ADMIN_QUEUE_SZ];
        uint8_t           buf[RX_BUF_SIZE];
    } rx CACHE_ALIGN;

} CanData CACHE_ALIGN;

/* =========================================================================
 * Introspection
 * ========================================================================= */
int
console_get_output_usage(void)
{
    int used = 0;
    for (int p = 0; p < CANBUS_PRIO_COUNT; p++) {
        const struct tx_half *h = &CanData.tx[p].half[CanData.tx[p].active];
        used += (int)(h->max - h->pos);
    }
    return used;
}

int canserial_get_tx_pending(void) { return console_get_output_usage(); }

int canserial_get_tx_free(void) {
    uint8_t fi = CanData.tx[CANBUS_PRIO_NORMAL].active ^ 1u;
    return (int)(TX_BUF_SIZE - CanData.tx[CANBUS_PRIO_NORMAL].half[fi].max);
}

/* =========================================================================
 * Mode setter
 * ========================================================================= */
void
canserial_set_mode(canbus_mode_t mode)
{
    CanData.active_mode    = mode;
    switch (mode) {
    case CANBUS_MODE_XL:        CanData.max_frame_dlen = CANXL_MAX_DLEN; break;
    case CANBUS_MODE_FD_BRS:
    case CANBUS_MODE_FD_NO_BRS: CanData.max_frame_dlen = CANFD_MAX_DLEN; break;
    default:                    CanData.max_frame_dlen = CAN20_MAX_DLEN;  break;
    }
}

/* =========================================================================
 * TX task — drains shadow halves in HIGH→NORMAL→LOW order
 * ========================================================================= */
void
canserial_notify_tx(void) { sched_wake_task(&CanData.tx_wake); }

/*
 * tx_drain_one — drain one frame from the active half of a priority ring.
 *
 * Builds the correct frame type for the active bus mode:
 *   CAN 2.0  : id, dlc ≤ 8, no flags
 *   CAN FD   : id, dlc (FD table), CANMSG_FLAG_FD [+ BRS]
 *   CAN XL   : id, dlc (XL table), CANMSG_FLAG_XL, xl.sdt/vcid/af
 *
 * For CAN XL frames, the canserial layer uses:
 *   SDT = CANXL_SDT_ADMIN  (Klipper admin protocol)
 *   VCID = 0               (default virtual channel)
 *   AF   = assigned_id     (acceptance field = node CAN ID for routing)
 *
 * Critical section bounded to memcpy of one frame payload (≤2048 bytes).
 * No memmove ever executes inside the lock.
 */
static int
tx_drain_one(struct tx_shadow *ts, uint32_t id)
{
    uint8_t        ai = ts->active;
    struct tx_half *h = &ts->half[ai];

    irqstatus_t flags = irq_save();
    uint32_t tpos = h->pos;
    uint32_t tmax = h->max;
    irq_restore(flags);

    int avail = (int)(tmax - tpos);
    if (avail <= 0) {
        /* Active half empty — try swap with fill half */
        uint8_t fi = ai ^ 1u;
        struct tx_half *fill = &ts->half[fi];
        flags = irq_save();
        if (fill->max > fill->pos) {
            ts->active = fi;
            h->pos = h->max = 0;
        }
        irq_restore(flags);
        return 0;
    }

    struct canbus_msg msg;
    memset(&msg, 0, sizeof(msg));
    msg.id = id + 1u;

    uint32_t frame_dlen = CanData.max_frame_dlen;
    int now = (avail > (int)frame_dlen) ? (int)frame_dlen : avail;

    switch (CanData.active_mode) {
    case CANBUS_MODE_XL: {
        /*
         * CAN XL frame (ISO 11898-1:2024 §10.5)
         * Payload must be 1..2048 bytes; pad to 4-byte boundary on bus
         * (hardware handles padding transparently).
         */
        uint32_t dlc = canxl_len_to_dlc((uint32_t)now);
        msg.dlc      = dlc;
        msg.flags    = CANMSG_FLAG_XL;
        msg.xl.sdt   = CANXL_SDT_ADMIN;
        msg.xl.vcid  = 0u;
        msg.xl.af    = id;               /* routing token = this node's CAN ID */
        memcpy(msg.data, &h->buf[tpos], (uint32_t)now);
        /* Zero-pad to 4-byte boundary for bus alignment */
        uint32_t padded = CANXL_PAD_LEN((uint32_t)now);
        if (padded > (uint32_t)now)
            memset(&msg.data[now], 0, padded - (uint32_t)now);
        break;
    }
    case CANBUS_MODE_FD_BRS:
    case CANBUS_MODE_FD_NO_BRS: {
        uint32_t dlc     = canfd_len_to_dlc((uint32_t)now);
        uint32_t rounded = canfd_dlc_to_len(dlc);
        msg.dlc   = dlc;
        msg.flags = CANMSG_FLAG_FD;
        if (CanData.active_mode == CANBUS_MODE_FD_BRS)
            msg.flags |= CANMSG_FLAG_BRS;
        memcpy(msg.data, &h->buf[tpos], (uint32_t)now);
        if (rounded > (uint32_t)now)
            memset(&msg.data[now], 0, rounded - (uint32_t)now);
        break;
    }
    default:  /* CANBUS_MODE_CLASSIC */
        msg.dlc = (uint32_t)now;
        memcpy(msg.data, &h->buf[tpos], (uint32_t)now);
        break;
    }

    int ret = canbus_send(&msg);
    if (ret > 0) {
        flags = irq_save();
        h->pos = tpos + (uint32_t)now;
        irq_restore(flags);
    }
    return ret;
}

void
canserial_tx_task(void)
{
#if NUM_CPUS > 1
    if (sched_get_cpuid() != 1) return;
#endif
    if (unlikely(!sched_check_wake(&CanData.tx_wake)))
        return;

    uint32_t id = CanData.assigned_id;
    if (unlikely(!id)) {
        for (int p = 0; p < CANBUS_PRIO_COUNT; p++) {
            CanData.tx[p].half[0].pos = CanData.tx[p].half[0].max = 0;
            CanData.tx[p].half[1].pos = CanData.tx[p].half[1].max = 0;
        }
        return;
    }

    for (;;) {
        int sent = 0;
        for (int p = 0; p < CANBUS_PRIO_COUNT; p++) {
            int ret = tx_drain_one(&CanData.tx[p], id);
            if (ret < 0) { sched_wake_task(&CanData.tx_wake); return; }
            if (ret > 0) { sent = 1; break; }
        }
        if (!sent) break;
    }
}
DECL_TASK(canserial_tx_task);

/*
 * console_sendf — encode + enqueue in one critical section (SAFETY-3).
 * Writes into the fill half (not being drained by TX task).
 */
void
console_sendf(const struct command_encoder *ce, va_list args)
{
    struct tx_shadow *ts  = &CanData.tx[CANBUS_PRIO_NORMAL];
    uint32_t          msz = ce->max_size;

    irqstatus_t flags = irq_save();

    uint8_t        fi   = ts->active ^ 1u;
    struct tx_half *fill = &ts->half[fi];

    if (fill->pos >= fill->max) fill->pos = fill->max = 0;

    if (unlikely(fill->max + msz > TX_BUF_SIZE)) {
        irq_restore(flags);
        return;
    }

    uint32_t msglen = command_encode_and_frame(&fill->buf[fill->max], ce, args);
    fill->max += msglen;

    irq_restore(flags);
    canserial_notify_tx();
}

/* =========================================================================
 * Admin command handling (uses CAN 2.0 frames on all bus types)
 * ========================================================================= */
#define CANBUS_CMD_QUERY_UNASSIGNED    0x00u
#define CANBUS_CMD_SET_KLIPPER_NODEID  0x01u
#define CANBUS_CMD_REQUEST_BOOTLOADER  0x02u
#define CANBUS_RESP_NEED_NODEID        0x20u

static int can_check_uuid(struct canbus_msg *msg) {
    if (CANMSG_DATA_LEN(msg) < 7u) return 0;
    return (memcmp(&msg->data[1], CanData.uuid, CANBUS_UUID_LEN) == 0);
}
static int     can_get_nodeid(void) {
    return CanData.assigned_id ? (int)((CanData.assigned_id - 0x100u) >> 1) : 0;
}
static uint32_t can_decode_nodeid(int n) { return ((uint32_t)n << 1) + 0x100u; }

static void
can_process_query_unassigned(struct canbus_msg *msg)
{
    if (CanData.assigned_id) return;
    struct canbus_msg send;
    memset(&send, 0, sizeof(send));
    send.id      = CANBUS_ID_ADMIN_RESP;
    send.dlc     = 8;
    /* Force classic frame for admin — compatible with all bus types */
    send.flags   = 0;
    send.data[0] = CANBUS_RESP_NEED_NODEID;
    memcpy(&send.data[1], CanData.uuid, CANBUS_UUID_LEN);
    send.data[7] = CANBUS_CMD_SET_KLIPPER_NODEID;
    for (;;) { if (canbus_send_prio(&send, CANBUS_PRIO_HIGH) >= 0) return; }
}

static void can_id_conflict(void) {
    CanData.assigned_id = 0;
    canbus_set_filter(0);
    shutdown("Another CAN node assigned this ID");
}

static void
can_process_set_klipper_nodeid(struct canbus_msg *msg)
{
    if (CANMSG_DATA_LEN(msg) < 8u) return;
    uint32_t newid = can_decode_nodeid(msg->data[7]);
    if (can_check_uuid(msg)) {
        if (newid != CanData.assigned_id) {
            CanData.assigned_id = newid;
            canbus_set_filter(CanData.assigned_id);
        }
    } else if (newid == CanData.assigned_id) {
        can_id_conflict();
    }
}

static void
can_process_request_bootloader(struct canbus_msg *msg) {
    if (!CONFIG_HAVE_BOOTLOADER_REQUEST || !can_check_uuid(msg)) return;
    bootloader_request();
}

static void
can_process_admin(struct canbus_msg *msg) {
    if (!CANMSG_DATA_LEN(msg)) return;
    switch (msg->data[0]) {
    case CANBUS_CMD_QUERY_UNASSIGNED:   can_process_query_unassigned(msg);   break;
    case CANBUS_CMD_SET_KLIPPER_NODEID: can_process_set_klipper_nodeid(msg); break;
    case CANBUS_CMD_REQUEST_BOOTLOADER: can_process_request_bootloader(msg); break;
    }
}

/* =========================================================================
 * RX path — HOT PATH (IRQ context)
 *
 * Frame demultiplexing for all three generations:
 *   id == assigned_id        → data frame, enqueue in RX ring
 *   id == CANBUS_ID_ADMIN    → admin frame, enqueue in admin queue
 *   id == assigned_id + 1    → admin echo / ID conflict check
 *
 * For CAN XL frames, VCID filtering can be added here if
 * hw_supports_vcid=0 (software VCID filter fallback).
 * ========================================================================= */
static void canserial_notify_rx(void) { sched_wake_task(&CanData.rx.wake); }

DECL_CONSTANT("RECEIVE_WINDOW", ARRAY_SIZE(CanData.rx.buf));

uint32_t canserial_get_assigned_id(void) { return CanData.assigned_id; }

uint8_t *
canserial_get_write_ptr(uint32_t len) {
    uint32_t rpos = CanData.rx.pos;
    if (unlikely(len > RX_BUF_SIZE - rpos)) return NULL;
    return &CanData.rx.buf[rpos];
}

void
canserial_commit_write(uint32_t len) {
    __atomic_fetch_add(&CanData.rx.pos, len, __ATOMIC_RELEASE);
    canserial_notify_rx();
}

void
canserial_process_data(struct canbus_msg *msg)
{
    uint32_t id          = msg->id;
    uint32_t assigned_id = CanData.assigned_id;

    if (likely(assigned_id && id == assigned_id)) {
        /*
         * Data frame.  CANMSG_DATA_LEN handles all three generations:
         *   CAN 2.0  : dlc ≤ 8
         *   CAN FD   : canfd_dlc_to_len(dlc) ≤ 64
         *   CAN XL   : canxl_dlc_to_len(dlc) ≤ 2048
         *
         * For XL frames we receive the logical payload (controller strips
         * the XL header fields SDT/VCID/AF before DMA to memory).
         * The xl_hdr fields in msg are populated by the HW driver.
         */
        uint32_t len  = CANMSG_DATA_LEN(msg);
        uint32_t rpos = __atomic_load_n(&CanData.rx.pos, __ATOMIC_RELAXED);

        if (unlikely(len > RX_BUF_SIZE - rpos)) return;

        memcpy(&CanData.rx.buf[rpos], msg->data, len);
        __atomic_store_n(&CanData.rx.pos, rpos + len, __ATOMIC_RELEASE);
        canserial_notify_rx();

    } else if (id == CANBUS_ID_ADMIN ||
               (assigned_id && id == assigned_id + 1u)) {

        uint32_t push = __atomic_load_n(&CanData.rx.admin_push, __ATOMIC_RELAXED);
        if (push >= CanData.rx.admin_pull + ADMIN_QUEUE_SZ) return;

        CanData.rx.admin_queue[push & (ADMIN_QUEUE_SZ - 1u)] = *msg;
        __atomic_store_n(&CanData.rx.admin_push, push + 1u, __ATOMIC_RELEASE);
        canserial_notify_rx();
    }
}

/* =========================================================================
 * RX task — retained parse_pos avoids redundant scanning (PERF-3)
 * ========================================================================= */
void
canserial_rx_task(void)
{
#if NUM_CPUS > 1
    if (sched_get_cpuid() != 1) return;
#endif
    if (!sched_check_wake(&CanData.rx.wake)) return;

    /* Drain admin queue */
    for (;;) {
        uint32_t push = __atomic_load_n(&CanData.rx.admin_push, __ATOMIC_ACQUIRE);
        uint32_t pull = CanData.rx.admin_pull;
        if (push == pull) break;

        struct canbus_msg *m =
            &CanData.rx.admin_queue[pull & (ADMIN_QUEUE_SZ - 1u)];

        if (CanData.assigned_id && m->id == CanData.assigned_id + 1u)
            can_id_conflict();
        else if (m->id == CANBUS_ID_ADMIN)
            can_process_admin(m);

        __atomic_store_n(&CanData.rx.admin_pull, pull + 1u, __ATOMIC_RELEASE);
    }

    /* Process data ring */
    uint32_t write_pos = __atomic_load_n(&CanData.rx.pos, __ATOMIC_ACQUIRE);
    uint32_t parse_pos = CanData.rx.parse_pos;

    if (unlikely(write_pos < parse_pos)) {
        /* State corruption — reset */
        CanData.rx.parse_pos = 0;
        irqstatus_t f = irq_save();
        CanData.rx.pos = 0;
        irq_restore(f);
        return;
    }

    uint32_t count = write_pos - parse_pos;
    if (!count) return;

    uint_fast8_t pop_count;
    int ret = command_find_block(&CanData.rx.buf[parse_pos], count, &pop_count);

    if (ret > 0) {
        command_dispatch(&CanData.rx.buf[parse_pos], pop_count);
        CanData.rx.parse_pos = parse_pos + pop_count;
        command_send_ack();
    } else if (ret < 0) {
        CanData.rx.parse_pos = parse_pos + pop_count;
    }
    /* ret == 0: incomplete block — retain parse_pos */

    /* Compact when parse_pos exceeds half the ring */
    uint32_t pp = CanData.rx.parse_pos;
    if (pp > RX_BUF_SIZE / 2u) {
        irqstatus_t f = irq_save();
        uint32_t wp        = CanData.rx.pos;
        uint32_t remaining = (wp > pp) ? (wp - pp) : 0u;
        if (remaining) memmove(&CanData.rx.buf[0], &CanData.rx.buf[pp], remaining);
        CanData.rx.pos       = remaining;
        CanData.rx.parse_pos = 0;
        irq_restore(f);
    }
}
DECL_TASK(canserial_rx_task);

/* =========================================================================
 * Identity, commands, shutdown
 * ========================================================================= */
void command_get_canbus_id(uint32_t *args) {
    sendf("canbus_id canbus_uuid=%.*s canbus_nodeid=%u",
          (int)sizeof(CanData.uuid), CanData.uuid, can_get_nodeid());
}
DECL_COMMAND_FLAGS(command_get_canbus_id, HF_IN_SHUTDOWN, "get_canbus_id");

void command_get_canbus_stats(uint32_t *args) {
    struct canbus_stats s;
    canbus_query_stats(&s);
    sendf("canbus_stats rx=%u tx=%u rx_fd=%u tx_fd=%u rx_xl=%u tx_xl=%u"
          " overflow=%u bus_err=%u crc=%u xl_crc32=%u"
          " lat_min=%u lat_max=%u lat_avg=%u tec=%u rec=%u err_state=%u",
          s.rx_packets, s.tx_packets,
          s.rx_fd_packets, s.tx_fd_packets,
          s.rx_xl_packets, s.tx_xl_packets,
          s.fifo_overflows, s.bus_errors, s.crc_errors, s.xl_crc32_errors,
          s.tx_latency_min_us, s.tx_latency_max_us, s.tx_latency_avg_us,
          (uint32_t)s.tec, (uint32_t)s.rec, s.error_state);
}
DECL_COMMAND_FLAGS(command_get_canbus_stats, HF_IN_SHUTDOWN, "get_canbus_stats");

void canserial_set_uuid(uint8_t *raw_uuid, uint32_t raw_uuid_len) {
    uint64_t hash = fasthash64(raw_uuid, raw_uuid_len, 0xA16231A7);
    memcpy(CanData.uuid, &hash, sizeof(CanData.uuid));
    canserial_notify_rx();
}

void canserial_shutdown(void) {
    canserial_notify_tx();
    canserial_notify_rx();
}
DECL_SHUTDOWN(canserial_shutdown);
