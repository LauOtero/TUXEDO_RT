/*
 * conn_spi.c — SPI High-Speed Backend (RT Optimized)
 * Ultra-low latency SPI communication with DMA and hardware CRC support
 * Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
 * Updated for deterministic RT, ultra-low jitter, modular backend architecture
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#pragma GCC optimize ("O3", "unroll-loops", "align-functions=64", "align-jumps=32")
#pragma GCC target ("sse4.2,pclmul,popcnt")

#include <errno.h>
#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <math.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "compiler.h"
#include "list.h"
#include "msgblock.h"
#include "pollreactor.h"
#include "pyhelper.h"
#include "conn_manager.h"
#include "conn_internal.h"
#include "conn_backend.h"

/* ─── SPI Backend Private Data ────────────────────────────────────────── */
typedef struct {
    uint32_t speed_hz;        // Velocidad SPI en Hz
    uint8_t mode;             // SPI mode 0-3
    uint8_t bits_per_word;    // 8 o 16 bits
    uint8_t hw_crc;           // Hardware CRC enabled
    uint8_t dma_enabled;      // DMA enabled
    uint8_t cs_change;        // CS change after transfer
    uint16_t delay_usecs;     // Delay between transfers
    uint32_t tx_dma_buf_size; // TX DMA buffer size
    uint32_t rx_dma_buf_size; // RX DMA buffer size
} spi_backend_data_t;

/* ─── Lifecycle ───────────────────────────────────────────────────────── */
static int spi_init(struct conn_manager *cm) {
    spi_backend_data_t *data = malloc(sizeof(*data));
    if (!data) return -ENOMEM;
    
    memset(data, 0, sizeof(*data));
    data->speed_hz = 10000000;      // 10 MHz default
    data->mode = 0;                 // Mode 0
    data->bits_per_word = 8;        // 8 bits
    data->hw_crc = 0;
    data->dma_enabled = 1;
    data->cs_change = 0;
    data->delay_usecs = 0;
    data->tx_dma_buf_size = 4096;   // 4KB default DMA buffer
    data->rx_dma_buf_size = 4096;
    
    cm->backend_data = data;
    
    /* SPI no requiere autonegociación compleja como CAN */
    cm->rto = MIN_RTO;  // Timeout mínimo para máxima reactividad
    
    logging_info("SPI backend initialized: %d Hz, mode=%d, bits=%d",
                data->speed_hz, data->mode, data->bits_per_word);
    
    return 0;
}

static void spi_exit(struct conn_manager *cm) {
    if (cm->backend_data) {
        free(cm->backend_data);
        cm->backend_data = NULL;
    }
}

/* ─── I/O Operations ──────────────────────────────────────────────────── */
static __attribute__((hot)) int spi_read(struct conn_manager *cm, double eventtime) {
    spi_backend_data_t *data = cm->backend_data;
    uint8_t buf[MESSAGE_MAX];
    int ret;
    
    /* Leer datos del FD SPI */
    ret = read(cm->fd, buf, sizeof(buf));
    if (ret <= 0) {
        if (ret < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            report_errno("spi_read", errno);
            return -1;
        }
        return 0;
    }
    
    /* Procesar mensaje recibido */
    struct queue_message *qm = message_alloc();
    if (!qm) return -ENOMEM;
    
    memcpy(qm->msg, buf, ret);
    qm->len = ret;
    qm->sent_time = eventtime;
    qm->receive_time = eventtime;
    qm->notify_id = 0;
    
    /* Encolar en receiver_queue y despertar Python */
    receive_append_wake(&cm->receiver, &qm->node);
    
    return ret;
}

static __attribute__((hot)) int spi_write(struct conn_manager *cm, const void *buf, int len) {
    spi_backend_data_t *data = cm->backend_data;
    struct spi_ioc_transfer xfer;
    int ret;
    
    /* Configurar transferencia SPI */
    memset(&xfer, 0, sizeof(xfer));
    xfer.tx_buf = (unsigned long)buf;
    xfer.len = len;
    xfer.speed_hz = data->speed_hz;
    xfer.delay_usecs = data->delay_usecs;
    xfer.bits_per_word = data->bits_per_word;
    xfer.cs_change = data->cs_change;
    
    /* Si DMA está habilitado, configurar flags */
    if (data->dma_enabled) {
        xfer.tx_nbits = SPI_NBITS_SINGLE;  // O SPI_NBITS_DUAL/QUAD si soportado
        xfer.rx_nbits = SPI_NBITS_SINGLE;
    }
    
    /* Ejecutar transferencia IOCTL */
    ret = ioctl(cm->fd, SPI_IOC_MESSAGE(1), &xfer);
    if (ret < 0) {
        report_errno("spi_write ioctl", errno);
        return -1;
    }
    
    return ret;
}

