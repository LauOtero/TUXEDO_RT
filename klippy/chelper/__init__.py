#!/usr/bin/env python3
# Klipper C Helper Build System — Ultra-High-Performance, RT-Deterministic
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# Updated for modular conn_manager architecture (Serial, CAN, EtherCAT/IGH)
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import logging
import sys
import platform
import subprocess
import re
import cffi

######################################################################
# C Helper Shared Library Compilation
######################################################################
# Configurable compiler (respects CC env var) and chelper-specific overrides
GCC_CMD = os.environ.get("CHELPER_CC") or os.environ.get("CC") or "gcc"

# Optimization level (default: aggressive for local builds)
_OPT = os.environ.get("CHELPER_OPT", "-O3")

# Base safe flags for RT: avoids "fast-math", preserves IEEE 754, enables vectorization
BASE_GCC_FLAGS = [
    "-Wall", "-g", _OPT, "-shared", "-fPIC", "-pthread", "-D_GNU_SOURCE",
    "-fno-use-linker-plugin", "-fno-math-errno", "-fno-trapping-math",
    "-fvisibility=hidden", "-ftree-vectorize"
]

# Optional generic flags (added only if compiler supports them)
OPTIONAL_GCC_FLAGS = [
    "-pipe",                        # Enable pipelining
    "-fomit-frame-pointer",         # Avoid frame pointer (lower latency)
    "-falign-functions=64",         # Align functions to full cache line
    "-falign-loops=32",             # Align loops to full cache line
    "-fno-plt",                     # Avoid PLT stubs — direct GOT calls (lower latency)
    "-fno-semantic-interposition",  # Enable inlining across TUs (important with -fPIC)
    "-ffunction-sections",          # Enable --gc-sections in linker
    "-fdata-sections",              # Enable --gc-sections for unused data
    "-Wl,--gc-sections",            # Remove dead sections from final .so
    "-Wl,-z,now",                   # Resolve all PLT symbols at load time (no lazy binding)
    "-Wl,-z,relro",                 # Mark GOT read-only after relocation
    "-funroll-loops",               # Unroll loops for deterministic branch behavior
]

######################################################################
# Hardware Acceleration Flags by Architecture (Tiered Profiles)
######################################################################
# x86_64: Maximum profile with AVX-512+VPCLMULQDQ for 512-bit CRC folding
X86_AVX512_FLAGS   = ["-msse4.2", "-mpclmul", "-mpopcnt", "-mavx512f", "-mavx512dq", "-mavx512vl", "-mvpclmulqdq"]
X86_MODERN_FLAGS   = ["-msse4.2", "-mpclmul", "-mpopcnt", "-mavx2", "-mfma"]
X86_COMPAT_FLAGS   = ["-msse4.2", "-mpclmul", "-mpopcnt"]
X86_FALLBACK_FLAGS = ["-msse2", "-mfpmath=sse"]

# ARM64 (AArch64): Maximum profile with CRC32+PMULL+EOR3 (SHA3)
AARCH64_EOR3_FLAGS   = ["-march=armv8.2-a+simd+crc+crypto+sha3"]
AARCH64_MODERN_FLAGS = ["-march=armv8.4-a+simd+crc+crypto"]
AARCH64_COMPAT_FLAGS = ["-march=armv8-a+simd+crc+crypto"]
AARCH64_FALLBACK_FLAGS = ["-march=armv8-a+simd"]

# ARM32 (ARMv7): NEON + VFPv4
ARMV7_MODERN_FLAGS   = ["-march=armv7-a+neon+vfpv4", "-mfpu=neon-vfpv4"]
ARMV7_FALLBACK_FLAGS = ["-mfpu=neon", "-mfloat-abi=hard"]

# RISC-V: Zba/Zbb/Zbc extensions (bit manipulation + hardware CRC32)
RISCV64_MODERN_FLAGS = ["-march=rv64gc_zba_zbb_zbc"]
RISCV32_MODERN_FLAGS = ["-march=rv32gc_zba_zbb_zbc"]
RISCV64_BASE_FLAGS   = ["-march=rv64gc"]
RISCV32_BASE_FLAGS   = ["-march=rv32gc"]

######################################################################
# Source Files (Modular conn_manager architecture)
######################################################################
SOURCE_FILES = [
    'pyhelper.c', 'ultracrc.c', 'ultracrc_tables.c', 
    'zckb_shm.c',  # Zero-Copy Kernel-Bypass Shared Memory
    'conn_manager.c', 'conn_serial.c', 'conn_can.c', 'conn_ethertux.c', 
    'conn_rs485.c', 'conn_spi.c', 'conn_debugpipe.c',
    'stepcompress.c', 'steppersync.c', 'itersolve.c',
    'trapq.c', 'pollreactor.c', 'msgblock.c', 'trdispatch.c',
    'kin_cartesian.c', 'kin_corexy.c', 'kin_corexz.c', 'kin_delta.c',
    'kin_deltesian.c', 'kin_polar.c', 'kin_rotary_delta.c', 'kin_winch.c',
    'kin_extruder.c', 'kin_shaper.c', 'kin_idex.c', 'kin_generic.c',
    'kin_ratos_hybrid_corexy.c', 'kin_5axis.c', 'gcode_parser.c',
]

