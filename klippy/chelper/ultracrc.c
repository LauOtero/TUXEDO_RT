/*
ultracrc.c - Ultra-High-Performance CRC Engine Core
Implements: Multi-arch dispatch, hardware acceleration, RT utilities
SPDX-License-Identifier: GPL-3.0-or-later
*/
#define _GNU_SOURCE
#include "ultracrc.h"
#include "ultracrc_tables.h"
#include <string.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sched.h>
#include <time.h>
#include <errno.h>
#include <unistd.h>
#include <sys/syscall.h>

/* ──────────────────────────────────────────────────────────────────────
§1  SOFTWARE FALLBACKS (Slice-by-N) - Always Available
────────────────────────────────────────────────────────────────────── */
static __hot uint8_t ultracrc8_sw(const uint8_t *data, size_t len, uint8_t init) {
    uint8_t crc = init;
    while (len >= 4) {
        crc = ultracrc_crc8_s[2][crc ^ data[0]] ^ ultracrc_crc8_s[1][data[1]] ^
              ultracrc_crc8_s[0][data[2]] ^ ultracrc_crc8_t0[data[3]];
        data += 4; len -= 4;
    }
    while (len--) crc = ultracrc_crc8_t0[crc ^ *data++];
    return crc;
}

static __hot uint16_t ultracrc16_sw(const uint8_t *data, size_t len, uint16_t init) {
    uint16_t crc = init;
    while (len >= 4) {
        crc = ultracrc_crc16_shift4_hi[crc >> 8] ^ ultracrc_crc16_shift4_lo[crc & 0xFF] ^
              ultracrc_crc16_d3[data[0]] ^ ultracrc_crc16_d2[data[1]] ^
              ultracrc_crc16_d1[data[2]] ^ ultracrc_crc16_t0[data[3]];
        data += 4; len -= 4;
    }
    while (len--) crc = (uint16_t)((crc << 8) ^ ultracrc_crc16_t0[((crc >> 8) ^ *data++) & 0xFF]);
    return crc;
}

static __hot uint32_t ultracrc32_slice8_core(const uint8_t *data, size_t len, uint32_t init,
                                              const uint32_t *t0, uint32_t slices[7][256]) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len && ((uintptr_t)data & 3)) { crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8); len--; }
    while (len >= 8) {
        uint32_t lo, hi;
        memcpy(&lo, data, 4); memcpy(&hi, data + 4, 4);
        lo ^= crc;
        crc = slices[6][lo & 0xFF] ^ slices[5][(lo >> 8) & 0xFF] ^
              slices[4][(lo >> 16) & 0xFF] ^ slices[3][lo >> 24] ^
              slices[2][hi & 0xFF] ^ slices[1][(hi >> 8) & 0xFF] ^
              slices[0][(hi >> 16) & 0xFF] ^ t0[hi >> 24];
        data += 8; len -= 8;
    }
    while (len--) crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFU;
}

static __hot uint32_t ultracrc32_sw(const uint8_t *d, size_t n, uint32_t i) {
    return ultracrc32_slice8_core(d, n, i, ultracrc_crc32_t0, ultracrc_crc32_s);
}

static __hot uint32_t ultracrc32c_sw(const uint8_t *d, size_t n, uint32_t i) {
    return ultracrc32_slice8_core(d, n, i, ultracrc_crc32c_t0, ultracrc_crc32c_s);
}

static __hot uint64_t ultracrc64_slice8_core(const uint8_t *data, size_t len, uint64_t init,
                                              const uint64_t *t0, uint64_t slices[7][256]) {
    uint64_t crc = init ^ 0xFFFFFFFFFFFFFFFFULL;
    while (len && ((uintptr_t)data & 7)) { crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8); len--; }
    while (len >= 8) {
        uint64_t w; memcpy(&w, data, 8);
        w ^= crc;
        crc = slices[6][w & 0xFF] ^ slices[5][(w >> 8) & 0xFF] ^
              slices[4][(w >> 16) & 0xFF] ^ slices[3][(w >> 24) & 0xFF] ^
              slices[2][(w >> 32) & 0xFF] ^ slices[1][(w >> 40) & 0xFF] ^
              slices[0][(w >> 48) & 0xFF] ^ t0[w >> 56];
        data += 8; len -= 8;
    }
    while (len--) crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFFFFFFFFFULL;
}

