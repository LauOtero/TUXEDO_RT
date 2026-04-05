#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>

// Forward declarations to mock Klipper environment
struct coord { double x, y, z; };
struct move {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord start_pos;
    struct { double x, y, z; } axes_r;
    void *node_next, *node_prev; // mock list_node
};

struct stepper_kinematics;
typedef double (*calc_position_cb_t)(struct stepper_kinematics *sk, struct move *m, double move_time);

struct stepper_kinematics {
    calc_position_cb_t calc_position_cb;
    int active_flags;
    // other fields ignored for this mock
};

// Mock move_get_coord
static inline struct coord move_get_coord(struct move *m, double move_time) {
    double move_dist = m->start_v * move_time + m->half_accel * move_time * move_time;
    struct coord c;
    c.x = m->start_pos.x + m->axes_r.x * move_dist;
    c.y = m->start_pos.y + m->axes_r.y * move_dist;
    c.z = m->start_pos.z + m->axes_r.z * move_dist;
    return c;
}

// Mock container_of
#define container_of(ptr, type, member) \
    ((type *) ((char *)(ptr) - offsetof(type, member)))

#define __visible

// Include actual kinematic files
#include "../klippy/chelper/kin_cartesian.c"
#include "../klippy/chelper/kin_corexy.c"
#include "../klippy/chelper/kin_corexz.c"
#include "../klippy/chelper/kin_delta.c"
#include "../klippy/chelper/kin_deltesian.c"
//#include "../klippy/chelper/kin_extruder.c"
//#include "../klippy/chelper/kin_generic.c"
//#include "../klippy/chelper/kin_polar.c"
//#include "../klippy/chelper/kin_rotary_delta.c"
//#include "../klippy/chelper/kin_winch.c"

int main() {
    printf("Running C-only benchmark...\n");
    
    struct stepper_kinematics *sk_cart = cartesian_stepper_alloc('x');
    struct stepper_kinematics *sk_corexy = corexy_stepper_alloc('+');
    struct stepper_kinematics *sk_delta = delta_stepper_alloc(100.0, 0.0, 0.0);
    
    struct move m;
    memset(&m, 0, sizeof(m));
    m.start_pos.x = 10.0;
    m.start_pos.y = 20.0;
    m.start_pos.z = 30.0;
    m.axes_r.x = 1.0;
    m.axes_r.y = 1.0;
    m.axes_r.z = 0.0;
    m.start_v = 10.0;
    m.half_accel = 500.0;
    m.move_t = 1.0;
    
    int iters = 10000000; // 10 million!
    clock_t start, end;
    volatile double dummy = 0.0;
    
    start = clock();
    for (int i = 0; i < iters; i++) {
        dummy += sk_cart->calc_position_cb(sk_cart, &m, 0.5);
    }
    end = clock();
    printf("Cartesian: %f seconds for %d calls\n", (double)(end - start) / CLOCKS_PER_SEC, iters);

    start = clock();
    for (int i = 0; i < iters; i++) {
        dummy += sk_corexy->calc_position_cb(sk_corexy, &m, 0.5);
    }
    end = clock();
    printf("CoreXY: %f seconds for %d calls\n", (double)(end - start) / CLOCKS_PER_SEC, iters);

    start = clock();
    for (int i = 0; i < iters; i++) {
        dummy += sk_delta->calc_position_cb(sk_delta, &m, 0.5);
    }
    end = clock();
    printf("Delta: %f seconds for %d calls\n", (double)(end - start) / CLOCKS_PER_SEC, iters);

    return 0;
}