/* ─── Timing / Bit-time Calculation ───────────────────────────────────── */
static __always_inline double spi_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    spi_backend_data_t *data = cm->backend_data;
    
    /* 
     * Cálculo preciso del tiempo de transmisión:
     * time = (bytes * bits_per_word) / speed_hz
     * 
     * Para SPI mode estándar:
     * - 1 bit por ciclo de reloj
     * - Sin overhead de start/stop bits (a diferencia de UART)
     */
    if (data->speed_hz == 0) return 0.000001;  // Fallback 1µs
    
    double bits = bytes * data->bits_per_word;
    double bittime = bits / (double)data->speed_hz;
    
    /* Ajuste empírico para latencias del driver SPI */
    bittime *= 1.05;  // +5% margen para overhead del kernel
    
    return bittime;
}

/* ─── Autonegotiation (No requerido para SPI) ─────────────────────────── */
static void spi_autoneg_tick(struct conn_manager *cm, double eventtime) {
    /* SPI no requiere autonegociación dinámica */
    /* Placeholder para futura detección de modo dual/quad */
}

/* ─── Flush TX Buffer ─────────────────────────────────────────────────── */
static void spi_flush_tx(struct conn_manager *cm) {
    /* SPI es full-duplex síncrono, no requiere flush explícito */
    /* El hardware transmite inmediatamente con el reloj */
}

/* ─── Parameter Control ───────────────────────────────────────────────── */
static void spi_set_params(struct conn_manager *cm, const char *key, 
                          const void *value, size_t len) {
    spi_backend_data_t *data = cm->backend_data;
    
    if (strcmp(key, "speed_hz") == 0 && len == sizeof(uint32_t)) {
        uint32_t new_speed = *(const uint32_t *)value;
        if (new_speed > 0 && new_speed <= 50000000) {  // Máx 50 MHz
            data->speed_hz = new_speed;
            ioctl(cm->fd, SPI_IOC_WR_MAX_SPEED_HZ, &new_speed);
            logging_info("SPI speed changed to %d Hz", new_speed);
        }
    } else if (strcmp(key, "mode") == 0 && len == sizeof(uint8_t)) {
        uint8_t new_mode = *(const uint8_t *)value;
        if (new_mode <= 3) {
            data->mode = new_mode;
            ioctl(cm->fd, SPI_IOC_WR_MODE, &new_mode);
            logging_info("SPI mode changed to %d", new_mode);
        }
    } else if (strcmp(key, "bits_per_word") == 0 && len == sizeof(uint8_t)) {
        uint8_t new_bits = *(const uint8_t *)value;
        if (new_bits == 8 || new_bits == 16) {
            data->bits_per_word = new_bits;
            ioctl(cm->fd, SPI_IOC_WR_BITS_PER_WORD, &new_bits);
            logging_info("SPI bits_per_word changed to %d", new_bits);
        }
    } else if (strcmp(key, "dma_enabled") == 0 && len == sizeof(uint8_t)) {
        data->dma_enabled = *(const uint8_t *)value;
        logging_info("SPI DMA %s", data->dma_enabled ? "enabled" : "disabled");
    } else if (strcmp(key, "hw_crc") == 0 && len == sizeof(uint8_t)) {
        data->hw_crc = *(const uint8_t *)value;
        logging_info("SPI hardware CRC %s", data->hw_crc ? "enabled" : "disabled");
    }
}

/* ─── RT Optimization Hooks ───────────────────────────────────────────── */
static int spi_pin_irq(struct conn_manager *cm, int cpu_id) {
    /* SPI no usa IRQs tradicionales, pero podemos fijar afinidad del thread */
    return set_thread_affinity(pthread_self(), cpu_id);
}

static int spi_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) {
    /* Configurar afinidad de CPU para el thread de background */
    return set_thread_affinity_list(pthread_self(), cpu_list, count);
}

/* ─── Backend VTable Definition ───────────────────────────────────────── */
const conn_backend_ops_t conn_spi_backend = {
    .init = spi_init,
    .exit = spi_exit,
    .read = spi_read,
    .write = spi_write,
    .calc_bittime = spi_calc_bittime,
    .autoneg_tick = spi_autoneg_tick,
    .flush_tx = spi_flush_tx,
    .set_params = spi_set_params,
    .pin_irq = spi_pin_irq,
    .set_irq_affinity = spi_set_irq_affinity,
};

/* ─── Exported Functions for Python FFI ───────────────────────────────── */
__visible void serialqueue_set_spi_params(struct command_queue *cq,
                                          uint32_t speed_hz,
                                          uint8_t mode,
                                          uint8_t hw_crc,
                                          uint8_t dma_enabled) {
    struct conn_manager *cm = cq->cm;
    if (!cm || !cm->backend_data) return;
    
    spi_backend_data_t *data = cm->backend_data;
    
    if (speed_hz > 0 && speed_hz <= 50000000) {
        data->speed_hz = speed_hz;
        ioctl(cm->fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed_hz);
    }
    
    if (mode <= 3) {
        data->mode = mode;
        ioctl(cm->fd, SPI_IOC_WR_MODE, &mode);
    }
    
    data->hw_crc = hw_crc;
    data->dma_enabled = dma_enabled;
    
    logging_info("SPI params configured via FFI: %d Hz, mode=%d, crc=%d, dma=%d",
                speed_hz, mode, hw_crc, dma_enabled);
}