DEST_LIB = "c_helper.so"

OTHER_FILES = [
    'list.h', 'conn_manager.h', 'conn_backend.h', 'conn_internal.h',  # ← New Headers
    'zckb_shm.h',  # Zero-Copy Kernel-Bypass header
    'stepcompress.h', 'steppersync.h', 'itersolve.h',
    'pyhelper.h', 'trapq.h', 'pollreactor.h', 'msgblock.h',
    'compiler.h', 'ultracrc.h', 'ultracrc_tables.h', 'gcode_parser.h',
]

######################################################################
# CFFI Definitions (Updated for conn_manager API)
######################################################################
defs_stepcompress = """
struct pull_history_steps {
    uint64_t first_clock, last_clock;
    int64_t start_position;
    int step_count, interval, add;
};
struct stepcompress *stepcompress_alloc(struct list_head *msg_queue);
void stepcompress_free(struct stepcompress *sc);
void stepcompress_fill(struct stepcompress *sc, uint32_t oid,
    uint32_t max_error, int32_t queue_step_msgtag,
    int32_t set_next_step_dir_msgtag);
void stepcompress_set_invert_sdir(struct stepcompress *sc,
    uint32_t invert_sdir);
int stepcompress_reset(struct stepcompress *sc, uint64_t last_step_clock);
int stepcompress_set_last_position(struct stepcompress *sc,
    uint64_t clock, int64_t last_position);
int64_t stepcompress_find_past_position(struct stepcompress *sc,
    uint64_t clock);
int stepcompress_extract_old(struct stepcompress *sc,
    struct pull_history_steps *p, int max,
    uint64_t start_clock, uint64_t end_clock);
"""

defs_steppersync = """
struct stepcompress *syncemitter_get_stepcompress(struct syncemitter *se);
void syncemitter_set_stepper_kinematics(struct syncemitter *se,
    struct stepper_kinematics *sk);
struct stepper_kinematics *syncemitter_get_stepper_kinematics(
    struct syncemitter *se);
void syncemitter_queue_msg(struct syncemitter *se, uint64_t req_clock,
    uint32_t *data, int len);
struct syncemitter *steppersync_alloc_syncemitter(struct steppersync *ss,
    char name[16], int alloc_stepcompress);
void steppersync_setup_movequeue(struct steppersync *ss,
    struct conn_manager *cm, int move_num);
void steppersync_set_time(struct steppersync *ss,
    double time_offset, double mcu_freq);
struct steppersyncmgr *steppersyncmgr_alloc(void);
void steppersyncmgr_free(struct steppersyncmgr *ssm);
struct steppersync *steppersyncmgr_alloc_steppersync(
    struct steppersyncmgr *ssm);
int32_t steppersyncmgr_gen_steps(struct steppersyncmgr *ssm,
    double flush_time, double gen_steps_time, double clear_history_time);
"""

defs_itersolve = """
enum {
    AF_X = 1<<0, AF_Y = 1<<1, AF_Z = 1<<2,
    AF_A = 1<<3, AF_B = 1<<4,
};
struct stepper_kinematics;
struct move;
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
double itersolve_check_active(struct stepper_kinematics *sk,
    double flush_time);
int32_t itersolve_is_active_axis(struct stepper_kinematics *sk, char axis);
void itersolve_set_trapq(struct stepper_kinematics *sk, struct trapq *tq,
    double step_dist);
struct trapq *itersolve_get_trapq(struct stepper_kinematics *sk);
double itersolve_calc_position_from_coord(struct stepper_kinematics *sk,
    double x, double y, double z);
void itersolve_set_position(struct stepper_kinematics *sk,
    double x, double y, double z);
double itersolve_get_commanded_pos(struct stepper_kinematics *sk);
double itersolve_get_gen_steps_pre_active(struct stepper_kinematics *sk);
double itersolve_get_gen_steps_post_active(struct stepper_kinematics *sk);
"""

defs_list = """
struct list_node {
    struct list_node *next, *prev;
};
struct list_head {
    struct list_node root;
};
"""