static __hot uint64_t ultracrc64_sw(const uint8_t *d, size_t n, uint64_t i) {
    return ultracrc64_slice8_core(d, n, i, ultracrc_crc64_t0, ultracrc_crc64_s);
}

/* ──────────────────────────────────────────────────────────────────────
§2  x86-64 ACCELERATED PATHS (PCLMUL / VPCLMUL)
────────────────────────────────────────────────────────────────────── */
#if defined(__x86_64__) || defined(__i386__)
#include <cpuid.h>
#include <immintrin.h>

#if defined(__PCLMUL__) || defined(__SSE4_2__)
__attribute__((target("sse4.2,pclmul")))
static __hot uint16_t ultracrc16_pclmul_impl(const uint8_t *data, size_t len, uint16_t init) {
    if (len < 32) return ultracrc16_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x8000, 0, 0x0002);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    uint16_t init_be = (init >> 8) | (init << 8);
    acc = _mm_xor_si128(acc, _mm_slli_si128(_mm_cvtsi32_si128(init_be), 14));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    uint32_t lo32 = (uint32_t)_mm_extract_epi32(acc, 0);
    uint64_t q = ((uint64_t)lo32 * 0x10811U) >> 32;
    uint16_t crc = (uint16_t)(lo32 ^ (uint32_t)(q * 0x1021U));
    return ((crc >> 8) | (crc << 8));
}

__attribute__((target("sse4.2,pclmul")))
static __hot uint32_t ultracrc32_pclmul_impl(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return ultracrc32_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x1C6E4156U, 0, 0x154442BDU);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    acc = _mm_xor_si128(acc, _mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU)));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    /* Barrett reduction with precomputed constants */
    __m128i mu = _mm_set_epi32(0, 1, 0, (int)ultracrc_barrett_crc32[0]);
    __m128i poly = _mm_set_epi32(0, 1, 0, (int)ultracrc_barrett_crc32[1]);
    __m128i t0v = _mm_clmulepi64_si128(acc, mu, 0x10);
    __m128i t1v = _mm_clmulepi64_si128(t0v, poly, 0x00);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, t1v), 1) ^ 0xFFFFFFFFU;
    return len ? ultracrc32_slice8_core(data, len, crc, ultracrc_crc32_t0, ultracrc_crc32_s) : crc;
}

__attribute__((target("sse4.2,pclmul")))
static __hot uint32_t ultracrc32c_pclmul_impl(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return ultracrc32c_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x493C7D27U, 0, 0x0DD45AABU);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    acc = _mm_xor_si128(acc, _mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU)));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    __m128i mu = _mm_set_epi32(0, 1, 0, (int)ultracrc_barrett_crc32c[0]);
    __m128i poly = _mm_set_epi32(0, 1, 0, (int)ultracrc_barrett_crc32c[1]);
    __m128i t0v = _mm_clmulepi64_si128(acc, mu, 0x10);
    __m128i t1v = _mm_clmulepi64_si128(t0v, poly, 0x00);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, t1v), 1) ^ 0xFFFFFFFFU;
    return len ? ultracrc32_slice8_core(data, len, crc, ultracrc_crc32c_t0, ultracrc_crc32c_s) : crc;
}
#endif

