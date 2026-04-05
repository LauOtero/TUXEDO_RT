#include "sq_internal.h"
#include "sq_backend.h"
#include <linux/can.h>
#include <linux/can/raw.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>

FILE *debug_file = NULL; // Variable global para el archivo de depuración

#ifndef CAN_MTU
#define CAN_MTU 16
#define CANFD_MTU 72
struct canfd_frame {
    canid_t can_id; __u8 len; __u8 flags; __u8 __res0; __u8 __res1;
    __u8 data[64] __attribute__((aligned(8)));
};
#endif
#ifndef CANXL_MTU
#define CANXL_MAX_DLC 2048
#define CANXL_MTU (sizeof(struct canxl_frame))
struct canxl_frame {
    canid_t prio; __u8 flags; __u8 sdt; __u16 len; __u32 af;
    __u8 data[CANXL_MAX_DLC];
};
#endif
#ifndef CAN_RAW_FD_FRAMES
#define CAN_RAW_FD_FRAMES 5
#endif
#ifndef CANFD_BRS
#define CANFD_BRS 0x01
#endif
#ifndef CAN_ERR_MASK
#define CAN_ERR_MASK 0x1FFFFFFF
#endif

static void an_enter(struct serialqueue *sq, int state, double now);
static void an_commit(struct serialqueue *sq, canbus_mode_t mode, double now);

__attribute__((cold)) static int can_init(struct serialqueue *sq) {
    sq->hw_supports_fd = 1; sq->hw_supports_brs = 1; sq->hw_supports_xl = 1;
    sq->can_mode = CANBUS_MODE_CLASSIC; sq->can_max_dlen = 8; sq->can_locked = 0;
    an_enter(sq, AUTONEG_STATE_IDLE_WAIT, 0.0);

    // Abrir el archivo de depuración
    if (debug_file == NULL) {
        debug_file = fopen("can_debug.log", "w");
        if (debug_file == NULL) {
            fprintf(stderr, "ERROR: No se pudo abrir can_debug.log para escritura.\n");
        } else {
            fprintf(debug_file, "DEBUG: can_debug.log iniciado.\n");
            fprintf(debug_file, "DEBUG: struct canxl_frame offsets: prio=%zu, flags=%zu, sdt=%zu, len=%zu, af=%zu, data=%zu, total=%zu\n",
                    offsetof(struct canxl_frame, prio),
                    offsetof(struct canxl_frame, flags),
                    offsetof(struct canxl_frame, sdt),
                    offsetof(struct canxl_frame, len),
                    offsetof(struct canxl_frame, af),
                    offsetof(struct canxl_frame, data),
                    sizeof(struct canxl_frame));
            fflush(debug_file);
        }
    }
    return 0;
}

