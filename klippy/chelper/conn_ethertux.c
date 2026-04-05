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

/* ─── IGH EtherCAT Master Header ───────────────────────────────────────── */
#if __has_include(<ecrt.h>)
#define HAVE_IGH_ETHERCAT 1
#include <ecrt.h>
#else
/* Fallback to local header */
#define HAVE_IGH_ETHERCAT 0
#include "ecrt.h"
#endif

#if HAVE_IGH_ETHERCAT

/* ─── Type Aliases for IGH EtherCAT Master ────────────────────────────── */
typedef ec_master_t ec_master_t;
typedef ec_domain_t ec_domain_t;
typedef ec_slave_config_t ec_slave_config_t;

/* ─── Klipper Protocol over EtherCAT Mapping ──────────────────────────── */
/* 
 * Klipper protocol messages are mapped to dedicated PDO entries:
 * - RxPDO (Master -> Slave): Klipper commands from host to MCU
 * - TxPDO (Slave -> Master): Klipper responses from MCU to host
 * 
 * PDO Mapping Configuration:
 *   Sync Manager 2 (Output): RxPDO at index 0x1600
 *   Sync Manager 3 (Input):  TxPDO at index 0x1A00
 * 
 * Each PDO entry contains:
 *   - [0-1]: Message length (uint16_t)
 *   - [2-3]: Sequence number (uint16_t)  
 *   - [4..]: Payload data (up to ETHERTUX_PDU_MAX_SIZE - 4 bytes)
 */

#define KLIPPER_PDO_RX_INDEX    0x1600  /* RxPDO mapping index */
#define KLIPPER_PDO_TX_INDEX    0x1A00  /* TxPDO mapping index */
#define KLIPPER_PDO_LENGTH_OFS  0       /* Length field offset */
#define KLIPPER_PDO_SEQ_OFS     2       /* Sequence number offset */
#define KLIPPER_PDO_DATA_OFS    4       /* Payload data offset */
#define KLIPPER_PDO_HEADER_SIZE 4       /* Header size (length + seq) */



/* ─── PDO Mapping Configuration for Klipper Protocol ──────────────────── */
/*
 * Define the PDO mapping for Klipper communication over EtherCAT.
 * This uses standard CiA402-like mapping with custom entries for Klipper protocol.
 */
static const ec_pdo_entry_info_t klipper_rxpdo_entries[] = {
    {0x1600, 0x00, 16},  /* Message length (16 bits) */
    {0x1600, 0x01, 16},  /* Sequence number (16 bits) */
    {0x1600, 0x02, 8},   /* Payload byte 0 */
    {0x1600, 0x03, 8},   /* Payload byte 1 */
    {0x1600, 0x04, 8},   /* Payload byte 2 */
    {0x1600, 0x05, 8},   /* Payload byte 3 */
    {0x1600, 0x06, 8},   /* Payload byte 4 */
    {0x1600, 0x07, 8},   /* Payload byte 5 */
    {0x1600, 0x08, 8},   /* Payload byte 6 */
    {0x1600, 0x09, 8},   /* Payload byte 7 */
    /* Add more payload bytes as needed up to ETHERTUX_PDU_MAX_SIZE */
};

static const ec_pdo_entry_info_t klipper_txpdo_entries[] = {
    {0x1A00, 0x00, 16},  /* Message length (16 bits) */
    {0x1A00, 0x01, 16},  /* Sequence number (16 bits) */
    {0x1A00, 0x02, 8},   /* Payload byte 0 */
    {0x1A00, 0x03, 8},   /* Payload byte 1 */
    {0x1A00, 0x04, 8},   /* Payload byte 2 */
    {0x1A00, 0x05, 8},   /* Payload byte 3 */
    {0x1A00, 0x06, 8},   /* Payload byte 4 */
    {0x1A00, 0x07, 8},   /* Payload byte 5 */
    {0x1A00, 0x08, 8},   /* Payload byte 6 */
    {0x1A00, 0x09, 8},   /* Payload byte 7 */
    /* Add more payload bytes as needed */
};

static const ec_pdo_info_t klipper_rxpdos[] = {
    {0x1600, ARRAY_SIZE(klipper_rxpdo_entries), klipper_rxpdo_entries},
};

