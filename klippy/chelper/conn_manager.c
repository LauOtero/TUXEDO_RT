/*
 * conn_manager.c — Connection Manager Core (RT Optimized)
 * Modular backend architecture for UART, CAN, EtherCAT (IGH)
 * Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
 * Updated for deterministic RT, ultra-low jitter, multi-backend support
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#pragma GCC optimize ("O3", "unroll-loops", "align-functions=64", "align-jumps=32")
#pragma GCC target ("sse4.2,pclmul,popcnt")

#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
#include <sys/stat.h>
#include "compiler.h"
#include "list.h"
#include "msgblock.h"
#include "pollreactor.h"
#include "pyhelper.h"
#include "conn_manager.h"
#include "conn_internal.h"
#include "conn_backend.h"
#include "ultracrc.h"

/* ─── Constants ───────────────────────────────────────────────────────── */
#define CONNPF_FD       0
#define CONNPF_PIPE     1
#define CONNPF_NUM      2

#define CONNPT_RETRANSMIT   0
#define CONNPT_COMMAND      1
#define CONNPT_AUTONEG      2
#define CONNPT_NUM          3

#define MIN_RTO                 0.025
#define MAX_RTO                 5.000
#define DEFAULT_MAX_PENDING     12
#define ABSOLUTE_MAX_PENDING    16
#define MIN_REQTIME_DELTA       0.100
#define MIN_BACKGROUND_DELTA    0.005
#define IDLE_QUERY_TIME         1.0
#define DEBUG_QUEUE_COUNT       100

/* ─── Debug Helpers ───────────────────────────────────────────────────── */
static __always_inline void conn_debug_queue_alloc(struct list_head *root, int count) {
    for (int i = 0; i < count; i++) {
        struct queue_message *qm = message_alloc();
        list_add_head(&qm->node, root);
    }
}

static __always_inline struct queue_message *_conn_debug_queue_add(struct list_head *root, struct queue_message *qm) {
    list_add_tail(&qm->node, root);
    struct queue_message *old = list_first_entry(root, struct queue_message, node);
    list_del(&old->node);
    return old;
}

static __always_inline void conn_debug_queue_add(struct list_head *root, struct queue_message *qm) {
    struct queue_message *old = _conn_debug_queue_add(root, qm);
    message_free(old);
}

static __always_inline void receive_append_wake(receiver_t *receiver, struct list_head *msgs) {
    int dokick = 0;
    pthread_mutex_lock(&receiver->lock);
    list_join_tail(msgs, &receiver->queue);
    if (receiver->waiting) { receiver->waiting = 0; dokick = 1; }
    pthread_mutex_unlock(&receiver->lock);
    if (dokick) pthread_cond_signal(&receiver->cond);
}

static __always_inline void conn_kick_bg_thread(struct conn_manager *cm) {
    int ret = write(cm->tx_sched.pipe_fds[1], ".", 1);
    if (ret < 0) report_errno("pipe write", ret);
}

/* ─── Bit Timing ──────────────────────────────────────────────────────── */
__attribute__((hot)) static double conn_calc_bittime(struct conn_manager *cm, uint32_t bytes) {
    if (cm->backend && cm->backend->calc_bittime)
        return cm->backend->calc_bittime(cm, bytes);
    return cm->bittime_adjust * bytes;
}

