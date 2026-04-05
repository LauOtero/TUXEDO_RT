/*
 * crc_utils.h - Ultra-High-Performance, RT-Deterministic CRC Library
 * 
 * Features:
 * • Architecture-aware runtime dispatch (x86_64, ARM64, ARM32, RISC-V)
 * • Hardware-accelerated folding (PCLMULQDQ, AVX-512, ARM CRC32/PMULL)
 * • Slice-by-N software fallback with cache-line alignment
 * • Zero-allocation, zero-syscall hot-path after warmup
 * • Integrated micro-benchmark API for throughput validation
 * 
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef CHELPER_CRC_UTILS_H
#define CHELPER_CRC_UTILS_H

#include "compiler.h"
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Hardware cache-line size (standard across x86/ARM/RISC-V) */
#define CRC_CACHE_LINE_SIZE 64

/* Architecture acceleration tiers */
typedef enum {
    CRC_ARCH_GENERIC          = 0, /* Slice-by-N software fallback */
    CRC_ARCH_X86_SSE42        = 1, /* SSE4.2 crc32q 3-way interleave */
    CRC_ARCH_X86_PCLMUL       = 2, /* PCLMULQDQ 128-bit carry-less folding */
    CRC_ARCH_X86_VPCLMUL      = 3, /* AVX-512 + VPCLMULQDQ 512-bit folding */
    CRC_ARCH_ARMV8_CRC        = 4, /* ARMv8 hardware CRC32 instructions */
    CRC_ARCH_ARMV8_PMULL      = 5, /* ARMv8 PMULL 128-bit folding */
    CRC_ARCH_ARMV8_PMULL_EOR3 = 6, /* ARMv8.2+ PMULL + EOR3 (SHA3) */
    CRC_ARCH_RISCV_ZBC        = 7, /* RISC-V Zbc CRC extensions */
} crc_arch_t;

/* ─── Public API (Zero-Branch after warmup) ─────────────────────────────── */
__visible void crc_utils_warmup(void);
__visible uint8_t  crc8_compute(const uint8_t *data, size_t len, uint8_t init);
__visible uint16_t crc16_ccitt_compute(const uint8_t *data, size_t len, uint16_t init);
__visible uint32_t crc32_compute(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t crc32c_compute(const uint8_t *data, size_t len, uint32_t init);

#if defined(__x86_64__) || defined(__i386__)
#if defined(__PCLMUL__) || defined(__SSE4_2__)
__visible uint16_t crc16_pclmul(const uint8_t *data, size_t len, uint16_t init);
__visible uint32_t crc32_pclmul(const uint8_t *data, size_t len, uint32_t init);
__visible uint32_t crc32c_pclmul(const uint8_t *data, size_t len, uint32_t init);
#endif
#if defined(__AVX512F__) && defined(__VPCLMULQDQ__)
__visible uint32_t crc32c_vpclmul(const uint8_t *data, size_t len, uint32_t init);
#endif
#endif

/* ─── Real-Time & Memory Utilities ──────────────────────────────────────── */
__visible int   crc_utils_lock_memory(void);
__visible int   crc_utils_set_rt_scheduler(int priority);
__visible int   crc_utils_pin_to_cpu(int cpu_id);
__visible void *crc_utils_alloc_aligned(size_t size, size_t alignment);
__visible void  crc_utils_prefetch_data(const void *ptr, size_t len);

/* ─── Diagnostics & Override ────────────────────────────────────────────── */
__visible crc_arch_t  crc_utils_detect_arch(void);
__visible void        crc_utils_set_override_arch(crc_arch_t arch);
__visible const char *crc_utils_arch_name(crc_arch_t arch);

/* ─── Integrated Benchmark ──────────────────────────────────────────────── */
typedef struct {
    double     crc8_mbs;
    double     crc16_mbs;
    double     crc32_mbs;
    double     crc32c_mbs;
    uint64_t   iterations;
    size_t     buf_size;
    crc_arch_t arch_tier;
} crc_bench_result_t;

__visible crc_bench_result_t crc_utils_run_benchmark(size_t buf_size, uint64_t iterations);

#ifdef __cplusplus
}
#endif
#endif /* CHELPER_CRC_UTILS_H */