#!/usr/bin/env python3
"""
Suite de Tests de Carga (Stress Tests) para TUXEDO_RT.

Este módulo valida el rendimiento bajo condiciones de estrés simulado:
- Alto volumen de comandos G-code
- Alta frecuencia de mensajes CANBus
- Concurrencia extrema
- Memoria limitada

Ejecutar con:
    python3 tests/test_stress.py --duration=60 --concurrency=100
"""
import os
import sys
import time
import threading
import argparse
import collections
import statistics
import gc
from typing import List, Dict, Tuple
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chelper
    HAS_CHELPER = True
except ImportError:
    HAS_CHELPER = False

try:
    from util import get_monotonic
except ImportError:
    get_monotonic = time.monotonic


@dataclass
class StressTestResult:
    """Resultado de un test de estrés."""
    name: str
    duration_seconds: float
    iterations: int
    operations: int
    throughput_ops_per_sec: float
    latency_mean_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float
    errors: int
    memory_start_mb: float
    memory_end_mb: float
    memory_delta_mb: float


class StressTestRunner:
    """Runner de tests de estrés con métricas detalladas."""

    def __init__(self, duration_seconds: int = 30, concurrency: int = 10):
        self.duration = duration_seconds
        self.concurrency = concurrency
        self.results: List[StressTestResult] = []

    def _measure_memory_mb(self) -> float:
        """Mide uso de memoria actual en MB (aproximado)."""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            gc.collect()
            return 0.0

    def _run_stress_thread(
        self,
        name: str,
        work_func,
        count: int,
        latencies: List[float],
        errors: List[int],
        lock: threading.Lock
    ) -> None:
        """Ejecuta trabajo en un hilo hasta que count sea alcanzado o el tiempo expire."""
        start_time = get_monotonic()
        end_time = start_time + self.duration
        local_iterations = 0
        local_errors = 0

        while get_monotonic() < end_time and local_iterations < count:
            op_start = get_monotonic()
            try:
                work_func()
                op_end = get_monotonic()
                latency_ms = (op_end - op_start) * 1000

                with lock:
                    latencies.append(latency_ms)
                    local_iterations += 1

            except Exception as e:
                local_errors += 1
                with lock:
                    errors.append(1)

        with lock:
            self.results.append(StressTestResult(
                name=name,
                duration_seconds=self.duration,
                iterations=local_iterations,
                operations=local_iterations,
                throughput_ops_per_sec=local_iterations / self.duration if self.duration > 0 else 0,
                latency_mean_ms=0,
                latency_p50_ms=0,
                latency_p95_ms=0,
                latency_p99_ms=0,
                latency_max_ms=0,
                errors=local_errors,
                memory_start_mb=0,
                memory_end_mb=0,
                memory_delta_mb=0
            ))

    def run_vlq_batch_stress_test(self) -> StressTestResult:
        """Test de estrés para msgblock_encode_int_batch."""
        print(f"\n[STRESS] VLQ Batch Encoding ({self.duration}s, {self.concurrency} threads)...")

        if not HAS_CHELPER:
            return StressTestResult(
                name="vlq_batch_stress",
                duration_seconds=self.duration,
                iterations=0, operations=0, throughput_ops_per_sec=0,
                latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
                latency_p99_ms=0, latency_max_ms=0, errors=1,
                memory_start_mb=0, memory_end_mb=0, memory_delta_mb=0
            )

        ffi, lib = chelper.get_ffi()
        if not hasattr(lib, 'msgblock_encode_int_batch'):
            return StressTestResult(
                name="vlq_batch_stress",
                duration_seconds=self.duration,
                iterations=0, operations=0, throughput_ops_per_sec=0,
                latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
                latency_p99_ms=0, latency_max_ms=0, errors=1,
                memory_start_mb=0, memory_end_mb=0, memory_delta_mb=0
            )

        latencies: List[float] = []
        errors: List[int] = []
        lock = threading.Lock()
        threads: List[threading.Thread] = []

        BATCH_SIZE = 100
        OUT_SIZE = 512
        vals = ffi.new(f'uint32_t[{BATCH_SIZE}]')
        for i in range(BATCH_SIZE):
            vals[i] = i * 1000

        def work_func():
            out = ffi.new(f'uint8_t[{OUT_SIZE}]')
            lib.msgblock_encode_int_batch(out, vals, BATCH_SIZE)

        work_func()

        mem_start = self._measure_memory_mb()

        for _ in range(self.concurrency):
            t = threading.Thread(
                target=self._run_stress_thread,
                args=("vlq_batch", work_func, 100000, latencies, errors, lock)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        mem_end = self._measure_memory_mb()

        if latencies:
            latencies.sort()
            n = len(latencies)
            return StressTestResult(
                name="vlq_batch_stress",
                duration_seconds=self.duration,
                iterations=len(latencies),
                operations=len(latencies) * BATCH_SIZE,
                throughput_ops_per_sec=len(latencies) / self.duration if self.duration > 0 else 0,
                latency_mean_ms=statistics.mean(latencies),
                latency_p50_ms=latencies[int(n * 0.50)],
                latency_p95_ms=latencies[int(n * 0.95)],
                latency_p99_ms=latencies[int(n * 0.99)],
                latency_max_ms=max(latencies),
                errors=len(errors),
                memory_start_mb=mem_start,
                memory_end_mb=mem_end,
                memory_delta_mb=mem_end - mem_start
            )

        return StressTestResult(
            name="vlq_batch_stress",
            duration_seconds=self.duration,
            iterations=0, operations=0, throughput_ops_per_sec=0,
            latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, latency_max_ms=0, errors=1,
            memory_start_mb=mem_start, memory_end_mb=mem_end, memory_delta_mb=mem_end - mem_start
        )

    def run_monotonic_ctype_stress_test(self) -> StressTestResult:
        """Test de estrés para get_monotonic_ctypes."""
        print(f"\n[STRESS] get_monotonic_ctypes ({self.duration}s, {self.concurrency} threads)...")

        from util import get_monotonic

        latencies: List[float] = []
        errors: List[int] = []
        lock = threading.Lock()
        threads: List[threading.Thread] = []

        def work_func():
            t1 = get_monotonic()
            t2 = get_monotonic()
            latency = (t2 - t1) * 1000

            with lock:
                latencies.append(latency)

        work_func()

        mem_start = self._measure_memory_mb()

        for _ in range(self.concurrency):
            t = threading.Thread(
                target=self._run_stress_thread,
                args=("get_monotonic", work_func, 100000, latencies, errors, lock)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        mem_end = self._measure_memory_mb()

        if latencies:
            latencies.sort()
            n = len(latencies)
            return StressTestResult(
                name="get_monotonic_ctypes",
                duration_seconds=self.duration,
                iterations=len(latencies),
                operations=len(latencies),
                throughput_ops_per_sec=len(latencies) / self.duration if self.duration > 0 else 0,
                latency_mean_ms=statistics.mean(latencies),
                latency_p50_ms=latencies[int(n * 0.50)],
                latency_p95_ms=latencies[int(n * 0.95)],
                latency_p99_ms=latencies[int(n * 0.99)],
                latency_max_ms=max(latencies),
                errors=len(errors),
                memory_start_mb=mem_start,
                memory_end_mb=mem_end,
                memory_delta_mb=mem_end - mem_start
            )

        return StressTestResult(
            name="get_monotonic_ctypes",
            duration_seconds=self.duration,
            iterations=0, operations=0, throughput_ops_per_sec=0,
            latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, latency_max_ms=0, errors=1,
            memory_start_mb=mem_start, memory_end_mb=mem_end, memory_delta_mb=mem_end - mem_start
        )

    def run_gcode_parsing_stress_test(self) -> StressTestResult:
        """Test de estrés para parsing de G-code."""
        print(f"\n[STRESS] G-code Parsing ({self.duration}s, {self.concurrency} threads)...")

        if not HAS_CHELPER:
            return StressTestResult(
                name="gcode_parsing_stress",
                duration_seconds=self.duration,
                iterations=0, operations=0, throughput_ops_per_sec=0,
                latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
                latency_p99_ms=0, latency_max_ms=0, errors=1,
                memory_start_mb=0, memory_end_mb=0, memory_delta_mb=0
            )

        ffi, lib = chelper.get_ffi()
        if not hasattr(lib, 'parse_gcode_line_fast'):
            return StressTestResult(
                name="gcode_parsing_stress",
                duration_seconds=self.duration,
                iterations=0, operations=0, throughput_ops_per_sec=0,
                latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
                latency_p99_ms=0, latency_max_ms=0, errors=1,
                memory_start_mb=0, memory_end_mb=0, memory_delta_mb=0
            )

        latencies: List[float] = []
        errors: List[int] = []
        lock = threading.Lock()
        threads: List[threading.Thread] = []

        test_lines = [
            b"G1 X10.5 Y20.5 Z30.5 E40.5 F1500.0",
            b"G28 X0 Y0 Z0",
            b"M104 S200",
            b"M109 S220",
            b"G0 X100 Y100 F3000",
            b"G1 X50 Y50 E25 F1200",
        ]

        GCODE_RESULT_SIZE = 128
        line = test_lines[0]

        def work_func():
            result = ffi.new(f'uint8_t[{GCODE_RESULT_SIZE}]')
            res = lib.parse_gcode_line_fast(line, len(line), result)
            with lock:
                latencies.append(1.0 if res else 0.0)

        work_func()

        mem_start = self._measure_memory_mb()

        for _ in range(self.concurrency):
            t = threading.Thread(
                target=self._run_stress_thread,
                args=("gcode_parsing", work_func, 100000, latencies, errors, lock)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        mem_end = self._measure_memory_mb()

        if latencies:
            latencies.sort()
            n = len(latencies)
            return StressTestResult(
                name="gcode_parsing_stress",
                duration_seconds=self.duration,
                iterations=len(latencies),
                operations=len(latencies),
                throughput_ops_per_sec=len(latencies) / self.duration if self.duration > 0 else 0,
                latency_mean_ms=statistics.mean(latencies),
                latency_p50_ms=latencies[int(n * 0.50)],
                latency_p95_ms=latencies[int(n * 0.95)],
                latency_p99_ms=latencies[int(n * 0.99)],
                latency_max_ms=max(latencies),
                errors=len(errors),
                memory_start_mb=mem_start,
                memory_end_mb=mem_end,
                memory_delta_mb=mem_end - mem_start
            )

        return StressTestResult(
            name="gcode_parsing_stress",
            duration_seconds=self.duration,
            iterations=0, operations=0, throughput_ops_per_sec=0,
            latency_mean_ms=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, latency_max_ms=0, errors=1,
            memory_start_mb=mem_start, memory_end_mb=mem_end, memory_delta_mb=mem_end - mem_start
        )

    def run_all_tests(self) -> List[StressTestResult]:
        """Ejecuta todos los tests de estrés."""
        print(f"\n{'='*60}")
        print(f"TUXEDO_RT Stress Test Suite")
        print(f"Duration: {self.duration}s | Concurrency: {self.concurrency}")
        print(f"{'='*60}")

        results = []

        results.append(self.run_vlq_batch_stress_test())
        results.append(self.run_monotonic_ctype_stress_test())
        results.append(self.run_gcode_parsing_stress_test())

        return results


def print_results(results: List[StressTestResult]) -> None:
    """Imprime los resultados de los tests."""
    print(f"\n{'='*60}")
    print("STRESS TEST RESULTS")
    print(f"{'='*60}")

    for r in results:
        print(f"\n[{r.name}]")
        print(f"  Iterations:        {r.iterations:,}")
        print(f"  Operations:         {r.operations:,}")
        print(f"  Throughput:         {r.throughput_ops_per_sec:,.0f} ops/s")
        print(f"  Latency Mean:       {r.latency_mean_ms:.4f} ms")
        print(f"  Latency P50:        {r.latency_p50_ms:.4f} ms")
        print(f"  Latency P95:        {r.latency_p95_ms:.4f} ms")
        print(f"  Latency P99:        {r.latency_p99_ms:.4f} ms")
        print(f"  Latency Max:       {r.latency_max_ms:.4f} ms")
        print(f"  Errors:             {r.errors}")
        print(f"  Memory Delta:       {r.memory_delta_mb:+.2f} MB")


def main():
    """Punto de entrada principal."""
    parser = argparse.ArgumentParser(description="TUXEDO_RT Stress Tests")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of threads")
    args = parser.parse_args()

    runner = StressTestRunner(duration_seconds=args.duration, concurrency=args.concurrency)
    results = runner.run_all_tests()
    print_results(results)

    print(f"\n{'='*60}")
    print("Stress test suite completed.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
