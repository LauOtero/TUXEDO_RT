#include <stdio.h>
#include <stddef.h>

struct coord_3 {
    union {
        struct { double x, y, z; };
        double axis[3];
    };
};

struct coord_5 {
    union {
        struct { double x, y, z, a, b; };
        double axis[5];
    };
};

struct move_3 {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord_3 start_pos, axes_r;
    void *node;
    double padding[2];
};

struct move_5 {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord_5 start_pos, axes_r;
    void *node;
    double padding[2];
};

int main() {
    printf("coord_3 size: %zu\n", sizeof(struct coord_3));
    printf("coord_5 size: %zu\n", sizeof(struct coord_5));
    printf("move_3 size: %zu\n", sizeof(struct move_3));
    printf("move_5 size: %zu\n", sizeof(struct move_5));
    return 0;
}