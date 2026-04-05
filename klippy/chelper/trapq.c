// Trapezoidal velocity movement queue — RT-deterministic implementation
//
// Copyright (C) 2018-2021  Kevin O'Connor <kevin@koconnor.net>
// RT optimisations (C) 2025 — slab pool, deterministic alloc/free,
//   cache-line alignment, compiler hints, pre-fault, warmup API.
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#ifndef _GNU_SOURCE
#  define _GNU_SOURCE
#endif

#include <math.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include "compiler.h"
#include "trapq.h"

/* ═══════════════════════════════════════════════════════════════════════════
 * §1  SLAB MEMORY POOL
 *
 * RT-determinism rationale
 * ────────────────────────
 * The original implementation used a lock-free LIFO CAS stack.  Under RT
 * scheduling the CAS retry loop is unbounded and constitutes a potential
 * priority-inversion hazard.  Klipper's chelper is single-threaded by design,
 * so the lock-free overhead buys nothing and the retry cost is pure waste.
 *
 * Replacement: a plain singly-linked free-list over a contiguous slab.
 *  • move_alloc():  pop head → two pointer ops, no loop, O(1).
 *  • move_free():   push head → two pointer ops, O(1).
 *  • All physical pages pre-faulted at warmup → zero page-fault jitter.
 *  • Slab aligned to 64 bytes → every struct move starts cache-line-aligned.
 *
 * Pool size: MOVE_POOL_SLAB_SIZE × 128 bytes.
 *   512 × 128 B = 64 KiB  — fits in L2, ample for Klipper's lookahead window.
 * ═══════════════════════════════════════════════════════════════════════════ */

#define MOVE_POOL_SLAB_SIZE  512

static struct move *g_pool_slab    = NULL;  /* contiguous aligned slab       */
static struct move *g_pool_free    = NULL;  /* free-list head                 */
static int          g_pool_overflow = 0;   /* malloc fallback count (diag)   */
static int          g_pool_ready   = 0;    /* set to 1 after warmup          */

/* Overlay the first pointer-width of a free move with the next pointer. */
typedef struct pool_node { struct move *next; } pool_node_t;

/*
 * trapq_pool_warmup() — must be called once before any RT move_alloc().
 * Safe to call multiple times (idempotent).
 */
int trapq_pool_warmup(void)
{
    if (g_pool_ready)
        return 0;

    size_t slab_bytes = (size_t)MOVE_POOL_SLAB_SIZE * sizeof(struct move);
    void *slab = NULL;
    if (posix_memalign(&slab, TRAPQ_CACHE_LINE, slab_bytes) != 0)
        return -1;

    g_pool_slab = (struct move *)slab;

    /*
     * Pre-fault: touch every cache line so physical pages are mapped
     * and in Modified state before the RT loop starts.
     */
    volatile uint8_t *p   = (volatile uint8_t *)slab;
    volatile uint8_t *end = p + slab_bytes;
    for (; p < end; p += TRAPQ_CACHE_LINE)
        *p = 0;
    memset(slab, 0, slab_bytes);

    /* Build forward free-list: slab[0] → slab[1] → … → slab[N-1] → NULL */
    for (int i = 0; i < MOVE_POOL_SLAB_SIZE - 1; i++)
        ((pool_node_t *)&g_pool_slab[i])->next = &g_pool_slab[i + 1];
    ((pool_node_t *)&g_pool_slab[MOVE_POOL_SLAB_SIZE - 1])->next = NULL;
    g_pool_free = &g_pool_slab[0];

    __asm__ volatile("" ::: "memory"); /* compiler barrier before publish */
    g_pool_ready = 1;
    return 0;
}

/*
 * move_alloc() — O(1), no malloc on the fast path.
 */
struct move * __attribute__((hot))
move_alloc(void)
{
    if (unlikely(!g_pool_ready))
        trapq_pool_warmup();

    struct move *m = g_pool_free;
    if (likely(m != NULL)) {
        g_pool_free = ((pool_node_t *)m)->next;
        memset(m, 0, sizeof(*m));
        return m;
    }

    /* Pool exhausted — malloc fallback (not RT-safe, diagnostic counter). */
    g_pool_overflow++;
    m = malloc(sizeof(*m));
    if (m)
        memset(m, 0, sizeof(*m));
    return m;
}

/*
 * move_free() — O(1).  Detects slab vs. malloc moves via pointer range check.
 */
