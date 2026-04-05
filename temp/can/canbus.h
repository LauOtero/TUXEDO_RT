#ifndef __CANBUS_H__
#define __CANBUS_H__

/*
 * canbus.h — unified CAN 2.0 / CAN FD / CAN XL interface
 *
 * Normative references:
 *   ISO 11898-1:2003  Classic CAN data link layer
 *   ISO 11898-1:2015  CAN FD data link layer (§10.4)
 *   ISO 11898-1:2024  CAN XL data link layer  (§10.5)
 *   ISO 11898-2:2016  High-speed physical layer
 *   CiA 601-1:2018    CAN FD system design recommendations
 *   CiA 611-1:2021    CAN XL application layer (SDT registry)
 *   CiA 103:2011      Bit-time specification
 */

#include <stdint.h>

/* =========================================================================
 * Static assertions
 * ========================================================================= */
#ifndef STATIC_ASSERT
#  define STATIC_ASSERT(cond, msg) \
     typedef char static_assert_##msg[(cond) ? 1 : -1]
#endif

/* =========================================================================
 * Operating modes — ordered by capability (used as comparison operands)
 *
 * A higher numeric value means more capability; comparisons like
 *   mode >= CANBUS_MODE_FD_NO_BRS
 * are intentional and correct.
 * ========================================================================= */
typedef enum {
    CANBUS_MODE_CLASSIC   = 0, /* ISO 11898-1:2003  — max 8-byte payload      */
    CANBUS_MODE_FD_NO_BRS = 1, /* ISO 11898-1:2015  — ≤64 bytes, single rate  */
    CANBUS_MODE_FD_BRS    = 2, /* ISO 11898-1:2015  — ≤64 bytes, BRS          */
    CANBUS_MODE_XL        = 3, /* ISO 11898-1:2024  — ≤2048 bytes, VCID/AF    */
} canbus_mode_t;

/* =========================================================================
 * Payload size limits
 * ========================================================================= */
#define CAN20_MAX_DLEN     8u     /* ISO 11898-1:2003 §10.3.2                 */
#define CANFD_MAX_DLEN    64u     /* ISO 11898-1:2015 §10.4.2.4               */
#define CANXL_MAX_DLEN  2048u     /* ISO 11898-1:2024 §10.5.2.4               */

/*
 * CAN XL payload alignment requirement (ISO 11898-1:2024 §10.5.2.4):
 * payload field is always a multiple of 4 bytes on the bus.
 * Padding bytes are added by the controller; software sees the logical
 * length.  This macro rounds up to the nearest 4-byte boundary.
 */
#define CANXL_PAD_LEN(n)  (((n) + 3u) & ~3u)

/* =========================================================================
 * DLC ↔ byte-length: CAN FD  (ISO 11898-1:2015 Table 8)
 *
 * always_inline prevents duplicate symbol generation when this header is
 * included from multiple translation units with LTO disabled.
 * ========================================================================= */
static __attribute__((always_inline)) inline uint32_t
canfd_dlc_to_len(uint32_t dlc)
{
    static const uint8_t tbl[16] = {
        0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64
    };
    return (dlc < 16u) ? (uint32_t)tbl[dlc] : 64u;
}

static __attribute__((always_inline)) inline uint32_t
canfd_len_to_dlc(uint32_t len)
{
    if (len <=  8u) return len;
    if (len <= 12u) return  9u;
    if (len <= 16u) return 10u;
    if (len <= 20u) return 11u;
    if (len <= 24u) return 12u;
    if (len <= 32u) return 13u;
    if (len <= 48u) return 14u;
    return 15u;
}

/* =========================================================================
 * DLC ↔ byte-length: CAN XL  (ISO 11898-1:2024 §10.5.2.4)
 *
 * CAN XL DLC field is 11 bits wide.
 * Encoding: DLC = payload_length - 1   (DLC=0 → 1 byte, DLC=2047 → 2048 bytes)
 * ========================================================================= */
static __attribute__((always_inline)) inline uint32_t
canxl_dlc_to_len(uint32_t dlc)
{
    /* dlc is 0..2047; clamp to valid range */
    return (dlc > 2047u ? 2048u : dlc + 1u);
}

static __attribute__((always_inline)) inline uint32_t
canxl_len_to_dlc(uint32_t len)
{
    if (len == 0u) len = 1u;          /* minimum 1 byte                      */
    if (len > 2048u) len = 2048u;     /* maximum 2048 bytes                  */
    return len - 1u;
}

/* =========================================================================
 * CAN XL SDT — Service Data Unit Type  (CiA 611-1:2021)
 *
 * SDT identifies the protocol carried in the XL frame payload.
 * ========================================================================= */
