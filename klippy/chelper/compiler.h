/*
 * compiler.h — Low-level compiler, architecture, and RT utilities
 *
 * Optimized for Klipper C Helper (chelper) with multi-arch support:
 * x86_64 / ARM64 / ARM32 / RISC-V.
 * Provides deterministic RT primitives, branch hints, alignment,
 * cache prefetching, and lightweight spinlocks.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef __COMPILER_H
#define __COMPILER_H

/* Force GNU source for modern libc features */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include <stdalign.h>

#ifdef _WIN32
#include <windows.h>
#include <intrin.h>
#else
#include <sched.h>
#include <sys/types.h>
#endif

/* ─── Compiler & Linker Attributes ──────────────────────────────────────── */
#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
#define __hot        __attribute__((hot))
#define __cold       __attribute__((cold))
#define __visible    __attribute__((visibility("default")))
#define __hidden     __attribute__((visibility("hidden")))
#define __noreturn   __attribute__((noreturn))
#define __packed     __attribute__((packed))
#define __weak       __attribute__((weak))
#define __unused     __attribute__((unused))
#define __aligned(x) __attribute__((aligned(x)))
#define __section(s) __attribute__((section(s)))
#define __assume_aligned(ptr, align) __builtin_assume_aligned(ptr, align)

/* ─── Branch Prediction Hints ───────────────────────────────────────────── */
#define likely(x)   __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)

/* ─── Memory Barriers & Prefetching ─────────────────────────────────────── */
#ifdef _WIN32
#define barrier()       MemoryBarrier()
#define cpu_relax()     YieldProcessor()
#define prefetch_read(p)  __noop(p)
#define prefetch_write(p) __noop(p)
#else
#define barrier()       __asm__ __volatile__("" ::: "memory")

#if defined(__x86_64__) || defined(__i386__)
#define cpu_relax()     __asm__ __volatile__("pause" ::: "memory")
#elif defined(__aarch64__) || defined(__arm__)
#define cpu_relax()     __asm__ __volatile__("yield" ::: "memory")
#elif defined(__riscv)
#define cpu_relax()     __asm__ __volatile__("nop" ::: "memory")
#else
#define cpu_relax()     __asm__ __volatile__("" ::: "memory")
#endif

#define prefetch_read(p)  __builtin_prefetch((p), 0, 3)
#define prefetch_write(p) __builtin_prefetch((p), 1, 3)
#endif

/* ─── Utility Macros ────────────────────────────────────────────────────── */
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ALIGN_UP(x, a)      (((x) + (typeof(x))(a) - 1) & ~((typeof(x))(a) - 1))
#define ALIGN_DOWN(x, a)    ((x) & ~((typeof(x))(a) - 1))
#define IS_ALIGNED(x, a)    (((x) & ((typeof(x))(a) - 1)) == 0)

#define DIV_ROUND_UP(n, d)   (((n) + (d) - 1) / (d))
#define DIV_ROUND_CLOSEST(x, divisor) \
    ({ typeof(divisor) __d = divisor; (((x) + (__d / 2)) / __d); })

#define __stringify_1(x) #x
#define __stringify(x)   __stringify_1(x)
#define ___PASTE(a,b)    a##b
#define __PASTE(a,b)     ___PASTE(a,b)

#ifndef offsetof
#define offsetof(type, member) ((size_t)&((type *)0)->member)
#endif

#define container_of(ptr, type, member) ({                      \
        const typeof(((type *)0)->member) *__mptr = (ptr);      \
        (type *)((char *)__mptr - offsetof(type, member)); })

/* ─── RT Spinlock ───────────────────────────────────────────────────────── */
/* Lightweight spinlock for short critical sections in RT paths.
 * Uses acquire/release semantics implicitly via compiler barriers. */
#ifdef _WIN32
typedef struct {
    volatile LONG value;
} rt_spinlock_t;

static __always_inline void rt_spin_init(rt_spinlock_t *lock) {
    lock->value = 0;
    barrier();
}
static __always_inline void rt_spin_lock(rt_spinlock_t *lock) {
    while (InterlockedExchange(&lock->value, 1)) {
        while (lock->value) cpu_relax();
    }
    barrier();
}
static __always_inline void rt_spin_unlock(rt_spinlock_t *lock) {
    barrier();
    lock->value = 0;
}
#else
typedef struct {
    volatile int value;
} rt_spinlock_t;

static __always_inline void rt_spin_init(rt_spinlock_t *lock) {
    lock->value = 0;
    barrier();
}
static __always_inline void rt_spin_lock(rt_spinlock_t *lock) {
    while (__sync_lock_test_and_set(&lock->value, 1)) {
        while (lock->value) cpu_relax();
    }
    barrier();
}
static __always_inline void rt_spin_unlock(rt_spinlock_t *lock) {
    barrier();
    __sync_lock_release(&lock->value);
}
#endif

#ifdef __cplusplus
#endif

#endif /* __COMPILER_H */