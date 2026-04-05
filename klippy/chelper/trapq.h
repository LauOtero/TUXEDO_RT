#ifndef TRAPQ_H
#define TRAPQ_H

/*
 * trapq.h — Trapezoidal velocity queue, RT-deterministic
 *
 * ── Memory model ─────────────────────────────────────────────────────────────
 *  struct move  : 128 bytes, 64-byte aligned (two cache lines).
 *                 All hot fields (print_time, move_t, start_v, half_accel,
 *                 start_pos) in the first cache line; axes_r + list_node
 *                 in the second.  Interpolation fast-path is one L1 hit.
 *
 *  struct coord : 40 bytes (5 × double), 32-byte aligned — a pair of
 *                 coords (start_pos + axes_r) spans 80 bytes and the
 *                 compiler can emit aligned AVX loads for both at once.
 *
 * ── Pool ─────────────────────────────────────────────────────────────────────
 *  The memory pool is allocated as a single contiguous, cache-aligned slab
 *  at startup via trapq_pool_warmup().  Hot-path alloc/free are O(1) with
 *  no malloc, no CAS loop, no unbounded retry in the real-time thread.
 *  The pool is NOT thread-safe by design — Klipper's chelper runs from a
 *  single RT thread.
 *
 * ── RT startup sequence ───────────────────────────────────────────────────────
 *  1. trapq_pool_warmup()        — allocate slab, build free-list, pre-fault
 *  2. crc_utils_warmup()         — CRC dispatch table
 *  3. crc_utils_lock_memory()    — mlockall
 *  4. crc_utils_set_rt_scheduler() — SCHED_FIFO
 *  Then call trapq_alloc() / trapq_append() freely — zero malloc in RT loop.
 */

#include "list.h"   /* list_node */
#include "compiler.h" // __visible
#include <stddef.h>
#include <stdint.h>

/* ── Cache-line constant ─────────────────────────────────────────────────── */
#ifndef TRAPQ_CACHE_LINE
#  define TRAPQ_CACHE_LINE 64
#endif

/* Compiler branch hints (match compiler.h style used elsewhere) */
#ifndef likely
#  define likely(x)   __builtin_expect(!!(x), 1)
#  define unlikely(x) __builtin_expect(!!(x), 0)
#endif

/* ── struct coord ────────────────────────────────────────────────────────── */
struct coord {
    union {
        struct { double x, y, z, a, b; };
        double axis[5];
    };
};

/* ── struct move ─────────────────────────────────────────────────────────── */
/*
 * Byte layout (LP64):
 *   0.. 7  print_time   ┐ cache line 0 — interpolation hot fields
 *   8..15  move_t       │
 *  16..23  start_v      │
 *  24..31  half_accel   │
 *  32..71  start_pos    ┘ (40 bytes, overflows 8 bytes into line 1)
 *  ────────────────────── 64-byte boundary
 *  64..103  axes_r      ┐ cache line 1 — direction + linkage
 * 104..119  node        │
 * 120..127  padding     ┘
 * Total: 128 bytes, 64-byte aligned → exactly 2 cache lines.
 */
struct move {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord start_pos;        /* 40 bytes */
    struct coord axes_r;           /* 40 bytes */
    struct list_node node;         /* 16 bytes */
    double padding[2];             /* 16 bytes  → total 128 B */
};

/* ── struct trapq ────────────────────────────────────────────────────────── */
struct trapq {
    struct list_head moves;
    struct list_head history;
};

/* ── struct pull_move (Python-facing) ────────────────────────────────────── */
struct pull_move {
    double print_time, move_t;
    double start_v, accel;
    double start_x, start_y, start_z;
    double x_r, y_r, z_r;
};

/* ═══════════════════════════════════════════════════════════════════════════
 * Pool API
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * trapq_pool_warmup() — must be called once at startup, before any RT thread
 * calls move_alloc().  Allocates a contiguous cache-aligned slab, builds the
 * internal free-list, and pre-faults every page into RSS.
 * Returns 0 on success, -1 on allocation failure.
 */
int  trapq_pool_warmup(void);

/** O(1) alloc from slab free-list.  Falls back to malloc only if slab exhausted. */
__visible struct move *move_alloc(void);

/* ═══════════════════════════════════════════════════════════════════════════
 * Inline hot-path helpers
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Scalar distance along move at move_time (Horner form). */
static inline double __attribute__((pure, always_inline))
move_get_distance(struct move *m, double move_time)
{
    return (m->start_v + m->half_accel * move_time) * move_time;
}

/* Full 5-axis coordinate at move_time. All axes computed unconditionally
 * so the compiler can auto-vectorise with FMA instructions. */
static inline struct coord __attribute__((pure, always_inline))
move_get_coord(struct move *m, double move_time)
{
    double d = (m->start_v + m->half_accel * move_time) * move_time;
    struct coord c;
    c.x = m->start_pos.x + m->axes_r.x * d;
    c.y = m->start_pos.y + m->axes_r.y * d;
    c.z = m->start_pos.z + m->axes_r.z * d;
    c.a = m->start_pos.a + m->axes_r.a * d;
    c.b = m->start_pos.b + m->axes_r.b * d;
    return c;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API
 * ═══════════════════════════════════════════════════════════════════════════ */

__visible struct trapq *trapq_alloc(void);
__visible void trapq_free(struct trapq *tq);
__visible void trapq_check_sentinels(struct trapq *tq);
__visible void trapq_add_move(struct trapq *tq, struct move *m);
__visible void trapq_append(struct trapq *tq, double print_time
                  , double accel_t, double cruise_t, double decel_t
                  , double start_pos_x, double start_pos_y, double start_pos_z
                  , double axes_r_x, double axes_r_y, double axes_r_z
                  , double start_v, double cruise_v, double accel);
__visible void trapq_finalize_moves(struct trapq *tq, double print_time
                          , double clear_history_time);
__visible void trapq_set_position(struct trapq *tq, double print_time
                        , double pos_x, double pos_y, double pos_z);
__visible int  trapq_extract_old(struct trapq *tq, struct pull_move *p, int max
                       , double start_time, double end_time);

#endif /* TRAPQ_H */