defs_trapq = """
struct coord {
    double x, y, z, a, b;
};
struct move {
    double print_time, move_t;
    double start_v, half_accel;
    struct coord start_pos;
    struct coord axes_r;
    struct list_node node;
    double padding[2];
};
struct pull_move {
    double print_time, move_t;
    double start_v, accel;
    double start_x, start_y, start_z;
    double x_r, y_r, z_r;
};
struct move *move_alloc(void);
struct trapq *trapq_alloc(void);
void trapq_free(struct trapq *tq);
void trapq_append(struct trapq *tq, double print_time,
    double accel_t, double cruise_t, double decel_t,
    double start_pos_x, double start_pos_y, double start_pos_z,
    double axes_r_x, double axes_r_y, double axes_r_z,
    double start_v, double cruise_v, double accel);
void trapq_finalize_moves(struct trapq *tq, double print_time,
    double clear_history_time);
void trapq_set_position(struct trapq *tq, double print_time,
    double pos_x, double pos_y, double pos_z);
void trapq_extract_old(struct trapq *tq, struct pull_move *p, int max,
    double start_time, double end_time);
void trapq_add_move(struct trapq *tq, struct move *m);
"""

defs_kin_cartesian = """
struct stepper_kinematics *cartesian_stepper_alloc(char axis);
"""

defs_kin_generic_cartesian = """
struct stepper_kinematics *generic_cartesian_stepper_alloc(double a_x,
    double a_y, double a_z);
void generic_cartesian_stepper_set_coeffs(struct stepper_kinematics *sk,
    double a_x, double a_y, double a_z);
"""

defs_kin_corexy = """
struct stepper_kinematics *corexy_stepper_alloc(char type);
"""

defs_kin_corexz = """
struct stepper_kinematics *corexz_stepper_alloc(char type);
"""

defs_kin_delta = """
struct stepper_kinematics *delta_stepper_alloc(double arm2,
    double tower_x, double tower_y);
"""

defs_kin_deltesian = """
struct stepper_kinematics *deltesian_stepper_alloc(double arm2,
    double arm_x);
"""

defs_kin_polar = """
struct stepper_kinematics *polar_stepper_alloc(char type);
"""

defs_kin_rotary_delta = """
struct stepper_kinematics *rotary_delta_stepper_alloc(
    double shoulder_radius, double shoulder_height,
    double angle, double upper_arm, double lower_arm);
"""

defs_kin_winch = """
struct stepper_kinematics *winch_stepper_alloc(double anchor_x,
    double anchor_y, double anchor_z);
"""

defs_kin_extruder = """
struct stepper_kinematics *extruder_stepper_alloc(void);
void extruder_stepper_free(struct stepper_kinematics *sk);
void extruder_set_pressure_advance(struct stepper_kinematics *sk,
    double print_time, double pressure_advance, double smooth_time);
"""

defs_kin_shaper = """
int input_shaper_set_shaper_params(struct stepper_kinematics *sk, char axis,
    int n, double a[], double t[]);
int input_shaper_set_sk(struct stepper_kinematics *sk,
    struct stepper_kinematics *orig_sk);
void input_shaper_update_sk(struct stepper_kinematics *sk);
struct stepper_kinematics *input_shaper_alloc(void);
"""

defs_kin_idex = """
void dual_carriage_set_sk(struct stepper_kinematics *sk,
    struct stepper_kinematics *orig_sk);
int dual_carriage_set_transform(struct stepper_kinematics *sk,
    char axis, double scale, double offs);
struct stepper_kinematics *dual_carriage_alloc(void);
"""

defs_kin_ratos_hybrid_corexy = """
struct stepper_kinematics *ratos_corexy_stepper_alloc(char type);
struct stepper_kinematics *ratos_hybrid_stepper_alloc(char type, uint32_t flags);
uint32_t ratos_get_active_flags(char type);
void ratos_dual_carriage_set_transform(struct stepper_kinematics *sk,
    char axis, double scale, double offs);
void ratos_dual_carriage_set_sk(struct stepper_kinematics *sk,
    struct stepper_kinematics *orig_sk);
void ratos_dual_carriage_set_carriage_state(struct stepper_kinematics *sk,
    uint32_t state);
uint32_t ratos_dual_carriage_get_carriage_state(struct stepper_kinematics *sk);
struct stepper_kinematics *ratos_dual_carriage_alloc(void);
void ratos_calc_position_batch(struct move *m, double move_time,
    double *x_out, double *y_out, double *z_out);
"""