static const ec_pdo_info_t klipper_txpdos[] = {
    {0x1A00, ARRAY_SIZE(klipper_txpdo_entries), klipper_txpdo_entries},
};

static const ec_sync_info_t klipper_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, ARRAY_SIZE(klipper_rxpdos), klipper_rxpdos, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, ARRAY_SIZE(klipper_txpdos), klipper_txpdos, EC_WD_DISABLE},
    {0xFF}
};

/* ─── Ethertux Context Helpers ────────────────────────────────────────── */
static ethertux_ctx_t *ethertux_ctx_alloc(void) {
    ethertux_ctx_t *ctx = calloc(1, sizeof(ethertux_ctx_t));
    if (!ctx) return NULL;
    ctx->sync0_cycle_time_ns = ETHERTUX_DEFAULT_CYCLE_TIME_NS;
    ctx->sync1_cycle_time_ns = ETHERTUX_DEFAULT_CYCLE_TIME_NS;
    ctx->sync0_shift_time_ns = 0;
    ctx->sync1_shift_time_ns = 0;
    ctx->use_hwtimestamp = true;
    ctx->irq_cpu_count = 0;
    return ctx;
}

static void ethertux_ctx_free(ethertux_ctx_t *ctx) {
    if (!ctx) return;
    /* Cleanup IGH resources if initialized */
    if (ctx->domain_handle) {
        /* Note: ecrt_domain_release() is not available in IGH 1.6,
         * domains are released automatically when master is released */
        ctx->domain_handle = NULL;
    }
    if (ctx->master_handle) {
        ecrt_release_master(ctx->master_handle);
        ctx->master_handle = NULL;
    }
    free(ctx);
}

/* ─── PDO Registration Helper ─────────────────────────────────────────── */
static int ethertux_register_pdos(ethertux_ctx_t *ctx) {
    int ret;
    
    /* Register RxPDO entries */
    ret = ecrt_slave_config_reg_pdo_entry(
        ctx->slave_config, 0x1600, 0x00, ctx->domain_handle, &ctx->rxpdo_offset);
    if (ret < 0) {
        errorf("ethertux: failed to register RxPDO length entry");
        return ret;
    }
    ctx->rxpdo_size = 2; /* Start with length field size */
    
    /* Register additional RxPDO payload entries */
    for (int i = 1; i < 8 && ctx->rxpdo_size < ETHERTUX_PDU_MAX_SIZE; i++) {
        unsigned int offset;
        ret = ecrt_slave_config_reg_pdo_entry(
            ctx->slave_config, 0x1600, i, ctx->domain_handle, &offset);
        if (ret < 0) break;
        ctx->rxpdo_size += 1;
    }
    
    /* Register TxPDO entries */
    ret = ecrt_slave_config_reg_pdo_entry(
        ctx->slave_config, 0x1A00, 0x00, ctx->domain_handle, &ctx->txpdo_offset);
    if (ret < 0) {
        errorf("ethertux: failed to register TxPDO length entry");
        return ret;
    }
    ctx->txpdo_size = 2; /* Start with length field size */
    
    /* Register additional TxPDO payload entries */
    for (int i = 1; i < 8 && ctx->txpdo_size < ETHERTUX_PDU_MAX_SIZE; i++) {
        unsigned int offset;
        ret = ecrt_slave_config_reg_pdo_entry(
            ctx->slave_config, 0x1A00, i, ctx->domain_handle, &offset);
        if (ret < 0) break;
        ctx->txpdo_size += 1;
    }
    
    return 0;
}

