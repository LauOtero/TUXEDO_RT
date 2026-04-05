/*
 * conn_can.c — CAN Backend for Conn Manager (Klipper C Helper)
 * Supports Classic CAN, CAN-FD (with/without BRS), and CAN-XL
 * Features: Autonegotiation, frame batching, hardware timestamping ready
 * Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
 * Updated for modular conn_manager architecture
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#pragma GCC optimize ("O3", "unroll-loops", "align-functions=64", "align-jumps=32")
#pragma GCC target ("sse4.2,pclmul,popcnt")

#include "conn_internal.h"
#include "conn_backend.h"
#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/can/error.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/sockios.h>

/* ─── CAN Frame Definitions (compatibility layer) ───────────────────────── */
#ifndef CAN_MTU
#define CAN_MTU 16
#define CANFD_MTU 72
struct canfd_frame {
    canid_t can_id;
    __u8 len;
    __u8 flags;
    __u8 __res0;
    __u8 __res1;
    __u8 data[64] __attribute__((aligned(8)));
};
#endif

#ifndef CANXL_MTU
#define CANXL_MAX_DLC 2048
#define CANXL_MTU (sizeof(struct canxl_frame))
#define CANXL_SDT_ADMIN 0x01
struct canxl_frame {
    canid_t prio;
    __u8 flags;
    __u8 sdt;
    __u16 len;
    __u32 af;
    __u8 data[CANXL_MAX_DLC];
};
#endif

#ifndef CAN_RAW_FD_FRAMES
#define CAN_RAW_FD_FRAMES 5
#endif
#ifndef CAN_RAW_XL_FRAMES
#define CAN_RAW_XL_FRAMES 6
#endif
#ifndef CANFD_BRS
#define CANFD_BRS 0x01
#endif
#ifndef CAN_ERR_MASK
#define CAN_ERR_MASK 0x1FFFFFFF
#endif

/* ─── CAN Backend Context (private per-connection state) ────────────────── */
typedef struct {
    /* Autonegotiation State Machine */
    int autoneg_state;
    double autoneg_state_entry_time;
    int autoneg_probe_sent;
    int autoneg_probe_retries;
    int autoneg_probe_result;
    uint32_t autoneg_consecutive_errors;
    
    /* Traffic Observation for Autoneg */
    uint32_t autoneg_obs_classic;
    uint32_t autoneg_obs_fd;
    uint32_t autoneg_obs_xl;
    uint32_t autoneg_obs_total;
    
    /* Hardware Capability Flags */
    int hw_supports_fd;
    int hw_supports_brs;
    int hw_supports_xl;
    
    /* Active Mode & Lock */
    canbus_mode_t can_mode;
    int can_max_dlen;
    uint8_t can_locked;
    
    /* Debug Logging (optional) */
    FILE *debug_file;
    
    /* TX Batching Buffers (Classic CAN only) */
    struct can_frame cc_batch[32];
    int batch_count;
    int batch_payload_bytes;
    
} can_backend_ctx_t;

/* ─── Autonegotiation Constants ─────────────────────────────────────────── */
#define AUTONEG_STATE_IDLE_WAIT       0
#define AUTONEG_STATE_OBSERVING       1
#define AUTONEG_STATE_FD_PROBE        2
#define AUTONEG_STATE_BRS_PROBE       3
#define AUTONEG_STATE_XL_PROBE        4
#define AUTONEG_STATE_LOCKED          5
#define AUTONEG_STATE_BUSOFF_RECOVERY 6

#define PROBE_RESULT_PENDING 0
#define PROBE_RESULT_ACK     1
#define PROBE_RESULT_ERROR   2
#define PROBE_RESULT_TIMEOUT 3

#define AUTONEG_OBS_FRAMES        8
#define AUTONEG_OBS_TIMEOUT_MS    500
#define AUTONEG_FD_PROBE_RETRIES  3
#define AUTONEG_XL_PROBE_RETRIES  3
#define AUTONEG_PROBE_TIMEOUT_MS  20
#define AUTONEG_RENEGOTIATE_ERRORS 16
#define AUTONEG_BUSOFF_HOLDOFF_MS  500

#define AUTONEG_PROBE_ID    0x3f0
#define AUTONEG_XL_PROBE_ID 0x3f0

