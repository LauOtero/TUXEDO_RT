/*
ultracrc.h - Ultra-High-Performance, RT-Deterministic CRC Library
Features:
• Architecture-aware runtime dispatch (x86_64, ARM64, ARM32, RISC-V)
• Hardware-accelerated folding (PCLMULQDQ, AVX-512, ARM CRC32/PMULL, RISC-V Zbc)
• CRC64-ECMA-182 support with PMULL fallback
• Slice-by-N software fallback with cache-line alignment
• Zero-allocation, zero-syscall hot-path after warmup
• Integrated micro-benchmark API for throughput validation
• Arduino UNO Q (Qualcomm QRB2210) optimized path
SPDX-License-Identifier: GPL-3.0-or-later
*/
#ifndef ULTRACRC_H
#define ULTRACRC_H

#include "compiler.h"
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ULTRACRC_CACHE_LINE_SIZE 64

/* ─── Architecture Acceleration Tiers ─────────────────────────────────── */
typedef enum {
    ULTRACRC_ARCH_GENERIC          = 0,  /* Slice-by-N software fallback */
    ULTRACRC_ARCH_X86_SSE42        = 1,  /* SSE4.2 crc32q 3-way interleave */
    ULTRACRC_ARCH_X86_PCLMUL       = 2,  /* PCLMULQDQ 128-bit carry-less folding */
    ULTRACRC_ARCH_X86_VPCLMUL      = 3,  /* AVX-512 + VPCLMULQDQ 512-bit folding */
    ULTRACRC_ARCH_ARMV8_CRC        = 4,  /* ARMv8 hardware CRC32 instructions */
    ULTRACRC_ARCH_ARMV8_PMULL      = 5,  /* ARMv8 PMULL 128-bit folding */
    ULTRACRC_ARCH_ARMV8_PMULL_EOR3 = 6,  /* ARMv8.2+ PMULL + EOR3 (SHA3) */
    ULTRACRC_ARCH_RISCV_ZBC        = 7,  /* RISC-V Zbc CRC extensions */
    ULTRACRC_ARCH_UNOQ_OPT         = 8   /* Arduino UNO Q (QRB2210) optimized */
} ultracrc_arch_t;

/* ─── CRC Polynomial Identifiers (for dual-key dispatch) ──────────────── */
typedef enum {
    ULTRACRC_POLY_CRC8    = 0,
    ULTRACRC_POLY_CRC16   = 1,
    ULTRACRC_POLY_CRC32   = 2,
    ULTRACRC_POLY_CRC32C  = 3,
    ULTRACRC_POLY_CRC64   = 4,
    ULTRACRC_POLY_COUNT   = 5
} ultracrc_poly_t;

/* ─── Public API (Zero-Branch after warmup) ───────────────────────────── */
__visible void ultracrc_warmup(void);

__visible uint8_t  ultracrc8_compute(const uint8_t *data, size_t len, uint8_t init);
__visible uint16_t ultracrc16_ccitt_compute(const uint8_t *data, size_t len, uint16_t init);
__visible uint32_t ultracrc32_compute(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t ultracrc32c_compute(const uint8_t *data, size_t len, uint32_t init);
__visible uint64_t ultracrc64_ecma_compute(const uint8_t *data, size_t len, uint64_t init);

/* ─── Hardware-Accelerated Entry Points (for explicit use) ────────────── */
#if defined(__x86_64__) || defined(__i386__)
#if defined(__PCLMUL__) || defined(__SSE4_2__)
__visible uint16_t ultracrc16_pclmul(const uint8_t *data, size_t len, uint16_t init);
__visible uint32_t ultracrc32_pclmul(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t ultracrc32c_pclmul(const uint8_t *data, size_t len, uint32_t init);
#endif
#if defined(__AVX512F__) && defined(__VPCLMULQDQ__)
__visible uint32_t ultracrc32c_vpclmul(const uint8_t *data, size_t len, uint32_t init);
#endif
#endif

#if defined(__aarch64__) || defined(__arm__)
#if defined(__ARM_FEATURE_CRC32)
__visible uint32_t ultracrc32_arm3way(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t ultracrc32c_arm3way(const uint8_t *data, size_t len, uint32_t init);
#endif
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRYPTO)
__visible uint32_t ultracrc32_arm_pmull(const uint8_t *data, size_t len, uint32_t init);
__visible uint64_t ultracrc64_arm_pmull(const uint8_t *data, size_t len, uint64_t init);
#endif
#endif

#if defined(__riscv) && defined(__riscv_zbc)
__visible uint32_t ultracrc32_riscv_zbc(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t ultracrc32c_riscv_zbc(const uint8_t *data, size_t len, uint32_t init);
#endif

/* ─── Real-Time & Memory Utilities ────────────────────────────────────── */
__visible int   ultracrc_lock_memory(void);
__visible int   ultracrc_set_rt_scheduler(int priority);
__visible int   ultracrc_pin_to_cpu(int cpu_id);
__visible void *ultracrc_alloc_aligned(size_t size, size_t alignment);
__visible void  ultracrc_prefetch_adaptive(const void *ptr, size_t len);

/* ─── Diagnostics & Override ──────────────────────────────────────────── */
__visible ultracrc_arch_t  ultracrc_detect_arch(void);
__visible void             ultracrc_set_override_arch(ultracrc_arch_t arch);
__visible const char      *ultracrc_arch_name(ultracrc_arch_t arch);

/* ─── Integrated Benchmark ────────────────────────────────────────────── */
typedef struct {
    double          crc8_mbs;
    double          crc16_mbs;
    double          crc32_mbs;
    double          crc32c_mbs;
    double          crc64_mbs;
    uint64_t        iterations;
    size_t          buf_size;
    ultracrc_arch_t arch_tier;
} ultracrc_bench_result_t;

__visible ultracrc_bench_result_t ultracrc_run_benchmark(size_t buf_size, uint64_t iterations);

#ifdef __cplusplus
}
#endif

#endif /* ULTRACRC_H */