#if defined(__AVX512F__) && defined(__VPCLMULQDQ__)
__attribute__((target("avx512f,avx512dq,avx512bw,avx512vl,vpclmulqdq")))
static __hot uint32_t ultracrc32c_vpclmul_impl(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 512) return ultracrc32c_pclmul_impl(data, len, init);
    /* 4-way 512-bit folding loop for maximum throughput */
    __m512i k = _mm512_set_epi32(0,(int)0xDDC0152BU,0,(int)0x1C291D04U,0,(int)0xDDC0152BU,0,(int)0x1C291D04U,
                                  0,(int)0xDDC0152BU,0,(int)0x1C291D04U,0,(int)0xDDC0152BU,0,(int)0x1C291D04U);
    __m512i a0 = _mm512_loadu_si512((const __m512i *)(data + 0));
    __m512i a1 = _mm512_loadu_si512((const __m512i *)(data + 64));
    __m512i a2 = _mm512_loadu_si512((const __m512i *)(data + 128));
    __m512i a3 = _mm512_loadu_si512((const __m512i *)(data + 192));
    a0 = _mm512_xor_si512(a0, _mm512_zextsi128_si512(_mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU))));
    data += 256; len -= 256;
    while (len >= 256) {
        a0 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a0,k,0x00), _mm512_clmulepi64_epi128(a0,k,0x11),
                                       _mm512_loadu_si512((const __m512i *)data), 0x96);
        a1 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a1,k,0x00), _mm512_clmulepi64_epi128(a1,k,0x11),
                                       _mm512_loadu_si512((const __m512i *)(data+64)), 0x96);
        a2 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a2,k,0x00), _mm512_clmulepi64_epi128(a2,k,0x11),
                                       _mm512_loadu_si512((const __m512i *)(data+128)), 0x96);
        a3 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a3,k,0x00), _mm512_clmulepi64_epi128(a3,k,0x11),
                                       _mm512_loadu_si512((const __m512i *)(data+192)), 0x96);
        data += 256; len -= 256;
    }
    /* Reduce 4x512-bit to 1x128-bit */
    __m256i b0 = _mm256_xor_si256(_mm512_castsi512_si256(a0), _mm512_extracti64x4_epi64(a0,1));
    __m256i b1 = _mm256_xor_si256(_mm512_castsi512_si256(a1), _mm512_extracti64x4_epi64(a1,1));
    __m256i b2 = _mm256_xor_si256(_mm512_castsi512_si256(a2), _mm512_extracti64x4_epi64(a2,1));
    __m256i b3 = _mm256_xor_si256(_mm512_castsi512_si256(a3), _mm512_extracti64x4_epi64(a3,1));
    __m128i acc = _mm_xor_si128(_mm256_castsi256_si128(_mm256_xor_si256(_mm256_xor_si256(b0,b1),_mm256_xor_si256(b2,b3))),
                                _mm256_extracti128_si256(_mm256_xor_si256(_mm256_xor_si256(b0,b1),_mm256_xor_si256(b2,b3)),1));
    /* Final Barrett reduction */
    __m128i mu = _mm_set_epi32(0,1,0,(int)ultracrc_barrett_crc32c[0]);
    __m128i poly = _mm_set_epi32(0,1,0,(int)ultracrc_barrett_crc32c[1]);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, _mm_clmulepi64_si128(_mm_clmulepi64_si128(acc, mu, 0x10), poly, 0x00)), 1) ^ 0xFFFFFFFFU;
    return len ? ultracrc32c_sw(data, len, crc) : crc;
}
#endif

static int x86_has_pclmul(void) { unsigned a,b,c,d; return __get_cpuid(1, &a, &b, &c, &d) && ((c>>1)&1); }
static int x86_has_avx512(void) {
    unsigned a,b,c,d,eax,edx;
    if(!__get_cpuid_count(7,0, &a, &b, &c, &d)) return 0;
    asm volatile("xgetbv" : "=a"(eax), "=d"(edx) : "c"(0));
    return ((eax&0xE6)==0xE6) && ((c>>10)&1) && ((d>>11)&1);
}
#endif

/* ──────────────────────────────────────────────────────────────────────
§3  ARM64/32 ACCELERATED PATHS (CRC32 / PMULL / UNO-Q Optimized)
────────────────────────────────────────────────────────────────────── */
#if defined(__aarch64__) || defined(__arm__)
#include <arm_acle.h>

