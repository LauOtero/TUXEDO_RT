#ifndef STEPCOMPRESS_H
#define STEPCOMPRESS_H

#include <stdint.h> // uint32_t

#define ERROR_RET -989898989

struct pull_history_steps {
    uint64_t first_clock, last_clock;
    int64_t start_position;
    int step_count, interval, add;
};

struct list_head;
__attribute__((visibility("default"))) struct stepcompress *stepcompress_alloc(struct list_head *msg_queue);
__attribute__((visibility("default"))) void stepcompress_fill(struct stepcompress *sc, uint32_t oid, uint32_t max_error
                       , int32_t queue_step_msgtag
                       , int32_t set_next_step_dir_msgtag);
__attribute__((visibility("default"))) void stepcompress_set_invert_sdir(struct stepcompress *sc
                                  , uint32_t invert_sdir);
__attribute__((visibility("default"))) void stepcompress_history_expire(struct stepcompress *sc, uint64_t end_clock);
__attribute__((visibility("default"))) void stepcompress_free(struct stepcompress *sc);
__attribute__((visibility("default"))) uint32_t stepcompress_get_oid(struct stepcompress *sc);
__attribute__((visibility("default"))) int stepcompress_get_step_dir(struct stepcompress *sc);
__attribute__((visibility("default"))) void stepcompress_set_time(struct stepcompress *sc
                           , double time_offset, double mcu_freq);
__attribute__((visibility("default"))) int stepcompress_append(struct stepcompress *sc, int sdir
                        , double print_time, double step_time);
__attribute__((visibility("default"))) int stepcompress_commit(struct stepcompress *sc);
__attribute__((visibility("default"))) int stepcompress_flush(struct stepcompress *sc, uint64_t move_clock);
__attribute__((visibility("default"))) int stepcompress_reset(struct stepcompress *sc, uint64_t last_step_clock);
__attribute__((visibility("default"))) int stepcompress_set_last_position(struct stepcompress *sc, uint64_t clock
                                   , int64_t last_position);
__attribute__((visibility("default"))) int64_t stepcompress_find_past_position(struct stepcompress *sc
                                        , uint64_t clock);
__attribute__((visibility("default"))) int stepcompress_extract_old(struct stepcompress *sc
                             , struct pull_history_steps *p, int max
                             , uint64_t start_clock, uint64_t end_clock);

#endif // stepcompress.h