/* ─── Sequence Management ─────────────────────────────────────────────── */
static void update_receive_seq(struct conn_manager *cm, double eventtime, uint64_t rseq) {
    uint64_t sent_seq = cm->receive_seq;
    for (;;) {
        struct queue_message *sent = list_first_entry(&cm->sent_queue, struct queue_message, node);
        if (list_empty(&cm->sent_queue)) { cm->send_seq = rseq; cm->last_receive_sent_time = 0.; break; }
        cm->need_ack_bytes -= sent->len;
        list_del(&sent->node); conn_debug_queue_add(&cm->old_sent, sent);
        sent_seq++;
        if (rseq == sent_seq) { cm->last_receive_sent_time = sent->receive_time; cm->last_ack_bytes = sent->len; break; }
    }
    cm->receive_seq = rseq;
    pollreactor_update_timer(cm->pr, CONNPT_COMMAND, PR_NOW);
    
    if (cm->rtt_sample_seq && rseq > cm->rtt_sample_seq && cm->last_receive_sent_time) {
        double delta = eventtime - cm->last_receive_sent_time;
        if (!cm->srtt) { cm->rttvar = delta / 2.0; cm->srtt = delta * 10.0; }
        else { cm->rttvar = (3.0 * cm->rttvar + fabs(cm->srtt - delta)) / 4.0; cm->srtt = (7.0 * cm->srtt + delta) / 8.0; }
        double rttvar4 = cm->rttvar * 4.0; if (rttvar4 < 0.001) rttvar4 = 0.001;
        cm->rto = cm->srtt + rttvar4;
        if (cm->rto < MIN_RTO) cm->rto = MIN_RTO; else if (cm->rto > MAX_RTO) cm->rto = MAX_RTO;
        cm->rtt_sample_seq = 0;
    }
    if (list_empty(&cm->sent_queue)) pollreactor_update_timer(cm->pr, CONNPT_RETRANSMIT, PR_NEVER);
    else {
        struct queue_message *sent = list_first_entry(&cm->sent_queue, struct queue_message, node);
        double nr = eventtime + cm->rto + conn_calc_bittime(cm, sent->len);
        pollreactor_update_timer(cm->pr, CONNPT_RETRANSMIT, nr);
    }
}

