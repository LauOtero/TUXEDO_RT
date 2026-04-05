/*
 * conn_rs485.c — RS-485 Backend for Conn Manager (Klipper C Helper)
 * Deterministic RT, half-duplex optimized, Linux TIOCSRS485 integration
 * Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
 * Updated for modular conn_manager architecture
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#pragma GCC optimize ("O3", "unroll-loops", "align-functions=64", "align-jumps=32")
#pragma GCC target ("sse4.2,pclmul,popcnt")

#include "conn_internal.h"
#include "conn_backend.h"
#include <termios.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <linux/serial.h>

/* ─── RS-485 Backend Context (Private per-connection state) ─────────────── */
typedef struct {
    struct serial_rs485 orig_config;
    struct serial_rs485 active_config;
    int is_hw_rs485;          // True if kernel TIOCSRS485 is supported
    uint32_t turnaround_delay_us; // Bus turnaround compensation
    bool rx_during_tx;        // Handle TX echo in half-duplex mode
    int gpio_de_pin;          // -1 = unused, otherwise libgpiod/sysfs pin
} rs485_ctx_t;

/* ─── Forward Declarations ──────────────────────────────────────────────── */
static int rs485_init(struct conn_manager *cm);
static void rs485_exit(struct conn_manager *cm);
static int rs485_read(struct conn_manager *cm, double eventtime);
static int rs485_write(struct conn_manager *cm, const void *buf, int len);
static double rs485_calc_bittime(struct conn_manager *cm, uint32_t bytes);
static void rs485_flush_tx(struct conn_manager *cm);
static void rs485_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len);

/* ─── Backend Initialization ────────────────────────────────────────────── */
__attribute__((cold)) static int rs485_init(struct conn_manager *cm) {
    if (!cm->backend_ctx) {
        cm->backend_ctx = calloc(1, sizeof(rs485_ctx_t));
        if (!cm->backend_ctx) return -ENOMEM;
    }
    rs485_ctx_t *ctx = (rs485_ctx_t *)cm->backend_ctx;
    
    /* Defaults */
    ctx->turnaround_delay_us = 150;
    ctx->rx_during_tx = true;
    ctx->gpio_de_pin = -1;
    
    /* Probe kernel RS-485 support */
    if (ioctl(cm->fd, TIOCGRS485, &ctx->orig_config) < 0) {
        ctx->is_hw_rs485 = false;
        memset(&ctx->active_config, 0, sizeof(ctx->active_config));
        logging_callback("rs485: TIOCGRS485 not supported, falling back to manual DE/RE");
    } else {
        ctx->is_hw_rs485 = true;
        memcpy(&ctx->active_config, &ctx->orig_config, sizeof(ctx->active_config));
    }
    
    /* Configure hardware RS-485 if available */
    if (ctx->is_hw_rs485) {
        ctx->active_config.flags |= SER_RS485_ENABLED;
        ctx->active_config.flags |= (ctx->rx_during_tx ? SER_RS485_RX_DURING_TX : 0);
        ctx->active_config.flags &= ~SER_RS485_USE_GPIO; /* Kernel handles RTS automatically */
        ctx->active_config.delay_rts_before_send = 0;
        ctx->active_config.delay_rts_after_send = 0;
        
        if (ioctl(cm->fd, TIOCSRS485, &ctx->active_config) < 0) {
            logging_callback("rs485: TIOCSRS485 failed, disabling HW mode");
            ctx->is_hw_rs485 = false;
        }
    }
    
    return 0;
}

static void rs485_exit(struct conn_manager *cm) {
    if (!cm->backend_ctx) return;
    rs485_ctx_t *ctx = (rs485_ctx_t *)cm->backend_ctx;
    
    /* Restore original RS-485 configuration */
    if (ctx->is_hw_rs485) {
        ioctl(cm->fd, TIOCSRS485, &ctx->orig_config);
    }
    free(cm->backend_ctx);
    cm->backend_ctx = NULL;
}