#if defined(__ARM_FEATURE_CRC32)
__attribute__((target("+crc")))
static __hot uint32_t ultracrc32_arm3way_impl(const uint8_t *data, size_t len, uint32_t init) {
    /* L1D-aware interleaving: detect cache size at runtime */
    static size_t l1d_chunk = 0;
    if (unlikely(!l1d_chunk)) {
        long sz = sysconf(_SC_LEVEL1_DCACHE_SIZE);
        l1d_chunk = (sz > 0) ? (size_t)(sz / 3) & ~63U : 1024;
    }
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len >= 3 * l1d_chunk) {
        uint32_t a=crc, b=0, c=0;
        for (size_t i=0; i<l1d_chunk; i+=8) {
            uint64_t w0,w1,w2;
            memcpy(&w0,data+i,8); memcpy(&w1,data+i+l1d_chunk,8); memcpy(&w2,data+i+2*l1d_chunk,8);
            a = __crc32d(a, w0); b = __crc32d(b, w1); c = __crc32d(c, w2);
        }
        crc = a^b^c; data += 3*l1d_chunk; len -= 3*l1d_chunk;
    }
    while (len >= 8) { uint64_t w; memcpy(&w,data,8); crc = __crc32d(crc,w); data+=8; len-=8; }
    while (len--) crc = __crc32b(crc, *data++);
    return crc ^ 0xFFFFFFFFU;
}

__attribute__((target("+crc")))
static __hot uint32_t ultracrc32c_arm3way_impl(const uint8_t *data, size_t len, uint32_t init) {
    static size_t l1d_chunk = 0;
    if (unlikely(!l1d_chunk)) {
        long sz = sysconf(_SC_LEVEL1_DCACHE_SIZE);
        l1d_chunk = (sz > 0) ? (size_t)(sz / 3) & ~63U : 1024;
    }
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len >= 3 * l1d_chunk) {
        uint32_t a=crc, b=0, c=0;
        for (size_t i=0; i<l1d_chunk; i+=8) {
            uint64_t w0,w1,w2;
            memcpy(&w0,data+i,8); memcpy(&w1,data+i+l1d_chunk,8); memcpy(&w2,data+i+2*l1d_chunk,8);
            a = __crc32cd(a, w0); b = __crc32cd(b, w1); c = __crc32cd(c, w2);
        }
        crc = a^b^c; data += 3*l1d_chunk; len -= 3*l1d_chunk;
    }
    while (len >= 8) { uint64_t w; memcpy(&w,data,8); crc = __crc32cd(crc,w); data+=8; len-=8; }
    while (len--) crc = __crc32cb(crc,*data++);
    return crc ^ 0xFFFFFFFFU;
}
#endif

#if defined(__aarch64__) && defined(__ARM_FEATURE_CRYPTO)
#include <arm_neon.h>
__attribute__((target("+crc+crypto")))
static __hot uint32_t ultracrc32_arm_pmull_impl(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return ultracrc32_sw(data, len, init);
    uint64x2_t k = vcombine_u64(vcreate_u64(0x154442BDULL), vcreate_u64(0x1C6E4156ULL));
    uint8x16_t acc = vld1q_u8(data);
    uint32x4_t v = vreinterpretq_u32_u8(acc);
    v = vsetq_lane_u32(vgetq_lane_u32(v,0)^(init^0xFFFFFFFFU), v, 0);
    acc = vreinterpretq_u8_u32(v);
    data += 16; len -= 16;
    while (len >= 16) {
        uint8x16_t blk = vld1q_u8(data);
        poly128_t lo = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(k,0));
        poly128_t hi = vmull_high_p64(vreinterpretq_p64_u8(acc), vreinterpretq_p64_u64(k));
        acc = veorq_u8(veorq_u8(vreinterpretq_u8_p128(lo), vreinterpretq_u8_p128(hi)), blk);
        data += 16; len -= 16;
    }
    /* Barrett reduction */
    uint64x2_t bar = vcombine_u64(vcreate_u64(ultracrc_barrett_crc32[0]), vcreate_u64(ultracrc_barrett_crc32[1]));
    poly128_t t0 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(bar,0));
    poly128_t t1 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_p128(t0),0), (poly64_t)vgetq_lane_u64(bar,1));
    uint32_t crc = vgetq_lane_u32(veorq_u32(vreinterpretq_u32_u8(acc), vreinterpretq_u32_p128(t1)), 1) ^ 0xFFFFFFFFU;
    return len ? ultracrc32_sw(data, len, crc) : crc;
}

