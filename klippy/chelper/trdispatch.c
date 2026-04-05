// Trigger sync "trsync" message dispatch
//
// Copyright (C) 2021  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // ARRAY_SIZE
#include "list.h" // list_add_tail
#include "pollreactor.h" // PR_NEVER
#include "pyhelper.h" // report_errno
#include "serialqueue.h" // serialqueue_add_fastreader

struct trdispatch {
    struct list_head tdm_list;

    rt_spinlock_t lock;
    uint32_t is_active, can_trigger, dispatch_reason;
    uint64_t sample_count, trigger_count;
    double last_latency_us, max_latency_us, total_latency_us;
};

struct trdispatch_rt_stats {
    uint64_t sample_count, trigger_count;
    double last_latency_us, max_latency_us, avg_latency_us;
};

static inline void
trdispatch_note_latency(struct trdispatch *td, double start_time)
{
    double latency_us = (get_monotonic() - start_time) * 1000000.;
    td->sample_count++;
    td->last_latency_us = latency_us;
    td->total_latency_us += latency_us;
    if (latency_us > td->max_latency_us)
        td->max_latency_us = latency_us;
}

struct trdispatch_mcu {
    struct fastreader fr;
    struct trdispatch *td;
    struct list_node node;
    struct serialqueue *sq;
    struct command_queue *cq;
    uint32_t trsync_oid, set_timeout_msgtag, trigger_msgtag;

    // Remaining fields protected by trdispatch lock
    uint64_t last_status_clock, expire_clock;
    uint64_t expire_ticks, min_extend_ticks;
    struct clock_estimate ce;
};

// Send: trsync_trigger oid=%c reason=%c
static void
send_trsync_trigger(struct trdispatch_mcu *tdm)
{
    uint32_t msg[3] = {
        tdm->trigger_msgtag, tdm->trsync_oid, tdm->td->dispatch_reason
    };
    struct queue_message *qm = message_alloc_and_encode(msg, ARRAY_SIZE(msg));
    serialqueue_send_one(tdm->sq, tdm->cq, qm);
}

// Send: trsync_set_timeout oid=%c clock=%u
static void
send_trsync_set_timeout(struct trdispatch_mcu *tdm)
{
    uint32_t msg[3] = {
        tdm->set_timeout_msgtag, tdm->trsync_oid, tdm->expire_clock
    };
    struct queue_message *qm = message_alloc_and_encode(msg, ARRAY_SIZE(msg));
    qm->req_clock = tdm->expire_clock;
    serialqueue_send_one(tdm->sq, tdm->cq, qm);
}

// Handle a trsync_state message (callback from serialqueue fastreader)
static void
handle_trsync_state(struct fastreader *fr, uint8_t *data, int len)
{
    struct trdispatch_mcu *tdm = container_of(fr, struct trdispatch_mcu, fr);
    double start_time = get_monotonic();

    // Parse: trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u
    uint32_t fields[5];
    int ret = msgblock_decode(fields, ARRAY_SIZE(fields), data, len);
    if (ret || fields[1] != tdm->trsync_oid)
        return;
    uint32_t can_trigger=fields[2], clock=fields[4];

    // Process message
    struct trdispatch *td = tdm->td;
    rt_spin_lock(&td->lock);
    if (!td->can_trigger)
        goto done;

    if (!can_trigger) {
        // mcu reports trigger or timeout - propagate to all mcus
        td->can_trigger = 0;
        td->trigger_count++;
        struct trdispatch_mcu *m;
        list_for_each_entry(m, &td->tdm_list, node) {
            send_trsync_trigger(m);
        }
        goto done;
    }

    // mcu is still working okay - update last_status_clock
    serialqueue_get_clock_est(tdm->sq, &tdm->ce);
    tdm->last_status_clock = clock_from_clock32(&tdm->ce, clock);

    // Determine minimum acknowledged time among all mcus
    double min_time = PR_NEVER, next_min_time = PR_NEVER;
    struct trdispatch_mcu *m, *min_tdm = NULL;
    list_for_each_entry(m, &td->tdm_list, node) {
        double status_time = clock_to_time(&m->ce, m->last_status_clock);
        if (status_time < next_min_time) {
            next_min_time = status_time;
            if (status_time < min_time) {
                next_min_time = min_time;
                min_time = status_time;
                min_tdm = m;
            }
        }
    }
    if (next_min_time == PR_NEVER)
        next_min_time = min_time;

    // Send trsync_set_timeout messages to other mcus (if needed)
    list_for_each_entry(m, &td->tdm_list, node) {
        double status_time = m == min_tdm ? next_min_time : min_time;
        uint64_t expire=clock_from_time(&m->ce, status_time) + m->expire_ticks;
        if (expire >= m->expire_clock
            && expire - m->expire_clock >= m->min_extend_ticks) {
            m->expire_clock = expire;
            send_trsync_set_timeout(m);
        }
    }

done:
    trdispatch_note_latency(td, start_time);
    rt_spin_unlock(&td->lock);
}

