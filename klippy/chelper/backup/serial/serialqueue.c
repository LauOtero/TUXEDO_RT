// Serial port command queuing (RT Optimized)
//
// Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
// This file may be distributed under the terms of the GNU GPLv3 license.
// The goal of this code is to handle low-level serial port
// communications with a microcontroller (mcu).  This code is written
// in C (instead of python) to reduce communication latencies and to
// reduce scheduling jitter.  The code queues messages to be
// transmitted, schedules transmission of commands at specified mcu
// clock times, prioritizes commands, and handles retransmissions.  A
// background thread is launched to do this work and minimize latency.

#pragma GCC optimize ("O3", "unroll-loops", "align-functions", "align-jumps")

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
#include "serialqueue.h"
#include "sq_internal.h"
#include "sq_backend.h"

// =========================================================================
// Pollreactor slots & backend types
// =========================================================================
#define SQPF_SERIAL 0
#define SQPF_PIPE   1
#define SQPF_NUM    2
#define SQPT_RETRANSMIT 0
#define SQPT_COMMAND    1
#define SQPT_CAN_AUTONEG 2
#define SQPT_NUM        3
#define SQT_UART 'u'
#define SQT_CAN  'c'
#define SQT_DEBUGFILE 'f'

#define MIN_RTO 0.025
#define MAX_RTO 5.000
#define DEFAULT_MAX_PENDING_BLOCKS 12
#define ABSOLUTE_MAX_PENDING_BLOCKS 16
#define MIN_REQTIME_DELTA 0.100
#define MIN_BACKGROUND_DELTA 0.005
#define IDLE_QUERY_TIME 1.0
#define DEBUG_QUEUE_SENT 100
#define DEBUG_QUEUE_RECEIVE 100

// =========================================================================
// Debug & Queue Helpers
// =========================================================================
static void debug_queue_alloc(struct list_head *root, int count) {
    for (int i=0; i<count; i++) {
        struct queue_message *qm = message_alloc();
        list_add_head(&qm->node, root);
    }
}

static struct queue_message *_debug_queue_add(struct list_head *root, struct queue_message *qm) {
    list_add_tail(&qm->node, root);
    struct queue_message *old = list_first_entry(root, struct queue_message, node);
    list_del(&old->node);
    return old;
}

static void debug_queue_add(struct list_head *root, struct queue_message *qm) {
    struct queue_message *old = _debug_queue_add(root, qm);
    message_free(old);
}

static void receive_append_wake(struct receiver *receiver, struct list_head *msgs) {
    int dokick = 0;
    pthread_mutex_lock(&receiver->lock);
    list_join_tail(msgs, &receiver->queue);
    if (receiver->waiting) { receiver->waiting = 0; dokick = 1; }
    pthread_mutex_unlock(&receiver->lock);
    if (dokick) pthread_cond_signal(&receiver->cond);
}

static void kick_bg_thread(struct serialqueue *sq) {
    int ret = write(sq->transmit_requests.pipe_fds[1], ".", 1);
    if (ret < 0) report_errno("pipe write", ret);
}

// =========================================================================
// Bit Timing & Autonegotiation
// =========================================================================

__attribute__((hot)) static double calculate_bittime(struct serialqueue *sq, uint32_t bytes) {
    if (sq->backend && sq->backend->calc_bittime) {
        return sq->backend->calc_bittime(sq, bytes);
    }
    return sq->bittime_adjust * bytes;
}

// =========================================================================
// Core Event Handlers
// =========================================================================
static void update_receive_seq(struct serialqueue *sq, double eventtime, uint64_t rseq) {
    uint64_t sent_seq = sq->receive_seq;
    for (;;) {
        struct queue_message *sent = list_first_entry(&sq->sent_queue, struct queue_message, node);
        if (list_empty(&sq->sent_queue)) { sq->send_seq = rseq; sq->last_receive_sent_time = 0.; break; }
        sq->need_ack_bytes -= sent->len;
        list_del(&sent->node); debug_queue_add(&sq->old_sent, sent);
        sent_seq++;
        if (rseq == sent_seq) { sq->last_receive_sent_time = sent->receive_time; sq->last_ack_bytes = sent->len; break; }
    }
    sq->receive_seq = rseq;
    pollreactor_update_timer(sq->pr, SQPT_COMMAND, PR_NOW);

    if (sq->rtt_sample_seq && rseq > sq->rtt_sample_seq && sq->last_receive_sent_time) {
        double delta = eventtime - sq->last_receive_sent_time;
        if (!sq->srtt) { sq->rttvar = delta / 2.0; sq->srtt = delta * 10.0; }
        else { sq->rttvar = (3.0 * sq->rttvar + fabs(sq->srtt - delta)) / 4.0; sq->srtt = (7.0 * sq->srtt + delta) / 8.0; }
        double rttvar4 = sq->rttvar * 4.0; if (rttvar4 < 0.001) rttvar4 = 0.001;
        sq->rto = sq->srtt + rttvar4;
        if (sq->rto < MIN_RTO) sq->rto = MIN_RTO; else if (sq->rto > MAX_RTO) sq->rto = MAX_RTO;
        sq->rtt_sample_seq = 0;
    }
    if (list_empty(&sq->sent_queue)) pollreactor_update_timer(sq->pr, SQPT_RETRANSMIT, PR_NEVER);
    else {
        struct queue_message *sent = list_first_entry(&sq->sent_queue, struct queue_message, node);
        double nr = eventtime + sq->rto + calculate_bittime(sq, sent->len);
        pollreactor_update_timer(sq->pr, SQPT_RETRANSMIT, nr);
    }
}