/* ─── Backend Initialization ──────────────────────────────────────────── */
__attribute__((cold)) static int ethertux_init(struct conn_manager *cm) {
    int ret;
    
    if (!cm->ethertux) {
        cm->ethertux = ethertux_ctx_alloc();
        if (!cm->ethertux) return -ENOMEM;
    }
    
    ethertux_ctx_t *ctx = cm->ethertux;
    
    /* Request IGH Master (index 0 = first master) */
    ec_master_t *master = ecrt_request_master(0);
    if (!master) {
        errorf("ethertux: failed to request IGH master (index 0)");
        return -ENODEV;
    }
    ctx->master_handle = master;
    
    /* Create Domain */
    ec_domain_t *domain = ecrt_master_create_domain(master);
    if (!domain) {
        errorf("ethertux: failed to create domain");
        ecrt_release_master(master);
        ctx->master_handle = NULL;
        return -ENOMEM;
    }
    ctx->domain_handle = domain;
    
    /* Get Slave Configuration */
    ctx->slave_config = ecrt_master_slave_config(
        master, ctx->alias, ctx->position, ctx->vendor_id, ctx->product_id);
    if (!ctx->slave_config) {
        errorf("ethertux: failed to get slave config (%u:%u, %04X:%04X)",
               ctx->alias, ctx->position, ctx->vendor_id, ctx->product_id);
        ecrt_release_master(master);
        ctx->master_handle = NULL;
        return -ENODEV;
    }
    
    /* Configure PDOs */
    ret = ethertux_register_pdos(ctx);
    if (ret < 0) {
        ecrt_release_master(master);
        ctx->master_handle = NULL;
        return ret;
    }
    
    /* Configure Distributed Clocks (DC) */
    /* assign_activate: 0x0300 = SYNC0 + SYNC1 activation */
    ecrt_slave_config_dc(ctx->slave_config, 0x0300,
                         ctx->sync0_cycle_time_ns, ctx->sync0_shift_time_ns,
                         ctx->sync1_cycle_time_ns, ctx->sync1_shift_time_ns);
    
    /* Set PDO mapping using sync manager configuration */
    ret = ecrt_slave_config_pdos(ctx->slave_config, EC_END, klipper_syncs);
    if (ret < 0) {
        errorf("ethertux: failed to configure PDOs");
        ecrt_release_master(master);
        ctx->master_handle = NULL;
        return ret;
    }
    
    /* Initial state */
    ctx->slave_online = false;
    ctx->domain_active = false;
    ctx->last_rx_seq = 0;
    ctx->last_tx_seq = 0;
    ctx->error_count = 0;
    
    /* RT Memory Locking (optional, requires CAP_IPC_LOCK) */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0) {
        errorf("ethertux: memory locked for real-time operation");
    } else {
        errorf("ethertux: warning - could not lock memory (run as root or set capabilities)");
    }
    
    return 0;
}

static void ethertux_exit(struct conn_manager *cm) {
    if (cm->ethertux) {
        ethertux_ctx_free(cm->ethertux);
        cm->ethertux = NULL;
    }
}

/* ─── Read: Extract Klipper Messages from TxPDO ───────────────────────── */
__attribute__((hot)) static int ethertux_read(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    
    if (!cm->ethertux || !cm->ethertux->slave_online) return 0;
    
    ethertux_ctx_t *ctx = cm->ethertux;
    ec_master_t *master = ctx->master_handle;
    ec_domain_t *domain = ctx->domain_handle;
    
    /* Receive process data from network */
    ecrt_master_receive(master);
    
    /* Process domain data and update states */
    int ret = ecrt_domain_process(domain);
    if (ret < 0) {
        ctx->error_count++;
        return 0;
    }
    
    /* Get pointer to domain process data */
    uint8_t *domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) return 0;
    
    /* Store domain pointer for later use */
    ctx->domain_pd = domain_pd;
    
    /* Read message from TxPDO (Slave -> Master) */
    if (ctx->txpdo_offset + KLIPPER_PDO_HEADER_SIZE >= ctx->txpdo_size) {
        return 0; /* No valid data */
    }
    
    /* Extract length and sequence from TxPDO */
    uint16_t msg_len = *(uint16_t*)(domain_pd + ctx->txpdo_offset + KLIPPER_PDO_LENGTH_OFS);
    uint16_t seq_num = *(uint16_t*)(domain_pd + ctx->txpdo_offset + KLIPPER_PDO_SEQ_OFS);
    
    /* Validate message */
    if (msg_len == 0 || msg_len > ETHERTUX_PDU_MAX_SIZE - KLIPPER_PDO_HEADER_SIZE) {
        return 0; /* Invalid length */
    }
    
    /* Check sequence number (simple duplicate detection) */
    if (seq_num == ctx->last_rx_seq) {
        return 0; /* Duplicate message */
    }
    ctx->last_rx_seq = seq_num;
    
    /* Copy payload to input buffer */
    uint8_t *src = domain_pd + ctx->txpdo_offset + KLIPPER_PDO_DATA_OFS;
    int copy_len = (msg_len < cm->input_pos) ? msg_len : (sizeof(cm->input_buf) - cm->input_pos);
    if (copy_len > 0 && cm->input_pos + copy_len <= sizeof(cm->input_buf)) {
        memcpy(&cm->input_buf[cm->input_pos], src, copy_len);
        cm->input_pos += copy_len;
        return copy_len;
    }
    
    return 0;
}