// Begin synchronization
void __visible
trdispatch_start(struct trdispatch *td, uint32_t dispatch_reason)
{
    rt_spin_lock(&td->lock);
    if (td->is_active || list_empty(&td->tdm_list)) {
        rt_spin_unlock(&td->lock);
        return;
    }
    td->dispatch_reason = dispatch_reason;
    td->is_active = td->can_trigger = 1;
    rt_spin_unlock(&td->lock);

    // Register handle_trsync_state message parser for each mcu
    struct trdispatch_mcu *tdm;
    list_for_each_entry(tdm, &td->tdm_list, node) {
        serialqueue_add_fastreader(tdm->sq, &tdm->fr);
    }
}

// Cleanup after a test completes
void __visible
trdispatch_stop(struct trdispatch *td)
{
    rt_spin_lock(&td->lock);
    if (!td->is_active) {
        rt_spin_unlock(&td->lock);
        return;
    }
    td->is_active = 0;
    rt_spin_unlock(&td->lock);

    // Unregister handle_trsync_state message parsers
    struct trdispatch_mcu *tdm;
    list_for_each_entry(tdm, &td->tdm_list, node) {
        serialqueue_rm_fastreader(tdm->sq, &tdm->fr);
    }
}

// Create a new 'struct trdispatch' object
struct trdispatch *
trdispatch_alloc(void)
{
    struct trdispatch *td = malloc(sizeof(*td));
    memset(td, 0, sizeof(*td));

    list_init(&td->tdm_list);
    rt_spin_init(&td->lock);
    return td;
}

// Create a new 'struct trdispatch_mcu' object
struct trdispatch_mcu *
trdispatch_mcu_alloc(struct trdispatch *td, struct serialqueue *sq
                     , struct command_queue *cq, uint32_t trsync_oid
                     , uint32_t set_timeout_msgtag, uint32_t trigger_msgtag
                     , uint32_t state_msgtag)
{
    struct trdispatch_mcu *tdm = malloc(sizeof(*tdm));
    memset(tdm, 0, sizeof(*tdm));

    tdm->sq = sq;
    tdm->cq = cq;
    tdm->trsync_oid = trsync_oid;
    tdm->set_timeout_msgtag = set_timeout_msgtag;
    tdm->trigger_msgtag = trigger_msgtag;

    // Setup fastreader to match trsync_state messages
    uint32_t state_prefix[] = {state_msgtag, trsync_oid};
    struct queue_message *dummy = message_alloc_and_encode(
        state_prefix, ARRAY_SIZE(state_prefix));
    memcpy(tdm->fr.prefix, dummy->msg, dummy->len);
    tdm->fr.prefix_len = dummy->len;
    free(dummy);
    tdm->fr.func = handle_trsync_state;

    tdm->td = td;
    rt_spin_lock(&td->lock);
    list_add_tail(&tdm->node, &td->tdm_list);
    rt_spin_unlock(&td->lock);

    return tdm;
}

// Setup for a trigger test
void __visible
trdispatch_mcu_setup(struct trdispatch_mcu *tdm
                     , uint64_t last_status_clock, uint64_t expire_clock
                     , uint64_t expire_ticks, uint64_t min_extend_ticks)
{
    struct trdispatch *td = tdm->td;
    rt_spin_lock(&td->lock);
    tdm->last_status_clock = last_status_clock;
    tdm->expire_clock = expire_clock;
    tdm->expire_ticks = expire_ticks;
    tdm->min_extend_ticks = min_extend_ticks;
    serialqueue_get_clock_est(tdm->sq, &tdm->ce);
    rt_spin_unlock(&td->lock);
}

void __visible
trdispatch_get_rt_stats(struct trdispatch *td, struct trdispatch_rt_stats *stats)
{
    rt_spin_lock(&td->lock);
    stats->sample_count = td->sample_count;
    stats->trigger_count = td->trigger_count;
    stats->last_latency_us = td->last_latency_us;
    stats->max_latency_us = td->max_latency_us;
    stats->avg_latency_us = td->sample_count
        ? td->total_latency_us / td->sample_count : 0.;
    rt_spin_unlock(&td->lock);
}
