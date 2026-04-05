#!/usr/bin/env python3
import sys
import os
import time
import statistics
sys.path.insert(0, '/mnt/d/Mi Mundo/Imprision3D/PROYECTOS 3D ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy')
os.chdir('/mnt/d/Mi Mundo/Imprision3D/PROYECTOS 3D ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy')

from chelper import get_ffi
ffi, lib = get_ffi()

ITERATIONS = 100000
WARMUP = 10000

def alloc_stepper():
    sk = lib.ratos_corexy_stepper_alloc(b'+')
    return sk

def benchmark_stepper_alloc():
    print("\n=== Benchmark: stepper_alloc ===")
    for _ in range(WARMUP):
        alloc_stepper()

    start = time.perf_counter()
    for _ in range(ITERATIONS):
        sk = alloc_stepper()
    end = time.perf_counter()

    elapsed = end - start
    throughput = ITERATIONS / elapsed
    latency_us = (elapsed / ITERATIONS) * 1_000_000

    print(f"  Iteraciones: {ITERATIONS:,}")
    print(f"  Tiempo total: {elapsed:.4f}s")
    print(f"  Throughput: {throughput:,.0f} ops/s")
    print(f"  Latencia media: {latency_us:.4f} µs")
    return throughput, latency_us

def benchmark_calc_position():
    print("\n=== Benchmark: calc_position (CoreXY '+') ===")
    sk = lib.ratos_corexy_stepper_alloc(b'+')

    m = ffi.new("struct move *")
    m.print_time = 0.0
    m.move_t = 1.0
    m.start_v = 0.0
    m.half_accel = 1000.0
    m.start_pos.x = 0.0
    m.start_pos.y = 0.0
    m.start_pos.z = 0.0
    m.axes_r.x = 1.0
    m.axes_r.y = 1.0
    m.axes_r.z = 0.0

    x_out = ffi.new("double *")
    y_out = ffi.new("double *")
    z_out = ffi.new("double *")

    for _ in range(WARMUP):
        lib.ratos_calc_position_batch(m, 0.5, x_out, y_out, z_out)

    latencies = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        lib.ratos_calc_position_batch(m, 0.5, x_out, y_out, z_out)
        end = time.perf_counter()
        latencies.append((end - start) * 1_000_000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    p999 = latencies[int(len(latencies) * 0.999)]
    avg = statistics.mean(latencies)
    throughput = ITERATIONS / sum(l / 1_000_000 for l in latencies)

    print(f"  Iteraciones: {ITERATIONS:,}")
    print(f"  Latencia media: {avg:.4f} µs")
    print(f"  Latencia P50: {p50:.4f} µs")
    print(f"  Latencia P99: {p99:.4f} µs")
    print(f"  Latencia P999: {p999:.4f} µs")
    print(f"  Throughput: {throughput:,.0f} ops/s")
    return throughput, p99

def benchmark_dual_carriage():
    print("\n=== Benchmark: dual_carriage operations ===")
    dc = lib.ratos_dual_carriage_alloc()

    sk = lib.ratos_corexy_stepper_alloc(b'+')
    lib.ratos_dual_carriage_set_sk(dc, sk)

    for _ in range(WARMUP):
        lib.ratos_dual_carriage_set_carriage_state(dc, 0)
        lib.ratos_dual_carriage_get_carriage_state(dc)

    start = time.perf_counter()
    for _ in range(ITERATIONS):
        lib.ratos_dual_carriage_set_carriage_state(dc, 0)
        lib.ratos_dual_carriage_get_carriage_state(dc)
        lib.ratos_dual_carriage_set_carriage_state(dc, 1)
        lib.ratos_dual_carriage_get_carriage_state(dc)
    end = time.perf_counter()

    elapsed = end - start
    total_ops = ITERATIONS * 4
    throughput = total_ops / elapsed
    latency_us = (elapsed / total_ops) * 1_000_000

    print(f"  Iteraciones: {ITERATIONS:,} x 4 ops")
    print(f"  Tiempo total: {elapsed:.4f}s")
    print(f"  Throughput: {throughput:,.0f} ops/s")
    print(f"  Latencia media: {latency_us:.4f} µs")
    return throughput, latency_us

def benchmark_active_flags():
    print("\n=== Benchmark: get_active_flags ===")

    for _ in range(WARMUP):
        lib.ratos_get_active_flags(b'+')

    start = time.perf_counter()
    for _ in range(ITERATIONS * 10):
        lib.ratos_get_active_flags(b'+')
    end = time.perf_counter()

    elapsed = end - start
    total_ops = ITERATIONS * 10
    throughput = total_ops / elapsed
    latency_us = (elapsed / total_ops) * 1_000_000

    print(f"  Iteraciones: {total_ops:,}")
    print(f"  Tiempo total: {elapsed:.4f}s")
    print(f"  Throughput: {throughput:,.0f} ops/s")
    print(f"  Latencia media: {latency_us:.4f} µs")
    return throughput, latency_us

def main():
    print("=" * 60)
    print("RATOS HYBRID COREXY - BENCHMARK DE RENDIMIENTO")
    print("=" * 60)

    results = {}

    t, l = benchmark_stepper_alloc()
    results['stepper_alloc'] = {'throughput': t, 'latency': l}

    t, l = benchmark_calc_position()
    results['calc_position'] = {'throughput': t, 'latency': l}

    t, l = benchmark_dual_carriage()
    results['dual_carriage'] = {'throughput': t, 'latency': l}

    t, l = benchmark_active_flags()
    results['active_flags'] = {'throughput': t, 'latency': l}

    print("\n" + "=" * 60)
    print("RESUMEN DE RESULTADOS")
    print("=" * 60)
    print(f"{'Función':<20} {'Throughput (ops/s)':<20} {'Latencia P99 (µs)':<20}")
    print("-" * 60)
    for name, data in results.items():
        print(f"{name:<20} {data['throughput']:>15,.0f}   {data['latency']:>15.4f}")
    print("=" * 60)

    print("\n✓ Benchmark completado exitosamente")

if __name__ == '__main__':
    main()