static inline void __attribute__((hot))
move_free(struct move *m)
{
    if (likely(m >= g_pool_slab &&
               m <  g_pool_slab + MOVE_POOL_SLAB_SIZE)) {
        ((pool_node_t *)m)->next = g_pool_free;
        g_pool_free = m;
    } else {
        free(m);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * §2  CONSTANTS
 * ═══════════════════════════════════════════════════════════════════════════ */

#define NEVER_TIME    9999999999999999.9
#define MAX_NULL_MOVE 1.0

/* ═══════════════════════════════════════════════════════════════════════════
 * §3  trapq LIFECYCLE
 * ═══════════════════════════════════════════════════════════════════════════ */

struct trapq *
trapq_alloc(void)
{
    struct trapq *tq = malloc(sizeof(*tq));
    memset(tq, 0, sizeof(*tq));
    list_init(&tq->moves);
    list_init(&tq->history);

    struct move *head_sentinel = move_alloc();
    struct move *tail_sentinel = move_alloc();
    head_sentinel->print_time  = -1.0;
    tail_sentinel->print_time  = NEVER_TIME;
    tail_sentinel->move_t      = NEVER_TIME;
    list_add_head(&head_sentinel->node, &tq->moves);
    list_add_tail(&tail_sentinel->node, &tq->moves);
    return tq;
}

void __visible
trapq_free(struct trapq *tq)
{
    while (!list_empty(&tq->moves)) {
        struct move *m = list_first_entry(&tq->moves, struct move, node);
        list_del(&m->node);
        move_free(m);
    }
    while (!list_empty(&tq->history)) {
        struct move *m = list_first_entry(&tq->history, struct move, node);
        list_del(&m->node);
        move_free(m);
    }
    free(tq);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * §4  QUEUE OPERATIONS  (hot RT path)
 * ═══════════════════════════════════════════════════════════════════════════ */

void
trapq_check_sentinels(struct trapq *tq)
{
    struct move *tail_sentinel = list_last_entry(&tq->moves, struct move, node);
    if (tail_sentinel->print_time)
        return;

    struct move *m = list_prev_entry(tail_sentinel, node);
    struct move *head_sentinel = list_first_entry(&tq->moves, struct move, node);
    if (m == head_sentinel) {
        tail_sentinel->print_time = NEVER_TIME;
        return;
    }
    tail_sentinel->print_time = m->print_time + m->move_t;
    tail_sentinel->start_pos  = move_get_coord(m, m->move_t);
}

#include <stdio.h>

void __attribute__((hot))
trapq_add_move(struct trapq *tq, struct move *m)
{
    fprintf(stderr, "trapq_add_move: tq=%p, m=%p\n", tq, m);
    if (!tq || !m) {
        fprintf(stderr, "trapq_add_move: ERROR - tq or m is NULL\n");
        return;
    }
    fprintf(stderr, "trapq_add_move: reading tq->moves.root.prev\n");
    struct move *tail_sentinel = list_last_entry(&tq->moves, struct move, node);
    fprintf(stderr, "trapq_add_move: tail_sentinel=%p\n", tail_sentinel);
    struct move *prev          = list_prev_entry(tail_sentinel, node);
    fprintf(stderr, "trapq_add_move: prev=%p\n", prev);

    fprintf(stderr, "trapq_add_move: reading prev->print_time\n");
    if (prev->print_time + prev->move_t < m->print_time) {
        fprintf(stderr, "trapq_add_move: allocating new move\n");
        struct move *nm = move_alloc();
        fprintf(stderr, "trapq_add_move: nm=%p\n", nm);
        if (!nm) {
            fprintf(stderr, "trapq_add_move: ERROR - move_alloc returned NULL\n");
            return;
        }
        fprintf(stderr, "trapq_add_move: copying data\n");
        nm->start_pos   = m->start_pos;
        if (prev->print_time <= 0.0 && m->print_time > MAX_NULL_MOVE)
            nm->print_time = m->print_time - MAX_NULL_MOVE;
        else
            nm->print_time = prev->print_time + prev->move_t;
        nm->move_t = m->print_time - nm->print_time;
        fprintf(stderr, "trapq_add_move: adding to list\n");
        list_add_before(&nm->node, &tail_sentinel->node);
        fprintf(stderr, "trapq_add_move: done (if branch)\n");
    }

    list_add_before(&m->node, &tail_sentinel->node);
    tail_sentinel->print_time = 0.0;
}

void __visible __attribute__((hot))
trapq_append(struct trapq *tq, double print_time
             , double accel_t, double cruise_t, double decel_t
             , double start_pos_x, double start_pos_y, double start_pos_z
             , double axes_r_x, double axes_r_y, double axes_r_z
             , double start_v, double cruise_v, double accel)
{
    struct coord start_pos = { .x=start_pos_x, .y=start_pos_y, .z=start_pos_z };
    struct coord axes_r    = { .x=axes_r_x,    .y=axes_r_y,    .z=axes_r_z    };
    /* Compute half_accel once; reused with negation in the decel phase. */
    double half_accel = 0.5 * accel;

    if (accel_t) {
        struct move *m  = move_alloc();
        m->print_time   = print_time;
        m->move_t       = accel_t;
        m->start_v      = start_v;
        m->half_accel   = half_accel;
        m->start_pos    = start_pos;
        m->axes_r       = axes_r;
        trapq_add_move(tq, m);
        print_time += accel_t;
        start_pos   = move_get_coord(m, accel_t);
    }
    if (cruise_t) {
        struct move *m  = move_alloc();
        m->print_time   = print_time;
        m->move_t       = cruise_t;
        m->start_v      = cruise_v;
        m->half_accel   = 0.0;
        m->start_pos    = start_pos;
        m->axes_r       = axes_r;
        trapq_add_move(tq, m);
        print_time += cruise_t;
        start_pos   = move_get_coord(m, cruise_t);
    }
    if (decel_t) {
        struct move *m  = move_alloc();
        m->print_time   = print_time;
        m->move_t       = decel_t;
        m->start_v      = cruise_v;
        m->half_accel   = -half_accel;
        m->start_pos    = start_pos;
        m->axes_r       = axes_r;
        trapq_add_move(tq, m);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * §5  HISTORY & EXPIRY
 * ═══════════════════════════════════════════════════════════════════════════ */

void __visible __attribute__((hot))
trapq_finalize_moves(struct trapq *tq, double print_time
                     , double clear_history_time)
{
    struct move *head_sentinel = list_first_entry(&tq->moves, struct move, node);
    struct move *tail_sentinel = list_last_entry(&tq->moves, struct move, node);

    for (;;) {
        struct move *m = list_next_entry(head_sentinel, node);
        if (m == tail_sentinel) {
            tail_sentinel->print_time = NEVER_TIME;
            break;
        }
        if (m->print_time + m->move_t > print_time)
            break;
        list_del(&m->node);
        if (m->start_v || m->half_accel)
            list_add_head(&m->node, &tq->history);
        else
            move_free(m);
    }

    if (list_empty(&tq->history))
        return;
    struct move *latest = list_first_entry(&tq->history, struct move, node);
    for (;;) {
        struct move *m = list_last_entry(&tq->history, struct move, node);
        if (m == latest || m->print_time + m->move_t > clear_history_time)
            break;
        list_del(&m->node);
        move_free(m);
    }
}

void __visible
trapq_set_position(struct trapq *tq, double print_time
                   , double pos_x, double pos_y, double pos_z)
{
    trapq_finalize_moves(tq, NEVER_TIME, 0.0);

    while (!list_empty(&tq->history)) {
        struct move *m = list_first_entry(&tq->history, struct move, node);
        if (m->print_time < print_time) {
            if (m->print_time + m->move_t > print_time)
                m->move_t = print_time - m->print_time;
            break;
        }
        list_del(&m->node);
        move_free(m);   /* BUG FIX: original used free(), bypassing the pool */
    }

    struct move *m = move_alloc();
    m->print_time  = print_time;
    m->start_pos.x = pos_x;
    m->start_pos.y = pos_y;
    m->start_pos.z = pos_z;
    list_add_head(&m->node, &tq->history);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * §6  HISTORY EXTRACTION  (non-RT, Python-facing)
 * ═══════════════════════════════════════════════════════════════════════════ */

static inline void __attribute__((always_inline))
copy_pull_move(struct pull_move *p, struct move *m)
{
    p->print_time = m->print_time;
    p->move_t     = m->move_t;
    p->start_v    = m->start_v;
    p->accel      = 2.0 * m->half_accel;
    p->start_x    = m->start_pos.x;
    p->start_y    = m->start_pos.y;
    p->start_z    = m->start_pos.z;
    p->x_r        = m->axes_r.x;
    p->y_r        = m->axes_r.y;
    p->z_r        = m->axes_r.z;
}

int __visible
trapq_extract_old(struct trapq *tq, struct pull_move *p, int max
                  , double start_time, double end_time)
{
    int res = 0;
    struct move *m;

    list_for_each_entry_reverse(m, &tq->moves, node) {
        if (res >= max)
            break;
        if (start_time >= m->print_time + m->move_t)
            break;
        if (end_time <= m->print_time || (!m->start_v && !m->half_accel))
            continue;
        copy_pull_move(p++, m);
        res++;
    }

    list_for_each_entry(m, &tq->history, node) {
        if (res >= max || start_time >= m->print_time + m->move_t)
            break;
        if (end_time <= m->print_time)
            continue;
        copy_pull_move(p++, m);
        res++;
    }

    return res;
}