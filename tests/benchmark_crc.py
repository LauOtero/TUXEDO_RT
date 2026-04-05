#!/usr/bin/env python3
# Ultra-High-Performance CRC Benchmark Suite
# Integrates with crc_utils warmup, architecture detection, and RT-aligned allocation.
# Copyright (C) 2026 Klipper Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import time
import random
import ctypes

# Ensure klippy/chelper path is available
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))

def run_crc_benchmarks():
    print("=" * 60)
    print("  CRC ULTRA-HIGH-PERFORMANCE BENCHMARK SUITE")
    print("=" * 60)

    # 1. Load C Helper & Verify CRC Utils
    try:
        from chelper import get_ffi
        ffi, lib = get_ffi()
    except Exception as e:
        print(f"[ERROR] Failed to load C helper: {e}")
        print("Ensure GCC is installed and chelper compiles successfully.")
        return

    if not lib or not hasattr(lib, 'crc_utils_warmup'):
        print("[WARN] crc_utils not available. Falling back to Python-only mode.")
        return

    # 2. Warmup & Architecture Detection
    print("\n[INFO] Warming up CRC dispatch tables...")
    lib.crc_utils_warmup()
    
    arch = lib.crc_utils_detect_arch()
    arch_name = ffi.string(lib.crc_utils_arch_name(arch)).decode('utf-8')
    print(f"[INFO] Active Backend : {arch_name} (Tier {arch})")
    print("-" * 60)

    # 3. Prepare RT-Aligned Test Buffers
    # Using crc_utils_alloc_aligned ensures cache-line alignment & page pre-faulting
    buf_sizes = [64, 256, 1500, 4096, 65536]
    buffers = {}
    
    print("[INFO] Allocating aligned test buffers...")
    for sz in buf_sizes:
        ptr_void = lib.crc_utils_alloc_aligned(sz, 64)  # 64B cache-line alignment
        if not ptr_void:
            print(f"[ERROR] Failed to allocate {sz} bytes.")
            continue
        
        # Castear el puntero void* a uint8_t* para que Python pueda indexarlo
        ptr = ffi.cast("uint8_t*", ptr_void)
            
        # Fill with deterministic pseudo-random data (seeded for reproducibility)
        random.seed(42 + sz)
        for i in range(sz):
            ptr[i] = random.randint(0, 255)
        buffers[sz] = ptr_void # Almacenar el puntero original (void*) para free

    if not buffers:
        print("[ABORT] No buffers allocated. Exiting.")
        return

    # 4. Benchmark Core
    def bench_crc(func_name, func, init_val, data_ptr, length, iterations):
        # Warmup run to eliminate cold-cache/page-fault overhead
        func(data_ptr, length, init_val)
        
        start = time.perf_counter()
        for _ in range(iterations):
            func(data_ptr, length, init_val)
        end = time.perf_counter()

        elapsed = end - start
        avg_ns = (elapsed / iterations) * 1e9
        throughput_mbs = (length * iterations) / (elapsed * 1e6)
        return avg_ns, throughput_mbs

    # 5. Test Configuration
    crc_algorithms = [
        ("CRC-8   (Dallas/Maxim)", lib.crc8_compute, 0x00),
        ("CRC-16  (CCITT)",        lib.crc16_ccitt_compute, 0x1D0F),
        ("CRC-32  (IEEE 802.3)",   lib.crc32_compute, 0x00000000),
        ("CRC-32C (Castagnoli)",   lib.crc32c_compute, 0x00000000),
    ]

    # Scale iterations inversely with buffer size to keep total runtime manageable
    iterations_map = {
        64:    1_500_000,
        256:   800_000,
        1500:  200_000,
        4096:  80_000,
        65536: 8_000
    }

    # 6. Execution & Output
    print(f"\n{'Algorithm':<26} | {'Size':<6} | {'Avg Latency':<11} | {'Throughput':<12}")
    print("-" * 60)

    for name, func, init in crc_algorithms:
        for sz, ptr in buffers.items():
            iters = iterations_map.get(sz, 50_000)
            try:
                avg_ns, mbs = bench_crc(name, func, init, ptr, sz, iters)
                
                # Format time dynamically
                if avg_ns < 1000:
                    time_str = f"{avg_ns:.1f} ns"
                else:
                    time_str = f"{avg_ns/1000:.2f} μs"
                    
                print(f"{name:<26} | {sz:<6} | {time_str:<11} | {mbs:>9.1f} MB/s")
            except Exception as e:
                print(f"{name:<26} | {sz:<6} | ERROR: {str(e)[:15]}")

    print("-" * 60)

    # 7. Cleanup Aligned Memory
    print("[INFO] Releasing aligned buffers...")
    for ptr in buffers.values():
        lib.free(ptr)  # Standard free() works with posix_memalign

    # 8. Optional: Override Test (Demonstrates runtime switching)
    print("\n[INFO] Testing forced fallback to GENERIC (slice-by-N)...")
    lib.crc_utils_set_override_arch(0)  # Force CRC_ARCH_GENERIC
    lib.crc_utils_warmup()
    new_arch = ffi.string(lib.crc_utils_arch_name(lib.crc_utils_detect_arch())).decode('utf-8')
    print(f"      Backend switched to: {new_arch}")
    # (You could re-run the benchmark here to compare generic vs HW)
    
    print("\n[OK] Benchmark suite completed successfully.")

if __name__ == '__main__':
    run_crc_benchmarks()