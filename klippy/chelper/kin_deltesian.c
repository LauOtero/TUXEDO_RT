// Deltesian kinematics stepper pulse time generation
//
// Copyright (C) 2022  Fabrice Gallet <tircown@gmail.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h> // sqrt
#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "trapq.h" // move_get_coord

struct deltesian_stepper {
    struct stepper_kinematics sk;
    double arm2, arm_x;
};

static double
deltesian_stepper_calc_position(struct stepper_kinematics *sk, struct move *m
                            , double move_time)
{
    struct deltesian_stepper *ds = container_of(
                sk, struct deltesian_stepper, sk);
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double z = m->start_pos.z + m->axes_r.z * move_dist;
    double dx = x - ds->arm_x;
    return sqrt(ds->arm2 - dx*dx) + z;
}

struct stepper_kinematics *
deltesian_stepper_alloc(double arm2, double arm_x)
{
    struct deltesian_stepper *ds = malloc(sizeof(*ds));
    memset(ds, 0, sizeof(*ds));
    ds->arm2 = arm2;
    ds->arm_x = arm_x;
    ds->sk.calc_position_cb = deltesian_stepper_calc_position;
    ds->sk.active_flags = AF_X | AF_Z;
    return &ds->sk;
}
