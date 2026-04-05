/*
ultracrc_tables.h - Precomputed CRC Tables (Cache-Line Aligned)
Auto-generated for UltraCRC Engine - Ultra-High-Performance, RT-Deterministic
SPDX-License-Identifier: GPL-3.0-or-later
*/
#ifndef ULTRACRC_TABLES_H
#define ULTRACRC_TABLES_H

#include <stdint.h>
#include "compiler.h"

#define ULTRACRC_CACHE_LINE_SIZE 64

/* ─── CRC8 (Polynomial: 0x31, Init: 0xFF, XORout: 0x00) ─────────────────── */
extern const uint8_t ultracrc_crc8_t0[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── CRC16-CCITT (Polynomial: 0x1021, Init: 0xFFFF, XORout: 0x0000) ───── */
extern const uint16_t ultracrc_crc16_t0[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── CRC32 IEEE 802.3 (Polynomial: 0xEDB88320, Init: 0xFFFFFFFF) ──────── */
extern const uint32_t ultracrc_crc32_t0[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── CRC32C Castagnoli (Polynomial: 0x82F63B78, Init: 0xFFFFFFFF) ─────── */
extern const uint32_t ultracrc_crc32c_t0[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── CRC64-ECMA-182 (Polynomial: 0xC96C5795D7870F42, Init: 0xFFFFFFFFFFFFFFFF) */
extern const uint64_t ultracrc_crc64_t0[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── Slice-by-N Tables (Generated at warmup) ──────────────────────────── */
extern uint8_t  ultracrc_crc8_s[3][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern uint16_t ultracrc_crc16_shift4_hi[256], ultracrc_crc16_shift4_lo[256],
                ultracrc_crc16_d3[256], ultracrc_crc16_d2[256], ultracrc_crc16_d1[256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern uint32_t ultracrc_crc32_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern uint32_t ultracrc_crc32c_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern uint64_t ultracrc_crc64_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── Barrett Reduction Constants (for PMULL/VPCLMUL) ──────────────────── */
extern const uint64_t ultracrc_barrett_crc64[2] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern const uint32_t ultracrc_barrett_crc32[2] __aligned(ULTRACRC_CACHE_LINE_SIZE);
extern const uint32_t ultracrc_barrett_crc32c[2] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ─── Initialization Functions (Called by warmup) ──────────────────────── */
__visible void ultracrc_tables_build_crc8(void);
__visible void ultracrc_tables_build_crc16(void);
__visible void ultracrc_tables_build_crc32(void);
__visible void ultracrc_tables_build_crc32c(void);
__visible void ultracrc_tables_build_crc64(void);

#endif /* ULTRACRC_TABLES_H */