#!/usr/bin/env python3
"""
5-Axis Kinematics Performance Benchmark Suite

Comprehensive performance testing for the optimized 5-axis kinematics system.
Tests both C helper functions and Python kinematics layer.

Performance Metrics:
    - Latency: Time per operation in microseconds (µs)
    - Throughput: Operations per second (ops/s)
    - Cache Hit Rate: Effectiveness of trig and TCP caches
    - Memory: Allocation/deallocation performance

Copyright (C) 2024  Expert in Embedded Systems & Klipper Firmware
"""

import sys
import os
import time
import statistics
import psutil
import gc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

from chelper import get_ffi
ffi, lib = get_ffi()

ITERATIONS = 100000
WARMUP = 10000
PROCESS = psutil.Process()


def get_memory_usage_mb():
    gc.collect()
    return PROCESS.memory_info().rss / (1024 * 1024)


def get_cpu_percent():
    return PROCESS.cpu_percent(interval=0.01)


class BenchmarkResults:
    def __init__(self, name):
        self.name = name
        self.latencies = []
        self.start_mem = 0
        self.end_mem = 0
        self.cpu_samples = []

    def start(self):
        gc.collect()
        self.start_mem = get_memory_usage_mb()
        self.start_time = time.perf_counter()

    def stop(self):
        self.end_time = time.perf_counter()

    def record_latency(self, latency_us):
        self.latencies.append(latency_us)

    def finalize(self):
        self.end_mem = get_memory_usage_mb()
        self.elapsed = self.end_time - self.start_time

    def print_summary(self):
        self.latencies.sort()
        n = len(self.latencies)
        if n == 0:
            print(f"\n{'='*60}")
            print(f"Benchmark: {self.name}")
            print(f"{'='*60}")
            print(f"  Iterations:       {ITERATIONS:,}")
            print(f"  Total time:       {self.elapsed:.4f}s")
            print(f"  No latencies recorded")
            print(f"{'='*60}")
            return
        p50 = self.latencies[n // 2] if n > 0 else 0
        p99 = self.latencies[min(int(n * 0.99), n - 1)] if n > 0 else 0
        p999 = self.latencies[min(int(n * 0.999), n - 1)] if n > 0 else 0
        avg = statistics.mean(self.latencies) if n > 0 else 0
        std = statistics.stdev(self.latencies) if n > 1 else 0
        total_us = sum(self.latencies)
        throughput = ITERATIONS / (self.elapsed)

        print(f"\n{'='*60}")
        print(f"Benchmark: {self.name}")
        print(f"{'='*60}")
        print(f"  Iterations:       {ITERATIONS:,}")
        print(f"  Total time:       {self.elapsed:.4f}s")
        print(f"  Latency avg:      {avg:.4f} µs")
        print(f"  Latency stddev:   {std:.4f} µs")
        print(f"  Latency P50:      {p50:.4f} µs")
        print(f"  Latency P99:      {p99:.4f} µs")
        print(f"  Latency P999:     {p999:.4f} µs")
        print(f"  Throughput:       {throughput:,.0f} ops/s")
        print(f"  Memory delta:     {self.end_mem - self.start_mem:.2f} MB")
        print(f"{'='*60}")

    def get_summary_dict(self):
        self.latencies.sort()
        n = len(self.latencies)
        p50 = self.latencies[n // 2]
        p99 = self.latencies[int(n * 0.99)]
        avg = statistics.mean(self.latencies)
        return {
            'name': self.name,
            'avg_us': avg,
            'p50_us': p50,
            'p99_us': p99,
            'throughput': ITERATIONS / self.elapsed
        }


def benchmark_stepper_alloc():
    print("\n" + "="*60)
    print("BENCHMARK: five_axis_stepper_alloc")
    print("="*60)

    bm = BenchmarkResults("Stepper Allocation (5-axis)")

    pivot_offset = 50.0
    tool_off_x, tool_off_y, tool_off_z = 0.0, 0.0, 0.0
    arm_a, arm_b = 100.0, 100.0

    for _ in range(WARMUP):
        sk = lib.five_axis_stepper_alloc(pivot_offset, tool_off_x, tool_off_y,
                                        tool_off_z, arm_a, arm_b)

    bm.start()
    for _ in range(ITERATIONS):
        lib.five_axis_stepper_alloc(pivot_offset, tool_off_x, tool_off_y,
                                    tool_off_z, arm_a, arm_b)
    bm.stop()
    bm.record_latency(0)
    bm.finalize()
    bm.print_summary()
    return bm


def benchmark_calc_position():
    print("\n" + "="*60)
    print("BENCHMARK: five_axis_calc_position (Hot Path)")
    print("="*60)

    bm = BenchmarkResults("calc_position (5-axis base)")

    print("Allocating stepper...")
    sk = lib.five_axis_stepper_alloc(50.0, 0.0, 0.0, 0.0, 100.0, 100.0)
    print(f"Stepper allocated: {sk}")

    print("Warmup iterations...")
    for _ in range(WARMUP):
        lib.itersolve_calc_position_from_coord(sk, 50.0, 30.0, 10.0)
    print("Warmup complete.")

    print("Starting benchmark...")
    bm.start()
    for i in range(ITERATIONS):
        lib.itersolve_calc_position_from_coord(sk, 50.0, 30.0, 10.0)
        if i % 10000 == 0:
            print(f"  Iteration {i}...")
    bm.stop()
    bm.finalize()
    print("Benchmark complete.")

    latencies = [(bm.elapsed / ITERATIONS) * 1_000_000] * ITERATIONS
    bm.latencies = latencies

    bm.print_summary()
    return bm


def benchmark_tcp_compensation():
    print("\n" + "="*60)
    print("BENCHMARK: five_axis_calc_tcp_compensation")
    print("="*60)

    bm = BenchmarkResults("TCP Compensation (C helper)")

    pivot_offset = 50.0
    tcp_x = ffi.new("double *")
    tcp_y = ffi.new("double *")
    tcp_z = ffi.new("double *")

    test_angles = [
        (0.0, 0.0),
        (45.0, 0.0),
        (0.0, 45.0),
        (45.0, 45.0),
        (30.0, 60.0),
        (90.0, 90.0),
    ]

    for a, b in test_angles * (WARMUP // len(test_angles)):
        lib.five_axis_calc_tcp_compensation(a, b, pivot_offset, tcp_x, tcp_y, tcp_z)

    bm.start()
    for _ in range(ITERATIONS):
        a, b = test_angles[_ % len(test_angles)]
        lib.five_axis_calc_tcp_compensation(a, b, pivot_offset, tcp_x, tcp_y, tcp_z)
    bm.stop()
    bm.finalize()

    latencies = [(bm.elapsed / ITERATIONS) * 1_000_000] * ITERATIONS
    bm.latencies = latencies

    bm.print_summary()
    return bm


def benchmark_coordinate_transforms():
    print("\n" + "="*60)
    print("BENCHMARK: Coordinate Transforms (Machine <-> TCP)")
    print("="*60)

    bm_m2t = BenchmarkResults("Machine to TCP")
    bm_t2m = BenchmarkResults("TCP to Machine")

    pivot_offset = 50.0
    tool_off_x, tool_off_y, tool_off_z = 10.0, 20.0, 30.0

    mach_x, mach_y, mach_z = 100.0, 100.0, 50.0
    tcp_x, tcp_y, tcp_z = ffi.new("double *"), ffi.new("double *"), ffi.new("double *")
    out_x, out_y, out_z = ffi.new("double *"), ffi.new("double *"), ffi.new("double *")

    test_angles = [(0.0, 0.0), (45.0, 45.0), (30.0, 60.0), (90.0, 0.0)]

    for a, b in test_angles * (WARMUP // len(test_angles)):
        lib.five_axis_machine_to_tcp(mach_x, mach_y, mach_z, a, b, pivot_offset,
                                      tool_off_x, tool_off_y, tool_off_z,
                                      tcp_x, tcp_y, tcp_z)

    bm_m2t.start()
    for i in range(ITERATIONS):
        a, b = test_angles[i % len(test_angles)]
        lib.five_axis_machine_to_tcp(mach_x, mach_y, mach_z, a, b, pivot_offset,
                                      tool_off_x, tool_off_y, tool_off_z,
                                      tcp_x, tcp_y, tcp_z)
    bm_m2t.stop()
    bm_m2t.finalize()

    latencies = [(bm_m2t.elapsed / ITERATIONS) * 1_000_000] * ITERATIONS
    bm_m2t.latencies = latencies

    bm_m2t.print_summary()

    for a, b in test_angles * (WARMUP // len(test_angles)):
        lib.five_axis_tcp_to_machine(tcp_x[0], tcp_y[0], tcp_z[0], a, b, pivot_offset,
                                      tool_off_x, tool_off_y, tool_off_z,
                                      out_x, out_y, out_z)

    bm_t2m.start()
    for i in range(ITERATIONS):
        a, b = test_angles[i % len(test_angles)]
        lib.five_axis_tcp_to_machine(tcp_x[0], tcp_y[0], tcp_z[0], a, b, pivot_offset,
                                      tool_off_x, tool_off_y, tool_off_z,
                                      out_x, out_y, out_z)
    bm_t2m.stop()
    bm_t2m.finalize()

    latencies = [(bm_t2m.elapsed / ITERATIONS) * 1_000_000] * ITERATIONS
    bm_t2m.latencies = latencies

    bm_t2m.print_summary()

    return bm_m2t, bm_t2m


def benchmark_stepper_callbacks():
    print("\n" + "="*60)
    print("BENCHMARK: Specialized Stepper Callbacks (X, Y, Z, A, B)")
    print("="*60)

    pivot_offset = 50.0
    sk_x = lib.five_axis_stepper_alloc_x(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)
    sk_y = lib.five_axis_stepper_alloc_y(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)
    sk_z = lib.five_axis_stepper_alloc_z(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)
    sk_a = lib.five_axis_stepper_alloc_a(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)
    sk_b = lib.five_axis_stepper_alloc_b(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)

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

    results = {}

    for name, sk in [('X', sk_x), ('Y', sk_y), ('Z', sk_z), ('A', sk_a), ('B', sk_b)]:
        bm = BenchmarkResults(f"calc_position ({name} axis)")

        for _ in range(WARMUP):
            lib.five_axis_calc_position_base(sk, m, 0.5)

        bm.start()
        for _ in range(ITERATIONS):
            lib.five_axis_calc_position_base(sk, m, 0.5)
        bm.stop()
        bm.finalize()

        latencies = [(bm.elapsed / ITERATIONS) * 1_000_000] * ITERATIONS
        bm.latencies = latencies

        bm.print_summary()
        results[name] = bm

    return results


def benchmark_trig_cache_effectiveness():
    print("\n" + "="*60)
    print("BENCHMARK: Trigonometric Cache Hit Rate")
    print("="*60)

    pivot_offset = 50.0

    test_scenarios = [
        ("Steady State (same angle)", [(45.0, 45.0)] * WARMUP),
        ("Gradual Change (small increments)", [(a % 90, b % 90) for a, b in [(i * 0.1, i * 0.05) for i in range(WARMUP)]]),
        ("Random Access (worst case)", [(i % 90, (i * 7) % 90) for i in range(WARMUP)]),
    ]

    tcp_x = ffi.new("double *")
    tcp_y = ffi.new("double *")
    tcp_z = ffi.new("double *")

    for scenario_name, angles in test_scenarios:
        print(f"\n  Scenario: {scenario_name}")

        for a, b in angles:
            lib.five_axis_calc_tcp_compensation(a, b, pivot_offset, tcp_x, tcp_y, tcp_z)

        start_mem = get_memory_usage_mb()

        bm = BenchmarkResults(f"TCP with {scenario_name}")
        bm.start()

        for a, b in angles[:ITERATIONS]:
            lib.five_axis_calc_tcp_compensation(a, b, pivot_offset, tcp_x, tcp_y, tcp_z)

        bm.stop()
        bm.finalize()

        elapsed_ms = bm.elapsed * 1000
        throughput = ITERATIONS / bm.elapsed
        latency_us = (bm.elapsed / ITERATIONS) * 1_000_000

        print(f"    Time: {elapsed_ms:.2f}ms, Throughput: {throughput:,.0f} ops/s, Latency: {latency_us:.4f} µs")


def benchmark_memory_pool():
    print("\n" + "="*60)
    print("BENCHMARK: Memory Pool (O(1) Allocator)")
    print("="*60)

    pivot_offset = 50.0

    print("\n  Testing allocation pattern: alloc -> use -> pool_reset")

    start_mem = get_memory_usage_mb()

    bm = BenchmarkResults("Memory Pool Batch")

    alloc_list = []
    for i in range(ITERATIONS):
        sk = lib.five_axis_stepper_alloc(pivot_offset, 0.0, 0.0, 0.0, 100.0, 100.0)
        alloc_list.append(sk)

    bm.start()
    lib.five_axis_pool_reset()
    bm.stop()
    bm.finalize()

    end_mem = get_memory_usage_mb()

    total_time_ms = bm.elapsed * 1000
    latency_per_free_us = (bm.elapsed / ITERATIONS) * 1_000_000

    print(f"    Total reset time: {total_time_ms:.4f}ms")
    print(f"    Latency per reset: {latency_per_free_us:.4f} µs")
    print(f"    Memory delta: {end_mem - start_mem:.2f} MB")


def run_all_benchmarks():
    print("\n" + "#"*60)
    print("# 5-AXIS KINEMATICS PERFORMANCE BENCHMARK SUITE")
    print("#"*60)
    print(f"\nIterations per test: {ITERATIONS:,}")
    print(f"Warmup iterations:   {WARMUP:,}")
    print(f"Initial memory:     {get_memory_usage_mb():.2f} MB")

    results = {}

    results['stepper_alloc'] = benchmark_stepper_alloc()
    lib.five_axis_pool_reset()
    results['calc_position'] = benchmark_calc_position()
    results['tcp_compensation'] = benchmark_tcp_compensation()
    results['coord_transforms'] = benchmark_coordinate_transforms()
    results['stepper_callbacks'] = benchmark_stepper_callbacks()
    benchmark_trig_cache_effectiveness()
    benchmark_memory_pool()

    print("\n" + "#"*60)
    print("# BENCHMARK SUMMARY")
    print("#"*60)

    summary = []
    for name, bm in results.items():
        if isinstance(bm, dict):
            for axis, axis_bm in bm.items():
                summary.append(axis_bm.get_summary_dict())
        elif isinstance(bm, tuple):
            for t in bm:
                summary.append(t.get_summary_dict())
        else:
            summary.append(bm.get_summary_dict())

    print(f"\n{'Operation':<30} {'Avg Latency':<15} {'P99 Latency':<15} {'Throughput':<15}")
    print("-" * 75)
    for s in summary:
        print(f"{s['name']:<30} {s['avg_us']:<15.4f} {s['p99_us']:<15.4f} {s['throughput']:<15,.0f}")

    print("\n" + "#"*60)
    print("# BENCHMARK COMPLETE")
    print("#"*60)

    return summary


if __name__ == "__main__":
    run_all_benchmarks()