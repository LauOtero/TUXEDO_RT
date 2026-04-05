// CoreXY kinematics stepper pulse time generation
//
// Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "trapq.h" // move_get_coord

static double
corexy_stepper_plus_calc_position(struct stepper_kinematics *sk, struct move *m
                                  , double move_time)
{
    (void)sk;
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double y = m->start_pos.y + m->axes_r.y * move_dist;
    return x + y;
}

static double
corexy_stepper_minus_calc_position(struct stepper_kinematics *sk, struct move *m
                                   , double move_time)
{
    (void)sk;
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double y = m->start_pos.y + m->axes_r.y * move_dist;
    return x - y;
}

__visible struct stepper_kinematics *
corexy_stepper_alloc(char type)
{
    struct stepper_kinematics *sk = malloc(sizeof(*sk));
    memset(sk, 0, sizeof(*sk));
    if (type == '+')
        sk->calc_position_cb = corexy_stepper_plus_calc_position;
    else if (type == '-')
        sk->calc_position_cb = corexy_stepper_minus_calc_position;
    sk->active_flags = AF_X | AF_Y;
    return sk;
}
