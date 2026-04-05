#ifndef __CANSERIAL_H__
#define __CANSERIAL_H__

#include <stdint.h>
#include "core/generic/canbus.h"

/* Admin channel IDs — shared across all three CAN generations */
#define CANBUS_ID_ADMIN       0x3f0u
#define CANBUS_ID_ADMIN_RESP  0x3f1u

void     canserial_notify_tx(void);
struct   canbus_msg;
void     canserial_process_data(struct canbus_msg *msg);
uint32_t canserial_get_assigned_id(void);
void     canserial_set_uuid(uint8_t *raw_uuid, uint32_t raw_uuid_len);
uint8_t *canserial_get_write_ptr(uint32_t len);
void     canserial_commit_write(uint32_t len);
int      canserial_get_tx_pending(void);
int      canserial_get_tx_free(void);

/* Called by canbus.c after mode is locked by auto-negotiation */
void     canserial_set_mode(canbus_mode_t mode);

#endif /* __CANSERIAL_H__ */
