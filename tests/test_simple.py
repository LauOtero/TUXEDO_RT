#!/usr/bin/env python3
import sys
sys.path.insert(0, '../klippy/chelper')
from cffi import FFI

ffi = FFI()

# Match the C definitions from __init__.py
ffi.cdef("""
struct stepper_kinematics {
    double step_dist, commanded_pos;
    void *sc;
    double last_flush_time, last_move_time;
    void *tq;
    int active_flags;
    double gen_steps_pre_active, gen_steps_post_active;
    double (*calc_position_cb)(void*, void*, double);
    void (*post_cb)(void*);
};

struct coord {
    union {
        struct { double x, y, z, a, b; };
        double axis[5];
    };
};

struct move {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord start_pos, axes_r;
    void *node;
    double padding[2];
};

void *five_axis_stepper_alloc(double pivot_offset_z, double tool_offset_x,
                               double tool_offset_y, double tool_offset_z,
                               double max_xy_accel, double max_z_accel);
double five_axis_calc_position_base(void *sk, struct move *m, double move_time);
void five_axis_calc_tcp_compensation(double a_rad, double b_rad,
                                     double pivot_offset,
                                     double *tcp_x, double *tcp_y, double *tcp_z);
""")

lib = ffi.dlopen('./c_helper.so')

print("Allocating stepper...")
sk = lib.five_axis_stepper_alloc(50.0, 0.0, 0.0, 0.0, 100.0, 100.0)
print(f"Stepper allocated: {sk}")

print("\nCreating struct move...")
m = ffi.new("struct move *")
m.print_time = 0.0
m.move_t = 1.0
m.start_v = 0.0
m.half_accel = 1000.0
m.start_pos.x = 0.0
m.start_pos.y = 0.0
m.start_pos.z = 0.0
m.start_pos.a = 0.0
m.start_pos.b = 0.0
m.axes_r.x = 1.0
m.axes_r.y = 1.0
m.axes_r.z = 0.0
m.axes_r.a = 0.0
m.axes_r.b = 0.0
print("struct move created successfully")

print("\nCalling five_axis_calc_position_base...")
result = lib.five_axis_calc_position_base(sk, m, 0.5)
print(f"Result: {result}")

print("\nTest PASSED!")
