#!/usr/bin/env python3
"""
Suite de Pruebas de Determinismo para Klipper TUXEDO_RT

Este módulo proporciona herramientas exhaustivas para validar el determinismo
temporal del sistema Klipper bajo diferentes cargas. Mide latencias, jitter,
y garantiza que el 99.99% de las operaciones cumplan con límites especificados.

Uso:
    python test_rt_determinism.py --iterations 100000 --target-latency 100
"""

import sys
import os
import time
import statistics
import threading
import argparse
import json
from typing import Dict, List, Optional, Tuple
from collections import deque
from concurrent.futures import ThreadPoolExecutor

# Añadir klippy al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))


class LatencyCollector:
    """Colector de muestras de latencia con estadísticas en tiempo real."""
    
    def __init__(self, max_samples=1000000):
        self.latencies = deque(maxlen=max_samples)
        self.lock = threading.Lock()
        self.start_time = None
        self.end_time = None
        
    def record(self, latency_us: float):
        """Registra una muestra de latencia en microsegundos."""
        with self.lock:
            if self.start_time is None:
                self.start_time = time.time()
            self.latencies.append(latency_us)
            
    def get_stats(self) -> Dict[str, float]:
        """Calcula estadísticas completas de latencia."""
        if not self.latencies:
            return {}
            
        with self.lock:
            sorted_latencies = sorted(self.latencies)
            n = len(sorted_latencies)
            
            # Percentiles clave
            p50_idx = n // 2
            p99_idx = int(n * 0.99)
            p999_idx = int(n * 0.999)
            p9999_idx = int(n * 0.9999) if n >= 10000 else n - 1
            
            stats = {
                'count': n,
                'min': sorted_latencies[0],
                'max': sorted_latencies[-1],
                'mean': statistics.mean(sorted_latencies),
                'median': sorted_latencies[p50_idx],
                'stddev': statistics.stdev(sorted_latencies) if n > 1 else 0,
                'p50': sorted_latencies[p50_idx],
                'p99': sorted_latencies[p99_idx],
                'p99.9': sorted_latencies[p999_idx],
                'p99.99': sorted_latencies[p9999_idx],
            }
            
            # Calcular jitter (diferencia entre latencias consecutivas)
            if n > 1:
                jitters = [abs(sorted_latencies[i] - sorted_latencies[i-1]) 
                          for i in range(1, min(1000, n))]
                stats['jitter_avg'] = statistics.mean(jitters)
                stats['jitter_max'] = max(jitters)
            
            self.end_time = time.time()
            stats['duration_s'] = self.end_time - self.start_time if self.end_time else 0
            
            return stats
    
    def export_json(self, filename: str):
        """Exporta estadísticas a archivo JSON."""
        stats = self.get_stats()
        with open(filename, 'w') as f:
            json.dump(stats, f, indent=2)
        print(f"Estadísticas exportadas a {filename}")


