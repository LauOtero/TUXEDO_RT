// Iterative solver for kinematic moves
//
// Copyright (C) 2018-2020  Kevin O'Connor <kevin@koconnor.net>
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h> // fabs
#include <stddef.h> // offsetof
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // itersolve_generate_steps
#include "pyhelper.h" // errorf
#include "stepcompress.h" // queue_append_start
#include "trapq.h" // struct move


/****************************************************************
 * Main iterative solver
 ****************************************************************/

struct timepos {
    double time, position;
};

#define SEEK_TIME_RESET 0.000100
#define STEP_EPSILON 1.0e-9

// Generate step times for a portion of a move
static int32_t
itersolve_gen_steps_range(struct stepper_kinematics *__restrict sk,
                          struct stepcompress *__restrict sc,
                          struct move *__restrict m,
                          double abs_start, double abs_end)
{
    sk_calc_callback calc_position_cb = sk->calc_position_cb;
    const double half_step = 0.5 * sk->step_dist;
    double start = abs_start - m->print_time;
    double end = abs_end - m->print_time;

    if (start < 0.0) start = 0.0;
    if (end > m->move_t) end = m->move_t;

    struct timepos old_guess = {start, sk->commanded_pos}, guess = old_guess;
    int sdir = stepcompress_get_step_dir(sc);
    int is_dir_change = 0, have_bracket = 0, check_oscillate = 0;
    double target = sk->commanded_pos + (sdir ? half_step : -half_step);
    double last_time = start, low_time = start;
    double high_time = start + SEEK_TIME_RESET;
    if (high_time > end) high_time = end;

    for (;;) {
        // Secant method
        double guess_dist = guess.position - target;
        double og_dist = old_guess.position - target;
        double denom = guess_dist - og_dist;
        double next_time = (denom != 0.0)
            ? ((old_guess.time * guess_dist - guess.time * og_dist) / denom)
            : (low_time + high_time) * 0.5;

        if (!(next_time > low_time && next_time < high_time)) {
            if (have_bracket) {
                next_time = (low_time + high_time) * 0.5;
                check_oscillate = 0;
            } else if (guess.time >= end) {
                break;
            } else {
                next_time = high_time;
                high_time = 2.0 * high_time - last_time;
                if (high_time > end) high_time = end;
            }
        }

        old_guess = guess;
        guess.time = next_time;
        guess.position = calc_position_cb(sk, m, next_time);
        guess_dist = guess.position - target;

        // Branchless absolute value for performance
        double abs_guess_dist = guess_dist < 0.0 ? -guess_dist : guess_dist;
        if (abs_guess_dist > STEP_EPSILON) {
            double rel_dist = sdir ? guess_dist : -guess_dist;
            if (rel_dist > 0.0) {
                if (have_bracket && old_guess.time <= low_time) {
                    if (check_oscillate) old_guess = guess;
                    check_oscillate = 1;
                }
                high_time = guess.time;
                have_bracket = 1;
            } else if (rel_dist < -(half_step + half_step + 1.0e-10)) {
                sdir = !sdir;
                target = sdir ? target + half_step + half_step : target - half_step - half_step;
                low_time = last_time;
                high_time = guess.time;
                is_dir_change = have_bracket = 1;
                check_oscillate = 0;
            } else {
                low_time = guess.time;
            }

            if (!have_bracket || high_time - low_time > STEP_EPSILON) {
                if (!is_dir_change && rel_dist >= -half_step)
                    stepcompress_commit(sc);
                continue;
            }
        }

        // Step found
        int ret = stepcompress_append(sc, sdir, m->print_time, guess.time);
        if (__builtin_expect(ret != 0, 0)) return ret;

        target = sdir ? target + half_step + half_step : target - half_step - half_step;

        double seek_time_delta = 1.5 * (guess.time - last_time);
        if (seek_time_delta < STEP_EPSILON) seek_time_delta = STEP_EPSILON;
        if (is_dir_change && seek_time_delta > SEEK_TIME_RESET)
            seek_time_delta = SEEK_TIME_RESET;

        last_time = low_time = guess.time;
        high_time = guess.time + seek_time_delta;
        if (high_time > end) high_time = end;
        is_dir_change = have_bracket = check_oscillate = 0;
    }

    sk->commanded_pos = target - (sdir ? half_step : -half_step);
    if (sk->post_cb) sk->post_cb(sk);
    return 0;
}


/****************************************************************
Interface functions
****************************************************************/
static inline int
check_active(struct stepper_kinematics *__restrict sk, const struct move *__restrict m)
{
    int af = sk->active_flags;
    return ((af & AF_X && m->axes_r.x != 0.0) ||
            (af & AF_Y && m->axes_r.y != 0.0) ||
            (af & AF_Z && m->axes_r.z != 0.0));
}