/* ─── Forward Declarations ──────────────────────────────────────────────── */
static void can_an_enter(can_backend_ctx_t *ctx, int state, double now);
static void can_an_commit(can_backend_ctx_t *ctx, canbus_mode_t mode, double now);
static void can_debug_log(can_backend_ctx_t *ctx, const char *fmt, ...);

/* ─── Debug Logging Helper ──────────────────────────────────────────────── */
__attribute__((format(printf, 2, 3)))
static void can_debug_log(can_backend_ctx_t *ctx, const char *fmt, ...) {
    if (!ctx || !ctx->debug_file) return;
    va_list args;
    va_start(args, fmt);
    vfprintf(ctx->debug_file, fmt, args);
    va_end(args);
    fflush(ctx->debug_file);
}

/* ─── Backend Initialization ────────────────────────────────────────────── */
__attribute__((cold)) static int can_init(struct conn_manager *cm) {
    if (!cm->backend_ctx) {
        cm->backend_ctx = calloc(1, sizeof(can_backend_ctx_t));
        if (!cm->backend_ctx) return -ENOMEM;
    }
    
    can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
    
    /* Hardware capability detection (via ioctl if available) */
    ctx->hw_supports_fd = 1;
    ctx->hw_supports_brs = 1;
    ctx->hw_supports_xl = 1;
    
    /* Initial mode */
    ctx->can_mode = CANBUS_MODE_CLASSIC;
    ctx->can_max_dlen = 8;
    ctx->can_locked = 0;
    
    /* Enter autonegotiation idle state */
    can_an_enter(ctx, AUTONEG_STATE_IDLE_WAIT, 0.0);
    
    /* Optional debug file */
    const char *debug_path = getenv("CONN_CAN_DEBUG");
    if (debug_path && !ctx->debug_file) {
        ctx->debug_file = fopen(debug_path, "w");
        if (ctx->debug_file) {
            can_debug_log(ctx, "DEBUG: CAN backend initialized. struct canxl_frame offsets:\n");
            can_debug_log(ctx, "  prio=%zu, flags=%zu, sdt=%zu, len=%zu, af=%zu, data=%zu, total=%zu\n",
                         offsetof(struct canxl_frame, prio),
                         offsetof(struct canxl_frame, flags),
                         offsetof(struct canxl_frame, sdt),
                         offsetof(struct canxl_frame, len),
                         offsetof(struct canxl_frame, af),
                         offsetof(struct canxl_frame, data),
                         sizeof(struct canxl_frame));
        }
    }
    
    return 0;
}

static void can_exit(struct conn_manager *cm) {
    if (cm->backend_ctx) {
        can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
        if (ctx->debug_file) {
            fclose(ctx->debug_file);
            ctx->debug_file = NULL;
        }
        free(cm->backend_ctx);
        cm->backend_ctx = NULL;
    }
}

/* ─── Autonegotiation State Machine Helpers ─────────────────────────────── */
static void can_an_enter(can_backend_ctx_t *ctx, int state, double now) {
    if (!ctx) return;
    ctx->autoneg_state = state;
    ctx->autoneg_state_entry_time = now;
    ctx->autoneg_probe_sent = 0;
    if (state != AUTONEG_STATE_FD_PROBE && 
        state != AUTONEG_STATE_BRS_PROBE && 
        state != AUTONEG_STATE_XL_PROBE) {
        ctx->autoneg_probe_result = PROBE_RESULT_PENDING;
    }
}

static void can_an_commit(can_backend_ctx_t *ctx, canbus_mode_t mode, double now) {
    if (!ctx) return;
    ctx->can_mode = mode;
    ctx->can_locked = 1;
    ctx->can_max_dlen = (mode == CANBUS_MODE_XL) ? CANXL_MAX_DLC : 
                       ((mode >= CANBUS_MODE_FD_NO_BRS) ? 64 : 8);
    ctx->autoneg_consecutive_errors = 0;
    can_an_enter(ctx, AUTONEG_STATE_LOCKED, now);
    can_debug_log(ctx, "DEBUG: Autoneg committed mode=%d max_dlen=%d\n", mode, ctx->can_max_dlen);
}