#define CANXL_SDT_CAN_CC      0x01u   /* Classic CAN tunneling                */
#define CANXL_SDT_CAN_FD      0x02u   /* CAN FD tunneling                     */
#define CANXL_SDT_AUTOSAR     0x03u   /* AUTOSAR I-PDU                        */
#define CANXL_SDT_SOMEIP      0x04u   /* SOME/IP                              */
#define CANXL_SDT_IEEE802     0x05u   /* IEEE 802.x framing                   */
#define CANXL_SDT_IETF_IP     0x06u   /* IETF IP (v4/v6)                      */
#define CANXL_SDT_ADMIN       0xFEu   /* Klipper admin channel over XL        */
#define CANXL_SDT_BROADCAST   0xFFu   /* Broadcast / management               */

/* =========================================================================
 * CAN XL extended header fields
 *
 * Present in every CAN XL frame, carried inline in struct canbus_msg.
 * ISO 11898-1:2024 §10.5.2.2–§10.5.2.3
 * ========================================================================= */
struct canxl_hdr {
    uint8_t  sdt;   /* Service Data Unit Type (8 bits)                        */
    uint8_t  vcid;  /* Virtual CAN ID — logical channel [0..255]              */
    uint8_t  _pad[2];
    uint32_t af;    /* Acceptance Field — 32-bit routing/filter token          */
};

STATIC_ASSERT(sizeof(struct canxl_hdr) == 8u, canxl_hdr_size_must_be_8);

/* =========================================================================
 * Unified frame structure — CAN 2.0 / CAN FD / CAN XL
 *
 * Memory layout:
 *   [id][dlc][flags][_pad[3]][xl_hdr (XL only)][data[]]
 *
 * For CAN 2.0 and CAN FD frames, xl_hdr is unused (zeroed).
 * For CAN XL frames, data[] starts right after xl_hdr.
 * The union exposes the full 2048-byte XL payload at data[].
 *
 * OWNERSHIP RULE: canbus_validate_msg() mutates this struct in-place.
 * Callers MUST NOT reuse *msg after passing it to any canbus_send* call.
 * ========================================================================= */
struct canbus_msg {
    uint32_t id;          /* 11-bit CAN identifier (arbitration phase)        */
    uint32_t dlc;         /* DLC as transmitted (FD: 0-15; XL: 0-2047)       */
    uint8_t  flags;       /* CANMSG_FLAG_* bitmask                            */
    uint8_t  _pad[3];

    struct canxl_hdr xl;  /* XL header fields — ignored for 2.0/FD           */

    union {
        uint8_t  data[CANXL_MAX_DLEN];
        uint32_t data32[CANXL_MAX_DLEN / 4];
    };
};

STATIC_ASSERT(sizeof(struct canbus_msg) ==
              (4u + 4u + 4u + 8u + CANXL_MAX_DLEN),
              canbus_msg_layout_check);

/* flags bits */
#define CANMSG_FLAG_RTR   (1u << 0)  /* Remote Transmission Request (2.0)     */
#define CANMSG_FLAG_EFF   (1u << 1)  /* Extended Frame Format (29-bit ID)      */
#define CANMSG_FLAG_FD    (1u << 2)  /* CAN FD frame  (ISO 11898-1:2015 §10.4) */
#define CANMSG_FLAG_BRS   (1u << 3)  /* Bit Rate Switch — FD/XL only           */
#define CANMSG_FLAG_ESI   (1u << 4)  /* Error State Indicator — FD/XL only     */
#define CANMSG_FLAG_XL    (1u << 5)  /* CAN XL frame  (ISO 11898-1:2024 §10.5) */
/* CANMSG_FLAG_XL implies CANMSG_FLAG_FD and CANMSG_FLAG_BRS on the bus;
 * software sets only CANMSG_FLAG_XL — the driver handles the wire encoding. */

/* Legacy id-field encoding — retained for source compatibility */
#define CANMSG_ID_RTR (1u << 30)
#define CANMSG_ID_EFF (1u << 31)

/*
 * CANMSG_DATA_LEN — effective payload byte count for any frame type.
 * Applies correct DLC table per protocol generation.
 */
#define CANMSG_DATA_LEN(msg)                                              \
    (((msg)->flags & CANMSG_FLAG_XL)                                      \
        ? canxl_dlc_to_len((msg)->dlc)                                    \
        : (((msg)->flags & CANMSG_FLAG_FD)                                \
            ? canfd_dlc_to_len((msg)->dlc)                                \
            : ((msg)->dlc > CAN20_MAX_DLEN ? CAN20_MAX_DLEN : (msg)->dlc)))

/* =========================================================================
 * Hardware capability descriptor
 * ========================================================================= */
