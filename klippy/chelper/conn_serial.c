#include "conn_internal.h"
#include "conn_backend.h"
#include <termios.h>
#include <unistd.h>
#include <errno.h>

__attribute__((cold)) static int serial_init(struct conn_manager *cm) {
    (void)cm;
    return 0;
}

__attribute__((hot)) static int serial_read(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    int ret;
    do {
        ret = read(cm->fd, &cm->input_buf[cm->input_pos], sizeof(cm->input_buf) - cm->input_pos);
    } while (ret < 0 && errno == EINTR);
    return ret;
}

__attribute__((hot)) static int serial_write(struct conn_manager *cm, const void *buf, int len) {
    int ret;
    do {
        ret = write(cm->fd, buf, len);
    } while (ret < 0 && errno == EINTR);
    return ret;
}

__attribute__((hot, flatten)) static double serial_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    return cm->bittime_adjust * bytes;
}

static void serial_flush_tx(struct conn_manager *cm) {
    if (isatty(cm->fd)) tcflush(cm->fd, TCOFLUSH);
}

static void serial_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    (void)cm; (void)key; (void)value; (void)len;
}

const conn_backend_ops_t conn_serial_backend __attribute__((aligned(64))) = {
    .init = serial_init,
    .exit = NULL,
    .read = serial_read,
    .write = serial_write,
    .calc_bittime = serial_calc_bittime,
    .autoneg_tick = NULL,
    .flush_tx = serial_flush_tx,
    .set_params = serial_set_params,
    .pin_irq = NULL,
    .set_irq_affinity = NULL
};