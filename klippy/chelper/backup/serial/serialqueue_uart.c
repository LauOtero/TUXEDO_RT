#include "sq_internal.h"
#include "sq_backend.h"
#include <termios.h>
#include <unistd.h>
#include <errno.h>

// Inicialización específica del backend UART
__attribute__((cold)) static int uart_init(struct serialqueue *sq) { 
    (void)sq; 
    return 0; 
}

// Lectura de datos crudos desde el descriptor de archivo UART
// Retorna la cantidad de bytes leídos, o código de error
__attribute__((hot)) static int uart_read(struct serialqueue *sq, double eventtime) {
    (void)eventtime;
    int ret;
    do {
        ret = read(sq->serial_fd, &sq->input_buf[sq->input_pos], sizeof(sq->input_buf) - sq->input_pos);
    } while (ret < 0 && errno == EINTR); // Reintento automático en caso de interrupción del sistema
    
    return ret;
}

// Escritura de datos crudos al descriptor de archivo UART
// Retorna la cantidad de bytes escritos, o código de error
__attribute__((hot)) static int uart_write(struct serialqueue *sq, const void *buf, int len) {
    int ret;
    do {
        ret = write(sq->serial_fd, buf, len);
    } while (ret < 0 && errno == EINTR); // Reintento automático en caso de interrupción del sistema
    
    return ret;
}

// Cálculo del tiempo estimado para transmitir una cantidad de bytes por UART
__attribute__((hot, flatten)) static double uart_calc_bittime(struct serialqueue *sq, uint32_t bytes) {
    return sq->bittime_adjust * bytes;
}

// Vaciar el buffer de transmisión de hardware
static void uart_flush_tx(struct serialqueue *sq) { 
    if (isatty(sq->serial_fd)) {
        tcflush(sq->serial_fd, TCOFLUSH); 
    }
}

// Dummy para la interfaz CAN
static void uart_set_can_params(struct serialqueue *sq, int mode, int retries, int xl_sdt) { 
    (void)sq; (void)mode; (void)retries; (void)xl_sdt; 
}

const sq_backend_ops_t sq_uart_backend __attribute__((aligned(64))) = {
    .init = uart_init, 
    .read = uart_read, 
    .write = uart_write,
    .calc_bittime = uart_calc_bittime, 
    .autoneg_tick = NULL,
    .flush_tx = uart_flush_tx, 
    .set_can_params = uart_set_can_params
};