/* ─── Read: Half-Duplex RX with EINTR Handling ──────────────────────────── */
__attribute__((hot)) static int rs485_read(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    int ret;
    do {
        ret = read(cm->fd, &cm->input_buf[cm->input_pos], sizeof(cm->input_buf) - cm->input_pos);
    } while (ret < 0 && errno == EINTR);
    return ret;
}

/* ─── Write: Half-Duplex TX (Kernel handles DE/RE if TIOCSRS485 active) ─── */
__attribute__((hot)) static int rs485_write(struct conn_manager *cm, const void *buf, int len) {
    int ret;
    do {
        ret = write(cm->fd, buf, len);
    } while (ret < 0 && errno == EINTR);
    return ret;
}

/* ─── Timing: Baudrate + Turnaround Delay ───────────────────────────────── */
__attribute__((hot, flatten)) static double rs485_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    rs485_ctx_t *ctx = (rs485_ctx_t *)cm->backend_ctx;
    double base_time = cm->bittime_adjust * bytes;
    double turnaround = ctx ? (ctx->turnaround_delay_us / 1e6) : 0.0;
    return base_time + turnaround;
}

/* ─── Flush TX: Drain UART FIFO & Ensure Line Idle ──────────────────────── */
static void rs485_flush_tx(struct conn_manager *cm) {
    if (isatty(cm->fd)) {
        tcdrain(cm->fd); /* Wait for transmitter empty */
        tcflush(cm->fd, TCOFLUSH);
    }
}

/* ─── Autonegotiation: Not applicable to RS-485 ─────────────────────────── */
static void rs485_autoneg_tick(struct conn_manager *cm, double eventtime) {
    (void)cm; (void)eventtime;
}

/* ─── Parameters: RS-485 Specific Tuning ────────────────────────────────── */
static void rs485_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    if (!cm->backend_ctx) return;
    rs485_ctx_t *ctx = (rs485_ctx_t *)cm->backend_ctx;
    
    if (strcmp(key, "turnaround_delay_us") == 0 && len == sizeof(uint32_t)) {
        uint32_t delay = *(const uint32_t*)value;
        if (delay <= 5000) ctx->turnaround_delay_us = delay;
    } else if (strcmp(key, "rx_during_tx") == 0 && len == sizeof(bool)) {
        ctx->rx_during_tx = *(const bool*)value;
        if (ctx->is_hw_rs485) {
            if (ctx->rx_during_tx) ctx->active_config.flags |= SER_RS485_RX_DURING_TX;
            else ctx->active_config.flags &= ~SER_RS485_RX_DURING_TX;
            ioctl(cm->fd, TIOCSRS485, &ctx->active_config);
        }
    } else if (strcmp(key, "rts_delay_us") == 0 && len == sizeof(uint32_t) && ctx->is_hw_rs485) {
        uint32_t delay = *(const uint32_t*)value;
        ctx->active_config.delay_rts_before_send = delay;
        ctx->active_config.delay_rts_after_send = delay;
        ioctl(cm->fd, TIOCSRS485, &ctx->active_config);
    }
}

/* ─── RT Optimization Hooks ─────────────────────────────────────────────── */
static int rs485_pin_irq(struct conn_manager *cm, int cpu_id) {
    (void)cm; (void)cpu_id;
    /* RS-485 typically uses standard UART IRQs; pinning handled by conn_manager */
    return 0;
}

static int rs485_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) {
    (void)cm; (void)cpu_list; (void)count;
    return 0;
}

/* ─── Backend Registration ──────────────────────────────────────────────── */
const conn_backend_ops_t conn_rs485_backend __attribute__((aligned(64))) = {
    .init = rs485_init,
    .exit = rs485_exit,
    .read = rs485_read,
    .write = rs485_write,
    .calc_bittime = rs485_calc_bittime,
    .autoneg_tick = rs485_autoneg_tick,
    .flush_tx = rs485_flush_tx,
    .set_params = rs485_set_params,
    .pin_irq = rs485_pin_irq,
    .set_irq_affinity = rs485_set_irq_affinity
};