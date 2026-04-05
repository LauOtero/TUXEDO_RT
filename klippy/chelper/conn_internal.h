#ifndef CONN_INTERNAL_H
#define CONN_INTERNAL_H

#include <stdint.h>
#include <pthread.h>
#include "list.h"
#include "msgblock.h"
#include "pollreactor.h"
#include "pyhelper.h"
#include "conn_manager.h"
#include "conn_backend.h"
#include "compiler.h"

/* ─── Constants ───────────────────────────────────────────────────────── */
#define CONN_PRIO_COUNT         4
#define TX_BUF_SIZE             4096u
#define RX_BUF_SIZE             4096u
#define CACHE_LINE              64u
#define CACHE_ALIGN             __attribute__((aligned(CACHE_LINE)))
#define MAX_MCU_NAME_LEN        16

/* ─── TX Buffer (lock-free double buffer) ──────────────────────────────── */
typedef struct {
    uint32_t pos, max;
    uint8_t buf[TX_BUF_SIZE];
} CACHE_ALIGN tx_half_t;

typedef struct {
    tx_half_t half[2];
    volatile uint8_t active;
    uint8_t _pad[3];
} CACHE_ALIGN tx_shadow_t;

/* ─── Message Queues ──────────────────────────────────────────────────── */
typedef struct message_sub_queue {
    struct list_head msg_queue;
    struct list_node node;
} message_sub_queue_t;

struct command_queue {
    message_sub_queue_t ready, upcoming;
};

/* ─── Receiver State ──────────────────────────────────────────────────── */
typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    int waiting;
    struct list_head queue, old_receive;
} receiver_t;

/* ─── Transmit Scheduler ──────────────────────────────────────────────── */
typedef struct {
    int pipe_fds[2];
    pthread_mutex_t lock;
    struct list_head upcoming_queues;
    int upcoming_bytes;
    uint64_t need_kick_clock, min_release_clock;
} tx_scheduler_t;

/* ─── EtherCAT Master Context (per connection) ────────────────────────── */
typedef struct {
    void *master_handle;          // ec_master_t*
    void *domain_handle;          // ec_domain_t*
    uint16_t alias;
    uint16_t position;
    uint32_t vendor_id, product_id;
    
    /* PDO Mapping */
    struct {
        uint32_t index;
        uint8_t subindex;
        uint8_t bit_length;
        void *data_ptr;           // Mapped to domain memory
    } pdos[ETHERTUX_MAX_PDOS_PER_SLAVE];
    int pdo_count;
    
    /* Sync Manager Configuration */
    uint32_t sync0_cycle_time_ns;
    uint32_t sync1_cycle_time_ns;
    
    /* RT Optimization */
    int irq_cpu_affinity[8];
    int irq_cpu_count;
    bool use_hwtimestamp;
    
    /* State */
    bool slave_online;
    uint64_t last_dc_sync;
    uint32_t error_count;
} ethertux_ctx_t;

/* ─── Full Connection Manager Structure ───────────────────────────────── */
struct conn_manager {
    /* Core */
    struct pollreactor *pr;
    int fd;
    char conn_type;
    int client_id;
    char mcu_name[MAX_MCU_NAME_LEN];
    
    /* Buffers */
    uint8_t input_buf[RX_BUF_SIZE];
    uint8_t need_sync;
    int input_pos;
    
    /* Threading */
    receiver_t receiver;
    tx_scheduler_t tx_sched;
    pthread_t tid;
    pthread_mutex_t lock;
    
    /* Timing */
    int receive_window;
    double bittime_adjust, idle_time;
    struct clock_estimate ce;
    double last_receive_sent_time;
    
    /* Sequence & Retransmit */
    uint64_t send_seq, receive_seq;
    uint64_t ignore_nak_seq, last_ack_seq, retransmit_seq, rtt_sample_seq;
    struct list_head sent_queue;
    double srtt, rttvar, rto;
    
    /* Queues */
    struct list_head ready_queues;
    int ready_bytes, need_ack_bytes, last_ack_bytes;
    int max_pending_blocks;
    struct list_head notify_queue;
    double last_write_fail_time;
    
    /* Fast Reader Dispatch */
    pthread_mutex_t fast_reader_dispatch_lock;
    struct list_head fast_readers;
    
    /* Debug */
    struct list_head old_sent;
    uint32_t bytes_write, bytes_read, bytes_retransmit, bytes_invalid;
    
    /* Backend */
    const conn_backend_ops_t *backend;
    void *backend_ctx;  // Opaque pointer for backend-specific data
    
    /* EtherCAT-specific (only if conn_type == CONN_TYPE_ETHERTUX) */
    ethertux_ctx_t *ethertux;
    
    /* RT Tuning */
    int rt_cpu_id;
    int rt_priority;
    bool irq_affinity_set;
} CACHE_ALIGN;

/* ─── Internal Helpers ────────────────────────────────────────────────── */

#endif /* CONN_INTERNAL_H */