__attribute__((hot)) static int can_read(struct serialqueue *sq, double eventtime) {
    (void)eventtime;
    union { struct can_frame cc; struct canfd_frame fd; struct canxl_frame xl; } frame;
    int total_added = 0;
    uint8_t read_buf[CANXL_MTU]; // Buffer para leer el frame completo
    int bytes_in_buf = 0;

    while (1) {
        int ret;
        ret = read(sq->serial_fd, read_buf + bytes_in_buf, sizeof(read_buf) - bytes_in_buf);

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
            int dlc = 0; const uint8_t *data = NULL; uint32_t id = 0;
            int frame_len = 0;
            canbus_mode_t current_mode = CANBUS_MODE_CLASSIC;

            // Intentar parsear basándose en el modo actual o si no está bloqueado
            if (!sq->can_locked || sq->can_mode == CANBUS_MODE_XL) {
                if (bytes_in_buf >= 12) {
                    uint16_t xl_len;
                    uint8_t xl_sdt;
                    memcpy(&xl_len, &read_buf[6], 2);
                    xl_sdt = read_buf[5];
                    
                    int expected_xl_len = 12 + xl_len;
                    // Solo aceptar como XL si la longitud es razonable y estamos en modo XL
                    // o si no estamos bloqueados y el SDT es válido.
                    if (bytes_in_buf >= expected_xl_len && 
                        (sq->can_mode == CANBUS_MODE_XL || (!sq->can_locked && (xl_sdt == 0x01 || xl_sdt == 0x03)))) {
                        uint32_t xl_af;
                        memcpy(&xl_af, &read_buf[8], 4);
                        id = xl_af; dlc = xl_len; data = &read_buf[12];
                        frame_len = expected_xl_len;
                        current_mode = CANBUS_MODE_XL;
                    }
                }
            }
            
            if (frame_len == 0 && (!sq->can_locked || sq->can_mode == CANBUS_MODE_FD_NO_BRS || sq->can_mode == CANBUS_MODE_FD_BRS)) {
                if (bytes_in_buf >= CANFD_MTU) {
                    memcpy(&frame.fd, read_buf, CANFD_MTU);
                    id = frame.fd.can_id; dlc = frame.fd.len; data = frame.fd.data;
                    frame_len = CANFD_MTU;
                    current_mode = (frame.fd.flags & CANFD_BRS) ? CANBUS_MODE_FD_BRS : CANBUS_MODE_FD_NO_BRS;
                }
            }
            
            if (frame_len == 0 && (!sq->can_locked || sq->can_mode == CANBUS_MODE_CLASSIC)) {
                if (bytes_in_buf >= CAN_MTU) {
                    memcpy(&frame.cc, read_buf, CAN_MTU);
                    id = frame.cc.can_id; dlc = frame.cc.can_dlc; data = frame.cc.data;
                    frame_len = CAN_MTU;
                    current_mode = CANBUS_MODE_CLASSIC;
                }
            }

            if (frame_len == 0) break; // No hay frame completo

            if (!sq->can_locked) {
                sq->can_mode = current_mode;
                sq->can_max_dlen = (current_mode == CANBUS_MODE_XL) ? CANXL_MAX_DLC : ((current_mode >= CANBUS_MODE_FD_NO_BRS) ? 64 : 8);
                sq->can_locked = 1;
            }

            if ((current_mode == CANBUS_MODE_CLASSIC || current_mode == CANBUS_MODE_FD_NO_BRS || current_mode == CANBUS_MODE_FD_BRS)) id &= CAN_ERR_MASK;
            if (id == (uint32_t)sq->client_id) {
                if ((size_t)sq->input_pos + (size_t)total_added + (size_t)dlc > sizeof(sq->input_buf)) {
                    break;
                }
                memcpy(&sq->input_buf[sq->input_pos + total_added], data, dlc);
                total_added += dlc;
            }

            memmove(read_buf, read_buf + frame_len, bytes_in_buf - frame_len);
            bytes_in_buf -= frame_len;
        }
    }
    return total_added;
}

__attribute__((hot)) static int can_write(struct serialqueue *sq, const void *buf, int buflen) {
    const uint8_t *p = buf;
    
    // Batching frames to reduce syscalls (only for Classic CAN)
    struct can_frame cc_batch[32];
    int batch_count = 0;
    int batch_payload_bytes = 0;
    
    while (buflen > 0) {
        int size = buflen > sq->can_max_dlen ? sq->can_max_dlen : buflen;
        int ret = -1;
        
        if (sq->can_mode == CANBUS_MODE_CLASSIC) {
            memset(&cc_batch[batch_count], 0, sizeof(struct can_frame));
            cc_batch[batch_count].can_id = sq->client_id;
            cc_batch[batch_count].can_dlc = size;
            memcpy(cc_batch[batch_count].data, p, size);
            batch_count++;
            batch_payload_bytes += size;
            
            p += size;
            buflen -= size;
            
            if (batch_count == 32 || buflen == 0) {
                do { ret = write(sq->serial_fd, cc_batch, batch_count * sizeof(struct can_frame)); } while (ret < 0 && errno == EINTR);
                if (ret < 0) {
                    if (errno == ENOBUFS || errno == EAGAIN) break;
                    double cur = get_monotonic();
                    if (sq->last_write_fail_time && cur > sq->last_write_fail_time + 10.0) return -1;
                    sq->last_write_fail_time = cur; 
                    return (const uint8_t *)p - (const uint8_t *)buf - batch_payload_bytes;
                }
                batch_count = 0;
                batch_payload_bytes = 0;
                sq->last_write_fail_time = 0.0;
            }
            continue;
        }
        
        if (sq->can_mode == CANBUS_MODE_XL) {
            struct canxl_frame xl;
            memset(&xl, 0, sizeof(xl));
            xl.af = sq->client_id;
            xl.sdt = 0x03;
            xl.len = size;
            memcpy(xl.data, p, size);
            do { ret = write(sq->serial_fd, &xl, 12 + size); } while (ret < 0 && errno == EINTR);
        } else {
            struct canfd_frame fd;
            memset(&fd, 0, sizeof(fd));
            fd.can_id = sq->client_id;
            fd.len = size;
            fd.flags = (sq->can_mode == CANBUS_MODE_FD_BRS) ? CANFD_BRS : 0;
            memcpy(fd.data, p, size);
            do { ret = write(sq->serial_fd, &fd, CANFD_MTU); } while (ret < 0 && errno == EINTR);
        }
        
        if (ret < 0) {
            if (errno == ENOBUFS || errno == EAGAIN) break;
            double cur = get_monotonic();
            if (sq->last_write_fail_time && cur > sq->last_write_fail_time + 10.0) return -1;
            sq->last_write_fail_time = cur; return ret;
        }
        sq->last_write_fail_time = 0.0; p += size; buflen -= size;
    }
    
    return (const uint8_t *)p - (const uint8_t *)buf;
}