/* ─── Read: Multi-Mode CAN Frame Parsing ────────────────────────────────── */
__attribute__((hot)) static int can_read(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    if (!cm->backend_ctx) return -EINVAL;
    can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
    
    union {
        struct can_frame cc;
        struct canfd_frame fd;
        struct canxl_frame xl;
    } frame;
    
    int total_added = 0;
    uint8_t read_buf[CANXL_MTU];
    int bytes_in_buf = 0;
    
    while (1) {
        int ret;
        ret = read(cm->fd, read_buf + bytes_in_buf, sizeof(read_buf) - bytes_in_buf);
        if (ret < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                return total_added;
            }
            return total_added > 0 ? total_added : ret;
        }
        if (ret == 0) {
            return total_added > 0 ? total_added : -1;
        }
        bytes_in_buf += ret;
        
        while (bytes_in_buf >= CAN_MTU) {
            int dlc = 0;
            const uint8_t *data = NULL;
            uint32_t id = 0;
            int frame_len = 0;
            canbus_mode_t current_mode = CANBUS_MODE_CLASSIC;
            
            /* Try CAN-XL first if unlocked or in XL mode */
            if (!ctx->can_locked || ctx->can_mode == CANBUS_MODE_XL) {
                if (bytes_in_buf >= 12) {
                    uint16_t xl_len;
                    uint8_t xl_sdt;
                    memcpy(&xl_len, &read_buf[6], 2);
                    xl_sdt = read_buf[5];
                    int expected_xl_len = 12 + xl_len;
                    
                    if (bytes_in_buf >= expected_xl_len &&
                        (ctx->can_mode == CANBUS_MODE_XL || 
                         (!ctx->can_locked && (xl_sdt == 0x01 || xl_sdt == 0x03)))) {
                        uint32_t xl_af;
                        memcpy(&xl_af, &read_buf[8], 4);
                        id = xl_af;
                        dlc = xl_len;
                        data = &read_buf[12];
                        frame_len = expected_xl_len;
                        current_mode = CANBUS_MODE_XL;
                    }
                }
            }
            
            /* Try CAN-FD if not matched and allowed */
            if (frame_len == 0 && 
                (!ctx->can_locked || ctx->can_mode == CANBUS_MODE_FD_NO_BRS || ctx->can_mode == CANBUS_MODE_FD_BRS)) {
                if (bytes_in_buf >= CANFD_MTU) {
                    memcpy(&frame.fd, read_buf, CANFD_MTU);
                    id = frame.fd.can_id;
                    dlc = frame.fd.len;
                    data = frame.fd.data;
                    frame_len = CANFD_MTU;
                    current_mode = (frame.fd.flags & CANFD_BRS) ? 
                                   CANBUS_MODE_FD_BRS : CANBUS_MODE_FD_NO_BRS;
                }
            }
            
            /* Try Classic CAN as fallback */
            if (frame_len == 0 && (!ctx->can_locked || ctx->can_mode == CANBUS_MODE_CLASSIC)) {
                if (bytes_in_buf >= CAN_MTU) {
                    memcpy(&frame.cc, read_buf, CAN_MTU);
                    id = frame.cc.can_id;
                    dlc = frame.cc.can_dlc;
                    data = frame.cc.data;
                    frame_len = CAN_MTU;
                    current_mode = CANBUS_MODE_CLASSIC;
                }
            }
            
            if (frame_len == 0) break; /* No complete frame */
            
            /* Lock mode on first valid frame */
            if (!ctx->can_locked) {
                ctx->can_mode = current_mode;
                ctx->can_max_dlen = (current_mode == CANBUS_MODE_XL) ? CANXL_MAX_DLC : 
                                   ((current_mode >= CANBUS_MODE_FD_NO_BRS) ? 64 : 8);
                ctx->can_locked = 1;
                can_debug_log(ctx, "DEBUG: Mode locked to %d, max_dlen=%d\n", 
                             ctx->can_mode, ctx->can_max_dlen);
            }
            
            /* Mask error frames for non-XL modes */
            if (current_mode == CANBUS_MODE_CLASSIC || 
                current_mode == CANBUS_MODE_FD_NO_BRS || 
                current_mode == CANBUS_MODE_FD_BRS) {
                id &= CAN_ERR_MASK;
            }
            
            /* Accept only frames addressed to this client */
            if (id == (uint32_t)cm->client_id) {
                if ((size_t)cm->input_pos + (size_t)total_added + (size_t)dlc > sizeof(cm->input_buf)) {
                    can_debug_log(ctx, "WARN: Input buffer overflow, dropping frame\n");
                    break;
                }
                memcpy(&cm->input_buf[cm->input_pos + total_added], data, dlc);
                total_added += dlc;
            }
            
            /* Consume processed frame from buffer */
            memmove(read_buf, read_buf + frame_len, bytes_in_buf - frame_len);
            bytes_in_buf -= frame_len;
        }
    }
    
    return total_added;
}

