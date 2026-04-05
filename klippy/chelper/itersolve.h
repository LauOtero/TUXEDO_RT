#ifndef ITERSOLVE_H
#define ITERSOLVE_H

#include <stdint.h>
#include "compiler.h" // __visible

enum {
    AF_X = 1 << 0, AF_Y = 1 << 1, AF_Z = 1 << 2,
    AF_A = 1 << 3, AF_B = 1 << 4,
};

struct stepper_kinematics;
struct move;
struct stepcompress;
struct trapq;

typedef double (*sk_calc_callback)(struct stepper_kinematics *sk, struct move *m, double move_time);
typedef void (*sk_post_callback)(struct stepper_kinematics *sk);

struct stepper_kinematics {
    double step_dist, commanded_pos;
    struct stepcompress *sc;
    double last_flush_time, last_move_time;
    struct trapq *tq;
    int active_flags;
    double gen_steps_pre_active, gen_steps_post_active;

    sk_calc_callback calc_position_cb;
    sk_post_callback post_cb;
};

__visible int32_t itersolve_generate_steps(struct stepper_kinematics *sk, struct stepcompress *sc, double flush_time);
__visible double itersolve_check_active(struct stepper_kinematics *sk, double flush_time);
__visible int32_t itersolve_is_active_axis(struct stepper_kinematics *sk, char axis);
__visible void itersolve_set_trapq(struct stepper_kinematics *sk, struct trapq *tq, double step_dist);
__visible struct trapq *itersolve_get_trapq(struct stepper_kinematics *sk);
__visible double itersolve_calc_position_from_coord(struct stepper_kinematics *sk, double x, double y, double z);
__visible void itersolve_set_position(struct stepper_kinematics *sk, double x, double y, double z);
__visible double itersolve_get_commanded_pos(struct stepper_kinematics *sk);
__visible double itersolve_get_gen_steps_pre_active(struct stepper_kinematics *sk);
__visible double itersolve_get_gen_steps_post_active(struct stepper_kinematics *sk);

#endif // ITERSOLVE_H