__attribute__((target("+crc+crypto")))
static __hot uint64_t ultracrc64_arm_pmull_impl(const uint8_t *data, size_t len, uint64_t init) {
    if (len < 64) return ultracrc64_sw(data, len, init);
    /* CRC64-ECMA with PMULL: 2x128-bit folding */
    uint64x2_t k = vcombine_u64(vcreate_u64(0xD8DC243484216F41ULL), vcreate_u64(0x0000000000000001ULL));
    uint8x16_t acc = vld1q_u8(data);
    uint64x2_t v = vreinterpretq_u64_u8(acc);
    v = vsetq_lane_u64(vgetq_lane_u64(v,0)^(init^0xFFFFFFFFFFFFFFFFULL), v, 0);
    acc = vreinterpretq_u8_u64(v);
    data += 16; len -= 16;
    while (len >= 16) {
        uint8x16_t blk = vld1q_u8(data);
        poly128_t lo = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(k,1));
        poly128_t hi = vmull_high_p64(vreinterpretq_p64_u8(acc), vreinterpretq_p64_u64(k));
        acc = veorq_u8(veorq_u8(vreinterpretq_u8_p128(lo), vreinterpretq_u8_p128(hi)), blk);
        data += 16; len -= 16;
    }
    /* Final reduction for CRC64 */
    uint64x2_t bar = vcombine_u64(vcreate_u64(ultracrc_barrett_crc64[0]), vcreate_u64(ultracrc_barrett_crc64[1]));
    poly128_t t0 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(bar,1));
    poly128_t t1 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_p128(t0),0), (poly64_t)vgetq_lane_u64(bar,0));
    uint64_t crc = vgetq_lane_u64(veorq_u64(vreinterpretq_u64_u8(acc), vreinterpretq_u64_p128(t1)), 1) ^ 0xFFFFFFFFFFFFFFFFULL;
    return len ? ultracrc64_sw(data, len, crc) : crc;
}
#endif

/* ─── Arduino UNO Q (Qualcomm QRB2210) Optimized Path ────────────────── */
/* QRB2210: ARMv8.2-A + CRC + PMULL + SHA3 (EOR3) - optimize for low jitter */
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32) && defined(__ARM_FEATURE_CRYPTO)
__attribute__((target("+crc+crypto+sha3")))
static __hot uint32_t ultracrc32_unoq_opt_impl(const uint8_t *data, size_t len, uint32_t init) {
    /* UNO-Q specific: smaller L1D (32KB), prioritize deterministic latency */
    const size_t chunk = 512; /* Conservative for RT determinism */
    uint32_t crc = init ^ 0xFFFFFFFFU;
    /* Prefetch adaptive: only for large buffers */
    if (len > 8192) __builtin_prefetch(data + 2048, 0, 1);
    while (len >= 3 * chunk) {
        uint32_t a=crc, b=0, c=0;
        for (size_t i=0; i<chunk; i+=8) {
            uint64_t w0,w1,w2;
            memcpy(&w0,data+i,8); memcpy(&w1,data+i+chunk,8); memcpy(&w2,data+i+2*chunk,8);
            a = __crc32d(a, w0); b = __crc32d(b, w1); c = __crc32d(c, w2);
        }
        crc = a^b^c; data += 3*chunk; len -= 3*chunk;
    }
    while (len >= 8) { uint64_t w; memcpy(&w,data,8); crc = __crc32d(crc,w); data+=8; len-=8; }
    while (len--) crc = __crc32b(crc, *data++);
    return crc ^ 0xFFFFFFFFU;
}
#endif
#endif

/* ──────────────────────────────────────────────────────────────────────
§4  RISC-V Zbc ACCELERATED PATHS
────────────────────────────────────────────────────────────────────── */
#if defined(__riscv) && defined(__riscv_zbc)
#include <riscv-crypto.h>

__attribute__((target("+zbc")))
static __hot uint32_t ultracrc32_riscv_zbc_impl(const uint8_t *data, size_t len, uint32_t init) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len >= 8) {
        uint64_t w; memcpy(&w, data, 8);
        crc = __riscv_crc32_d(crc, w);
        data += 8; len -= 8;
    }
    while (len--) crc = __riscv_crc32_b(crc, *data++);
    return crc ^ 0xFFFFFFFFU;
}

__attribute__((target("+zbc")))
static __hot uint32_t ultracrc32c_riscv_zbc_impl(const uint8_t *data, size_t len, uint32_t init) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len >= 8) {
        uint64_t w; memcpy(&w, data, 8);
        crc = __riscv_crc32cd(crc, w);
        data += 8; len -= 8;
    }
    while (len--) crc = __riscv_crc32cb(crc, *data++);
    return crc ^ 0xFFFFFFFFU;
}
#endif