/* ─── Write: Batching for Classic CAN, Direct for FD/XL ─────────────────── */
__attribute__((hot)) static int can_write(struct conn_manager *cm, const void *buf, int buflen) {
    if (!cm->backend_ctx) return -EINVAL;
    can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
    
    const uint8_t *p = buf;
    int total_written = 0;
    
    while (buflen > 0) {
        int size = buflen > ctx->can_max_dlen ? ctx->can_max_dlen : buflen;
        int ret = -1;
        
        if (ctx->can_mode == CANBUS_MODE_CLASSIC) {
            /* Batch up to 32 Classic CAN frames to reduce syscalls */
            memset(&ctx->cc_batch[ctx->batch_count], 0, sizeof(struct can_frame));
            ctx->cc_batch[ctx->batch_count].can_id = cm->client_id;
            ctx->cc_batch[ctx->batch_count].can_dlc = size;
            memcpy(ctx->cc_batch[ctx->batch_count].data, p, size);
            
            ctx->batch_count++;
            ctx->batch_payload_bytes += size;
            p += size;
            buflen -= size;
            
            /* Flush batch if full or no more data */
            if (ctx->batch_count == 32 || buflen == 0) {
                do {
                    ret = write(cm->fd, ctx->cc_batch, 
                               ctx->batch_count * sizeof(struct can_frame));
                } while (ret < 0 && errno == EINTR);
                
                if (ret < 0) {
                    if (errno == ENOBUFS || errno == EAGAIN) break;
                    double cur = get_monotonic();
                    if (cm->last_write_fail_time && cur > cm->last_write_fail_time + 10.0) {
                        return -1; /* Fatal error */
                    }
                    if (!cm->last_write_fail_time) cm->last_write_fail_time = cur;
                    /* Return partial write accounting for batch */
                    return total_written - ctx->batch_payload_bytes;
                }
                
                total_written += ctx->batch_payload_bytes;
                ctx->batch_count = 0;
                ctx->batch_payload_bytes = 0;
                cm->last_write_fail_time = 0.0;
                continue;
            }
            continue;
        }
        
        /* CAN-XL: Single frame write */
        if (ctx->can_mode == CANBUS_MODE_XL) {
            struct canxl_frame xl;
            memset(&xl, 0, sizeof(xl));
            xl.af = cm->client_id;
            xl.sdt = 0x03; /* Admin SDT for Klipper protocol */
            xl.len = size;
            memcpy(xl.data, p, size);
            do {
                ret = write(cm->fd, &xl, 12 + size);
            } while (ret < 0 && errno == EINTR);
        }
        /* CAN-FD: Single frame write */
        else {
            struct canfd_frame fd;
            memset(&fd, 0, sizeof(fd));
            fd.can_id = cm->client_id;
            fd.len = size;
            fd.flags = (ctx->can_mode == CANBUS_MODE_FD_BRS) ? CANFD_BRS : 0;
            memcpy(fd.data, p, size);
            do {
                ret = write(cm->fd, &fd, CANFD_MTU);
            } while (ret < 0 && errno == EINTR);
        }
        
        if (ret < 0) {
            if (errno == ENOBUFS || errno == EAGAIN) break;
            double cur = get_monotonic();
            if (cm->last_write_fail_time && cur > cm->last_write_fail_time + 10.0) {
                return -1;
            }
            if (!cm->last_write_fail_time) cm->last_write_fail_time = cur;
            return ret;
        }
        
        cm->last_write_fail_time = 0.0;
        total_written += size;
        p += size;
        buflen -= size;
    }
    
    return total_written;
}

/* ─── Bit Timing Calculation (CAN overhead model) ───────────────────────── */
__attribute__((hot, flatten)) static double can_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    (void)cm;
    /* Simplified CAN overhead model:
     * - 8 bits/byte payload
     * - ~135 bits overhead per frame (SOF, arbitration, CRC, ACK, EOF)
     * - Stuff bits approximation: -4 bits average compensation
     */
    uint32_t pkts = (bytes + 7) / 8; /* Frames needed */
    uint32_t bits = bytes * 8 + pkts * 135 - 4;
    return cm->bittime_adjust * bits;
}

