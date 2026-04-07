#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <stdint.h>

#include "conn_manager.h"
#include "conn_backend.h"
#include "conn_internal.h"
#include "list.h"
#include "msgblock.h"

/* Inicialización */
static int debugpipe_init(struct conn_manager *cm) {
    cm->fd = -1;
    return 0;
}

/* Lectura (simulada para debug) */
static int debugpipe_read(struct conn_manager *cm, double eventtime) {
    return 0;
}

/* Escritura directa a FD o stdout */
static int debugpipe_write(struct conn_manager *cm, const void *buf, int len) {
    if (cm->fd >= 0) {
        return write(cm->fd, buf, len);
    } else {
        fwrite(buf, 1, len, stdout);
        fflush(stdout);
        return len;
    }
}

/* Cálculo de bit-time (instantáneo para file) */
static double debugpipe_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    return 0.0;
}

/* VTable del backend */
const conn_backend_ops_t conn_debugpipe_backend = {
    .init = debugpipe_init,
    .read = debugpipe_read,
    .write = debugpipe_write,
    .calc_bittime = debugpipe_calc_bittime,
    .autoneg_tick = NULL,
    .flush_tx = NULL,
    .set_params = NULL,
    .pin_irq = NULL,
    .set_irq_affinity = NULL
};
