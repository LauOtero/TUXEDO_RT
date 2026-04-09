#!/usr/bin/env python3
"""
Benchmark de mathutil.py para identificar funciones candidatas a migración C.
Ejecutar: python3 tests/benchmark_mathutil.py
"""
import sys
import os
import time
import cProfile
import pstats
from io import StringIO

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

def benchmark_stats_functions():
    """Benchmark de funciones estadísticas."""
    print("=" * 60)
    print("Benchmark: stats_variance y stats_stddev")
    print("=" * 60)

    try:
        from mathutil import stats_variance, stats_stddev, stats_mean

        data = [float(i) / 100.0 for i in range(1000)]

        ITERATIONS = 10000

        start = time.time()
        for _ in range(ITERATIONS):
            mean = stats_mean(data)
            var = stats_variance(data)
            std = stats_stddev(data)
        elapsed = time.time() - start

        print(f"  {ITERATIONS:,} iteraciones completadas")
        print(f"  Tiempo total: {elapsed:.5f} s")
        print(f"  Tiempo por iteración: {elapsed/ITERATIONS*1e6:.2f} µs")
        print(f"  Mean: {mean:.4f}, Variance: {var:.4f}, StdDev: {std:.4f}")

        if elapsed / ITERATIONS < 0.01:
            print("  ✓ Rendimiento aceptable para hot-path")
        else:
            print("  ⚠️ Considerar migración a C si profiling indica bottleneck")

        return elapsed / ITERATIONS

    except Exception as e:
        print(f"Error: {e}")
        return None

def profile_mathutil():
    """Profile las funciones de mathutil."""
    print("\n" + "=" * 60)
    print("Profiling: cProfile de mathutil")
    print("=" * 60)

    try:
        from mathutil import (
            stats_variance, stats_stddev, stats_mean,
            coordinate_descent, trilateration,
            quat_from_axis_angle, quat_mul, quat_rotate
        )

        data = [float(i) / 100.0 for i in range(1000)]

        profiler = cProfile.Profile()
        profiler.enable()

        for _ in range(1000):
            stats_mean(data)
            stats_variance(data)
            stats_stddev(data)

            sphere_coords = [[0, 0, 0], [1, 0, 0], [0.5, 0.866, 0]]
            radius2 = [0.1, 0.1, 0.1]
            trilateration(sphere_coords, radius2)

            axis = [0, 0, 1]
            quat = quat_from_axis_angle(axis, 1.5708)
            quat2 = quat_from_axis_angle([1, 0, 0], 0.7854)
            quat_mul(quat, quat2)
            quat_rotate(quat, [1, 0, 0])

        profiler.disable()

        s = StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(20)

        print(s.getvalue())

    except Exception as e:
        print(f"Error en profiling: {e}")

def benchmark_trilateration():
    """Benchmark de trilateración."""
    print("\n" + "=" * 60)
    print("Benchmark: trilateration")
    print("=" * 60)

    try:
        from mathutil import trilateration

        sphere_coords = [[0, 0, 0], [1, 0, 0], [0.5, 0.866, 0]]
        radius2 = [0.1, 0.1, 0.1]

        ITERATIONS = 5000

        start = time.time()
        for _ in range(ITERATIONS):
            result = trilateration(sphere_coords, radius2)
        elapsed = time.time() - start

        print(f"  {ITERATIONS:,} iteraciones")
        print(f"  Tiempo total: {elapsed:.5f} s")
        print(f"  Tiempo por iteración: {elapsed/ITERATIONS*1e6:.2f} µs")
        print(f"  Resultado: {result}")

        return elapsed / ITERATIONS

    except Exception as e:
        print(f"Error: {e}")
        return None

def benchmark_quaternions():
    """Benchmark de operaciones de cuaterniones."""
    print("\n" + "=" * 60)
    print("Benchmark: Quaternion Operations")
    print("=" * 60)

    try:
        from mathutil import quat_from_axis_angle, quat_mul, quat_rotate

        ITERATIONS = 10000

        start = time.time()
        for i in range(ITERATIONS):
            axis = [0, 0, 1]
            angle = 1.5708 + (i % 100) * 0.01
            quat = quat_from_axis_angle(axis, angle)
            quat2 = quat_from_axis_angle([1, 0, 0], 0.7854)
            result = quat_mul(quat, quat2)
            vec = quat_rotate(result, [1, 0, 0])
        elapsed = time.time() - start

        print(f"  {ITERATIONS:,} iteraciones")
        print(f"  Tiempo total: {elapsed:.5f} s")
        print(f"  Tiempo por iteración: {elapsed/ITERATIONS*1e6:.2f} µs")

        return elapsed / ITERATIONS

    except Exception as e:
        print(f"Error: {e}")
        return None

def main():
    print("=" * 60)
    print("TUXEDO_RT Mathutil Benchmark Suite")
    print("=" * 60)

    results = {}

    print("\n[1/4] Funciones estadísticas...")
    results['stats'] = benchmark_stats_functions()

    print("\n[2/4] Profiling detallado...")
    profile_mathutil()

    print("\n[3/4] Trilateración...")
    results['trilateration'] = benchmark_trilateration()

    print("\n[4/4] Cuaterniones...")
    results['quaternions'] = benchmark_quaternions()

    print("\n" + "=" * 60)
    print("RESUMEN DE RESULTADOS")
    print("=" * 60)
    for name, time_val in results.items():
        if time_val is not None:
            print(f"  {name}: {time_val*1e6:.2f} µs por llamada")

    print("\n" + "=" * 60)
    print("Recomendaciones:")
    print("  - stats_variance/stddev: Candidatas a migración C si >5% CPU")
    print("  - trilateration: No migrar (específica de cinemáticas)")
    print("  - quaternions: No migrar (específica de cinemáticas)")
    print("=" * 60)

if __name__ == '__main__':
    main()