/* ──────────────────────────────────────────────────────────────────────
§5  DUAL-KEY DISPATCH MATRIX [POLYNOMIAL][ARCHITECTURE]
────────────────────────────────────────────────────────────────────── */
typedef uint8_t  (fn_crc8_t) (const uint8_t*, size_t, uint8_t);
typedef uint16_t (fn_crc16_t)(const uint8_t*, size_t, uint16_t);
typedef uint32_t (fn_crc32_t)(const uint8_t*, size_t, uint32_t);
typedef uint64_t (fn_crc64_t)(const uint8_t*, size_t, uint64_t);

static struct __aligned(ULTRACRC_CACHE_LINE_SIZE) {
    fn_crc8_t  *f8[ULTRACRC_ARCH_UNOQ_OPT+1];
    fn_crc16_t *f16[ULTRACRC_ARCH_UNOQ_OPT+1];
    fn_crc32_t *f32[ULTRACRC_ARCH_UNOQ_OPT+1];
    fn_crc32_t *f32c[ULTRACRC_ARCH_UNOQ_OPT+1];
    fn_crc64_t *f64[ULTRACRC_ARCH_UNOQ_OPT+1];
} g_dispatch;

static atomic_int g_override = -1, g_warmed = 0;

static void init_dispatch(void) {
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    
    /* Initialize all to software fallbacks */
    for (int a = 0; a <= ULTRACRC_ARCH_UNOQ_OPT; a++) {
        g_dispatch.f8[a]  = ultracrc8_sw;
        g_dispatch.f16[a] = ultracrc16_sw;
        g_dispatch.f32[a] = ultracrc32_sw;
        g_dispatch.f32c[a]= ultracrc32c_sw;
        g_dispatch.f64[a] = ultracrc64_sw;
    }
    
    /* Override with hardware-accelerated implementations per architecture */
    switch (arch) {
#if defined(__x86_64__) || defined(__i386__)
#if defined(__PCLMUL__) || defined(__SSE4_2__)
        case ULTRACRC_ARCH_X86_VPCLMUL:
            g_dispatch.f16[ULTRACRC_ARCH_X86_VPCLMUL] = ultracrc16_pclmul_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_X86_VPCLMUL] = ultracrc32c_vpclmul_impl;
            /* fallthrough */
        case ULTRACRC_ARCH_X86_PCLMUL:
            g_dispatch.f16[ULTRACRC_ARCH_X86_PCLMUL] = ultracrc16_pclmul_impl;
            g_dispatch.f32[ULTRACRC_ARCH_X86_PCLMUL] = ultracrc32_pclmul_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_X86_PCLMUL] = ultracrc32c_pclmul_impl;
            break;
#endif
#endif
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32) && defined(__ARM_FEATURE_CRYPTO)
        case ULTRACRC_ARCH_UNOQ_OPT:
            g_dispatch.f32[ULTRACRC_ARCH_UNOQ_OPT] = ultracrc32_unoq_opt_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_UNOQ_OPT] = ultracrc32c_arm3way_impl;
            g_dispatch.f64[ULTRACRC_ARCH_UNOQ_OPT] = ultracrc64_arm_pmull_impl;
            /* fallthrough */
        case ULTRACRC_ARCH_ARMV8_PMULL_EOR3:
        case ULTRACRC_ARCH_ARMV8_PMULL:
            g_dispatch.f32[ULTRACRC_ARCH_ARMV8_PMULL] = ultracrc32_arm_pmull_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_ARMV8_PMULL] = ultracrc32c_arm3way_impl;
            g_dispatch.f64[ULTRACRC_ARCH_ARMV8_PMULL] = ultracrc64_arm_pmull_impl;
            break;
#endif
#if defined(__aarch64__) || defined(__arm__)
#if defined(__ARM_FEATURE_CRC32)
        case ULTRACRC_ARCH_ARMV8_CRC:
            g_dispatch.f32[ULTRACRC_ARCH_ARMV8_CRC] = ultracrc32_arm3way_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_ARMV8_CRC] = ultracrc32c_arm3way_impl;
            break;