int32_t __visible
itersolve_generate_steps(struct stepper_kinematics *__restrict sk,
                         struct stepcompress *__restrict sc,
                         double flush_time)
{
    double last_flush_time = sk->last_flush_time;
    sk->last_flush_time = flush_time;
    if (!sk->tq) return 0;

    struct move *m = list_first_entry(&sk->tq->moves, struct move, node);
    while (last_flush_time >= m->print_time + m->move_t)
        m = list_next_entry(m, node);

    double force_steps_time = sk->last_move_time + sk->gen_steps_post_active;
    int skip_count = 0;

    for (;;) {
        double move_start = m->print_time;
        double move_end = move_start + m->move_t;

        if (check_active(sk, m)) {
            if (skip_count && sk->gen_steps_pre_active) {
                double abs_start = move_start - sk->gen_steps_pre_active;
                if (abs_start < last_flush_time) abs_start = last_flush_time;
                if (abs_start < force_steps_time) abs_start = force_steps_time;

                struct move *pm = list_prev_entry(m, node);
                while (--skip_count && pm->print_time > abs_start)
                    pm = list_prev_entry(pm, node);

                do {
                    int32_t ret = itersolve_gen_steps_range(sk, sc, pm, abs_start, flush_time);
                    if (__builtin_expect(ret != 0, 0)) return ret;
                    pm = list_next_entry(pm, node);
                } while (pm != m);
            }

            int32_t ret = itersolve_gen_steps_range(sk, sc, m, last_flush_time, flush_time);
            if (__builtin_expect(ret != 0, 0)) return ret;

            if (move_end >= flush_time) {
                sk->last_move_time = flush_time;
                return 0;
            }
            skip_count = 0;
            sk->last_move_time = move_end;
            force_steps_time = sk->last_move_time + sk->gen_steps_post_active;
        } else {
            if (move_start < force_steps_time) {
                double abs_end = force_steps_time;
                if (abs_end > flush_time) abs_end = flush_time;
                int32_t ret = itersolve_gen_steps_range(sk, sc, m, last_flush_time, abs_end);
                if (__builtin_expect(ret != 0, 0)) return ret;
                skip_count = 1;
            } else {
                skip_count++;
            }
            if (flush_time + sk->gen_steps_pre_active <= move_end) return 0;
        }
        m = list_next_entry(m, node);
    }
}

double __visible
itersolve_check_active(struct stepper_kinematics *sk, double flush_time)
{
    if (!sk->tq) return 0.0;
    trapq_check_sentinels(sk->tq);
    struct move *m = list_first_entry(&sk->tq->moves, struct move, node);
    while (sk->last_flush_time >= m->print_time + m->move_t)
        m = list_next_entry(m, node);
    for (;;) {
        if (check_active(sk, m)) return m->print_time;
        if (flush_time <= m->print_time + m->move_t) return 0.0;
        m = list_next_entry(m, node);
    }
}

int32_t __visible
itersolve_is_active_axis(struct stepper_kinematics *sk, char axis)
{
    if (axis < 'x' || axis > 'z') return 0;
    return (sk->active_flags & (AF_X << (axis - 'x'))) != 0;
}

void __visible
itersolve_set_trapq(struct stepper_kinematics *sk, struct trapq *tq, double step_dist)
{
    sk->tq = tq;
    sk->step_dist = step_dist;
}

struct trapq *
itersolve_get_trapq(struct stepper_kinematics *sk)
{
    return sk->tq;
}

double __visible
itersolve_calc_position_from_coord(struct stepper_kinematics *sk, double x, double y, double z)
{
    struct move m;
    memset(&m, 0, sizeof(m));
    m.start_pos.x = x;
    m.start_pos.y = y;
    m.start_pos.z = z;
    m.move_t = 1000.0;
    return sk->calc_position_cb(sk, &m, 500.0);
}

void __visible
itersolve_set_position(struct stepper_kinematics *sk, double x, double y, double z)
{
    sk->commanded_pos = itersolve_calc_position_from_coord(sk, x, y, z);
}

double __visible
itersolve_get_commanded_pos(struct stepper_kinematics *sk)
{
    return sk->commanded_pos;
}

double __visible
itersolve_get_gen_steps_pre_active(struct stepper_kinematics *sk)
{
    return sk->gen_steps_pre_active;
}

double __visible
itersolve_get_gen_steps_post_active(struct stepper_kinematics *sk)
{
    return sk->gen_steps_post_active;
}
