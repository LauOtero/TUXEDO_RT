#ifndef CONN_MANAGER_H
#define CONN_MANAGER_H

#include <stdint.h>
#include <stddef.h>
#include "list.h"
#include "msgblock.h"

/* ─── Constants ───────────────────────────────────────────────────────── */
#define MAX_CLOCK               0x7fffffffffffffffLL
#define BACKGROUND_PRIORITY_CLOCK 0x7fffffff00000000LL
#define MAX_MCU_NAME_LEN        16

/* ─── Forward Declarations ────────────────────────────────────────────── */
struct conn_manager;
struct command_queue;
struct queue_message;
struct clock_estimate;

/* ─── Fast Reader Interface (zero-copy dispatch) ──────────────────────── */
struct fastreader;
typedef void (*fastreader_cb)(struct fastreader *fr, uint8_t *data, int len);

struct fastreader {
    struct list_node node;
    fastreader_cb func;
    int prefix_len;
    uint8_t prefix[MESSAGE_MAX];
};

/* ─── Pull Queue Message (Public API container) ───────────────────────── */
struct pull_queue_message {
    uint8_t msg[MESSAGE_MAX];
    int len;
    double sent_time;
    double receive_time;
    uint64_t notify_id;
};

/* ─── Public API ──────────────────────────────────────────────────────── */
#ifdef __cplusplus
extern "C" {
#endif

/* Lifecycle */
struct conn_manager *conn_alloc(int fd, int conn_type, int client_id, const char name[MAX_MCU_NAME_LEN]);
void conn_exit(struct conn_manager *cm);
void conn_free(struct conn_manager *cm);

/* Command Queue Management */
struct command_queue *conn_alloc_commandqueue(void);
void conn_free_commandqueue(struct command_queue *cq);
int conn_commandqueue_is_empty(struct command_queue *cq);

void conn_send(struct conn_manager *cm, struct command_queue *cq,
               uint8_t *msg, int len, uint64_t min_clock,
               uint64_t req_clock, uint64_t notify_id);
void conn_send_one(struct conn_manager *cm, struct command_queue *cq,
                   struct queue_message *qm);
void conn_send_batch(struct conn_manager *cm, struct command_queue *cq,
                     struct list_head *msgs);

/* Reception & Fast Dispatch */
void conn_pull(struct conn_manager *cm, struct pull_queue_message *pqm);
void conn_add_fastreader(struct conn_manager *cm, struct fastreader *fr);
void conn_rm_fastreader(struct conn_manager *cm, struct fastreader *fr);

/* Configuration & Tuning */
void conn_set_wire_frequency(struct conn_manager *cm, double frequency);
void conn_set_receive_window(struct conn_manager *cm, int receive_window);
void conn_set_clock_est(struct conn_manager *cm, double est_freq,
                        double conv_time, uint64_t conv_clock, uint64_t last_clock);
void conn_get_clock_est(struct conn_manager *cm, struct clock_estimate *ce);

/* Backend-specific Parameters */
void conn_set_backend_params(struct conn_manager *cm, const char *key, const void *value, size_t len);

/* CAN/EtherCAT/SPI Profile Overrides */
void conn_set_can_params(struct conn_manager *cm, int mode, int retries, int xl_sdt);
void conn_set_usb_profile(struct conn_manager *cm, int max_pending_blocks);
void conn_set_spi_params(struct conn_manager *cm, uint32_t speed, int mode, int crc, int dma);
void conn_set_ethertux_params(struct conn_manager *cm, uint16_t alias, uint16_t position,
                              uint32_t vendor_id, uint32_t product_id,
                              uint32_t cycle_time_ns);

/* RT Optimization Hooks */
int conn_pin_to_cpu(struct conn_manager *cm, int cpu_id);
int conn_set_fifo_priority(struct conn_manager *cm, int priority);
int conn_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count);

/* Diagnostics & Debug */
void conn_get_stats(struct conn_manager *cm, char *buf, int len);
int conn_extract_old(struct conn_manager *cm, int sentq,
                     struct pull_queue_message *q, int max);

/* RTT Statistics (exposed to Python for unified metrics) */
void conn_get_rtt_stats(struct conn_manager *cm, double *srtt, double *rttvar, double *rto);

#ifdef __cplusplus
}
#endif

#endif /* CONN_MANAGER_H */