#endif
#endif
#if defined(__riscv) && defined(__riscv_zbc)
        case ULTRACRC_ARCH_RISCV_ZBC:
            g_dispatch.f32[ULTRACRC_ARCH_RISCV_ZBC] = ultracrc32_riscv_zbc_impl;
            g_dispatch.f32c[ULTRACRC_ARCH_RISCV_ZBC] = ultracrc32c_riscv_zbc_impl;
            break;
#endif
        default: break;
    }
    barrier();
    atomic_store(&g_warmed, 1);
}

__visible void ultracrc_warmup(void) {
    if (unlikely(atomic_load(&g_warmed))) return;
    ultracrc_tables_build_crc8();
    ultracrc_tables_build_crc16();
    ultracrc_tables_build_crc32();
    ultracrc_tables_build_crc32c();
    ultracrc_tables_build_crc64();
    init_dispatch();
}

/* ──────────────────────────────────────────────────────────────────────
§6  PUBLIC API (Zero-Branch Hot-Path)
────────────────────────────────────────────────────────────────────── */
__visible uint8_t ultracrc8_compute(const uint8_t *d, size_t n, uint8_t i) {
    if (unlikely(!atomic_load(&g_warmed))) ultracrc_warmup();
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    return g_dispatch.f8[arch](d, n, i);
}

__visible uint16_t ultracrc16_ccitt_compute(const uint8_t *d, size_t n, uint16_t i) {
    if (unlikely(!atomic_load(&g_warmed))) ultracrc_warmup();
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    return g_dispatch.f16[arch](d, n, i);
}

__visible uint32_t ultracrc32_compute(const uint8_t *d, size_t n, uint32_t i) {
    if (unlikely(!atomic_load(&g_warmed))) ultracrc_warmup();
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    return g_dispatch.f32[arch](d, n, i);
}

__visible uint32_t ultracrc32c_compute(const uint8_t *d, size_t n, uint32_t i) {
    if (unlikely(!atomic_load(&g_warmed))) ultracrc_warmup();
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    return g_dispatch.f32c[arch](d, n, i);
}

__visible uint64_t ultracrc64_ecma_compute(const uint8_t *d, size_t n, uint64_t i) {
    if (unlikely(!atomic_load(&g_warmed))) ultracrc_warmup();
    ultracrc_arch_t arch = (atomic_load(&g_override) >= 0) ? (ultracrc_arch_t)atomic_load(&g_override) : ultracrc_detect_arch();
    return g_dispatch.f64[arch](d, n, i);
}

/* ──────────────────────────────────────────────────────────────────────
§7  REAL-TIME UTILITIES
────────────────────────────────────────────────────────────────────── */
__visible int ultracrc_lock_memory(void) {
    return mlockall(MCL_CURRENT | MCL_FUTURE) == 0 ? 0 : -errno;
}

__visible int ultracrc_set_rt_scheduler(int p) {
    if (p < 1 || p > 99) { errno = EINVAL; return -1; }
    struct sched_param s = {.sched_priority = p};
    return sched_setscheduler(0, SCHED_FIFO, &s);
}

__visible int ultracrc_pin_to_cpu(int c) {
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(c, &set);
    return sched_setaffinity(0, sizeof(set), &set);
}

__visible void *ultracrc_alloc_aligned(size_t sz, size_t al) {
    if (al < ULTRACRC_CACHE_LINE_SIZE) al = ULTRACRC_CACHE_LINE_SIZE;
    sz = (sz + al - 1) & ~(al - 1);
    void *p;
    if (posix_memalign(&p, al, sz)) return NULL;
    memset(p, 0, sz);
    return p;
}

__visible void ultracrc_prefetch_adaptive(const void *ptr, size_t len) {
    if (!ptr || !len) return;
    /* Adaptive strategy: small buffers skip prefetch, large use aggressive */
    if (len < 256) return;
    const char *p = ptr, *end = p + len;
    size_t stride = (len > 131072) ? 128 : 64; /* 2x cache-line for large buffers */
    for (; p + stride <= end; p += stride)
        __builtin_prefetch(p, 0, 3);
}