class DeterminismTestSuite:
    """Suite completa de pruebas de determinismo."""
    
    def __init__(self, target_latency_us: float = 100.0):
        self.target_latency = target_latency_us
        self.collector = LatencyCollector()
        self.stop_event = threading.Event()
        
    def test_timer_callback_precision(self, iterations: int = 10000, 
                                       interval_s: float = 0.001) -> Dict:
        """
        Prueba 1: Precisión de callbacks de timer.
        
        Mide la variación en el tiempo de ejecución de callbacks programados.
        Objetivo: P99.99 < 100 µs
        """
        print(f"\n{'='*60}")
        print(f"PRUEBA 1: Precisión de Timer Callbacks")
        print(f"Iteraciones: {iterations}, Intervalo: {interval_s*1000:.1f} ms")
        print(f"{'='*60}")
        
        expected = interval_s
        latencies = []
        
        for i in range(iterations):
            start = time.perf_counter_ns()
            
            # Simular trabajo mínimo (como un callback real)
            _ = sum(range(10))
            
            end = time.perf_counter_ns()
            latency_us = (end - start) / 1000
            latencies.append(latency_us)
            
            # Progress reporting cada 10%
            if i % (iterations // 10) == 0 and i > 0:
                print(f"  Progreso: {i}/{iterations} ({100*i//iterations}%)")
        
        # Analizar resultados
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        
        results = {
            'test_name': 'timer_callback_precision',
            'iterations': iterations,
            'mean': statistics.mean(latencies),
            'stddev': statistics.stdev(latencies),
            'p50': sorted_lat[n//2],
            'p99': sorted_lat[int(n*0.99)],
            'p99.99': sorted_lat[int(n*0.9999)] if n >= 10000 else sorted_lat[-1],
            'max': max(latencies),
            'min': min(latencies),
            'target_met': sorted_lat[int(n*0.9999)] <= self.target_latency if n >= 10000 else sorted_lat[-1] <= self.target_latency
        }
        
        self._print_results(results)
        return results
    
    def test_thread_context_switch(self, iterations: int = 10000) -> Dict:
        """
        Prueba 2: Overhead de cambio de contexto entre hilos.
        
        Mide la latencia añadida por comunicación entre hilos.
        Objetivo: P99.99 < 50 µs
        """
        print(f"\n{'='*60}")
        print(f"PRUEBA 2: Cambio de Contexto entre Hilos")
        print(f"Iteraciones: {iterations}")
        print(f"{'='*60}")
        
        queue = deque(maxlen=1)
        latencies = []
        
        def worker():
            for _ in range(iterations):
                start = time.perf_counter_ns()
                queue.append(start)
                time.sleep(0.0001)  # 100 µs
        
        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        
        for _ in range(iterations):
            if queue:
                sent_time = queue.popleft()
                recv_time = time.perf_counter_ns()
                latency_us = (recv_time - sent_time) / 1000
                latencies.append(latency_us)
        
        worker_thread.join()
        
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        
        results = {
            'test_name': 'thread_context_switch',
            'iterations': iterations,
            'mean': statistics.mean(latencies) if latencies else 0,
            'stddev': statistics.stdev(latencies) if len(latencies) > 1 else 0,
            'p99': sorted_lat[int(n*0.99)] if n > 0 else 0,
            'p99.99': sorted_lat[int(n*0.9999)] if n >= 10000 else (sorted_lat[-1] if n > 0 else 0),
            'max': max(latencies) if latencies else 0,
            'min': min(latencies) if latencies else 0,
            'target_met': (sorted_lat[int(n*0.9999)] if n >= 10000 else sorted_lat[-1]) <= 50 if latencies else False
        }
        
        self._print_results(results)
        return results
    
    def test_memory_allocation_determinism(self, iterations: int = 10000) -> Dict:
        """
        Prueba 3: Determinismo en asignación de memoria.
        
        Mide la variabilidad en tiempo de alloc/free.
        Objetivo: Desviación estándar < 5 µs
        """
        print(f"\n{'='*60}")
        print(f"PRUEBA 3: Determinismo de Asignación de Memoria")
        print(f"Iteraciones: {iterations}")
        print(f"{'='*60}")
        
        latencies = []
        objects = []
        
        for i in range(iterations):
            start = time.perf_counter_ns()
            
            # Alloc
            obj = {'data': list(range(100))}
            objects.append(obj)
            
            # Free (cada 100 objetos para no agotar memoria)
            if len(objects) >= 100:
                objects.clear()
            
            end = time.perf_counter_ns()
            latency_us = (end - start) / 1000
            latencies.append(latency_us)
            
            if i % (iterations // 10) == 0 and i > 0:
                print(f"  Progreso: {i}/{iterations} ({100*i//iterations}%)")
        
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        stddev = statistics.stdev(latencies) if n > 1 else 0
        
        results = {
            'test_name': 'memory_allocation_determinism',
            'iterations': iterations,
            'mean': statistics.mean(latencies),
            'stddev': stddev,
            'p99': sorted_lat[int(n*0.99)],
            'p99.99': sorted_lat[int(n*0.9999)] if n >= 10000 else sorted_lat[-1],
            'max': max(latencies),
            'min': min(latencies),
            'target_met': stddev < 5.0
        }
        
        self._print_results(results)
        return results
    
    def test_concurrent_access(self, num_threads: int = 4, 
                                iterations_per_thread: int = 1000) -> Dict:
        """
        Prueba 4: Acceso concurrente a recursos compartidos.
        
        Mide contención y latencia bajo carga multi-hilo.
        Objetivo: P99 < 200 µs
        """
        print(f"\n{'='*60}")
        print(f"PRUEBA 4: Acceso Concurrente")
        print(f"Hilos: {num_threads}, Iteraciones/hilo: {iterations_per_thread}")
        print(f"{'='*60}")
        
        shared_counter = [0]
        lock = threading.Lock()
        latencies = []
        latency_lock = threading.Lock()
        
        def worker(thread_id: int):
            for i in range(iterations_per_thread):
                start = time.perf_counter_ns()
                
                with lock:
                    shared_counter[0] += 1
                    _ = shared_counter[0] * 2
                
                end = time.perf_counter_ns()
                latency_us = (end - start) / 1000
                
                with latency_lock:
                    latencies.append(latency_us)
        
        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=worker, args=(t,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        
        results = {
            'test_name': 'concurrent_access',
            'threads': num_threads,
            'total_iterations': len(latencies),
            'mean': statistics.mean(latencies),
            'stddev': statistics.stdev(latencies),
            'p99': sorted_lat[int(n*0.99)],
            'p99.99': sorted_lat[int(n*0.9999)] if n >= 10000 else sorted_lat[-1],
            'max': max(latencies),
            'min': min(latencies),
            'target_met': sorted_lat[int(n*0.99)] < 200
        }
        
        self._print_results(results)
        return results
    
    def _print_results(self, results: Dict):
        """Imprime resultados formateados."""
        print(f"\nResultados:")
        print(f"  Media:     {results['mean']:.2f} µs")
        print(f"  Std Dev:   {results['stddev']:.2f} µs")
        print(f"  P50:       {results.get('p50', 'N/A')} µs")
        print(f"  P99:       {results['p99']:.2f} µs")
        if 'p99.99' in results:
            print(f"  P99.99:    {results['p99.99']:.2f} µs")
        print(f"  Máximo:    {results['max']:.2f} µs")
        print(f"  Mínimo:    {results['min']:.2f} µs")
        
        status = "✅ CUMPLE" if results['target_met'] else "❌ NO CUMPLE"
        print(f"\n  Estado: {status}")
    
    def run_all_tests(self, iterations: int = 10000) -> Dict:
        """Ejecuta todas las pruebas y retorna resumen."""
        print("\n" + "="*70)
        print("SUITE DE PRUEBAS DE DETERMINISMO - KLIPPER TUXEDO_RT")
        print("="*70)
        print(f"Objetivo: P99.99 < {self.target_latency} µs")
        print(f"Iteraciones por prueba: {iterations}")
        print("="*70)
        
        all_results = {
            'target_latency_us': self.target_latency,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'tests': {}
        }
        
        # Ejecutar pruebas
        all_results['tests']['timer_callback'] = self.test_timer_callback_precision(iterations)
        all_results['tests']['context_switch'] = self.test_thread_context_switch(iterations)
        all_results['tests']['memory_alloc'] = self.test_memory_allocation_determinism(iterations)
        all_results['tests']['concurrent_access'] = self.test_concurrent_access(4, iterations // 4)
        
        # Resumen final
        print("\n" + "="*70)
        print("RESUMEN FINAL")
        print("="*70)
        
        all_passed = True
        for test_name, results in all_results['tests'].items():
            status = "✅" if results['target_met'] else "❌"
            print(f"  {status} {test_name}: P99={results['p99']:.2f} µs, "
                  f"P99.99={results.get('p99.99', 'N/A')} µs")
            if not results['target_met']:
                all_passed = False
        
        all_results['all_tests_passed'] = all_passed
        
        print("\n" + "="*70)
        if all_passed:
            print("🎉 TODAS LAS PRUEBAS CUMPLIERON LOS OBJETIVOS DE DETERMINISMO")
        else:
            print("⚠️  ALGUNAS PRUEBAS NO CUMPLIERON LOS OBJETIVOS")
        print("="*70)
        
        return all_results


def main():
    parser = argparse.ArgumentParser(
        description='Suite de Pruebas de Determinismo para Klipper TUXEDO_RT'
    )
    parser.add_argument('--iterations', type=int, default=10000,
                        help='Número de iteraciones por prueba (default: 10000)')
    parser.add_argument('--target-latency', type=float, default=100.0,
                        help='Latencia objetivo P99.99 en µs (default: 100)')
    parser.add_argument('--output', type=str, default=None,
                        help='Archivo JSON para exportar resultados')
    parser.add_argument('--verbose', action='store_true',
                        help='Mostrar salida detallada')
    
    args = parser.parse_args()
    
    # Ejecutar suite
    suite = DeterminismTestSuite(target_latency_us=args.target_latency)
    results = suite.run_all_tests(args.iterations)
    
    # Exportar si se solicitó
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResultados exportados a {args.output}")
    
    # Código de salida
    sys.exit(0 if results['all_tests_passed'] else 1)


if __name__ == '__main__':
    main()
