#ifndef __CANBUS_AUTONEG_H__
#define __CANBUS_AUTONEG_H__

/*
 * canbus_autoneg.h — CAN bus mode auto-detection: 2.0 / FD / XL
 *
 * Normative references:
 *   ISO 11898-1:2015 §9.3.3    Bus idle — 11 recessive bits
 *   ISO 11898-1:2015 §10.4.2   FD frame / FDF bit
 *   ISO 11898-1:2015 §10.4.2.8 res bit: ISO FD=0, non-ISO FD=1
 *   ISO 11898-1:2015 §12.1.4   Error types (applies to all generations)
 *   ISO 11898-1:2015 §12.1.7   Bus-off recovery (128×11 recessive bits)
 *   ISO 11898-1:2024 §10.5     CAN XL frame format
 *   ISO 11898-1:2024 §10.5.2.1 XLDF bit position in control field
 *   ISO 11898-1:2024 §12.2     Coexistence of 2.0/FD/XL on the same bus
 *   ISO 11898-2:2016 §6.4      Bus-idle detection
 *   CiA 601-1:2018   §5.3      Node start-up procedure
 *   CiA 611-1:2021   §4        XL SDT registry
 *
 * ─────────────────────────────────────────────────────────────────────────
 * DETECTION SEQUENCE  (passive-first, ISO-compliant)
 * ─────────────────────────────────────────────────────────────────────────
 *
 *  Phase 0  IDLE_WAIT      Wait for bus-idle (11 recessive bits, §9.3.3)
 *  Phase 1  OBSERVING      Classify incoming frames: 2.0 / FD / XL
 *  Phase 2  FD_PROBE       Active probe with FD frame (no-BRS)
 *  Phase 3  BRS_PROBE      Active probe with FD frame (BRS=1)
 *  Phase 4  XL_PROBE       Active probe with XL frame (XLDF=1)
 *  Phase 5  LOCKED         Normal operation; monitors for re-trigger
 *  Phase 6  BUSOFF_RECOVERY ISO §12.1.7 hold-off, then restart from 0
 *
 * ─────────────────────────────────────────────────────────────────────────
 * COEXISTENCE RULES  (ISO 11898-1:2024 §12.2)
 * ─────────────────────────────────────────────────────────────────────────
 *  • A CAN 2.0 node receives an XL frame as a form error (XLDF bit),
 *    generates an error frame.  The XL node retransmits.
 *  • Mixed 2.0/XL buses are not recommended (CiA 611).
 *  • Mixed FD/XL buses work if all nodes are at least FD-capable.
 *    FD nodes that are not XL-capable will generate form errors on XL
 *    frames — same as 2.0 vs XL.
 *  • Our auto-detection probe strategy:
 *    If XL probe returns error → check whether FD probe ACKs (fall to FD).
 *    If FD probe also errors → fall to CAN 2.0.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * CONCURRENCY INVARIANTS  (all preserved from FD implementation)
 * ─────────────────────────────────────────────────────────────────────────
 *  • IRQ context: only __atomic ops on counters and flag bytes.
 *  • Task context: sole owner of state, timing, probe_sent, probe_retries.
 *  • IRQ→task signalling: irq_flags struct, consumed on each task tick.
 *  • No timing arithmetic in IRQ context.
 *  • Probe transmitted exactly once per state entry (probe_sent flag).
 */

#include <stdint.h>
#include "core/generic/canbus.h"

/* =========================================================================
 * Tuning constants
 * ========================================================================= */
#define AUTONEG_OBS_FRAMES            8u   /* frames to classify before deciding */
#define AUTONEG_OBS_TIMEOUT_MS      500u   /* observation window timeout         */
#define AUTONEG_FD_PROBE_RETRIES      3u   /* CiA 601 §5.3.4                     */
#define AUTONEG_XL_PROBE_RETRIES      3u   /* same policy for XL probe           */
#define AUTONEG_PROBE_TIMEOUT_MS     20u   /* per-probe ACK timeout              */
#define AUTONEG_RENEGOTIATE_ERRORS   16u   /* consecutive errors → re-negotiate  */

/* Probe frame IDs */
#define AUTONEG_PROBE_ID      CANBUS_ID_ADMIN   /* FD probe CAN ID               */
#define AUTONEG_XL_PROBE_ID   CANBUS_ID_ADMIN   /* XL probe CAN ID               */

/*
 * Bus-off recovery hold-off [ms]  (ISO 11898-1 §12.1.7)
 * Formula: ceil(128 × 11 × 1.1 / nom_bitrate_bps × 1000) ms
 * The XL spec does not change this value — bus-off recovery is the same
 * for all three frame generations on the same physical bus.
 */
#define AUTONEG_BUSOFF_HOLDOFF_MS(nom_bps) \
    ((uint32_t)(((uint64_t)128u * 11u * 1100000u) / (nom_bps) / 1000u + 1u))

/* =========================================================================
 * State machine
 * ========================================================================= */