__attribute__((hot)) static void handle_message(struct serialqueue *sq, double eventtime, uint8_t *msg_buf, int len) {
    pthread_mutex_lock(&sq->lock);
    
    int is_v2 = (msg_buf[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2);
    uint8_t seq_byte = is_v2 ? msg_buf[3] : msg_buf[MESSAGE_POS_SEQ_V1];
    
    uint32_t rseq_delta = ((seq_byte - sq->receive_seq) & MESSAGE_SEQ_MASK);
    uint64_t rseq = sq->receive_seq + rseq_delta;
    if (rseq != sq->receive_seq) {
        if (rseq > sq->send_seq && sq->receive_seq != 1) { sq->bytes_invalid += len; pthread_mutex_unlock(&sq->lock); return; }
        update_receive_seq(sq, eventtime, rseq);
    }
    sq->bytes_read += len;

    struct list_head received; list_init(&received);
    while (!list_empty(&sq->notify_queue)) {
        struct queue_message *qm = list_first_entry(&sq->notify_queue, struct queue_message, node);
        uint64_t wake_seq = rseq - 1 - (len > MESSAGE_MIN ? 1 : 0);
        if (qm->req_clock > wake_seq) break;
        list_del(&qm->node); qm->len = 0; qm->sent_time = sq->last_receive_sent_time; qm->receive_time = eventtime;
        list_add_tail(&qm->node, &received);
    }

    if (len == MESSAGE_MIN) {
        if (sq->last_ack_seq < rseq) sq->last_ack_seq = rseq;
        else if (rseq > sq->ignore_nak_seq && !list_empty(&sq->sent_queue)) pollreactor_update_timer(sq->pr, SQPT_RETRANSMIT, PR_NOW);
    } else {
        struct queue_message *qm = message_fill(msg_buf, len);
        qm->sent_time = (rseq > sq->retransmit_seq ? sq->last_receive_sent_time : 0.);
        qm->receive_time = get_monotonic(); qm->receive_time -= calculate_bittime(sq, len);
        list_add_tail(&qm->node, &received);
    }
    if (!list_empty(&received)) receive_append_wake(&sq->receiver, &received);

    struct fastreader *fr;
    list_for_each_entry(fr, &sq->fast_readers, node) {
        int header_size = (msg_buf[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2) ? 4 : MESSAGE_HEADER_SIZE_V1;
        if (len < fr->prefix_len + header_size + MESSAGE_TRAILER_SIZE || memcmp(&msg_buf[header_size], fr->prefix, fr->prefix_len) != 0) continue;
        // Copiar la información del fastreader para llamarlo fuera del lock principal
        void (*fastreader_func)(struct fastreader *, uint8_t *, int) = fr->func;
        struct fastreader *fastreader_ptr = fr;
        
        pthread_mutex_unlock(&sq->lock); // Liberar el lock principal antes de llamar a la función externa
        
        // Proteger la llamada a la función del fastreader con su propio lock
        pthread_mutex_lock(&sq->fast_reader_dispatch_lock);
        fastreader_func(fastreader_ptr, msg_buf, len);
        pthread_mutex_unlock(&sq->fast_reader_dispatch_lock);
        return;
    }
    pthread_mutex_unlock(&sq->lock);
}

__attribute__((hot)) static void input_event(struct serialqueue *sq, double eventtime) {
    if (sq->backend && sq->backend->read) {
        int ret = sq->backend->read(sq, eventtime);
        if (ret < 0) {
            report_errno("backend read", ret);
            pollreactor_do_exit(sq->pr);
            return;
        }
        if (ret == 0 && sq->serial_fd_type != SQT_CAN) {
            errorf("Got EOF when reading from device");
            pollreactor_do_exit(sq->pr);
            return;
        }
        sq->input_pos += ret;
    } else {
        // Fallback genérico si no hay backend definido
        int ret = read(sq->serial_fd, &sq->input_buf[sq->input_pos], sizeof(sq->input_buf) - sq->input_pos);
        if (ret <= 0) {
            if (ret < 0) report_errno("read", ret);
            else errorf("Got EOF when reading from device");
            pollreactor_do_exit(sq->pr);
            return;
        }
        sq->input_pos += ret;
    }

    int processed = 0;
    for (;;) {
        int len = msgblock_check(&sq->need_sync, &sq->input_buf[processed], sq->input_pos - processed);
        if (!len) break;
        if (len > 0) {
            handle_message(sq, eventtime, &sq->input_buf[processed], len);
        } else {
            len = -len;
            pthread_mutex_lock(&sq->lock);
            sq->bytes_invalid += len;
            pthread_mutex_unlock(&sq->lock);
        }
        processed += len;
    }
    if (processed) {
        sq->input_pos -= processed;
        if (sq->input_pos) memmove(sq->input_buf, &sq->input_buf[processed], sq->input_pos);
    }
}

static void kick_event(struct serialqueue *sq, double eventtime) {
    (void)eventtime; char dummy[4096];
    int ret = read(sq->transmit_requests.pipe_fds[0], dummy, sizeof(dummy));
    if (ret < 0 && errno != EAGAIN && errno != EWOULDBLOCK) report_errno("pipe read", ret);
    pollreactor_update_timer(sq->pr, SQPT_COMMAND, PR_NOW);
}

__attribute__((hot)) static void do_write(struct serialqueue *sq, const void *buf, int buflen) {
    if (sq->backend && sq->backend->write) {
        int ret = sq->backend->write(sq, buf, buflen);
        if (ret < 0) {
            double cur = get_monotonic();
            if (sq->last_write_fail_time && cur > sq->last_write_fail_time + 10.0) { 
                errorf("Halting due to continuous write errors."); 
                pollreactor_do_exit(sq->pr); 
            }
            if (!sq->last_write_fail_time) sq->last_write_fail_time = cur;
        } else {
            sq->last_write_fail_time = 0.0;
        }
        return;
    }
    // Fallback genérico si no hay backend definido
    int ret = write(sq->serial_fd, buf, buflen);
    if (ret < 0) report_errno("write", ret);
}

__attribute__((hot)) static double retransmit_event(struct serialqueue *sq, double eventtime) {
    if (sq->serial_fd_type == SQT_UART && isatty(sq->serial_fd)) { int ret = tcflush(sq->serial_fd, TCOFLUSH); if (ret < 0) report_errno("tcflush", ret); }
    pthread_mutex_lock(&sq->lock);
    uint8_t buf[MESSAGE_MAX * ABSOLUTE_MAX_PENDING_BLOCKS + 1];
    int buflen = 0, first_buflen = 0;
    buf[buflen++] = MESSAGE_SYNC;
    struct queue_message *qm;
    list_for_each_entry(qm, &sq->sent_queue, node) {
        memcpy(&buf[buflen], qm->msg, qm->len); buflen += qm->len;
        if (!first_buflen) first_buflen = qm->len + 1;
    }
    do_write(sq, buf, buflen); sq->bytes_retransmit += buflen;

    if (pollreactor_get_timer(sq->pr, SQPT_RETRANSMIT) == PR_NOW) {
        sq->ignore_nak_seq = sq->receive_seq;
        if (sq->receive_seq < sq->retransmit_seq) sq->ignore_nak_seq = sq->retransmit_seq;
    } else { sq->rto *= 2.0; if (sq->rto > MAX_RTO) sq->rto = MAX_RTO; sq->ignore_nak_seq = sq->send_seq; }
    sq->retransmit_seq = sq->send_seq; sq->rtt_sample_seq = 0;
    sq->idle_time = eventtime + calculate_bittime(sq, buflen);
    double waketime = eventtime + sq->rto + calculate_bittime(sq, first_buflen);
    pthread_mutex_unlock(&sq->lock);
    return waketime;
}

__attribute__((hot)) static int build_and_send_command(struct serialqueue *sq, uint8_t *buf, int pending, double eventtime) {
    int len = MESSAGE_HEADER_SIZE_V1;
    int is_v2 = 0;
    while (sq->ready_bytes) {
        uint64_t min_clock = MAX_CLOCK;
        struct command_queue *q, *cq = NULL;
        struct queue_message *qm = NULL;
        list_for_each_entry(q, &sq->ready_queues, ready.node) {
            struct queue_message *m = list_first_entry(&q->ready.msg_queue, struct queue_message, node);
            if (m->req_clock < min_clock) { min_clock = m->req_clock; cq = q; qm = m; }
        }
        if (len + qm->len > MESSAGE_MAX_V1 - MESSAGE_TRAILER_SIZE) {
            // Upgrade to V2 if supported and necessary
            if (sq->max_pending_blocks * MESSAGE_MAX_V1 > MESSAGE_MAX_V1) { // Basic heuristic for V2 capability
                if (!is_v2) {
                    is_v2 = 1;
                    // Move already copied payloads forward by 2 bytes to make room for V2 header
                    if (len > MESSAGE_HEADER_SIZE_V1) {
                        memmove(&buf[MESSAGE_HEADER_SIZE_V2], &buf[MESSAGE_HEADER_SIZE_V1], len - MESSAGE_HEADER_SIZE_V1);
                    }
                    len += (MESSAGE_HEADER_SIZE_V2 - MESSAGE_HEADER_SIZE_V1);
                }
                if (len + qm->len > MESSAGE_MAX_V2 - MESSAGE_TRAILER_SIZE) break;
            } else {
                break;
            }
        }
        list_del(&qm->node);
        if (list_empty(&cq->ready.msg_queue)) list_del(&cq->ready.node);
        memcpy(&buf[len], qm->msg, qm->len); len += qm->len;
        sq->ready_bytes -= qm->len;
        if (qm->notify_id) { qm->req_clock = sq->send_seq; list_add_tail(&qm->node, &sq->notify_queue); }
        else message_free(qm);
    }
    if (len == (is_v2 ? 4 : MESSAGE_HEADER_SIZE_V1)) return 0;
    
    if (is_v2) {
        buf[0] = MESSAGE_SYNC_V2;
        buf[1] = (len + MESSAGE_TRAILER_SIZE) >> 8;
        buf[2] = (len + MESSAGE_TRAILER_SIZE) & 0xFF;
        buf[3] = (sq->send_seq & MESSAGE_SEQ_MASK) | MESSAGE_DEST;
    } else {
        buf[MESSAGE_POS_LEN] = len + MESSAGE_TRAILER_SIZE;
        buf[MESSAGE_POS_SEQ_V1] = (sq->send_seq & MESSAGE_SEQ_MASK) | MESSAGE_DEST;
    }
    
    uint16_t crc = msgblock_crc16_ccitt(buf, len);
    buf[len] = crc >> 8;
    buf[len+1] = crc & 0xff;
    buf[len+2] = MESSAGE_SYNC;
    len += MESSAGE_TRAILER_SIZE;

    double idletime = eventtime > sq->idle_time ? eventtime : sq->idle_time;
    idletime += calculate_bittime(sq, pending + len);
    struct queue_message *out = message_alloc();
    memcpy(out->msg, buf, len); out->len = len; out->sent_time = eventtime; out->receive_time = idletime;
    if (list_empty(&sq->sent_queue)) pollreactor_update_timer(sq->pr, SQPT_RETRANSMIT, idletime + sq->rto);
    if (!sq->rtt_sample_seq) sq->rtt_sample_seq = sq->send_seq;
    sq->send_seq++; sq->need_ack_bytes += len;
    list_add_tail(&out->node, &sq->sent_queue);
    return len;
}

__attribute__((hot)) static uint64_t check_upcoming_queues(struct serialqueue *sq, uint64_t ack_clock) {
    pthread_mutex_lock(&sq->transmit_requests.lock);
    sq->transmit_requests.need_kick_clock = 0;
    uint64_t min_release_clock = sq->transmit_requests.min_release_clock;
    if (ack_clock < min_release_clock) { pthread_mutex_unlock(&sq->transmit_requests.lock); return min_release_clock; }
    uint64_t min_stalled_clock = MAX_CLOCK;
    struct command_queue *cq, *_ncq;
    list_for_each_entry_safe(cq, _ncq, &sq->transmit_requests.upcoming_queues, upcoming.node) {
        int not_in_ready = list_empty(&cq->ready.msg_queue);
        struct queue_message *qm, *_nqm;
        list_for_each_entry_safe(qm, _nqm, &cq->upcoming.msg_queue, node) {
            if (ack_clock < qm->min_clock) { if (qm->min_clock < min_stalled_clock) min_stalled_clock = qm->min_clock; break; }
            list_del(&qm->node); list_add_tail(&qm->node, &cq->ready.msg_queue);
            sq->transmit_requests.upcoming_bytes -= qm->len; sq->ready_bytes += qm->len;
        }
        if (list_empty(&cq->upcoming.msg_queue)) list_del(&cq->upcoming.node);
        if (not_in_ready && !list_empty(&cq->ready.msg_queue)) list_add_tail(&cq->ready.node, &sq->ready_queues);
    }
    sq->transmit_requests.min_release_clock = min_stalled_clock;
    pthread_mutex_unlock(&sq->transmit_requests.lock);
    return min_stalled_clock;
}

__attribute__((hot)) static int update_need_kick_clock(struct serialqueue *sq, uint64_t wantclock) {
    pthread_mutex_lock(&sq->transmit_requests.lock);
    if (wantclock > sq->transmit_requests.min_release_clock) { pthread_mutex_unlock(&sq->transmit_requests.lock); return -1; }
    sq->transmit_requests.need_kick_clock = wantclock;
    pthread_mutex_unlock(&sq->transmit_requests.lock);
    return 0;
}

__attribute__((hot)) static double check_send_command(struct serialqueue *sq, int pending, double eventtime) {
    double idletime = eventtime > sq->idle_time ? eventtime : sq->idle_time;
    idletime += calculate_bittime(sq, pending + MESSAGE_MIN);
    uint64_t ack_clock = clock_from_time(&sq->ce, idletime);
    uint64_t min_stalled = check_upcoming_queues(sq, ack_clock);
    if (sq->send_seq - sq->receive_seq >= (uint64_t)sq->max_pending_blocks && sq->receive_seq != (uint64_t)-1) return eventtime + 0.250;
    if (sq->send_seq > sq->receive_seq && sq->receive_window) {
        int need = sq->need_ack_bytes + MESSAGE_MAX;
        if (sq->last_ack_seq < sq->receive_seq) need += sq->last_ack_bytes;
        if (need > sq->receive_window) return eventtime + 0.250;
    }
    if (sq->ready_bytes >= MESSAGE_PAYLOAD_MAX) return PR_NOW;
    if (!sq->ce.est_freq) { if (sq->ready_bytes) return PR_NOW; if (update_need_kick_clock(sq, 1)) return eventtime; return PR_NEVER; }

    uint64_t min_ready = MAX_CLOCK;
    struct command_queue *cq;
    list_for_each_entry(cq, &sq->ready_queues, ready.node) {
        struct queue_message *qm = list_first_entry(&cq->ready.msg_queue, struct queue_message, node);
        uint64_t req = qm->req_clock;
        double bgtime = pending ? idletime : sq->idle_time;
        if (req == BACKGROUND_PRIORITY_CLOCK) req = clock_from_time(&sq->ce, bgtime + MIN_REQTIME_DELTA + MIN_BACKGROUND_DELTA);
        if (req < min_ready) min_ready = req;
    }
    uint64_t delta = MIN_REQTIME_DELTA * sq->ce.est_freq;
    if (min_ready <= ack_clock + delta) return PR_NOW;
    if (pending) return eventtime;
    uint64_t want = min_ready - delta;
    if (min_stalled < want) want = min_stalled;
    if (update_need_kick_clock(sq, want)) return eventtime;
    return idletime + (want - ack_clock) / sq->ce.est_freq;
}

__attribute__((hot)) static double command_event(struct serialqueue *sq, double eventtime) {
    pthread_mutex_lock(&sq->lock);
    uint8_t buf[MESSAGE_MAX * ABSOLUTE_MAX_PENDING_BLOCKS];
    int buflen = 0; double waketime;
    for (;;) {
        waketime = check_send_command(sq, buflen, eventtime);
        if (waketime != PR_NOW) break;
        buflen += build_and_send_command(sq, &buf[buflen], buflen, eventtime);
        if (buflen + MESSAGE_MAX > MESSAGE_MAX * sq->max_pending_blocks) break;
    }
    if (buflen) {
        do_write(sq, buf, buflen); sq->bytes_write += buflen;
        double idletime = eventtime > sq->idle_time ? eventtime : sq->idle_time;
        sq->idle_time = idletime + calculate_bittime(sq, buflen);
        waketime = PR_NOW;
    }
    pthread_mutex_unlock(&sq->lock);
    return waketime;
}

// =========================================================================
// Thread & Public API
// =========================================================================
static void *background_thread(void *data) {
    struct serialqueue *sq = data;
    set_thread_name(sq->name);
    pollreactor_run(sq->pr);
    struct list_head dummy; list_init(&dummy);
    receive_append_wake(&sq->receiver, &dummy);
    return NULL;
}

static int detect_usb_speed(int fd, int *out_payload_size) {
    char tty_path[256];
    if (ttyname_r(fd, tty_path, sizeof(tty_path)) != 0) {
        return -1; // Not a tty or error
    }

    // Extract tty name (e.g., ttyACM0 from /dev/ttyACM0)
    char *tty_name = strrchr(tty_path, '/');
    if (!tty_name) tty_name = tty_path; else tty_name++;

    char sysfs_path[512];
    snprintf(sysfs_path, sizeof(sysfs_path), "/sys/class/tty/%s/device/speed", tty_name);

    FILE *f = fopen(sysfs_path, "r");
    if (!f) return -1; // Not a USB serial device or no sysfs access

    char speed_str[32] = {0};
    if (!fgets(speed_str, sizeof(speed_str) - 1, f)) {
        fclose(f);
        return -1;
    }
    fclose(f);

    double speed = atof(speed_str);
    if (speed >= 5000.0) {
        *out_payload_size = 1024; // USB 3.x
        return 3;
    } else if (speed >= 480.0) {
        *out_payload_size = 512;  // USB 2.0
        return 2;
    } else {
        *out_payload_size = 64;   // USB 1.1 or unknown
        return 1;
    }
}

static double autoneg_event_wrapper(struct serialqueue *sq, double eventtime) {
    if (sq->backend && sq->backend->autoneg_tick) {
        sq->backend->autoneg_tick(sq, eventtime);
        return eventtime + 0.01; // 10ms polling tick for autoneg
    }
    return PR_NEVER;
}

__visible int serialqueue_get_max_pending_blocks(struct serialqueue *sq) {
    return sq->max_pending_blocks;
}

__visible struct serialqueue *
serialqueue_alloc(int serial_fd, char serial_fd_type, int client_id, const char name[16])
{
    struct serialqueue *sq = malloc(sizeof(*sq));
    memset(sq, 0, sizeof(*sq));
    sq->serial_fd = serial_fd; sq->serial_fd_type = serial_fd_type; sq->client_id = client_id;

    if (serial_fd_type == SQT_CAN) {
        sq->backend = &sq_can_backend;
    } else {
        sq->backend = &sq_uart_backend;
    }

    if (sq->backend && sq->backend->init) {
        sq->backend->init(sq);
    }

    sq->max_pending_blocks = DEFAULT_MAX_PENDING_BLOCKS;

    // USB Version Detection
    if (serial_fd_type != SQT_CAN) {
        int payload_size = 0;
        int usb_version = detect_usb_speed(serial_fd, &payload_size);
        if (usb_version > 0) {
            sq->max_pending_blocks = payload_size / 64;
            if (sq->max_pending_blocks < 1) sq->max_pending_blocks = 1;
        }
    }
    strncpy(sq->name, name, sizeof(sq->name)); sq->name[sizeof(sq->name)-1] = '\0';
    int ret = pipe(sq->transmit_requests.pipe_fds); if (ret) goto fail;

    sq->pr = pollreactor_alloc(SQPF_NUM, SQPT_NUM, sq);
    pollreactor_add_fd(sq->pr, SQPF_SERIAL, serial_fd, input_event, serial_fd_type==SQT_DEBUGFILE);
    pollreactor_add_fd(sq->pr, SQPF_PIPE, sq->transmit_requests.pipe_fds[0], kick_event, 0);
    pollreactor_add_timer(sq->pr, SQPT_RETRANSMIT, retransmit_event);
    pollreactor_add_timer(sq->pr, SQPT_COMMAND, command_event);
    pollreactor_add_timer(sq->pr, SQPT_CAN_AUTONEG, (pollreactor_timer_cb)autoneg_event_wrapper);
    fd_set_non_blocking(serial_fd);
    fd_set_non_blocking(sq->transmit_requests.pipe_fds[0]);
    fd_set_non_blocking(sq->transmit_requests.pipe_fds[1]);

    if (serial_fd_type == SQT_CAN) {
        pollreactor_update_timer(sq->pr, SQPT_CAN_AUTONEG, PR_NOW);
    }

    sq->send_seq = 1;
    if (serial_fd_type == SQT_DEBUGFILE) { sq->receive_seq = -1; sq->rto = PR_NEVER; }
    else { sq->receive_seq = 1; sq->rto = MIN_RTO; }

    sq->transmit_requests.need_kick_clock = MAX_CLOCK;
    sq->transmit_requests.min_release_clock = MAX_CLOCK;
    list_init(&sq->transmit_requests.upcoming_queues);
    pthread_mutex_init(&sq->transmit_requests.lock, NULL);
    list_init(&sq->ready_queues); list_init(&sq->sent_queue);
    list_init(&sq->receiver.queue); list_init(&sq->notify_queue); list_init(&sq->fast_readers);

    list_init(&sq->old_sent); list_init(&sq->receiver.old_receive);
    debug_queue_alloc(&sq->old_sent, DEBUG_QUEUE_SENT);
    debug_queue_alloc(&sq->receiver.old_receive, DEBUG_QUEUE_RECEIVE);

    pthread_mutex_init(&sq->lock, NULL); pthread_mutex_init(&sq->receiver.lock, NULL);
    pthread_cond_init(&sq->receiver.cond, NULL); pthread_mutex_init(&sq->fast_reader_dispatch_lock, NULL);
    ret = pthread_create(&sq->tid, NULL, background_thread, sq); if (ret) goto fail;
    return sq;
fail:
    report_errno("init", ret); return NULL;
}

void __visible serialqueue_exit(struct serialqueue *sq) {
    pollreactor_do_exit(sq->pr);
    kick_bg_thread(sq);
    int ret = pthread_join(sq->tid, NULL);
    if (ret) report_errno("pthread_join", ret);
}

void __visible serialqueue_set_can_params(struct serialqueue *sq, int mode, int retries, int xl_sdt) {
    if (sq->serial_fd_type != SQT_CAN) return;
    pthread_mutex_lock(&sq->lock);
    if (sq->backend && sq->backend->set_can_params) {
        sq->backend->set_can_params(sq, mode, retries, xl_sdt);
    }
    pthread_mutex_unlock(&sq->lock);
}

void __visible serialqueue_set_usb_profile(struct serialqueue *sq, int max_pending_blocks) {
    pthread_mutex_lock(&sq->lock);
    if (max_pending_blocks > 0 && max_pending_blocks <= ABSOLUTE_MAX_PENDING_BLOCKS) sq->max_pending_blocks = max_pending_blocks;
    pthread_mutex_unlock(&sq->lock);
}

void __visible serialqueue_free(struct serialqueue *sq) {
    if (!sq) return;
    if (!pollreactor_is_exit(sq->pr)) serialqueue_exit(sq);
    pthread_mutex_lock(&sq->lock);
    message_queue_free(&sq->sent_queue);
    pthread_mutex_lock(&sq->receiver.lock);
    message_queue_free(&sq->receiver.queue);
    message_queue_free(&sq->receiver.old_receive);
    pthread_mutex_unlock(&sq->receiver.lock);
    message_queue_free(&sq->notify_queue); message_queue_free(&sq->old_sent);
    while (!list_empty(&sq->ready_queues)) {
        struct command_queue *cq = list_first_entry(&sq->ready_queues, struct command_queue, ready.node);
        list_del(&cq->ready.node); message_queue_free(&cq->ready.msg_queue);
    }
    pthread_mutex_lock(&sq->transmit_requests.lock);
    while (!list_empty(&sq->transmit_requests.upcoming_queues)) {
        struct command_queue *cq = list_first_entry(&sq->transmit_requests.upcoming_queues, struct command_queue, upcoming.node);
        list_del(&cq->upcoming.node); message_queue_free(&cq->upcoming.msg_queue);
    }
    pthread_mutex_unlock(&sq->transmit_requests.lock);
    pthread_mutex_unlock(&sq->lock);
    pollreactor_free(sq->pr); free(sq);
}

__visible struct command_queue * serialqueue_alloc_commandqueue(void) {
    struct command_queue *cq = malloc(sizeof(*cq));
    memset(cq, 0, sizeof(*cq));
    list_init(&cq->ready.msg_queue); list_init(&cq->upcoming.msg_queue);
    return cq;
}

void __visible serialqueue_free_commandqueue(struct command_queue *cq) {
    if (!cq) return;
    if (!list_empty(&cq->ready.msg_queue) || !list_empty(&cq->upcoming.msg_queue)) { errorf("Memory leak! Can't free non-empty commandqueue"); return; }
    free(cq);
}

__visible int serialqueue_commandqueue_is_empty(struct command_queue *cq) {
    if (!cq) return 1; // Consider null queue as empty
    return list_empty(&cq->ready.msg_queue) && list_empty(&cq->upcoming.msg_queue);
}

void serialqueue_add_fastreader(struct serialqueue *sq, struct fastreader *fr) {
    pthread_mutex_lock(&sq->lock); list_add_tail(&fr->node, &sq->fast_readers); pthread_mutex_unlock(&sq->lock);
}
void serialqueue_rm_fastreader(struct serialqueue *sq, struct fastreader *fr) {
    pthread_mutex_lock(&sq->lock); list_del(&fr->node); pthread_mutex_unlock(&sq->lock);
    pthread_mutex_lock(&sq->fast_reader_dispatch_lock); pthread_mutex_unlock(&sq->fast_reader_dispatch_lock);
}

void serialqueue_send_batch(struct serialqueue *sq, struct command_queue *cq, struct list_head *msgs) {
    int len = 0; struct queue_message *qm;
    list_for_each_entry(qm, msgs, node) {
        if (qm->min_clock + (3LL<<29) < qm->req_clock && qm->req_clock != BACKGROUND_PRIORITY_CLOCK) qm->min_clock = qm->req_clock - (3LL<<29);
        len += qm->len;
    }
    if (!len) return;
    qm = list_first_entry(msgs, struct queue_message, node);
    uint64_t min_clock = qm->min_clock;
    int mustwake = 0;
    pthread_mutex_lock(&sq->transmit_requests.lock);
    if (list_empty(&cq->upcoming.msg_queue)) {
        list_add_tail(&cq->upcoming.node, &sq->transmit_requests.upcoming_queues);
        if (min_clock < sq->transmit_requests.min_release_clock) sq->transmit_requests.min_release_clock = min_clock;
        if (min_clock < sq->transmit_requests.need_kick_clock) { sq->transmit_requests.need_kick_clock = 0; mustwake = 1; }
    }
    list_join_tail(msgs, &cq->upcoming.msg_queue);
    sq->transmit_requests.upcoming_bytes += len;
    pthread_mutex_unlock(&sq->transmit_requests.lock);
    if (mustwake) kick_bg_thread(sq);
}

void serialqueue_send_one(struct serialqueue *sq, struct command_queue *cq, struct queue_message *qm) {
    struct list_head msgs; list_init(&msgs); list_add_tail(&qm->node, &msgs);
    serialqueue_send_batch(sq, cq, &msgs);
}

void __visible serialqueue_send(struct serialqueue *sq, struct command_queue *cq, uint8_t *msg, int len, uint64_t min_clock, uint64_t req_clock, uint64_t notify_id) {
    struct queue_message *qm = message_fill(msg, len);
    qm->min_clock = min_clock; qm->req_clock = req_clock; qm->notify_id = notify_id;
    serialqueue_send_one(sq, cq, qm);
}

void __visible serialqueue_pull(struct serialqueue *sq, struct pull_queue_message *pqm) {
    struct receiver *r = &sq->receiver;
    pthread_mutex_lock(&r->lock);
    while (list_empty(&r->queue)) {
        if (pollreactor_is_exit(sq->pr)) goto exit;
        r->waiting = 1;
        int ret = pthread_cond_wait(&r->cond, &r->lock); if (ret) report_errno("pthread_cond_wait", ret);
    }
    struct queue_message *qm = list_first_entry(&r->queue, struct queue_message, node);
    list_del(&qm->node);
    memcpy(pqm->msg, qm->msg, qm->len); pqm->len = qm->len;
    pqm->sent_time = qm->sent_time; pqm->receive_time = qm->receive_time; pqm->notify_id = qm->notify_id;
    if (qm->len) qm = _debug_queue_add(&r->old_receive, qm);
    pthread_mutex_unlock(&r->lock); message_free(qm); return;
exit:
    pqm->len = -1; pthread_mutex_unlock(&r->lock);
}

void __visible serialqueue_set_wire_frequency(struct serialqueue *sq, double frequency) {
    pthread_mutex_lock(&sq->lock);
    sq->bittime_adjust = (sq->serial_fd_type == SQT_CAN) ? 1. / frequency : 10. / frequency;
    pthread_mutex_unlock(&sq->lock);
}
void __visible serialqueue_set_receive_window(struct serialqueue *sq, int receive_window) {
    pthread_mutex_lock(&sq->lock); sq->receive_window = receive_window; pthread_mutex_unlock(&sq->lock);
}
void __visible serialqueue_set_clock_est(struct serialqueue *sq, double est_freq, double conv_time, uint64_t conv_clock, uint64_t last_clock) {
    pthread_mutex_lock(&sq->lock); clock_fill(&sq->ce, est_freq, conv_time, conv_clock, last_clock); pthread_mutex_unlock(&sq->lock);
}
void serialqueue_get_clock_est(struct serialqueue *sq, struct clock_estimate *ce) {
    pthread_mutex_lock(&sq->lock); memcpy(ce, &sq->ce, sizeof(sq->ce)); pthread_mutex_unlock(&sq->lock);
}
void __visible serialqueue_get_stats(struct serialqueue *sq, char *buf, int len) {
    struct serialqueue stats;
    pthread_mutex_lock(&sq->lock); pthread_mutex_lock(&sq->transmit_requests.lock);
    memcpy(&stats, sq, sizeof(stats));
    pthread_mutex_unlock(&sq->transmit_requests.lock); pthread_mutex_unlock(&sq->lock);
    snprintf(buf, len, "bytes_write=%u bytes_read=%u bytes_retransmit=%u bytes_invalid=%u "
             "send_seq=%u receive_seq=%u retransmit_seq=%u srtt=%.3f rttvar=%.3f rto=%.3f ready_bytes=%u upcoming_bytes=%u",
             (unsigned int)stats.bytes_write, (unsigned int)stats.bytes_read, (unsigned int)stats.bytes_retransmit, (unsigned int)stats.bytes_invalid,
             (unsigned int)stats.send_seq, (unsigned int)stats.receive_seq, (unsigned int)stats.retransmit_seq,
             stats.srtt, stats.rttvar, stats.rto, (unsigned int)stats.ready_bytes, (unsigned int)stats.transmit_requests.upcoming_bytes);
}

int __visible serialqueue_extract_old(struct serialqueue *sq, int sentq, struct pull_queue_message *q, int max) {
    int count = sentq ? DEBUG_QUEUE_SENT : DEBUG_QUEUE_RECEIVE;
    struct list_head repl, curr; list_init(&repl); debug_queue_alloc(&repl, count); list_init(&curr);
    if (sentq) {
        pthread_mutex_lock(&sq->lock); list_join_tail(&sq->old_sent, &curr); list_init(&sq->old_sent); list_join_tail(&repl, &sq->old_sent); pthread_mutex_unlock(&sq->lock);
    } else {
        pthread_mutex_lock(&sq->receiver.lock); list_join_tail(&sq->receiver.old_receive, &curr); list_init(&sq->receiver.old_receive); list_join_tail(&repl, &sq->receiver.old_receive); pthread_mutex_unlock(&sq->receiver.lock);
    }
    int pos = 0;
    while (!list_empty(&curr)) {
        struct queue_message *qm = list_first_entry(&curr, struct queue_message, node);
        if (qm->len && pos < max) { memcpy(q[pos].msg, qm->msg, qm->len); q[pos].len = qm->len; q[pos].sent_time = qm->sent_time; q[pos].receive_time = qm->receive_time; pos++; }
        list_del(&qm->node); message_free(qm);
    }
    return pos;
}