defs_kin_5axis = """
struct stepper_kinematics *five_axis_stepper_alloc(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
struct stepper_kinematics *five_axis_stepper_alloc_x(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
struct stepper_kinematics *five_axis_stepper_alloc_y(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
struct stepper_kinematics *five_axis_stepper_alloc_z(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
struct stepper_kinematics *five_axis_stepper_alloc_a(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
struct stepper_kinematics *five_axis_stepper_alloc_b(double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double arm_length_a, double arm_length_b);
void five_axis_stepper_set_offsets(struct stepper_kinematics *sk,
    double pivot_offset_z, double tool_offset_x,
    double tool_offset_y, double tool_offset_z);
void five_axis_calc_tcp_compensation(double a_deg, double b_deg,
    double pivot_offset_z,
    double *tcp_comp_x, double *tcp_comp_y, double *tcp_comp_z);
void five_axis_tcp_to_machine(double tcp_x, double tcp_y, double tcp_z,
    double a_deg, double b_deg, double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double *mach_x, double *mach_y, double *mach_z);
void five_axis_machine_to_tcp(double mach_x, double mach_y, double mach_z,
    double a_deg, double b_deg, double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double *tcp_x, double *tcp_y, double *tcp_z);
void five_axis_pool_reset(void);
double five_axis_calc_position_base(struct stepper_kinematics *sk,
    struct move *m, double move_time);
void five_axis_stepper_free(struct stepper_kinematics *sk);
"""

defs_conn_manager = """
#define MESSAGE_MAX 4096
/* Connection Type Constants (deben coincidir con conn_backend.h) */
#define CONN_TYPE_SERIAL    0x01
#define CONN_TYPE_CAN       0x02
#define CONN_TYPE_ETHERTUX  0x03
#define CONN_TYPE_DEBUGFILE 0x04
#define CONN_TYPE_RS485     0x05
#define CONN_TYPE_SPI       0x06
#define CONN_TYPE_DEBUGPIPE 0x07
#define CONN_TYPE_ZCKB_SHM  0x08

struct pull_queue_message {
    uint8_t msg[MESSAGE_MAX];
    int len;
    double sent_time, receive_time;
    uint64_t notify_id;
};
struct conn_manager *conn_alloc(int fd, char conn_type, int client_id, const char name[16]);
void conn_exit(struct conn_manager *cm);
void conn_free(struct conn_manager *cm);
struct command_queue *conn_alloc_commandqueue(void);
void conn_free_commandqueue(struct command_queue *cq);
int conn_commandqueue_is_empty(struct command_queue *cq);
void conn_send(struct conn_manager *cm, struct command_queue *cq,
    uint8_t *msg, int len, uint64_t min_clock,
    uint64_t req_clock, uint64_t notify_id);
void conn_send_one(struct conn_manager *cm, struct command_queue *cq, struct queue_message *qm);
void conn_send_batch(struct conn_manager *cm, struct command_queue *cq, struct list_head *msgs);
void conn_pull(struct conn_manager *cm, struct pull_queue_message *pqm);
void conn_add_fastreader(struct conn_manager *cm, struct fastreader *fr);
void conn_rm_fastreader(struct conn_manager *cm, struct fastreader *fr);
void conn_set_wire_frequency(struct conn_manager *cm, double frequency);
void conn_set_receive_window(struct conn_manager *cm, int receive_window);
void conn_set_clock_est(struct conn_manager *cm, double est_freq,
    double conv_time, uint64_t conv_clock, uint64_t last_clock);
void conn_get_clock_est(struct conn_manager *cm, struct clock_estimate *ce);
void conn_set_backend_params(struct conn_manager *cm, const char *key, const void *value, size_t len);
void conn_set_can_params(struct conn_manager *cm, int mode, int retries, int xl_sdt);
void conn_set_usb_profile(struct conn_manager *cm, int max_pending_blocks);
void conn_set_ethertux_params(struct conn_manager *cm, uint16_t alias, uint16_t position,
    uint32_t vendor_id, uint32_t product_id, uint32_t cycle_time_ns);
void conn_set_spi_params(struct command_queue *cq, uint32_t speed_hz,
    uint8_t mode, uint8_t hw_crc, uint8_t dma_enabled);
int conn_pin_to_cpu(struct conn_manager *cm, int cpu_id);
int conn_set_fifo_priority(struct conn_manager *cm, int priority);
int conn_set_irq_affinity(struct conn_manager *cm, const int *cpu_list, int count);
void conn_get_stats(struct conn_manager *cm, char *buf, int len);
int conn_extract_old(struct conn_manager *cm, int sentq, struct pull_queue_message *q, int max);

/* ZCKB Shared Memory Functions */
void *zckb_create_context(const char *name, int is_master);
void zckb_destroy_context(void *ctx);
int zckb_map_region(void *ctx, const char *name, uint64_t size);
void zckb_unmap_region(void *ctx, int region_id);
int zckb_ring_push(void *region, const void *data, uint32_t len);
int zckb_ring_pop(void *region, void *buffer, uint32_t max_len);
uint64_t zckb_get_timestamp_ns(void);
"""

