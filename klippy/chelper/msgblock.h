#ifndef MSGBLOCK_H
#define MSGBLOCK_H

#include <stdint.h> // uint8_t
#include "list.h" // struct list_node

// Protocol Versions
#define MSG_PROTOCOL_V1 1
#define MSG_PROTOCOL_V2 2

#define MESSAGE_MIN 5
#define MESSAGE_MAX_V1 64
#define MESSAGE_MAX_V2 4096
#define MESSAGE_MAX MESSAGE_MAX_V2

#define MESSAGE_HEADER_SIZE_V1 2
#define MESSAGE_HEADER_SIZE_V2 4 // V2 uses 2 bytes for length

#define MESSAGE_TRAILER_SIZE 3

#define MESSAGE_POS_LEN 0
#define MESSAGE_POS_LEN_MSB_V2 1
#define MESSAGE_POS_SEQ_V1 1
#define MESSAGE_POS_SEQ_V2 2

#define MESSAGE_TRAILER_CRC  3
#define MESSAGE_TRAILER_SYNC 1

#define MESSAGE_PAYLOAD_MAX_V1 (MESSAGE_MAX_V1 - MESSAGE_MIN)
#define MESSAGE_PAYLOAD_MAX_V2 (MESSAGE_MAX_V2 - (MESSAGE_HEADER_SIZE_V2 + MESSAGE_TRAILER_SIZE))
#define MESSAGE_PAYLOAD_MAX MESSAGE_PAYLOAD_MAX_V2

#define MESSAGE_SEQ_MASK 0x0f
#define MESSAGE_DEST 0x10
#define MESSAGE_SYNC 0x7E

// V2 Sync Byte (used at start of message to identify V2)
#define MESSAGE_SYNC_V2 0x7F

struct queue_message {
    int len;
    uint8_t protocol_version; // V1 or V2
    uint8_t msg[MESSAGE_MAX];
    union {
        // Filled when on a command queue
        struct {
            uint64_t min_clock, req_clock;
        };
        // Filled when in sent/receive queues
        struct {
            double sent_time, receive_time;
        };
    };
    uint64_t notify_id;
    struct list_node node;
    
    // For lock-free pool
    struct queue_message *next_free;
};

struct clock_estimate {
    uint64_t last_clock, conv_clock;
    double conv_time, est_freq;
};

uint16_t msgblock_crc16_ccitt(uint8_t *buf, int len);
int msgblock_check(uint8_t *need_sync, uint8_t *buf, int buf_len);
uint8_t *msgblock_encode_int(uint8_t *p, uint32_t v);
uint32_t msgblock_parse_int(uint8_t **pp);
int msgblock_decode(uint32_t *data, int data_len, uint8_t *msg, int msg_len);
struct queue_message *message_alloc(void);
struct queue_message *message_fill(uint8_t *data, int len);
struct queue_message *message_alloc_and_encode(uint32_t *data, int len);
void message_free(struct queue_message *qm);
void message_queue_free(struct list_head *root);
uint64_t clock_from_clock32(struct clock_estimate *ce, uint32_t clock32);
double clock_to_time(struct clock_estimate *ce, uint64_t clock);
uint64_t clock_from_time(struct clock_estimate *ce, double time);
void clock_fill(struct clock_estimate *ce, double est_freq, double conv_time
                , uint64_t conv_clock, uint64_t last_clock);


void msgblock_pool_init(void);
#endif // msgblock.h