/* ──────────────────────────────────────────────────────────────────────
§8  DIAGNOSTICS & BENCHMARK
────────────────────────────────────────────────────────────────────── */
__visible ultracrc_arch_t ultracrc_detect_arch(void) {
    int ov = atomic_load(&g_override);
    if (ov >= 0) return (ultracrc_arch_t)ov;
    
#if defined(__x86_64__) || defined(__i386__)
    if (x86_has_avx512()) return ULTRACRC_ARCH_X86_VPCLMUL;
    if (x86_has_pclmul()) return ULTRACRC_ARCH_X86_PCLMUL;
    return ULTRACRC_ARCH_GENERIC;
#elif defined(__aarch64__)
    /* Detect Arduino UNO Q (Qualcomm QRB2210) via CPU part number */
    unsigned long midr;
    asm volatile("mrs %0, midr_el1" : "=r"(midr));
    if ((midr & 0xFFF0) == 0x5100) { /* Qualcomm vendor ID */
        return ULTRACRC_ARCH_UNOQ_OPT;
    }
    if (__builtin_cpu_supports("crc32") && __builtin_cpu_supports("pmull")) {
        if (__builtin_cpu_supports("sha3")) return ULTRACRC_ARCH_ARMV8_PMULL_EOR3;
        return ULTRACRC_ARCH_ARMV8_PMULL;
    }
    if (__builtin_cpu_supports("crc32")) return ULTRACRC_ARCH_ARMV8_CRC;
    return ULTRACRC_ARCH_GENERIC;
#elif defined(__arm__) && defined(__ARM_FEATURE_CRC32)
    return ULTRACRC_ARCH_ARMV8_CRC;
#elif defined(__riscv) && defined(__riscv_zbc)
    return ULTRACRC_ARCH_RISCV_ZBC;
#else
    return ULTRACRC_ARCH_GENERIC;
#endif
}

__visible void ultracrc_set_override_arch(ultracrc_arch_t a) {
    atomic_store(&g_override, (int)a);
    atomic_store(&g_warmed, 0);
}

__visible const char *ultracrc_arch_name(ultracrc_arch_t a) {
    static const char *names[] = {
        "generic/slice-by-N", "x86 SSE4.2", "x86 PCLMUL", "x86 VPCLMUL",
        "ARM CRC32", "ARM PMULL", "ARM PMULL+EOR3", "RISC-V Zbc", "UNO-Q Opt"
    };
    return (a >= 0 && a <= ULTRACRC_ARCH_UNOQ_OPT) ? names[a] : "unknown";
}

__visible ultracrc_bench_result_t ultracrc_run_benchmark(size_t buf_sz, uint64_t iters) {
    ultracrc_warmup();
    uint8_t *buf = ultracrc_alloc_aligned(buf_sz, ULTRACRC_CACHE_LINE_SIZE);
    if (!buf) return (ultracrc_bench_result_t){0};
    memset(buf, 0xAA, buf_sz);
    struct timespec t0, t1;
    
    double mb_s(double elapsed_ns, double bytes) { return (bytes / 1e6) / (elapsed_ns / 1e9); }
    ultracrc_bench_result_t res = {.buf_size=buf_sz, .iterations=iters, .arch_tier=ultracrc_detect_arch()};

#define BENCH(fn_name, init_val, res_field) \
    clock_gettime(CLOCK_MONOTONIC, &t0); \
    for (uint64_t i=0; i<iters; i++) fn_name(buf, buf_sz, init_val); \
    clock_gettime(CLOCK_MONOTONIC, &t1); \
    res.res_field = mb_s((t1.tv_sec-t0.tv_sec)*1e9 + (t1.tv_nsec-t0.tv_nsec), (double)(buf_sz*iters));

    BENCH(ultracrc8_compute, 0, crc8_mbs);
    BENCH(ultracrc16_ccitt_compute, 0x1D0F, crc16_mbs);
    BENCH(ultracrc32_compute, 0, crc32_mbs);
    BENCH(ultracrc32c_compute, 0, crc32c_mbs);
    BENCH(ultracrc64_ecma_compute, 0xFFFFFFFFFFFFFFFFULL, crc64_mbs);
#undef BENCH

    free(buf);
    return res;
}
