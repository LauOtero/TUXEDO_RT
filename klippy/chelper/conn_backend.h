#ifndef CONN_BACKEND_H
#define CONN_BACKEND_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* Forward declaration */
struct conn_manager;

/* =========================================================================
 * Connection Type Identifiers
 * Usamos números para mejor mantenibilidad y coherencia
 * ========================================================================= */
#define CONN_TYPE_SERIAL    0x01
#define CONN_TYPE_CAN       0x02
#define CONN_TYPE_ETHERTUX  0x03
#define CONN_TYPE_DEBUGFILE 0x04
#define CONN_TYPE_RS485     0x05
#define CONN_TYPE_SPI       0x06   /* SPI High-Speed (SPI/DSPI/QSPI) */
#define CONN_TYPE_DEBUGPIPE 0x07   /* Unified DEBUGFILE/PIPE backend */
#define CONN_TYPE_ZCKB_SHM  0x08   /* Zero-Copy Kernel-Bypass Shared Memory */

/* =========================================================================
 * CAN Bus Mode & Autonegotiation Constants
 * (Migrados de sq_backend.h para mantener compatibilidad con lógica existente)
 * ========================================================================= */
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

#define AUTONEG_OBS_FRAMES          8
#define AUTONEG_OBS_TIMEOUT_MS      500
#define AUTONEG_FD_PROBE_RETRIES    3
#define AUTONEG_XL_PROBE_RETRIES    3
#define AUTONEG_PROBE_TIMEOUT_MS    20
#define AUTONEG_RENEGOTIATE_ERRORS  16
#define AUTONEG_BUSOFF_HOLDOFF_MS   500
#define AUTONEG_PROBE_ID            0x3f0
#define AUTONEG_XL_PROBE_ID         0x3f0
#define CANXL_SDT_ADMIN             0x01

/* =========================================================================
 * EtherCAT (Ethertux) Specific Constants
 * ========================================================================= */
#define ETHERTUX_DEFAULT_CYCLE_TIME_NS  250000   // 250 µs típico para impresión 3D
#define ETHERTUX_MAX_SLAVES_PER_MASTER  64
#define ETHERTUX_MAX_PDOS_PER_SLAVE     32
#define ETHERTUX_PDU_MAX_SIZE           1498     // MTU Ethernet - cabeceras IP/UDP

/* =========================================================================
 * Backend Operations Interface (VTable)
 * Diseñado para dispatch de costo cero y determinismo en tiempo real.
 * Todas las funciones reciben el contexto completo (struct conn_manager *)
 * para evitar punteros globales y garantizar reentrancia segura.
 * ========================================================================= */
typedef struct {
    /* ── Lifecycle ─────────────────────────────────────────────────── */
    int  (*init)(struct conn_manager *cm);
    void (*exit)(struct conn_manager *cm);

    /* ── I/O ───────────────────────────────────────────────────────── */
    int  (*read)(struct conn_manager *cm, double eventtime);
    int  (*write)(struct conn_manager *cm, const void *buf, int len);

    /* ── Timing / Bit-time Estimation ──────────────────────────────── */
    double (*calc_bittime)(struct conn_manager *cm, uint32_t bytes);

    /* ── Autonegotiation / Background Ticks ────────────────────────── */
    void (*autoneg_tick)(struct conn_manager *cm, double eventtime);

    /* ── Flush & Parameter Control ─────────────────────────────────── */
    void (*flush_tx)(struct conn_manager *cm);
    void (*set_params)(struct conn_manager *cm, const char *key, const void *value, size_t len);

    /* ── RT Optimization Hooks (Opcionales, pueden ser NULL) ───────── */
    int  (*pin_irq)(struct conn_manager *cm, int cpu_id);
    int  (*set_irq_affinity)(struct conn_manager *cm, const int *cpu_list, int count);

} conn_backend_ops_t;

/* =========================================================================
 * Backend Instances (Definidos en los archivos .c correspondientes)
 * ========================================================================= */
extern const conn_backend_ops_t conn_serial_backend;
extern const conn_backend_ops_t conn_can_backend;
extern const conn_backend_ops_t conn_ethertux_backend;
extern const conn_backend_ops_t conn_rs485_backend;
extern const conn_backend_ops_t conn_spi_backend;  /* SPI High-Speed */
extern const conn_backend_ops_t conn_debugpipe_backend;  /* DEBUGFILE/PIPE unified */

/* =========================================================================
 * Factory / Dispatcher
 * Devuelve la tabla de operaciones adecuada según el tipo de conexión.
 * Retorna NULL si el tipo no es soportado.
 * ========================================================================= */
const conn_backend_ops_t *conn_backend_get_ops(int conn_type);

#endif /* CONN_BACKEND_H */