/* ─── Message Handler (zero-copy fast path) ───────────────────────────── */
__attribute__((hot)) static void handle_message(struct conn_manager *cm, double eventtime, uint8_t *msg_buf, int len) {
    pthread_mutex_lock(&cm->lock);
    int is_v2 = (msg_buf[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2);
    uint8_t seq_byte = is_v2 ? msg_buf[3] : msg_buf[MESSAGE_POS_SEQ_V1];
    uint32_t rseq_delta = ((seq_byte - cm->receive_seq) & MESSAGE_SEQ_MASK);
    uint64_t rseq = cm->receive_seq + rseq_delta;
    
    if (rseq != cm->receive_seq) {
        if (rseq > cm->send_seq && cm->receive_seq != 1) { cm->bytes_invalid += len; pthread_mutex_unlock(&cm->lock); return; }
        update_receive_seq(cm, eventtime, rseq);
    }
    cm->bytes_read += len;
    
    struct list_head received; list_init(&received);
    while (!list_empty(&cm->notify_queue)) {
        struct queue_message *qm = list_first_entry(&cm->notify_queue, struct queue_message, node);
        uint64_t wake_seq = rseq - 1 - (len > MESSAGE_MIN ? 1 : 0);
        if (qm->req_clock > wake_seq) break;
        list_del(&qm->node); qm->len = 0; qm->sent_time = cm->last_receive_sent_time; qm->receive_time = eventtime;
        list_add_tail(&qm->node, &received);
    }
    
    if (len == MESSAGE_MIN) {
        if (cm->last_ack_seq < rseq) cm->last_ack_seq = rseq;
        else if (rseq > cm->ignore_nak_seq && !list_empty(&cm->sent_queue))
            pollreactor_update_timer(cm->pr, CONNPT_RETRANSMIT, PR_NOW);
    } else {
        struct queue_message *qm = message_fill(msg_buf, len);
        qm->sent_time = (rseq > cm->retransmit_seq ? cm->last_receive_sent_time : 0.);
        qm->receive_time = get_monotonic(); qm->receive_time -= conn_calc_bittime(cm, len);
        list_add_tail(&qm->node, &received);
    }
    if (!list_empty(&received)) receive_append_wake(&cm->receiver, &received);
    
    /* Fast Reader Dispatch (lock-free after prefix match) */
    struct fastreader *fr;
    list_for_each_entry(fr, &cm->fast_readers, node) {
        int header_size = (msg_buf[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2) ? 4 : MESSAGE_HEADER_SIZE_V1;
        if (len < fr->prefix_len + header_size + MESSAGE_TRAILER_SIZE ||
            memcmp(&msg_buf[header_size], fr->prefix, fr->prefix_len) != 0) continue;
        
        void (*fastreader_func)(struct fastreader *, uint8_t *, int) = fr->func;
        struct fastreader *fastreader_ptr = fr;
        pthread_mutex_unlock(&cm->lock);
        pthread_mutex_lock(&cm->fast_reader_dispatch_lock);
        fastreader_func(fastreader_ptr, msg_buf, len);
        pthread_mutex_unlock(&cm->fast_reader_dispatch_lock);
        return;
    }
    pthread_mutex_unlock(&cm->lock);
}

/* ─── Input Event Handler (backend-agnostic) ──────────────────────────── */
__attribute__((hot)) static void input_event(struct conn_manager *cm, double eventtime) {
    if (cm->backend && cm->backend->read) {
        int ret = cm->backend->read(cm, eventtime);
        if (ret < 0) { report_errno("backend read", ret); pollreactor_do_exit(cm->pr); return; }
        if (ret == 0 && cm->conn_type != CONN_TYPE_CAN) { errorf("Got EOF when reading from device"); pollreactor_do_exit(cm->pr); return; }
        cm->input_pos += ret;
    } else {
        int ret = read(cm->fd, &cm->input_buf[cm->input_pos], sizeof(cm->input_buf) - cm->input_pos);
        if (ret <= 0) {
            if (ret < 0) report_errno("read", ret);
            else errorf("Got EOF when reading from device");
            pollreactor_do_exit(cm->pr); return;
        }
        cm->input_pos += ret;
    }
    
    int processed = 0;
    for (;;) {
        int len = msgblock_check(&cm->need_sync, &cm->input_buf[processed], cm->input_pos - processed);
        if (!len) break;
        if (len > 0) handle_message(cm, eventtime, &cm->input_buf[processed], len);
        else { len = -len; pthread_mutex_lock(&cm->lock); cm->bytes_invalid += len; pthread_mutex_unlock(&cm->lock); }
        processed += len;
    }
    if (processed) {
        cm->input_pos -= processed;
        if (cm->input_pos) memmove(cm->input_buf, &cm->input_buf[processed], cm->input_pos);
    }
}

/* ─── Background Thread ───────────────────────────────────────────────── */
static void kick_event(struct conn_manager *cm, double eventtime) {
    (void)eventtime;
    char dummy[4096];
    int ret = read(cm->tx_sched.pipe_fds[0], dummy, sizeof(dummy));
    if (ret < 0 && errno != EAGAIN) report_errno("pipe read", ret);
    pollreactor_update_timer(cm->pr, CONNPT_COMMAND, PR_NOW);
}

static void retransmit_event(struct conn_manager *cm, double eventtime) {
    (void)cm; (void)eventtime;
}

static void command_event(struct conn_manager *cm, double eventtime) {
    (void)cm; (void)eventtime;
}

static void *background_thread(void *data) {
    struct conn_manager *cm = data;
    set_thread_name(cm->mcu_name);
    
    /* RT Pinning (if configured) */
    if (cm->rt_cpu_id >= 0) {
        if (ultracrc_pin_to_cpu(cm->rt_cpu_id) == 0)
            errorf("conn: pinned to CPU %d", cm->rt_cpu_id);
        if (cm->rt_priority > 0)
            ultracrc_set_rt_scheduler(cm->rt_priority);
    }
    
    pollreactor_run(cm->pr);
    struct list_head dummy; list_init(&dummy);
    receive_append_wake(&cm->receiver, &dummy);
    return NULL;
}

/* ─── Public API: Allocation ──────────────────────────────────────────── */
__visible struct conn_manager *
conn_alloc(int fd, char conn_type, int client_id, const char name[MAX_MCU_NAME_LEN])
{
    struct conn_manager *cm = malloc(sizeof(*cm));
    if (!cm) return NULL;
    memset(cm, 0, sizeof(*cm));
    
    cm->fd = fd; cm->conn_type = conn_type; cm->client_id = client_id;
    strncpy(cm->mcu_name, name, MAX_MCU_NAME_LEN - 1); cm->mcu_name[MAX_MCU_NAME_LEN - 1] = '\0';
    
    /* Backend Selection */
    cm->backend = conn_backend_get_ops(conn_type);
    if (!cm->backend) { free(cm); return NULL; }
    
    /* Backend Initialization */
    if (cm->backend->init) {
        if (cm->backend->init(cm) != 0) { free(cm); return NULL; }
    }
    
    cm->max_pending_blocks = DEFAULT_MAX_PENDING;
    cm->send_seq = 1; cm->receive_seq = (conn_type == CONN_TYPE_DEBUGFILE) ? -1 : 1;
    cm->rto = (conn_type == CONN_TYPE_DEBUGFILE) ? PR_NEVER : MIN_RTO;
    
    /* Pipe for kick */
    if (pipe(cm->tx_sched.pipe_fds) != 0) goto fail;
    
    /* Pollreactor Setup */
    cm->pr = pollreactor_alloc(CONNPF_NUM, CONNPT_NUM, cm);
    pollreactor_add_fd(cm->pr, CONNPF_FD, fd, input_event, conn_type == CONN_TYPE_DEBUGFILE);
    pollreactor_add_fd(cm->pr, CONNPF_PIPE, cm->tx_sched.pipe_fds[0], kick_event, 0);
    pollreactor_add_timer(cm->pr, CONNPT_RETRANSMIT, retransmit_event);
    pollreactor_add_timer(cm->pr, CONNPT_COMMAND, command_event);
    if (cm->backend->autoneg_tick)
        pollreactor_add_timer(cm->pr, CONNPT_AUTONEG, (pollreactor_timer_cb)cm->backend->autoneg_tick);
    
    /* Non-blocking I/O */
    fd_set_non_blocking(fd);
    fd_set_non_blocking(cm->tx_sched.pipe_fds[0]);
    fd_set_non_blocking(cm->tx_sched.pipe_fds[1]);
    
    /* Queue Initialization */
    cm->tx_sched.need_kick_clock = MAX_CLOCK;
    cm->tx_sched.min_release_clock = MAX_CLOCK;
    list_init(&cm->tx_sched.upcoming_queues);
    pthread_mutex_init(&cm->tx_sched.lock, NULL);
    list_init(&cm->ready_queues); list_init(&cm->sent_queue);
    list_init(&cm->receiver.queue); list_init(&cm->notify_queue); list_init(&cm->fast_readers);
    list_init(&cm->old_sent); list_init(&cm->receiver.old_receive);
    conn_debug_queue_alloc(&cm->old_sent, DEBUG_QUEUE_COUNT);
    conn_debug_queue_alloc(&cm->receiver.old_receive, DEBUG_QUEUE_COUNT);
    
    /* Mutexes */
    pthread_mutex_init(&cm->lock, NULL);
    pthread_mutex_init(&cm->receiver.lock, NULL);
    pthread_cond_init(&cm->receiver.cond, NULL);
    pthread_mutex_init(&cm->fast_reader_dispatch_lock, NULL);
    
    /* Thread Creation */
    if (pthread_create(&cm->tid, NULL, background_thread, cm) != 0) goto fail;
    
    cm->rt_cpu_id = -1; cm->rt_priority = 0; cm->irq_affinity_set = false;
    return cm;
    
fail:
    report_errno("conn_alloc", errno);
    if (cm->pr) pollreactor_free(cm->pr);
    free(cm);
    return NULL;
}

/* ─── Public API: Lifecycle ───────────────────────────────────────────── */
__visible void conn_exit(struct conn_manager *cm) {
    if (!cm) return;
    if (cm->backend && cm->backend->exit) cm->backend->exit(cm);
    pollreactor_do_exit(cm->pr);
    conn_kick_bg_thread(cm);
    pthread_join(cm->tid, NULL);
}

__visible void conn_free(struct conn_manager *cm) {
    if (!cm) return;
    if (!pollreactor_is_exit(cm->pr)) conn_exit(cm);
    
    pthread_mutex_lock(&cm->lock);
    message_queue_free(&cm->sent_queue);
    pthread_mutex_lock(&cm->receiver.lock);
    message_queue_free(&cm->receiver.queue);
    message_queue_free(&cm->receiver.old_receive);
    pthread_mutex_unlock(&cm->receiver.lock);
    message_queue_free(&cm->notify_queue); message_queue_free(&cm->old_sent);
    
    while (!list_empty(&cm->ready_queues)) {
        struct command_queue *cq = list_first_entry(&cm->ready_queues, struct command_queue, ready.node);
        list_del(&cq->ready.node); message_queue_free(&cq->ready.msg_queue);
    }
    pthread_mutex_lock(&cm->tx_sched.lock);
    while (!list_empty(&cm->tx_sched.upcoming_queues)) {
        struct command_queue *cq = list_first_entry(&cm->tx_sched.upcoming_queues, struct command_queue, upcoming.node);
        list_del(&cq->upcoming.node); message_queue_free(&cq->upcoming.msg_queue);
    }
    pthread_mutex_unlock(&cm->tx_sched.lock);
    pthread_mutex_unlock(&cm->lock);
    
    pollreactor_free(cm->pr);
    if (cm->backend_ctx) free(cm->backend_ctx);
    if (cm->ethertux) free(cm->ethertux);
    free(cm);
}

/* ─── Public API: Command Queue ───────────────────────────────────────── */
__visible struct command_queue *conn_alloc_commandqueue(void) {
    struct command_queue *cq = malloc(sizeof(*cq));
    if (!cq) return NULL;
    memset(cq, 0, sizeof(*cq));
    list_init(&cq->ready.msg_queue); list_init(&cq->upcoming.msg_queue);
    return cq;
}

__visible void conn_free_commandqueue(struct command_queue *cq) {
    if (!cq) return;
    if (!list_empty(&cq->ready.msg_queue) || !list_empty(&cq->upcoming.msg_queue)) {
        errorf("Memory leak! Can't free non-empty commandqueue"); return;
    }
    free(cq);
}

__visible int conn_commandqueue_is_empty(struct command_queue *cq) {
    if (!cq) return 1;
    return list_empty(&cq->ready.msg_queue) && list_empty(&cq->upcoming.msg_queue);
}

/* ─── Public API: Send ────────────────────────────────────────────────── */
__visible void conn_send(struct conn_manager *cm, struct command_queue *cq,
                         uint8_t *msg, int len, uint64_t min_clock,
                         uint64_t req_clock, uint64_t notify_id)
{
    struct queue_message *qm = message_fill(msg, len);
    if (!qm) return;
    qm->min_clock = min_clock; qm->req_clock = req_clock; qm->notify_id = notify_id;
    conn_send_one(cm, cq, qm);
}

void conn_send_one(struct conn_manager *cm, struct command_queue *cq, struct queue_message *qm) {
    struct list_head msgs; list_init(&msgs); list_add_tail(&qm->node, &msgs);
    conn_send_batch(cm, cq, &msgs);
}

void conn_send_batch(struct conn_manager *cm, struct command_queue *cq, struct list_head *msgs) {
    int len = 0; struct queue_message *qm;
    list_for_each_entry(qm, msgs, node) {
        if (qm->min_clock + (3LL << 29) < qm->req_clock && qm->req_clock != BACKGROUND_PRIORITY_CLOCK)
            qm->min_clock = qm->req_clock - (3LL << 29);
        len += qm->len;
    }
    if (!len) return;
    
    qm = list_first_entry(msgs, struct queue_message, node);
    uint64_t min_clock = qm->min_clock;
    int mustwake = 0;
    
    pthread_mutex_lock(&cm->tx_sched.lock);
    if (list_empty(&cq->upcoming.msg_queue)) {
        list_add_tail(&cq->upcoming.node, &cm->tx_sched.upcoming_queues);
        if (min_clock < cm->tx_sched.min_release_clock) cm->tx_sched.min_release_clock = min_clock;
        if (min_clock < cm->tx_sched.need_kick_clock) { cm->tx_sched.need_kick_clock = 0; mustwake = 1; }
    }
    list_join_tail(msgs, &cq->upcoming.msg_queue);
    cm->tx_sched.upcoming_bytes += len;
    pthread_mutex_unlock(&cm->tx_sched.lock);
    
    if (mustwake) conn_kick_bg_thread(cm);
}

/* ─── Public API: Pull & Fast Reader ──────────────────────────────────── */
__visible void conn_pull(struct conn_manager *cm, struct pull_queue_message *pqm) {
    receiver_t *r = &cm->receiver;
    pthread_mutex_lock(&r->lock);
    while (list_empty(&r->queue)) {
        if (pollreactor_is_exit(cm->pr)) goto exit;
        r->waiting = 1;
        int ret = pthread_cond_wait(&r->cond, &r->lock); if (ret) report_errno("pthread_cond_wait", ret);
    }
    struct queue_message *qm = list_first_entry(&r->queue, struct queue_message, node);
    list_del(&qm->node);
    memcpy(pqm->msg, qm->msg, qm->len); pqm->len = qm->len;
    pqm->sent_time = qm->sent_time; pqm->receive_time = qm->receive_time; pqm->notify_id = qm->notify_id;
    if (qm->len) qm = _conn_debug_queue_add(&r->old_receive, qm);
    pthread_mutex_unlock(&r->lock); message_free(qm); return;
exit:
    pqm->len = -1; pthread_mutex_unlock(&r->lock);
}

__visible void conn_add_fastreader(struct conn_manager *cm, struct fastreader *fr) {
    pthread_mutex_lock(&cm->lock); list_add_tail(&fr->node, &cm->fast_readers); pthread_mutex_unlock(&cm->lock);
}

__visible void conn_rm_fastreader(struct conn_manager *cm, struct fastreader *fr) {
    pthread_mutex_lock(&cm->lock); list_del(&fr->node); pthread_mutex_unlock(&cm->lock);
    pthread_mutex_lock(&cm->fast_reader_dispatch_lock); pthread_mutex_unlock(&cm->fast_reader_dispatch_lock);
}

/* ─── Public API: Configuration ───────────────────────────────────────── */
__visible void conn_set_wire_frequency(struct conn_manager *cm, double frequency) {
    pthread_mutex_lock(&cm->lock);
    cm->bittime_adjust = (cm->conn_type == CONN_TYPE_CAN) ? 1. / frequency : 10. / frequency;
    pthread_mutex_unlock(&cm->lock);
}

__visible void conn_set_receive_window(struct conn_manager *cm, int receive_window) {
    pthread_mutex_lock(&cm->lock); cm->receive_window = receive_window; pthread_mutex_unlock(&cm->lock);
}

__visible void conn_set_clock_est(struct conn_manager *cm, double est_freq, double conv_time,
                                   uint64_t conv_clock, uint64_t last_clock)
{
    pthread_mutex_lock(&cm->lock);
    clock_fill(&cm->ce, est_freq, conv_time, conv_clock, last_clock);
    pthread_mutex_unlock(&cm->lock);
}

__visible void conn_get_clock_est(struct conn_manager *cm, struct clock_estimate *ce) {
    pthread_mutex_lock(&cm->lock);
    memcpy(ce, &cm->ce, sizeof(cm->ce));
    pthread_mutex_unlock(&cm->lock);
}

__visible void conn_set_backend_params(struct conn_manager *cm, const char *key, const void *value, size_t len) {
    if (cm->backend && cm->backend->set_params)
        cm->backend->set_params(cm, key, value, len);
}

/* ─── Backend-specific Wrappers ───────────────────────────────────────── */
__visible void conn_set_can_params(struct conn_manager *cm, int mode, int retries, int xl_sdt) {
    if (cm->conn_type != CONN_TYPE_CAN) return;
    pthread_mutex_lock(&cm->lock);
    if (cm->backend && cm->backend->set_params)
        cm->backend->set_params(cm, "can_mode", &mode, sizeof(mode));
    pthread_mutex_unlock(&cm->lock);
}

__visible void conn_set_usb_profile(struct conn_manager *cm, int max_pending_blocks) {
    pthread_mutex_lock(&cm->lock);
    if (max_pending_blocks > 0 && max_pending_blocks <= ABSOLUTE_MAX_PENDING)
        cm->max_pending_blocks = max_pending_blocks;
    pthread_mutex_unlock(&cm->lock);
}

__visible void conn_set_ethertux_params(struct conn_manager *cm, uint16_t alias, uint16_t position,
                                         uint32_t vendor_id, uint32_t product_id, uint32_t cycle_time_ns)
{
    if (cm->conn_type != CONN_TYPE_ETHERTUX || !cm->ethertux) return;
    pthread_mutex_lock(&cm->lock);
    cm->ethertux->alias = alias;
    cm->ethertux->position = position;
    cm->ethertux->vendor_id = vendor_id;
    cm->ethertux->product_id = product_id;
    cm->ethertux->sync0_cycle_time_ns = cycle_time_ns;
    pthread_mutex_unlock(&cm->lock);
}

/* ─── RT Optimization Hooks ───────────────────────────────────────────── */
__visible int conn_pin_to_cpu(struct conn_manager *cm, int cpu_id) {
    if (cpu_id < 0) return -1;
    cm->rt_cpu_id = cpu_id;
    if (cm->tid) return ultracrc_pin_to_cpu(cpu_id);
    return 0;
}

__visible int conn_set_fifo_priority(struct conn_manager *cm, int priority) {
    if (priority < 1 || priority > 99) return -1;
    cm->rt_priority = priority;
    if (cm->tid) return ultracrc_set_rt_scheduler(priority);
    return 0;
}

__visible int conn_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count) {
    if (!cpu_list || count <= 0 || count > 8) return -1;
    if (cm->backend && cm->backend->set_irq_affinity)
        return cm->backend->set_irq_affinity(cm, cpu_list, count);
    return -1;
}

/* ─── Diagnostics ─────────────────────────────────────────────────────── */
__visible void conn_get_stats(struct conn_manager *cm, char *buf, int len) {
    struct conn_manager stats;
    pthread_mutex_lock(&cm->lock); pthread_mutex_lock(&cm->tx_sched.lock);
    memcpy(&stats, cm, sizeof(stats));
    pthread_mutex_unlock(&cm->tx_sched.lock); pthread_mutex_unlock(&cm->lock);
    
    snprintf(buf, len, "bytes_write=%u bytes_read=%u bytes_retransmit=%u bytes_invalid=%u "
             "send_seq=%u receive_seq=%u retransmit_seq=%u srtt=%.3f rttvar=%.3f rto=%.3f "
             "ready_bytes=%u upcoming_bytes=%u conn_type=%c",
             (unsigned int)stats.bytes_write, (unsigned int)stats.bytes_read,
             (unsigned int)stats.bytes_retransmit, (unsigned int)stats.bytes_invalid,
             (unsigned int)stats.send_seq, (unsigned int)stats.receive_seq,
             (unsigned int)stats.retransmit_seq, stats.srtt, stats.rttvar, stats.rto,
             (unsigned int)stats.ready_bytes, (unsigned int)stats.tx_sched.upcoming_bytes,
             stats.conn_type);
}

__visible int conn_extract_old(struct conn_manager *cm, int sentq, struct pull_queue_message *q, int max) {
    int count = sentq ? DEBUG_QUEUE_COUNT : DEBUG_QUEUE_COUNT;
    struct list_head repl, curr; list_init(&repl); conn_debug_queue_alloc(&repl, count); list_init(&curr);
    
    if (sentq) {
        pthread_mutex_lock(&cm->lock);
        list_join_tail(&cm->old_sent, &curr); list_init(&cm->old_sent); list_join_tail(&repl, &cm->old_sent);
        pthread_mutex_unlock(&cm->lock);
    } else {
        pthread_mutex_lock(&cm->receiver.lock);
        list_join_tail(&cm->receiver.old_receive, &curr); list_init(&cm->receiver.old_receive); list_join_tail(&repl, &cm->receiver.old_receive);
        pthread_mutex_unlock(&cm->receiver.lock);
    }
    
    int pos = 0;
    while (!list_empty(&curr)) {
        struct queue_message *qm = list_first_entry(&curr, struct queue_message, node);
        if (qm->len && pos < max) {
            memcpy(q[pos].msg, qm->msg, qm->len); q[pos].len = qm->len;
            q[pos].sent_time = qm->sent_time; q[pos].receive_time = qm->receive_time; pos++;
        }
        list_del(&qm->node); message_free(qm);
    }
    return pos;
}

/* ─── Backend Factory ─────────────────────────────────────────────────── */
const conn_backend_ops_t *conn_backend_get_ops(char conn_type) {
    switch (conn_type) {
        case CONN_TYPE_SERIAL:    return &conn_serial_backend;
        case CONN_TYPE_CAN:       return &conn_can_backend;
        case CONN_TYPE_ETHERTUX:  return &conn_ethertux_backend;
        case CONN_TYPE_RS485:     return &conn_rs485_backend;
        case CONN_TYPE_DEBUGFILE: return NULL; // Debug file uses generic path
        default: return NULL;
    }
}