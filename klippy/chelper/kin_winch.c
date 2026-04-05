// Cable winch stepper kinematics
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

struct winch_stepper {
    struct stepper_kinematics sk;
    struct coord anchor;
};

static double
winch_stepper_calc_position(struct stepper_kinematics *sk, struct move *m
                            , double move_time)
{
    struct winch_stepper *hs = container_of(sk, struct winch_stepper, sk);
    double move_dist = (m->start_v + m->half_accel * move_time) * move_time;
    double x = m->start_pos.x + m->axes_r.x * move_dist;
    double y = m->start_pos.y + m->axes_r.y * move_dist;
    double z = m->start_pos.z + m->axes_r.z * move_dist;
    double dx = hs->anchor.x - x, dy = hs->anchor.y - y, dz = hs->anchor.z - z;
    return sqrt(dx*dx + dy*dy + dz*dz);
}

struct stepper_kinematics *
winch_stepper_alloc(double anchor_x, double anchor_y, double anchor_z)
{
    struct winch_stepper *hs = malloc(sizeof(*hs));
    memset(hs, 0, sizeof(*hs));
    hs->anchor.x = anchor_x;
    hs->anchor.y = anchor_y;
    hs->anchor.z = anchor_z;
    hs->sk.calc_position_cb = winch_stepper_calc_position;
    hs->sk.active_flags = AF_X | AF_Y | AF_Z;
    return &hs->sk;
}