/* ─── Write: Send Klipper Messages via RxPDO ──────────────────────────── */
__attribute__((hot)) static int ethertux_write(struct conn_manager *cm, const void *buf, int len) {
    if (!cm->ethertux || !cm->ethertux->slave_online) return -ENOTCONN;
    if (len <= 0 || len > ETHERTUX_PDU_MAX_SIZE - KLIPPER_PDO_HEADER_SIZE) return -EMSGSIZE;
    
    ethertux_ctx_t *ctx = cm->ethertux;
    ec_master_t *master = ctx->master_handle;
    ec_domain_t *domain = ctx->domain_handle;
    
    /* Get pointer to domain process data */
    uint8_t *domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) return -ENOMEM;
    
    ctx->domain_pd = domain_pd;
    
    /* Increment sequence number */
    ctx->last_tx_seq++;
    
    /* Write length to RxPDO */
    *(uint16_t*)(domain_pd + ctx->rxpdo_offset + KLIPPER_PDO_LENGTH_OFS) = (uint16_t)len;
    
    /* Write sequence number to RxPDO */
    *(uint16_t*)(domain_pd + ctx->rxpdo_offset + KLIPPER_PDO_SEQ_OFS) = ctx->last_tx_seq;
    
    /* Copy payload to RxPDO */
    memcpy(domain_pd + ctx->rxpdo_offset + KLIPPER_PDO_DATA_OFS, buf, len);
    
    /* Queue domain for transmission */
    int ret = ecrt_domain_queue(domain);
    if (ret < 0) {
        ctx->error_count++;
        return ret;
    }
    
    /* Send process data to network */
    ret = ecrt_master_send(master);
    if (ret < 0) {
        ctx->error_count++;
        return ret;
    }
    
    return len;
}

/* ─── Timing: EtherCAT Cycle-based Bit Time Calculation ───────────────── */
__attribute__((hot, flatten)) static double ethertux_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    (void)bytes; /* EtherCAT uses fixed cycle time, not bit-time */
    
    if (!cm->ethertux) return 0.0;
    
    /* Return cycle time in seconds */
    return cm->ethertux->sync0_cycle_time_ns / 1e9;
}

/* ─── Autonegotiation: Slave Detection & Activation ───────────────────── */
__attribute__((hot)) static void ethertux_autoneg_tick(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    
    if (!cm->ethertux || cm->ethertux->slave_online) return;
    
    ethertux_ctx_t *ctx = cm->ethertux;
    ec_master_state_t ms;
    
    /* Check master state */
    ecrt_master_state(ctx->master_handle, &ms);
    
    /* Check if slaves are responding */
    if (ms.slaves_responding == 0) {
        return; /* No slaves detected yet */
    }
    
    /* Check slave configuration state */
    ec_slave_config_state_t sc_state;
    ecrt_slave_config_state(ctx->slave_config, &sc_state);
    
    if (sc_state.online && sc_state.operational) {
        /* Slave is online and operational - activate domain */
        int ret = ecrt_master_activate(ctx->master_handle);
        if (ret == 0) {
            ctx->domain_active = true;
            ctx->slave_online = true;
            errorf("ethertux: slave %u:%u online and operational",
                   ctx->alias, ctx->position);
        } else {
            ctx->error_count++;
            errorf("ethertux: failed to activate master (error %d)", ret);
        }
    }
}

/* ─── Flush: Synchronize Master ───────────────────────────────────────── */
static void ethertux_flush_tx(struct conn_manager *cm) {
    if (!cm->ethertux || !cm->ethertux->master_handle) return;
    
    /* Force immediate transmission */
    ecrt_master_send(cm->ethertux->master_handle);
}

