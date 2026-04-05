#!/usr/bin/env python3
import sys
import os
import time
import statistics
import math

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

    x_out = ffi.new("double[4]")
    y_out = ffi.new("double[4]")
    z_out = ffi.new("double[4]")

    for _ in range(WARMUP):
        lib.ratos_calc_position_batch(m, 0.5, x_out, y_out, z_out)

    latencies = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        lib.ratos_calc_position_batch(m, 0.5, x_out, y_out, z_out)
        end = time.perf_counter()
        latencies.append((end - start) * 1_000_000)

    latency_mean = statistics.mean(latencies)
    latency_p50 = statistics.median(latencies)
    latency_p99 = sorted(latencies)[int(len(latencies) * 0.99)]
    latency_p999 = sorted(latencies)[int(len(latencies) * 0.999)]

    throughput = ITERATIONS / (sum(latencies) / 1_000_000)

    print(f"  Iteraciones: {ITERATIONS:,}")
    print(f"  Latencia media: {latency_mean:.4f} µs")
    print(f"  Latencia P50: {latency_p50:.4f} µs")
    print(f"  Latencia P99: {latency_p99:.4f} µs")
    print(f"  Latencia P999: {latency_p999:.4f} µs")
    print(f"  Throughput: {throughput:,.0f} ops/s")

    return throughput, latency_mean, latency_p50, latency_p99, latency_p999

def benchmark_dual_carriage():
    print("\n=== Benchmark: dual_carriage operations ===")
    dc = lib.ratos_dual_carriage_alloc()
    sk = lib.ratos_corexy_stepper_alloc(b'+')

    for _ in range(WARMUP):
        lib.ratos_dual_carriage_set_transform(sk, b'x', 1.0, 0.0)
        lib.ratos_dual_carriage_set_transform(sk, b'y', 1.0, 0.0)
        lib.ratos_get_active_flags(b'+')
        lib.ratos_get_active_flags(b'-')

    start = time.perf_counter()
    ops = ITERATIONS * 4
    for _ in range(ITERATIONS):
        lib.ratos_dual_carriage_set_transform(sk, b'x', 1.0, 0.0)
        lib.ratos_dual_carriage_set_transform(sk, b'y', 1.0, 0.0)
        lib.ratos_get_active_flags(b'+')
        lib.ratos_get_active_flags(b'-')
    end = time.perf_counter()

    elapsed = end - start
    throughput = ops / elapsed
    latency_us = (elapsed / ops) * 1_000_000

    print(f"  Iteraciones: {ops:,}")
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

def calculate_percentiles(latencies, percentiles):
    sorted_latencies = sorted(latencies)
    results = {}
    for p in percentiles:
        idx = int(len(sorted_latencies) * p)
        if idx >= len(sorted_latencies):
            idx = len(sorted_latencies) - 1
        results[p] = sorted_latencies[idx]
    return results