__attribute__((hot, flatten)) static double can_calc_bittime(struct serialqueue *sq, uint32_t bytes) {
    uint32_t pkts = (bytes + 7) / 8;
    uint32_t bits = bytes * 8 + pkts * 135 - 4; // Simplified CAN overhead
    return sq->bittime_adjust * bits;
}

static void can_flush_tx(struct serialqueue *sq) { (void)sq; }

static void an_enter(struct serialqueue *sq, int state, double now) {
    sq->autoneg_state = state; sq->autoneg_state_entry_time = now; sq->autoneg_probe_sent = 0;
    if (state != AUTONEG_STATE_FD_PROBE && state != AUTONEG_STATE_BRS_PROBE && state != AUTONEG_STATE_XL_PROBE)
        sq->autoneg_probe_result = PROBE_RESULT_PENDING;
}

static void an_commit(struct serialqueue *sq, canbus_mode_t mode, double now) {
    sq->can_mode = mode; sq->can_locked = 1;
    sq->can_max_dlen = (mode == CANBUS_MODE_XL) ? CANXL_MAX_DLC : ((mode >= CANBUS_MODE_FD_NO_BRS) ? 64 : 8);
    sq->autoneg_consecutive_errors = 0;
    an_enter(sq, AUTONEG_STATE_LOCKED, now);
}