/* ─── Parameters: EtherCAT-specific Configuration ─────────────────────── */
static void ethertux_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    if (!cm->ethertux) return;
    
    ethertux_ctx_t *ctx = cm->ethertux;
    
    if (strcmp(key, "cycle_time_ns") == 0 && len == sizeof(uint32_t)) {
        uint32_t ns = *(const uint32_t*)value;
        /* Valid range: 125µs to 10ms (typical EtherCAT cycle times) */
        if (ns >= 125000 && ns <= 10000000) {
            ctx->sync0_cycle_time_ns = ns;
            ctx->sync1_cycle_time_ns = ns;
        }
    } else if (strcmp(key, "sync0_shift_ns") == 0 && len == sizeof(int32_t)) {
        ctx->sync0_shift_time_ns = *(const int32_t*)value;
    } else if (strcmp(key, "sync1_shift_ns") == 0 && len == sizeof(int32_t)) {
        ctx->sync1_shift_time_ns = *(const int32_t*)value;
    } else if (strcmp(key, "hwtimestamp") == 0 && len == sizeof(bool)) {
        ctx->use_hwtimestamp = *(const bool*)value;
    } else if (strcmp(key, "alias") == 0 && len == sizeof(uint16_t)) {
        ctx->alias = *(const uint16_t*)value;
    } else if (strcmp(key, "position") == 0 && len == sizeof(uint16_t)) {
        ctx->position = *(const uint16_t*)value;
    } else if (strcmp(key, "vendor_id") == 0 && len == sizeof(uint32_t)) {
        ctx->vendor_id = *(const uint32_t*)value;
    } else if (strcmp(key, "product_id") == 0 && len == sizeof(uint32_t)) {
        ctx->product_id = *(const uint32_t*)value;
    }
}

/* ─── RT Optimization: CPU Affinity ───────────────────────────────────── */
static int ethertux_pin_irq(struct conn_manager *cm, int cpu_id) {
    if (!cm->ethertux || cpu_id < 0) return -ENODEV;
    
    /* Store CPU affinity for later use */
    cm->ethertux->irq_cpu_affinity[0] = cpu_id;
    cm->ethertux->irq_cpu_count = 1;
    
    /* Pin current thread to CPU */
    return conn_pin_to_cpu(cm, cpu_id);
}

static int ethertux_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) {
    if (!cm->ethertux || !cpu_list || count <= 0 || count > 8) return -EINVAL;
    
    /* Store CPU affinity mask */
    memcpy(cm->ethertux->irq_cpu_affinity, cpu_list, count * sizeof(int));
    cm->ethertux->irq_cpu_count = count;
    
    /* Pin to first CPU in list */
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

#else

/* ─── Dummy Implementation for Missing IGH EtherCAT Master ────────────── */
static int dummy_init(struct conn_manager *cm) {
    errorf("ethertux: IGH EtherCAT Master library not available");
    return -ENOSYS;
}
static void dummy_exit(struct conn_manager *cm) {}
static int dummy_read(struct conn_manager *cm, double eventtime) { return 0; }
static int dummy_write(struct conn_manager *cm, const void *buf, int len) { return -ENOSYS; }
static double dummy_calc_bittime(struct conn_manager *cm, uint32_t bytes) { return 0.0; }
static void dummy_autoneg_tick(struct conn_manager *cm, double eventtime) {}
static void dummy_flush_tx(struct conn_manager *cm) {}
static void dummy_set_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {}
static int dummy_pin_irq(struct conn_manager *cm, int cpu_id) { return -ENOSYS; }
static int dummy_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) { return -ENOSYS; }

const conn_backend_ops_t conn_ethertux_backend __attribute__((aligned(64))) = {
    .init = dummy_init,
    .exit = dummy_exit,
    .read = dummy_read,
    .write = dummy_write,
    .calc_bittime = dummy_calc_bittime,
    .autoneg_tick = dummy_autoneg_tick,
    .flush_tx = dummy_flush_tx,
    .set_params = dummy_set_params,
    .pin_irq = dummy_pin_irq,
    .set_irq_affinity = dummy_set_irq_affinity
};

#endif