typedef enum {
    AUTONEG_STATE_IDLE_WAIT       = 0,
    AUTONEG_STATE_OBSERVING       = 1,
    AUTONEG_STATE_FD_PROBE        = 2,
    AUTONEG_STATE_BRS_PROBE       = 3,
    AUTONEG_STATE_XL_PROBE        = 4,  /* NEW — ISO 11898-1:2024 §10.5        */
    AUTONEG_STATE_LOCKED          = 5,
    AUTONEG_STATE_BUSOFF_RECOVERY = 6,
} autoneg_state_t;

/* =========================================================================
 * Observation counters (atomic, IRQ-written)
 * ========================================================================= */
struct autoneg_obs {
    volatile uint32_t classic_frames;
    volatile uint32_t fd_frames;
    volatile uint32_t xl_frames;        /* frames with CANMSG_FLAG_XL set     */
    volatile uint32_t frames_total;
};

/* =========================================================================
 * Probe result (IRQ → task, atomic)
 * ========================================================================= */
typedef enum {
    PROBE_RESULT_PENDING = 0,
    PROBE_RESULT_ACK     = 1,
    PROBE_RESULT_ERROR   = 2,
    PROBE_RESULT_TIMEOUT = 3,
} probe_result_t;

/* =========================================================================
 * IRQ → task pending flags (lock-free, no state mutation in IRQ)
 * ========================================================================= */
struct autoneg_irq_flags {
    volatile uint8_t busoff_pending;
    volatile uint8_t form_error_pending; /* form error = probable mode mismatch */
    uint8_t _pad[2];
};

/* =========================================================================
 * Auto-negotiation context
 * ========================================================================= */
struct canbus_autoneg {
    /* State — task context only */
    autoneg_state_t   state;
    canbus_mode_t     detected_mode;

    /* HW capabilities (init-time, read-only) */
    uint8_t           hw_supports_fd;
    uint8_t           hw_supports_brs;
    uint8_t           hw_supports_tdc;
    uint8_t           hw_is_iso_fd;
    uint8_t           hw_supports_xl;   /* 1 if HW can generate/receive XL    */
    uint8_t           hw_supports_vcid;
    uint8_t           _pad[2];
    uint32_t          nom_bitrate;

    /* Timing (task context only) */
    uint32_t          state_entry_ms;

    /* Observation (IRQ writes, task reads) */
    struct autoneg_obs obs;

    /* Probe state (task context, except probe_result) */
    uint8_t           probe_retries;
    uint8_t           probe_sent;
    uint8_t           _pad2[2];
    volatile uint8_t  probe_result;

    /* Consecutive errors (atomic) */
    volatile uint32_t consecutive_errors;

    /* IRQ→task flags */
    struct autoneg_irq_flags irq_flags;

    /* Diagnostics */
    uint32_t          negotiation_count;
    uint32_t          renegotiation_count;

    /* Mode-change callback */
    void (*on_mode_change)(canbus_mode_t mode, void *ctx);
    void *on_mode_change_ctx;
};

/* =========================================================================
 * Error codes (hw IRQ → canbus_autoneg_bus_error)
 * ========================================================================= */
#define AUTONEG_ERR_STUFF   0u   /* ISO 11898-1 §12.1.4.1                     */
#define AUTONEG_ERR_FORM    1u   /* ISO 11898-1 §12.1.4.2 — also XLDF mismatch */
#define AUTONEG_ERR_ACK     2u   /* ISO 11898-1 §12.1.4.3                     */
#define AUTONEG_ERR_BIT1    3u   /* ISO 11898-1 §12.1.4.4                     */
#define AUTONEG_ERR_BIT0    4u   /* ISO 11898-1 §12.1.4.5                     */
#define AUTONEG_ERR_CRC     5u   /* ISO 11898-1 §12.1.4.6 (CRC-15/17/21/32)  */
#define AUTONEG_ERR_BUSOFF  6u   /* ISO 11898-1 §12.1.7                       */

/* =========================================================================
 * Public API
 * ========================================================================= */
void canbus_autoneg_init(struct canbus_autoneg *an,
                         const struct canbus_hw_caps *caps,
                         void (*on_mode_change)(canbus_mode_t, void *),
                         void *ctx);

autoneg_state_t canbus_autoneg_run(struct canbus_autoneg *an, uint32_t now_ms);

/* IRQ-safe — atomic counter updates only */
void canbus_autoneg_rx_frame(struct canbus_autoneg *an,
                              const struct canbus_msg *msg);

/* IRQ-safe — sets pending flag, no state mutation */
void canbus_autoneg_bus_error(struct canbus_autoneg *an, uint8_t error_type);

/* IRQ-safe — atomic CAS on probe_result */
void canbus_autoneg_probe_ack(struct canbus_autoneg *an);

/* Task / init context */
void canbus_autoneg_force_renegotiate(struct canbus_autoneg *an, uint32_t now_ms);

static inline int
canbus_autoneg_is_locked(const struct canbus_autoneg *an)
{
    return (an->state == AUTONEG_STATE_LOCKED);
}

static inline canbus_mode_t
canbus_autoneg_get_mode(const struct canbus_autoneg *an)
{
    return an->detected_mode;
}

const char *canbus_autoneg_state_name(autoneg_state_t s);
const char *canbus_autoneg_mode_name(canbus_mode_t m);

#endif /* __CANBUS_AUTONEG_H__ */