defs_trdispatch = """
struct trdispatch_rt_stats {
    uint64_t sample_count, trigger_count;
    double last_latency_us, max_latency_us, avg_latency_us;
};
void trdispatch_start(struct trdispatch *td, uint32_t dispatch_reason);
void trdispatch_stop(struct trdispatch *td);
struct trdispatch *trdispatch_alloc(void);
struct trdispatch_mcu *trdispatch_mcu_alloc(struct trdispatch *td,
    struct conn_manager *cm, struct command_queue *cq, uint32_t trsync_oid,
    uint32_t set_timeout_msgtag, uint32_t trigger_msgtag,
    uint32_t state_msgtag);
void trdispatch_mcu_setup(struct trdispatch_mcu *tdm,
    uint64_t last_status_clock, uint64_t expire_clock,
    uint64_t expire_ticks, uint64_t min_extend_ticks);
void trdispatch_get_rt_stats(struct trdispatch *td,
    struct trdispatch_rt_stats *stats);
"""

defs_pyhelper = """
void set_python_logging_callback(void (*func)(const char *));
double get_monotonic(void);
int set_thread_name(char name[16]);
"""

defs_std = """
void free(void*);
"""

defs_ultracrc = """
typedef enum {
    ULTRACRC_ARCH_GENERIC          = 0,
    ULTRACRC_ARCH_X86_SSE42        = 1,
    ULTRACRC_ARCH_X86_PCLMUL       = 2,
    ULTRACRC_ARCH_X86_VPCLMUL      = 3,
    ULTRACRC_ARCH_ARMV8_CRC        = 4,
    ULTRACRC_ARCH_ARMV8_PMULL      = 5,
    ULTRACRC_ARCH_ARMV8_PMULL_EOR3 = 6,
    ULTRACRC_ARCH_RISCV_ZBC        = 7,
    ULTRACRC_ARCH_UNOQ_OPT         = 8
} ultracrc_arch_t;
typedef struct {
    double     crc8_mbs;
    double     crc16_mbs;
    double     crc32_mbs;
    double     crc32c_mbs;
    double     crc64_mbs;
    uint64_t   iterations;
    size_t     buf_size;
    ultracrc_arch_t arch_tier;
} ultracrc_bench_result_t;
void ultracrc_warmup(void);
uint8_t  ultracrc8_compute(const uint8_t *data, size_t len, uint8_t init);
uint16_t ultracrc16_ccitt_compute(const uint8_t *data, size_t len, uint16_t init);
uint32_t ultracrc32_compute(const uint8_t *data, size_t len, uint32_t init);
uint32_t ultracrc32c_compute(const uint8_t *data, size_t len, uint32_t init);
uint64_t ultracrc64_ecma_compute(const uint8_t *data, size_t len, uint64_t init);
int   ultracrc_lock_memory(void);
int   ultracrc_set_rt_scheduler(int priority);
int   ultracrc_pin_to_cpu(int cpu_id);
void *ultracrc_alloc_aligned(size_t size, size_t alignment);
void  ultracrc_prefetch_adaptive(const void *ptr, size_t len);
ultracrc_arch_t  ultracrc_detect_arch(void);
void             ultracrc_set_override_arch(ultracrc_arch_t arch);
const char      *ultracrc_arch_name(ultracrc_arch_t arch);
ultracrc_bench_result_t ultracrc_run_benchmark(size_t buf_size, uint64_t iterations);
"""

defs_msgblock = """
void msgblock_pool_init(void);
uint16_t msgblock_crc16_ccitt(uint8_t *buf, int len);
int msgblock_check(uint8_t *need_sync, uint8_t *buf, int buf_len);
uint8_t *msgblock_encode_int(uint8_t *p, uint32_t v);
uint32_t msgblock_parse_int(uint8_t **pp);
int msgblock_decode(uint32_t *data, int data_len, uint8_t *msg, int msg_len);
"""

defs_gcode_parser = """
typedef struct {
    const uint8_t *cmd;
    uint32_t cmd_len;
    const uint8_t *params;
    uint32_t params_len;
    int32_t star_pos;
    uint32_t crc32;
    uint8_t checksum_ok;
} GCodeParseResult;
int parse_gcode_line_fast(const uint8_t *line, int32_t len, GCodeParseResult *out);
void gcode_parser_warmup(void);
uint8_t gcode_validate_checksum(const uint8_t *line, int32_t len,
    int32_t star_pos, uint32_t expected_crc);
"""