struct canbus_hw_caps {
    canbus_mode_t max_mode;        /* Highest mode the controller supports    */
    uint32_t      nom_bitrate;     /* Nominal / arbitration bitrate [bit/s]   */
    uint32_t      data_bitrate;    /* Data-phase bitrate [bit/s] (FD/XL)      */
    uint16_t      xl_max_dlen;     /* Max XL payload hw can handle [bytes]    */
    uint8_t       tx_fifo_depth;   /* HW TX FIFO depth [frames]               */
    uint8_t       rx_fifo_depth;   /* HW RX FIFO depth [frames]               */
    uint8_t       supports_iso_fd; /* 1 = ISO CAN FD (res=0)                  */
    uint8_t       supports_tdc;    /* 1 = Transceiver Delay Compensation      */
    uint8_t       supports_vcid;   /* 1 = HW VCID filtering                   */
    uint8_t       supports_af;     /* 1 = HW Acceptance Field filtering        */
};

/* =========================================================================
 * Extended statistics
 * ========================================================================= */
struct canbus_stats {
    /* Traffic counters */
    uint32_t rx_packets;
    uint32_t tx_packets;
    uint32_t rx_bytes;
    uint32_t tx_bytes;
    uint32_t rx_fd_packets;
    uint32_t tx_fd_packets;
    uint32_t rx_xl_packets;        /* CAN XL received frames                  */
    uint32_t tx_xl_packets;        /* CAN XL transmitted frames               */
    uint32_t rx_xl_bytes;
    uint32_t tx_xl_bytes;

    /* Error counters — ISO 11898-1 §12.1.4 (applies to all generations) */
    uint32_t fifo_overflows;
    uint32_t hw_rx_overflows;
    uint32_t bus_errors;
    uint32_t arb_lost;
    uint32_t stuff_errors;         /* ISO §12.1.4.1                           */
    uint32_t form_errors;          /* ISO §12.1.4.2 — also triggers on XLDF   */
    uint32_t ack_errors;           /* ISO §12.1.4.3                           */
    uint32_t crc_errors;           /* CRC-15/17/21/32 — ISO §12.1.4.6        */
    uint32_t xl_crc32_errors;      /* CRC-32 errors specific to XL frames     */

    /* Latency metrics */
    uint32_t irq_batch_max;
    uint32_t irq_max_cycles;
    uint32_t tx_latency_min_us;
    uint32_t tx_latency_max_us;
    uint32_t tx_latency_avg_us;

    /* Bus state machine — ISO §12.1.6 / §12.1.7 */
    uint32_t error_state;
    uint32_t error_warning_count;
    uint32_t error_passive_count;
    uint32_t bus_off_count;
    uint8_t  tec;
    uint8_t  rec;
    uint8_t  _pad[2];
};

#define CANBUS_ERRSTATE_ACTIVE   0u
#define CANBUS_ERRSTATE_WARNING  1u
#define CANBUS_ERRSTATE_PASSIVE  2u
#define CANBUS_ERRSTATE_BUSOFF   3u

/* =========================================================================
 * TX priority levels
 * ========================================================================= */
typedef enum {
    CANBUS_PRIO_HIGH   = 0,
    CANBUS_PRIO_NORMAL = 1,
    CANBUS_PRIO_LOW    = 2,
    CANBUS_PRIO_COUNT  = 3,
} canbus_prio_t;

/* =========================================================================
 * Board-driver callbacks
 * ========================================================================= */
int  canhw_send(struct canbus_msg *msg);
void canhw_set_filter(uint32_t id);
void canhw_query_caps(struct canbus_hw_caps *caps);
void canhw_query_stats(struct canbus_stats *stats);
int  canhw_set_mode(canbus_mode_t mode);

/* Returns 1 if ≥11 consecutive recessive bits detected (ISO §9.3.3),
 * 0 if bus active, -1 if not supported by this driver. */
int  canhw_is_bus_idle(void);

/* Configure VCID hardware filter (XL only).
 * vcid_mask = bitmask of accepted VCIDs; 0xFF = accept all. */
void canhw_set_vcid_filter(uint8_t vcid_mask);

/* =========================================================================
 * Public API — canbus.c
 * ========================================================================= */
int           canbus_send(struct canbus_msg *msg);
int           canbus_send_prio(struct canbus_msg *msg, canbus_prio_t prio);
void          canbus_set_filter(uint32_t id);
canbus_mode_t canbus_get_mode(void);
uint32_t      canbus_max_dlen(void);
int           canbus_autoneg_is_ready(void);
void          canbus_notify_tx(void);
void          canbus_notify_bus_error(uint8_t hw_error_type);
void          canbus_process_data(struct canbus_msg *msg);
void          canbus_init(canbus_mode_t requested_mode);
void          canbus_query_stats(struct canbus_stats *out);

#endif /* __CANBUS_H__ */