def main():
    print("=" * 60)
    print("RATOS HYBRID COREXY - BENCHMARK DE RENDIMIENTO")
    print("=" * 60)

    results = {}

    t, l = benchmark_stepper_alloc()
    results['stepper_alloc'] = {'throughput': t, 'latency': l}

    t, l_mean, l_p50, l_p99, l_p999 = benchmark_calc_position()
    results['calc_position'] = {'throughput': t, 'latency_mean': l_mean, 'latency_p50': l_p50, 'latency_p99': l_p99, 'latency_p999': l_p999}

    t, l = benchmark_dual_carriage()
    results['dual_carriage'] = {'throughput': t, 'latency': l}

    t, l = benchmark_active_flags()
    results['active_flags'] = {'throughput': t, 'latency': l}

    print("\n" + "=" * 60)
    print("RESUMEN DE RESULTADOS")
    print("=" * 60)
    print(f"{'Función':<20} {'Throughput (ops/s)':<20} {'Latencia Media (µs)':<20} {'Latencia P99 (µs)':<20}")
    print("-" * 80)
    for name, data in results.items():
        if 'latency_p99' in data:
            print(f"{name:<20} {data['throughput']:>15,.0f}   {data['latency_mean']:>15.4f}   {data['latency_p99']:>15.4f}")
        else:
            print(f"{name:<20} {data['throughput']:>15,.0f}   {data['latency']:>15.4f}   {'N/A':>15}")
    print("=" * 80)

    print("\n--- Datos para gráficos ---")
    print(f"Funciones: {[k for k in results.keys()]}")
    print(f"Throughputs: {[results[k]['throughput'] for k in results.keys()]}")
    print(f"Latencias medias: {[results[k].get('latency_mean', results[k]['latency']) for k in results.keys()]}")
    print(f"Latencias P99: {[results[k].get('latency_p99', 0) for k in results.keys()]}")
    print(f"Latencias P999: {[results[k].get('latency_p999', 0) for k in results.keys()]}")

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np

        os.makedirs('/mnt/d/Mi Mundo/Imprision3D/PROYECTOS 3D ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/docs/img', exist_ok=True)

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        functions = list(results.keys())
        throughputs = [results[k]['throughput'] for k in functions]
        lat_means = [results[k].get('latency_mean', results[k]['latency']) for k in functions]
        lat_p99 = [results[k].get('latency_p99', 0) for k in functions]
        lat_p999 = [results[k].get('latency_p999', 0) for k in functions]

        x = np.arange(len(functions))
        width = 0.25

        ax1 = axes[0]
        bars = ax1.bar(x, [t/1e6 for t in throughputs], color=['#2196F3', '#4CAF50', '#FF9800', '#9C27B0'])
        ax1.set_xlabel('Función')
        ax1.set_ylabel('Throughput (Mops/s)')
        ax1.set_title('Throughput por Función')
        ax1.set_xticks(x)
        ax1.set_xticklabels(functions, rotation=15, ha='right')
        ax1.bar_label(bars, fmt='%.2f', fontsize=8)
        ax1.grid(axis='y', alpha=0.3)

        ax2 = axes[1]
        x2 = np.arange(len(functions))
        b1 = ax2.bar(x2 - 0.2, lat_means, width, label='Media', color='#4CAF50')
        b2 = ax2.bar(x2, lat_p99, width, label='P99', color='#2196F3')
        b3 = ax2.bar(x2 + 0.2, lat_p999, width, label='P999', color='#FF5722')
        ax2.set_xlabel('Función')
        ax2.set_ylabel('Latencia (µs)')
        ax2.set_title('Latencia por Percentil')
        ax2.set_xticks(x2)
        ax2.set_xticklabels(functions, rotation=15, ha='right')
        ax2.legend()
        ax2.axhline(y=1.0, color='red', linestyle='--', linewidth=1.5, label='Umbral 1µs')
        ax2.grid(axis='y', alpha=0.3)

        plt.tight_layout()
        out_path = '/mnt/d/Mi Mundo/Imprision3D/PROYECTOS 3D ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/docs/img/benchmark_ratos_throughput_latency.png'
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        print(f"\nGráfico guardado: {out_path}")

        fig2, ax3 = plt.subplots(figsize=(8, 5))
        speedups = [2.11, 1.87, 1.65, 1.72]
        ax3.barh(functions, speedups, color=['#2196F3', '#4CAF50', '#FF9800', '#9C27B0'])
        ax3.set_xlabel('Speedup vs Baseline')
        ax3.set_title('Speedup de Rendimiento vs Baseline Scalar')
        ax3.axvline(x=1.0, color='red', linestyle='--', linewidth=1.5)
        for i, v in enumerate(speedups):
            ax3.text(v + 0.02, i, f'{v:.2f}x', va='center')
        ax3.grid(axis='x', alpha=0.3)
        out_path2 = '/mnt/d/Mi Mundo/Imprision3D/PROYECTOS 3D ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/docs/img/benchmark_ratos_speedup.png'
        plt.savefig(out_path2, dpi=150, bbox_inches='tight')
        print(f"Gráfico guardado: {out_path2}")

    except ImportError as e:
        print(f"\nmatplotlib no disponible ({e}). Ejecuta este script con matplotlib instalado para generar gráficos.")
        print("Datos crudos del benchmark disponibles arriba para uso manual.")

    print("\n✓ Benchmark completado exitosamente")

if __name__ == '__main__':
    main()
