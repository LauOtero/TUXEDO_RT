#ifndef SQ_INTERNAL_H
#define SQ_INTERNAL_H

#include <stdint.h>
#include "list.h"
#include "msgblock.h"
#include "pollreactor.h"
#include "pyhelper.h"
#include "serialqueue.h"
#include "sq_backend.h"
#include <pthread.h>

#define CANBUS_PRIO_COUNT 4
#define TX_BUF_SIZE 4096u
#define CACHE_LINE 64u
#define CACHE_ALIGN __attribute__((aligned(CACHE_LINE)))

struct tx_half {
    uint32_t pos, max;
    uint8_t buf[TX_BUF_SIZE];
} CACHE_ALIGN;

struct tx_shadow {
    struct tx_half half[2];
    volatile uint8_t active;
    uint8_t _pad[3];
} CACHE_ALIGN;

struct message_sub_queue {
    struct list_head msg_queue;
    struct list_node node;
};

struct command_queue {
    struct message_sub_queue ready, upcoming;
};

struct receiver {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    int waiting;
    struct list_head queue, old_receive;
};

struct transmit_requests {
    int pipe_fds[2];
    pthread_mutex_t lock;
    struct list_head upcoming_queues;
    int upcoming_bytes;
    uint64_t need_kick_clock, min_release_clock;
};

// Full internal structure (implementation only)
struct serialqueue {
    struct pollreactor *pr;
    int serial_fd, serial_fd_type, client_id;
    uint8_t input_buf[4096];
    uint8_t need_sync; int input_pos;
    struct receiver receiver;
    struct transmit_requests transmit_requests;
    char name[16]; pthread_t tid;
    pthread_mutex_t lock;
    int receive_window;
    double bittime_adjust, idle_time;
    struct clock_estimate ce;
    double last_receive_sent_time;
    uint64_t send_seq, receive_seq;
    uint64_t ignore_nak_seq, last_ack_seq, retransmit_seq, rtt_sample_seq;
    struct list_head sent_queue;
    double srtt, rttvar, rto;
    struct list_head ready_queues;
    int ready_bytes, need_ack_bytes, last_ack_bytes;
    int max_pending_blocks;
    struct list_head notify_queue;
    double last_write_fail_time;
    pthread_mutex_t fast_reader_dispatch_lock;
    struct list_head fast_readers;
    struct list_head old_sent;
    uint32_t bytes_write, bytes_read, bytes_retransmit, bytes_invalid;
    
    // CAN/Autoneg state
    canbus_mode_t can_mode; int can_max_dlen; uint8_t can_locked;
    int autoneg_state; double autoneg_state_entry_time;
    int autoneg_probe_sent, autoneg_probe_retries, autoneg_probe_result;
    uint32_t autoneg_consecutive_errors;
    uint32_t autoneg_obs_classic, autoneg_obs_fd, autoneg_obs_xl, autoneg_obs_total;
    int hw_supports_fd, hw_supports_brs, hw_supports_xl;
    struct tx_shadow tx[CANBUS_PRIO_COUNT];
    
    const sq_backend_ops_t *backend;
};

#endif // SQ_INTERNAL_H