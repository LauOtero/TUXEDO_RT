#ifndef SQ_BACKEND_H
#define SQ_BACKEND_H

#include <stdint.h>
#include <stddef.h>

struct serialqueue;

// CAN types & constants (shared across backends)
typedef enum {
    CANBUS_MODE_CLASSIC   = 0,
    CANBUS_MODE_FD_NO_BRS = 1,
    CANBUS_MODE_FD_BRS    = 2,
    CANBUS_MODE_XL        = 3
} canbus_mode_t;

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
#define AUTONEG_OBS_FRAMES       8
#define AUTONEG_OBS_TIMEOUT_MS   500
#define AUTONEG_FD_PROBE_RETRIES 3
#define AUTONEG_XL_PROBE_RETRIES 3
#define AUTONEG_PROBE_TIMEOUT_MS 20
#define AUTONEG_RENEGOTIATE_ERRORS 16
#define AUTONEG_BUSOFF_HOLDOFF_MS  500
#define AUTONEG_PROBE_ID    0x3f0
#define AUTONEG_XL_PROBE_ID 0x3f0
#define CANXL_SDT_ADMIN     0x01

// Backend operations
typedef struct {
    int  (*init)(struct serialqueue *sq);
    int  (*read)(struct serialqueue *sq, double eventtime);
    int  (*write)(struct serialqueue *sq, const void *buf, int len);
    double (*calc_bittime)(struct serialqueue *sq, uint32_t bytes);
    void (*autoneg_tick)(struct serialqueue *sq, double eventtime);
    void (*flush_tx)(struct serialqueue *sq);
    void (*set_can_params)(struct serialqueue *sq, int mode, int retries, int xl_sdt);
} sq_backend_ops_t;

extern const sq_backend_ops_t sq_can_backend;
extern const sq_backend_ops_t sq_uart_backend;

#endif // SQ_BACKEND_H