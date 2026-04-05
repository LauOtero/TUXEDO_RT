// Delta kinematics stepper pulse time generation
//
// Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h> // sqrt
#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "trapq.h" // move_get_coord

struct delta_stepper {
    struct stepper_kinematics sk;
    double arm2, tower_x, tower_y;
};

static double
delta_stepper_calc_position(struct stepper_kinematics *sk, struct move *m
                            , double move_time)
{
    struct delta_stepper *ds = container_of(sk, struct delta_stepper, sk);
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double y = m->start_pos.y + m->axes_r.y * move_dist;
    double z = m->start_pos.z + m->axes_r.z * move_dist;
    double dx = ds->tower_x - x, dy = ds->tower_y - y;
    return sqrt(ds->arm2 - dx*dx - dy*dy) + z;
}

struct stepper_kinematics *
delta_stepper_alloc(double arm2, double tower_x, double tower_y)
{
    struct delta_stepper *ds = malloc(sizeof(*ds));
    memset(ds, 0, sizeof(*ds));
    ds->arm2 = arm2;
    ds->tower_x = tower_x;
    ds->tower_y = tower_y;
    ds->sk.calc_position_cb = delta_stepper_calc_position;
    ds->sk.active_flags = AF_X | AF_Y | AF_Z;
    return &ds->sk;
}