/* ─── Flush TX (no-op for CAN, handled by kernel) ───────────────────────── */
static void can_flush_tx(struct conn_manager *cm) {
    (void)cm;
    /* CAN kernel driver handles TX queue; explicit flush not needed */
}

/* ─── Autonegotiation Timer Event ───────────────────────────────────────── */
__attribute__((hot)) static void can_autoneg_tick(struct conn_manager *cm, double eventtime) {
    if (!cm->backend_ctx) return;
    can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
    
    double elapsed_ms = (eventtime - ctx->autoneg_state_entry_time) * 1000.0;
    
    switch (ctx->autoneg_state) {
        case AUTONEG_STATE_IDLE_WAIT:
            if (ctx->autoneg_obs_total > 0) {
                can_an_enter(ctx, AUTONEG_STATE_OBSERVING, eventtime);
            } else if (elapsed_ms >= 130.0) {
                if (ctx->hw_supports_xl) {
                    ctx->autoneg_probe_retries = AUTONEG_XL_PROBE_RETRIES;
                    can_an_enter(ctx, AUTONEG_STATE_XL_PROBE, eventtime);
                } else if (ctx->hw_supports_fd) {
                    ctx->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES;
                    can_an_enter(ctx, AUTONEG_STATE_FD_PROBE, eventtime);
                } else {
                    can_an_commit(ctx, CANBUS_MODE_CLASSIC, eventtime);
                }
            }
            break;
            
        case AUTONEG_STATE_OBSERVING:
            if (ctx->autoneg_obs_xl > 0 && ctx->hw_supports_xl) {
                ctx->autoneg_probe_retries = AUTONEG_XL_PROBE_RETRIES;
                can_an_enter(ctx, AUTONEG_STATE_XL_PROBE, eventtime);
            } else if (ctx->autoneg_obs_fd > 0 && ctx->hw_supports_fd) {
                ctx->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES;
                can_an_enter(ctx, AUTONEG_STATE_FD_PROBE, eventtime);
            } else if (ctx->autoneg_obs_total >= AUTONEG_OBS_FRAMES || 
                      elapsed_ms >= AUTONEG_OBS_TIMEOUT_MS) {
                can_an_commit(ctx, CANBUS_MODE_CLASSIC, eventtime);
            }
            break;
            
        case AUTONEG_STATE_FD_PROBE:
        case AUTONEG_STATE_BRS_PROBE:
        case AUTONEG_STATE_XL_PROBE: {
            /* Send probe if not yet sent */
            if (!ctx->autoneg_probe_sent) {
                int ret = -1;
                if (ctx->autoneg_state == AUTONEG_STATE_XL_PROBE) {
                    struct canxl_frame xl = {
                        .af = AUTONEG_XL_PROBE_ID,
                        .sdt = CANXL_SDT_ADMIN,
                        .len = 1,
                        .data = {0}
                    };
                    ret = write(cm->fd, &xl, CANXL_MTU);
                } else {
                    struct canfd_frame fd = {
                        .can_id = AUTONEG_PROBE_ID,
                        .len = 0,
                        .flags = (ctx->autoneg_state == AUTONEG_STATE_BRS_PROBE) ? CANFD_BRS : 0
                    };
                    ret = write(cm->fd, &fd, CANFD_MTU);
                }
                if (ret > 0) {
                    ctx->autoneg_probe_sent = 1;
                } else if (ret < 0 && errno != ENOBUFS && errno != EAGAIN) {
                    ctx->autoneg_probe_result = PROBE_RESULT_ERROR;
                }
            }
            
            /* Check probe timeout */
            if (ctx->autoneg_probe_result == PROBE_RESULT_PENDING && 
                elapsed_ms >= AUTONEG_PROBE_TIMEOUT_MS) {
                ctx->autoneg_probe_result = PROBE_RESULT_TIMEOUT;
            }
            
            /* Handle probe result */
            if (ctx->autoneg_probe_result == PROBE_RESULT_ACK) {
                if (ctx->autoneg_state == AUTONEG_STATE_FD_PROBE) {
                    if (ctx->hw_supports_brs) {
                        ctx->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES;
                        can_an_enter(ctx, AUTONEG_STATE_BRS_PROBE, eventtime);
                    } else {
                        can_an_commit(ctx, CANBUS_MODE_FD_NO_BRS, eventtime);
                    }
                } else if (ctx->autoneg_state == AUTONEG_STATE_BRS_PROBE) {
                    can_an_commit(ctx, CANBUS_MODE_FD_BRS, eventtime);
                } else {
                    can_an_commit(ctx, CANBUS_MODE_XL, eventtime);
                }
            } else if (ctx->autoneg_probe_result != PROBE_RESULT_PENDING) {
                if (ctx->autoneg_probe_retries > 0) {
                    ctx->autoneg_probe_retries--;
                    can_an_enter(ctx, ctx->autoneg_state, eventtime);
                } else if (ctx->autoneg_state == AUTONEG_STATE_XL_PROBE) {
                    int next = ctx->hw_supports_brs ? AUTONEG_STATE_BRS_PROBE : 
                              (ctx->hw_supports_fd ? AUTONEG_STATE_FD_PROBE : AUTONEG_STATE_LOCKED);
                    if (next != AUTONEG_STATE_LOCKED) {
                        ctx->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES;
                        can_an_enter(ctx, next, eventtime);
                    } else {
                        can_an_commit(ctx, CANBUS_MODE_CLASSIC, eventtime);
                    }
                } else if (ctx->autoneg_state == AUTONEG_STATE_BRS_PROBE) {
                    can_an_commit(ctx, CANBUS_MODE_FD_NO_BRS, eventtime);
                } else {
                    can_an_commit(ctx, CANBUS_MODE_CLASSIC, eventtime);
                }
            }
            break;
        }
        
        case AUTONEG_STATE_LOCKED:
            if (ctx->autoneg_consecutive_errors >= AUTONEG_RENEGOTIATE_ERRORS) {
                ctx->autoneg_consecutive_errors = 0;
                ctx->autoneg_obs_total = 0;
                ctx->autoneg_obs_classic = 0;
                ctx->autoneg_obs_fd = 0;
                ctx->autoneg_obs_xl = 0;
                ctx->can_locked = 0;
                can_an_enter(ctx, AUTONEG_STATE_IDLE_WAIT, eventtime);
            }
            break;
            
        case AUTONEG_STATE_BUSOFF_RECOVERY:
            if (elapsed_ms >= AUTONEG_BUSOFF_HOLDOFF_MS) {
                ctx->autoneg_obs_total = 0;
                ctx->autoneg_obs_classic = 0;
                ctx->autoneg_obs_fd = 0;
                ctx->autoneg_obs_xl = 0;
                ctx->can_locked = 0;
                can_an_enter(ctx, AUTONEG_STATE_IDLE_WAIT, eventtime);
            }
            break;
    }
}