__attribute__((hot)) static void can_autoneg_event(struct serialqueue *sq, double eventtime) {
    double elapsed_ms = (eventtime - sq->autoneg_state_entry_time) * 1000.0;
    switch (sq->autoneg_state) {
    case AUTONEG_STATE_IDLE_WAIT:
        if (sq->autoneg_obs_total > 0) an_enter(sq, AUTONEG_STATE_OBSERVING, eventtime);
        else if (elapsed_ms >= 130.0) {
            if (sq->hw_supports_xl) { sq->autoneg_probe_retries = AUTONEG_XL_PROBE_RETRIES; an_enter(sq, AUTONEG_STATE_XL_PROBE, eventtime); }
            else if (sq->hw_supports_fd) { sq->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES; an_enter(sq, AUTONEG_STATE_FD_PROBE, eventtime); }
            else an_commit(sq, CANBUS_MODE_CLASSIC, eventtime);
        }
        break;
    case AUTONEG_STATE_OBSERVING:
        if (sq->autoneg_obs_xl > 0 && sq->hw_supports_xl) { sq->autoneg_probe_retries = AUTONEG_XL_PROBE_RETRIES; an_enter(sq, AUTONEG_STATE_XL_PROBE, eventtime); }
        else if (sq->autoneg_obs_fd > 0 && sq->hw_supports_fd) { sq->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES; an_enter(sq, AUTONEG_STATE_FD_PROBE, eventtime); }
        else if (sq->autoneg_obs_total >= AUTONEG_OBS_FRAMES || elapsed_ms >= AUTONEG_OBS_TIMEOUT_MS)
            an_commit(sq, CANBUS_MODE_CLASSIC, eventtime);
        break;
    case AUTONEG_STATE_FD_PROBE: case AUTONEG_STATE_BRS_PROBE: case AUTONEG_STATE_XL_PROBE: {
        if (!sq->autoneg_probe_sent) {
            int ret = -1;
            if (sq->autoneg_state == AUTONEG_STATE_XL_PROBE) {
                struct canxl_frame xl = { .af = AUTONEG_XL_PROBE_ID, .sdt = CANXL_SDT_ADMIN, .len = 1, .data = {0} };
                ret = write(sq->serial_fd, &xl, CANXL_MTU);
            } else {
                struct canfd_frame fd = { .can_id = AUTONEG_PROBE_ID, .len = 0,
                                          .flags = (sq->autoneg_state == AUTONEG_STATE_BRS_PROBE) ? CANFD_BRS : 0 };
                ret = write(sq->serial_fd, &fd, CANFD_MTU);
            }
            if (ret > 0) sq->autoneg_probe_sent = 1;
            else if (ret < 0 && errno != ENOBUFS && errno != EAGAIN) sq->autoneg_probe_result = PROBE_RESULT_ERROR;
        }
        if (sq->autoneg_probe_result == PROBE_RESULT_PENDING && elapsed_ms >= AUTONEG_PROBE_TIMEOUT_MS)
            sq->autoneg_probe_result = PROBE_RESULT_TIMEOUT;

        if (sq->autoneg_probe_result == PROBE_RESULT_ACK) {
            if (sq->autoneg_state == AUTONEG_STATE_FD_PROBE) {
                if (sq->hw_supports_brs) { sq->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES; an_enter(sq, AUTONEG_STATE_BRS_PROBE, eventtime); }
                else an_commit(sq, CANBUS_MODE_FD_NO_BRS, eventtime);
            } else if (sq->autoneg_state == AUTONEG_STATE_BRS_PROBE) an_commit(sq, CANBUS_MODE_FD_BRS, eventtime);
            else an_commit(sq, CANBUS_MODE_XL, eventtime);
        } else if (sq->autoneg_probe_result != PROBE_RESULT_PENDING) {
            if (sq->autoneg_probe_retries > 0) { sq->autoneg_probe_retries--; an_enter(sq, sq->autoneg_state, eventtime); }
            else if (sq->autoneg_state == AUTONEG_STATE_XL_PROBE) {
                int next = sq->hw_supports_brs ? AUTONEG_STATE_BRS_PROBE : (sq->hw_supports_fd ? AUTONEG_STATE_FD_PROBE : AUTONEG_STATE_LOCKED);
                if (next != AUTONEG_STATE_LOCKED) { sq->autoneg_probe_retries = AUTONEG_FD_PROBE_RETRIES; an_enter(sq, next, eventtime); }
                else an_commit(sq, CANBUS_MODE_CLASSIC, eventtime);
            } else if (sq->autoneg_state == AUTONEG_STATE_BRS_PROBE) an_commit(sq, CANBUS_MODE_FD_NO_BRS, eventtime);
            else an_commit(sq, CANBUS_MODE_CLASSIC, eventtime);
        }
        break; }
    case AUTONEG_STATE_LOCKED:
        if (sq->autoneg_consecutive_errors >= AUTONEG_RENEGOTIATE_ERRORS) {
            sq->autoneg_consecutive_errors = 0; sq->autoneg_obs_total = 0; sq->autoneg_obs_classic = 0;
            sq->autoneg_obs_fd = 0; sq->autoneg_obs_xl = 0; sq->can_locked = 0;
            an_enter(sq, AUTONEG_STATE_IDLE_WAIT, eventtime);
        }
        break;
    case AUTONEG_STATE_BUSOFF_RECOVERY:
        if (elapsed_ms >= AUTONEG_BUSOFF_HOLDOFF_MS) {
            sq->autoneg_obs_total = 0; sq->autoneg_obs_classic = 0; sq->autoneg_obs_fd = 0;
            sq->autoneg_obs_xl = 0; sq->can_locked = 0;
            an_enter(sq, AUTONEG_STATE_IDLE_WAIT, eventtime);
        }
        break;
    }
}

static void can_set_can_params(struct serialqueue *sq, int mode, int retries, int xl_sdt) {
    (void)retries; (void)xl_sdt;
    if (mode != 0) an_commit(sq, (canbus_mode_t)mode, get_monotonic());
}

const sq_backend_ops_t sq_can_backend __attribute__((aligned(64))) = {
    .init = can_init, .read = can_read, .write = can_write,
    .calc_bittime = can_calc_bittime, .autoneg_tick = can_autoneg_event,
    .flush_tx = can_flush_tx, .set_can_params = can_set_can_params
};