defs_pollreactor = """
#define PR_NOW   0.
#define PR_NEVER 9999999999999999.
struct pollreactor *pollreactor_alloc(int num_fds, int num_timers, void *callback_data);
void pollreactor_free(struct pollreactor *pr);
void pollreactor_add_fd(struct pollreactor *pr, int pos, int fd, void *callback, int write_only);
void pollreactor_add_timer(struct pollreactor *pr, int pos, void *callback);
double pollreactor_get_timer(struct pollreactor *pr, int pos);
void pollreactor_update_timer(struct pollreactor *pr, int pos, double waketime);
void pollreactor_run(struct pollreactor *pr);
void pollreactor_do_exit(struct pollreactor *pr);
int pollreactor_is_exit(struct pollreactor *pr);
int fd_set_non_blocking(int fd);
"""

defs_all = [
    defs_pyhelper, defs_conn_manager, defs_std, defs_ultracrc, defs_list,
    defs_stepcompress, defs_steppersync, defs_itersolve, defs_trapq, defs_trdispatch,
    defs_kin_cartesian, defs_kin_corexy, defs_kin_corexz, defs_kin_delta,
    defs_kin_deltesian, defs_kin_polar, defs_kin_rotary_delta, defs_kin_winch,
    defs_kin_extruder, defs_kin_shaper, defs_kin_idex,
    defs_kin_generic_cartesian, defs_msgblock, defs_kin_ratos_hybrid_corexy,
    defs_kin_5axis, defs_gcode_parser, defs_pollreactor,
]

######################################################################
# Build Helpers
######################################################################
def get_abs_files(srcdir, filelist):
    return [os.path.join(srcdir, fname) for fname in filelist]

def get_mtimes(filelist):
    out = []
    for filename in filelist:
        try:
            t = os.path.getmtime(filename)
        except os.error:
            continue
        out.append(t)
    return out

def check_build_code(sources, target):
    src_times = get_mtimes(sources)
    obj_times = get_mtimes([target])
    return not obj_times or max(src_times) > min(obj_times)

def check_gcc_option(option):
    cmd = "%s %s -S -o /dev/null -xc /dev/null > /dev/null 2>&1" % (
        GCC_CMD, option)
    res = os.system(cmd)
    return res == 0

