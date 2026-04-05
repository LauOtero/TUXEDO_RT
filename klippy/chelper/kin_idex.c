// Idex dual carriage kinematics
//
// Copyright (C) 2023  Dmitry Butyugin <dmbutyugin@google.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "trapq.h" // struct move

#define DUMMY_T 500.0

struct dual_carriage_stepper {
    struct stepper_kinematics sk;
    struct stepper_kinematics *orig_sk;
    struct move m;
    double x_scale, x_offs, y_scale, y_offs;
};

double
dual_carriage_calc_position(struct stepper_kinematics *sk, struct move *m
                            , double move_time)
{
    struct dual_carriage_stepper *dc = container_of(
            sk, struct dual_carriage_stepper, sk);
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double y = m->start_pos.y + m->axes_r.y * move_dist;
    double z = m->start_pos.z + m->axes_r.z * move_dist;
    dc->m.start_pos.x = x * dc->x_scale + dc->x_offs;
    dc->m.start_pos.y = y * dc->y_scale + dc->y_offs;
    dc->m.start_pos.z = z;
    return dc->orig_sk->calc_position_cb(dc->orig_sk, &dc->m, DUMMY_T);
}

void __visible
dual_carriage_set_sk(struct stepper_kinematics *sk
                     , struct stepper_kinematics *orig_sk)
{
    struct dual_carriage_stepper *dc = container_of(
            sk, struct dual_carriage_stepper, sk);
    dc->sk.calc_position_cb = dual_carriage_calc_position;
    dc->sk.active_flags = orig_sk->active_flags;
    dc->orig_sk = orig_sk;
}

int __visible
dual_carriage_set_transform(struct stepper_kinematics *sk, char axis
                            , double scale, double offs)
{
    struct dual_carriage_stepper *dc = container_of(
            sk, struct dual_carriage_stepper, sk);
    if (axis == 'x') {
        dc->x_scale = scale;
        dc->x_offs = offs;
        if (!scale)
            dc->sk.active_flags &= ~AF_X;
        else if (scale && dc->orig_sk->active_flags & AF_X)
            dc->sk.active_flags |= AF_X;
        return 0;
    }
    if (axis == 'y') {
        dc->y_scale = scale;
        dc->y_offs = offs;
        if (!scale)
            dc->sk.active_flags &= ~AF_Y;
        else if (scale && dc->orig_sk->active_flags & AF_Y)
            dc->sk.active_flags |= AF_Y;
        return 0;
    }
    return -1;
}

struct stepper_kinematics *
dual_carriage_alloc(void)
{
    struct dual_carriage_stepper *dc = malloc(sizeof(*dc));
    memset(dc, 0, sizeof(*dc));
    dc->m.move_t = 2. * DUMMY_T;
    dc->x_scale = dc->y_scale = 1.0;
    return &dc->sk;
}
