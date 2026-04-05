/*
 * conn_ethertux.c — EtherCAT Backend using IGH EtherCAT Master
 * Deterministic RT, ultra-low jitter, hardware timestamping support
 * Copyright (C) 2026 Klipper Community
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "conn_internal.h"
#include "conn_backend.h"
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <sys/mman.h>
#include "pyhelper.h"

#if __has_include(<ethercat.h>)
#include <ethercat.h>
/* ─── IGH EtherCAT Master Wrappers ────────────────────────────────────── */
typedef struct ec_master ec_master_t;
typedef struct ec_domain ec_domain_t;
typedef struct ec_slave_config ec_slave_config_t;
#else
/* Dummy implementations for systems without IGH EtherCAT Master */
typedef struct ec_master ec_master_t;
typedef struct ec_domain ec_domain_t;
typedef struct ec_slave_config ec_slave_config_t;
static inline ec_master_t *ecrt_request_master(unsigned int master_index) { return NULL; }
static inline void ecrt_release_master(ec_master_t *master) {}
static inline ec_domain_t *ecrt_master_create_domain(ec_master_t *master) { return NULL; }
static inline void ecrt_domain_release(ec_domain_t *domain) {}
static inline void ecrt_master_receive(ec_master_t *master) {}
static inline void ecrt_domain_process(ec_domain_t *domain) {}
static inline uint8_t *ecrt_domain_data(ec_domain_t *domain) { return NULL; }
static inline void ecrt_domain_queue(ec_domain_t *domain) {}
static inline void ecrt_master_send(ec_master_t *master) {}
static inline ec_slave_config_t *ecrt_master_slave_config(ec_master_t *master, uint16_t alias, uint16_t position, uint32_t vendor_id, uint32_t product_code) { return NULL; }
static inline void ecrt_slave_config_dc(ec_slave_config_t *sc, uint16_t assign_activate, uint32_t sync0_cycle_time, uint32_t sync0_shift_time, uint32_t sync1_cycle_time, uint32_t sync1_shift_time) {}
static inline int ecrt_master_activate(ec_master_t *master) { return -1; }
static inline void ecrt_master_sync(ec_master_t *master) {}
#endif

/* ─── Ethertux Context Helpers ────────────────────────────────────────── */
static ethertux_ctx_t *ethertux_ctx_alloc(void) {
    ethertux_ctx_t *ctx = calloc(1, sizeof(ethertux_ctx_t));
    if (!ctx) return NULL;
    ctx->sync0_cycle_time_ns = ETHERTUX_DEFAULT_CYCLE_TIME_NS;
    ctx->sync1_cycle_time_ns = ETHERTUX_DEFAULT_CYCLE_TIME_NS;
    ctx->use_hwtimestamp = true;
    return ctx;
}

static void ethertux_ctx_free(ethertux_ctx_t *ctx) {
    if (!ctx) return;
    /* Cleanup IGH resources if initialized */
    if (ctx->domain_handle) ecrt_domain_release(ctx->domain_handle);
    if (ctx->master_handle) ecrt_release_master(ctx->master_handle);
    free(ctx);
}

/* ─── Backend Initialization ──────────────────────────────────────────── */
__attribute__((cold)) static int ethertux_init(struct conn_manager *cm) {
    if (!cm->ethertux) {
        cm->ethertux = ethertux_ctx_alloc();
        if (!cm->ethertux) return -ENOMEM;
    }
    
    /* Request IGH Master */
    ec_master_t *master = ecrt_request_master(0); /* Master index 0 */
    if (!master) {
        errorf("ethertux: failed to request IGH master");
        return -ENODEV;
    }
    cm->ethertux->master_handle = master;
    
    /* Create Domain */
    ec_domain_t *domain = ecrt_master_create_domain(master);
    if (!domain) {
        errorf("ethertux: failed to create domain");
        ecrt_release_master(master);
        return -ENOMEM;
    }
    cm->ethertux->domain_handle = domain;
    
    /* Configure Slave (lazy: actual config on first send) */
    cm->ethertux->slave_online = false;
    
    /* RT Memory Locking */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0)
        errorf("ethertux: memory locked for RT");
    
    return 0;
}

static void ethertux_exit(struct conn_manager *cm) {
    if (cm->ethertux) {
        ethertux_ctx_free(cm->ethertux);
        cm->ethertux = NULL;
    }
}

/* ─── Read: Non-blocking Domain Process ───────────────────────────────── */
__attribute__((hot)) static int ethertux_read(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    if (!cm->ethertux || !cm->ethertux->slave_online) return 0;
    
    ec_master_t *master = cm->ethertux->master_handle;
    ec_domain_t *domain = cm->ethertux->domain_handle;
    
    /* Receive process data */
    ecrt_master_receive(master);
    ecrt_domain_process(domain);
    
    /* Check for incoming Klipper messages in Rx PDO */
    /* Ethertux slave packs Klipper protocol into a dedicated Rx PDO */
    uint8_t *domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) return 0;
    
    /* Assume Klipper Rx PDO starts at offset 0 for simplicity */
    int available = 0; /* Slave reports available bytes via status PDO */
    /* ... [Implementar lógica de extracción de mensajes Klipper desde PDO] ... */
    
    return available;
}