def check_compiler(cmd):
    try:
        subprocess.check_call([cmd, "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def do_build_code(cmd):
    res = os.system(cmd)
    if res:
        msg = "Unable to build C code module (error=%s)" % (res,)
        logging.error(msg)
        raise Exception(msg)

def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def _get_cpu_features():
    """Read /proc/cpuinfo and return a set of CPU feature tokens."""
    cpuinfo = _read_file("/proc/cpuinfo").lower()
    features = set()
    for line in cpuinfo.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('flags') or line.startswith('features'):
            parts = line.split(':', 1)
            if len(parts) > 1:
                features.update(parts[1].strip().split())
        elif line.startswith('isa'):
            parts = line.split(':', 1)
            if len(parts) > 1:
                isa_str = parts[1].strip()
                if ' ' in isa_str:
                    features.update(ext.lower() for ext in isa_str.split(' ')[1:])
                features.add(isa_str.split(' ')[0].lower())
    return features

_CPU_FEATURES = None

def _cpu_has_token(token):
    global _CPU_FEATURES
    if _CPU_FEATURES is None:
        _CPU_FEATURES = _get_cpu_features()
    return token.lower() in _CPU_FEATURES

def _machine():
    return (platform.machine() or "").lower()

def _get_gcc_version():
    """Detect major.minor version of GCC_CMD."""
    try:
        output = subprocess.check_output([GCC_CMD, "--version"], stderr=subprocess.DEVNULL).decode()
        match = re.search(r'(\d+)\.(\d+)', output.splitlines()[0])
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 0, 0

def _build_arch_flags():
    """
    Return optimized compilation flags per architecture/CPU.
    Evaluates hardware features and compiler compatibility.
    Profiles: modern > compatible > fallback.
    Control: CHELPER_NATIVE=0 (disable), CHELPER_ARCH_FLAGS (append extras).
    """
    if os.environ.get("CHELPER_NATIVE", "1") == "0":
        return []
    
    arch = _machine()
    cpu_features = _get_cpu_features()
    flags = []
    
    # ------------------------------------------------------------------
    # x86 / x86_64
    # ------------------------------------------------------------------
    if arch in ("x86_64", "amd64", "i386", "i686"):
        avx512_cpu = all(f in cpu_features for f in ["avx512f", "vpclmulqdq", "pclmul"])
        if avx512_cpu and all(check_gcc_option(f) for f in X86_AVX512_FLAGS):
            flags.extend(X86_AVX512_FLAGS)
        elif all(f in cpu_features for f in ["avx2", "fma", "pclmul"]) and \
             all(check_gcc_option(f) for f in X86_MODERN_FLAGS):
            flags.extend(X86_MODERN_FLAGS)
        elif all(f in cpu_features for f in ["sse4_2", "pclmul"]) and \
             all(check_gcc_option(f) for f in X86_COMPAT_FLAGS):
            flags.extend(X86_COMPAT_FLAGS)
        else:
            flags.extend([f for f in X86_FALLBACK_FLAGS if check_gcc_option(f)])
    
    # ------------------------------------------------------------------
    # ARM64-bit (AArch64)
    # ------------------------------------------------------------------
    elif arch in ("aarch64", "arm64"):
        eor3_cpu = all(f in cpu_features for f in ["crc32", "pmull", "sha3"])
        if eor3_cpu and check_gcc_option(AARCH64_EOR3_FLAGS[0]):
            flags.extend(AARCH64_EOR3_FLAGS)
        elif all(f in cpu_features for f in ["crc32", "asimd", "pmull"]) and \
             check_gcc_option(AARCH64_MODERN_FLAGS[0]):
            flags.extend(AARCH64_MODERN_FLAGS)
        elif all(f in cpu_features for f in ["crc32", "asimd"]) and \
             check_gcc_option(AARCH64_COMPAT_FLAGS[0]):
            flags.extend(AARCH64_COMPAT_FLAGS)
        else:
            flags.extend([f for f in AARCH64_FALLBACK_FLAGS if check_gcc_option(f)])
    
    # ------------------------------------------------------------------
    # ARM32-bit (ARMv7)
    # ------------------------------------------------------------------
    elif arch.startswith("armv7") or arch == "armv7l":
        if all(f in cpu_features for f in ["neon", "vfpv4"]) and \
           all(check_gcc_option(f) for f in ARMV7_MODERN_FLAGS):
            flags.extend(ARMV7_MODERN_FLAGS)
        else:
            flags.extend([f for f in ARMV7_FALLBACK_FLAGS if check_gcc_option(f)])
    
    # ------------------------------------------------------------------
    # RISC-V (RV64 / RV32)
    # ------------------------------------------------------------------
    elif arch in ("riscv64", "riscv32", "risc-v"):
        is_64bit = "64" in arch
        modern_flags = RISCV64_MODERN_FLAGS if is_64bit else RISCV32_MODERN_FLAGS
        base_flags = RISCV64_BASE_FLAGS if is_64bit else RISCV32_BASE_FLAGS
        if (any(f in cpu_features for f in ["zbc", "crc", "zbkb"]) and \
            all(check_gcc_option(f) for f in modern_flags)):
            flags.extend(modern_flags)
        elif all(check_gcc_option(f) for f in base_flags):
            flags.extend(base_flags)
        else:
            fallback = "-march=rv64imafdc" if is_64bit else "-march=rv32imafdc"
            if check_gcc_option(fallback):
                flags.append(fallback)
    
    # Generic fallback
    else:
        if check_gcc_option("-march=native"):
            flags.append("-march=native")
        if check_gcc_option("-mtune=native"):
            flags.append("-mtune=native")
    
    # Manual extra flags
    extra = os.environ.get("CHELPER_ARCH_FLAGS", "").strip()
    if extra:
        flags.extend(extra.split())
    
    return flags

def build_gcc_compile_args():
    flags = list(BASE_GCC_FLAGS)
    flags.extend(_build_arch_flags())
    
    # Optional generic flags (check compiler compatibility)
    linker_flags = []
    for option in OPTIONAL_GCC_FLAGS:
        if option.startswith("-Wl,"):
            if check_gcc_option(option):
                linker_flags.append(option)
        elif check_gcc_option(option):
            flags.append(option)
    
    # ------------------------------------------------------------------
    # Conditional optimizations by GCC version
    # ------------------------------------------------------------------
    gcc_major, gcc_minor = _get_gcc_version()
    
    if gcc_major >= 13:
        if check_gcc_option("-fno-ipa-icf"):
            flags.append("-fno-ipa-icf")
    
    if gcc_major >= 15 or GCC_CMD == "gcc-15":
        gcc15_flags = [
            "-falign-jumps=32",
            "-falign-labels=32",
            "-flto=auto",
            "-fipa-pta",
        ]
        for f in gcc15_flags:
            if check_gcc_option(f):
                flags.append(f)
        logging.info("GCC 15+: advanced optimizations applied (LTO, IPA-PTA, I-cache alignment)")
    
    # Link EtherCAT master if conn_ethertux.c is present in sources and ethercat is installed
    if any(f.endswith("conn_ethertux.c") for f in SOURCE_FILES) and os.path.exists("/usr/include/ecrt.h"):
        linker_flags.append("-lethercat")
        logging.info("Linking IGH EtherCAT Master library (-lethercat)")
    
    flags.extend(linker_flags)
    
    extra = os.environ.get("CHELPER_CFLAGS_EXTRA", "").strip()
    if extra:
        flags.extend(extra.split())
    
    return " ".join(flags) + " -o %s %s"

def check_build_c_library():
    srcdir = os.path.dirname(os.path.realpath(__file__))
    srcfiles = get_abs_files(srcdir, SOURCE_FILES)
    ofiles = get_abs_files(srcdir, OTHER_FILES)
    destlib = get_abs_files(srcdir, [DEST_LIB])[0]
    
    if not check_build_code(srcfiles + ofiles + [__file__], destlib):
        return destlib
    
    tempdestlib = get_abs_files(srcdir, ["temp" + DEST_LIB])[0]
    if check_compiler(GCC_CMD):
        cmd = "%s %s" % (GCC_CMD, build_gcc_compile_args())
        logging.info("Building Conn Manager C module %s using GCC (Linux target)", DEST_LIB)
        do_build_code(cmd % (f"'{tempdestlib}'", ' '.join(f"'{f}'" for f in srcfiles)))
        os.rename(tempdestlib, destlib)
        return destlib
    
    msg = "GCC compiler not found in PATH. C code optimizations disabled."
    logging.warning(msg)
    raise Exception(msg)

######################################################################
# FFI Interface
######################################################################
FFI_main = None
FFI_lib = None
pyhelper_logging_callback = None

def logging_callback(msg):
    logging.error(FFI_main.string(msg))

def run_crc_benchmark(buf_size=1_048_576, iterations=50_000):
    """
    Run hardware-accelerated CRC benchmark.
    Returns dict with MB/s for each algorithm and detected arch.
    """
    ffi, lib = get_ffi()
    if not lib:
        return {"error": "C helper not available"}
    
    res = lib.ultracrc_run_benchmark(buf_size, iterations)
    return {
        "arch": lib.ultracrc_arch_name(res.arch_tier),
        "crc8_mbs": res.crc8_mbs,
        "crc16_mbs": res.crc16_mbs,
        "crc32_mbs": res.crc32_mbs,
        "crc32c_mbs": res.crc32c_mbs,
        "crc64_mbs": res.crc64_mbs,
        "iterations": res.iterations,
        "buf_size": res.buf_size
    }

def get_ffi():
    global FFI_main, FFI_lib, pyhelper_logging_callback
    if FFI_lib is None:
        try:
            destlib = check_build_c_library()
            flags_used = build_gcc_compile_args() % ("", "")
            logging.info("C optimizations applied: %s", flags_used)
        except Exception as e:
            logging.warning("Falling back to Python-only mode: %s", str(e))
            return None, None
        
        FFI_main = cffi.FFI()
        for d in defs_all:
            FFI_main.cdef(d)
        FFI_lib = FFI_main.dlopen(destlib)
        
        pyhelper_logging_callback = FFI_main.callback("void(const char*)", logging_callback)
        FFI_lib.set_python_logging_callback(pyhelper_logging_callback)
        
        # Warm up the CRC dispatch table eagerly
        try:
            FFI_lib.ultracrc_warmup()
            arch = FFI_lib.ultracrc_arch_name(FFI_lib.ultracrc_detect_arch())
            logging.info("ultracrc: warmed up (arch=%s)", arch)
        except Exception as e:
            logging.warning("ultracrc_warmup() failed (non-fatal): %s", str(e))
        
        # Warm up the G-code parser LUTs
        try:
            if hasattr(FFI_lib, 'gcode_parser_warmup'):
                FFI_lib.gcode_parser_warmup()
                logging.info("gcode_parser: LUTs and dispatch warmed up")
        except Exception as e:
            logging.warning("gcode_parser_warmup() failed (non-fatal): %s", str(e))
        
    return FFI_main, FFI_lib

######################################################################
# hub-ctrl USB Hub Power Controller
######################################################################
HC_COMPILE_CMD = "gcc -Wall -g -O2 -o %s %s -lusb"
HC_SOURCE_FILES = ['hub-ctrl.c']
HC_SOURCE_DIR = '../../lib/hub-ctrl'
HC_TARGET = "hub-ctrl"
HC_CMD = "sudo %s/hub-ctrl -h 0 -P 2 -p %d"

def run_hub_ctrl(enable_power):
    srcdir = os.path.dirname(os.path.realpath(__file__))
    hubdir = os.path.join(srcdir, HC_SOURCE_DIR)
    srcfiles = get_abs_files(hubdir, HC_SOURCE_FILES)
    destlib = get_abs_files(hubdir, [HC_TARGET])[0]
    
    if check_build_code(srcfiles, destlib):
        logging.info("Building C code module %s", HC_TARGET)
        do_build_code(HC_COMPILE_CMD % (destlib, ' '.join(srcfiles)))
    
    os.system(HC_CMD % (hubdir, enable_power))

if __name__ == '__main__':
    get_ffi()