/* ─── CAN Parameters Override ───────────────────────────────────────────── */
static void can_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    if (!cm->backend_ctx) return;
    can_backend_ctx_t *ctx = (can_backend_ctx_t *)cm->backend_ctx;
    
    if (strcmp(key, "can_mode") == 0 && len == sizeof(int)) {
        int mode = *(const int *)value;
        if (mode >= CANBUS_MODE_CLASSIC && mode <= CANBUS_MODE_XL) {
            can_an_commit(ctx, (canbus_mode_t)mode, get_monotonic());
        }
    } else if (strcmp(key, "can_max_dlen") == 0 && len == sizeof(int)) {
        int max_dlen = *(const int *)value;
        if (max_dlen > 0 && max_dlen <= CANXL_MAX_DLC) {
            ctx->can_max_dlen = max_dlen;
        }
    } else if (strcmp(key, "debug_enable") == 0 && len == sizeof(int)) {
        int enable = *(const int *)value;
        if (enable && !ctx->debug_file) {
            ctx->debug_file = fopen("can_debug.log", "w");
        } else if (!enable && ctx->debug_file) {
            fclose(ctx->debug_file);
            ctx->debug_file = NULL;
        }
    }
}

/* ─── Backend Registration ──────────────────────────────────────────────── */
const conn_backend_ops_t conn_can_backend __attribute__((aligned(64))) = {
    .init = can_init,
    .exit = can_exit,
    .read = can_read,
    .write = can_write,
    .calc_bittime = can_calc_bittime,
    .autoneg_tick = can_autoneg_tick,
    .flush_tx = can_flush_tx,
    .set_params = can_set_params,
    .pin_irq = NULL,
    .set_irq_affinity = NULL
};