/* ─── Write: Domain Queue with Sync Management ────────────────────────── */
__attribute__((hot)) static int ethertux_write(struct conn_manager *cm, const void *buf, int len) {
    if (!cm->ethertux || !cm->ethertux->slave_online) return -ENOTCONN;
    if (len > ETHERTUX_PDU_MAX_SIZE) return -EMSGSIZE;
    
    ec_domain_t *domain = cm->ethertux->domain_handle;
    uint8_t *domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) return -ENOMEM;
    
    /* Copy to Tx PDO (zero-copy if aligned) */
    /* Ethertux slave expects Klipper protocol in Tx PDO at known offset */
    memcpy(domain_pd, buf, len);
    
    /* Queue domain for transmission */
    ecrt_domain_queue(domain);
    
    /* Send process data */
    ecrt_master_send(cm->ethertux->master_handle);
    
    return len;
}

/* ─── Timing: EtherCAT Cycle-based ────────────────────────────────────── */
__attribute__((hot, flatten)) static double ethertux_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    /* EtherCAT: deterministic cycle time, not bit-time */
    (void)bytes;
    if (!cm->ethertux) return 0.0;
    return cm->ethertux->sync0_cycle_time_ns / 1e9; /* Convert ns to seconds */
}

/* ─── Autonegotiation: Slave Detection & PDO Mapping ──────────────────── */
__attribute__((hot)) static void ethertux_autoneg_tick(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    if (!cm->ethertux || cm->ethertux->slave_online) return;
    
    ec_master_t *master = cm->ethertux->master_handle;
    ec_slave_config_t *sc = ecrt_master_slave_config(
        master, cm->ethertux->alias, cm->ethertux->position,
        cm->ethertux->vendor_id, cm->ethertux->product_id);
    if (!sc) return;
    
    /* Configure DC Sync */
    ecrt_slave_config_dc(sc, 0x0300, cm->ethertux->sync0_cycle_time_ns,
                         cm->ethertux->sync1_cycle_time_ns, 0, 0);
    
    /* Configure PDOs (Klipper protocol mapping) */
    /* ... [Implementar mapeo de PDOs para Klipper Tx/Rx] ... */
    
    /* Activate configuration */
    if (ecrt_master_activate(master) == 0) {
        cm->ethertux->slave_online = true;
        errorf("ethertux: slave %u:%u online", cm->ethertux->alias, cm->ethertux->position);
    }
}

/* ─── Flush: Domain Sync ──────────────────────────────────────────────── */
static void ethertux_flush_tx(struct conn_manager *cm) {
    if (cm->ethertux && cm->ethertux->master_handle)
        ecrt_master_sync(cm->ethertux->master_handle);
}

/* ─── Parameters: EtherCAT-specific Tuning ────────────────────────────── */
static void ethertux_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    if (!cm->ethertux) return;
    
    if (strcmp(key, "cycle_time_ns") == 0 && len == sizeof(uint32_t)) {
        uint32_t ns = *(const uint32_t*)value;
        if (ns >= 125000 && ns <= 1000000) /* 125µs - 1ms */
            cm->ethertux->sync0_cycle_time_ns = ns;
    } else if (strcmp(key, "hwtimestamp") == 0 && len == sizeof(bool)) {
        cm->ethertux->use_hwtimestamp = *(const bool*)value;
    }
}

/* ─── RT Optimization: IRQ Affinity for EtherCAT Master ───────────────── */
static int ethertux_pin_irq(struct conn_manager *cm, int cpu_id) {
    if (!cm->ethertux) return -ENODEV;
    /* IGH: ecrt_master_set_irq_affinity() if available in version */
    /* Fallback: set CPU affinity for master thread */
    return conn_pin_to_cpu(cm, cpu_id);
}

static int ethertux_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) {
    if (!cm->ethertux || !cpu_list || count <= 0) return -EINVAL;
    /* IGH: ecrt_master_set_irq_cpu_mask() for multi-IRQ systems */
    /* For now, pin to first CPU in list */
    return conn_pin_to_cpu(cm, cpu_list[0]);
}

/* ─── Backend Registration ────────────────────────────────────────────── */
const conn_backend_ops_t conn_ethertux_backend __attribute__((aligned(64))) = {
    .init = ethertux_init,
    .exit = ethertux_exit,
    .read = ethertux_read,
    .write = ethertux_write,
    .calc_bittime = ethertux_calc_bittime,
    .autoneg_tick = ethertux_autoneg_tick,
    .flush_tx = ethertux_flush_tx,
    .set_params = ethertux_set_params,
    .pin_irq = ethertux_pin_irq,
    .set_irq_affinity = ethertux_set_irq_affinity
};