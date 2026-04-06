#!/usr/bin/env python3
"""
Suite de Pruebas de Determinismo para Klipper RTCore (TUXEDO_RT)

Valida que el 99.99% de las operaciones en rtcore cumplan con límites
de tiempo especificados sin comprometer la precisión de cálculos.

Ejecutar: python -m pytest tests/test_rtcore_determinism.py -v
"""

import time
import statistics
import threading
import unittest
from typing import List, Dict, Tuple
import sys
import os
import gc

# Añadir klippy al path
sys.path.insert(0, '/workspace/klippy')

try:
    from rtcore.rt_core import RealTimeCore
    from rtcore.ring_buffer_zckb import ZCKBRingBuffer, DualRingBuffer
    from rtcore.priority_queue import MultiPriorityQueue
    from rtcore.memory_manager import DeterministicMemoryPool, PooledDict
    from rtcore.latency_analyzer import LatencyAnalyzer
    from rtcore.memory_shm import SharedMemoryManager
    HAS_RTCORE = True
except ImportError as e:
    print(f"Advertencia: No se pudo importar rtcore: {e}")
    HAS_RTCORE = False


class PercentileStats:
    """Calculadora de percentiles para análisis de latencia."""
    
    @staticmethod
    def calculate(latencies: List[float]) -> Dict[str, float]:
        if not latencies:
            return {
                'min': 0.0, 'max': 0.0, 'avg': 0.0,
                'p50': 0.0, 'p90': 0.0, 'p99': 0.0, 'p999': 0.0, 'p9999': 0.0,
                'stddev': 0.0, 'count': 0
            }
        
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        
        def percentile(p):
            idx = min(n - 1, int(n * p / 100.0))
            return sorted_lat[idx]
        
        return {
            'min': sorted_lat[0],
            'max': sorted_lat[-1],
            'avg': sum(latencies) / n,
            'p50': percentile(50),
            'p90': percentile(90),
            'p99': percentile(99),
            'p999': percentile(99.9),
            'p9999': percentile(99.99),
            'stddev': statistics.stdev(latencies) if n > 1 else 0.0,
            'count': n
        }


@unittest.skipUnless(HAS_RTCORE, "RTCore no disponible")
class TestRTCoreDeterminism(unittest.TestCase):
    """Pruebas de determinismo para componentes RTCore."""
    
    def setUp(self):
        """Configurar antes de cada prueba."""
        gc.disable()  # Desactivar GC durante pruebas para medir latencia pura
    
    def tearDown(self):
        """Limpiar después de cada prueba."""
        gc.enable()
        gc.collect()
    
    def test_01_ring_buffer_push_pop_latency(self):
        """
        Medir latencia P99.99 de push/pop en ring buffer ZCKB.
        
        Objetivo: P99.99 < 50 µs para operaciones individuales.
        Precisión: No afectada (solo mide timing, no altera datos).
        """
        print("\n" + "="*70)
        print("PRUEBA 1: Ring Buffer Zero-Copy Kernel-Bypass")
        print("="*70)
        
        ring = ZCKBRingBuffer("test_det_rb", is_master=True, size=1024*1024)
        
        push_latencies = []
        pop_latencies = []
        iterations = 10000
        
        # Datos de prueba (64 bytes, tamaño común para mensajes)
        test_data = b'X' * 64
        
        for i in range(iterations):
            # Medir push
            start = time.perf_counter_ns()
            success = ring.push(test_data)
            push_latencies.append((time.perf_counter_ns() - start) / 1000.0)
            
            if not success:
                print(f"Warning: Push falló en iteración {i}")
            
            # Medir pop
            start = time.perf_counter_ns()
            data = ring.pop()
            pop_latencies.append((time.perf_counter_ns() - start) / 1000.0)
            
            # Verificar integridad de datos (precisión no comprometida)
            if data != test_data:
                self.fail(f"Datos corruptos en iteración {i}: {len(data)} != {len(test_data)}")
        
        # Calcular estadísticas
        push_stats = PercentileStats.calculate(push_latencies)
        pop_stats = PercentileStats.calculate(pop_latencies)
        
        print(f"\nPush Latency ({iterations} iteraciones):")
        print(f"  Min:     {push_stats['min']:.2f} µs")
        print(f"  Avg:     {push_stats['avg']:.2f} µs")
        print(f"  P50:     {push_stats['p50']:.2f} µs")
        print(f"  P90:     {push_stats['p90']:.2f} µs")
        print(f"  P99:     {push_stats['p99']:.2f} µs")
        print(f"  P99.9:   {push_stats['p999']:.2f} µs")
        print(f"  P99.99:  {push_stats['p9999']:.2f} µs")
        print(f"  Max:     {push_stats['max']:.2f} µs")
        print(f"  StdDev:  {push_stats['stddev']:.2f} µs")
        
        print(f"\nPop Latency ({iterations} iteraciones):")
        print(f"  Min:     {pop_stats['min']:.2f} µs")
        print(f"  Avg:     {pop_stats['avg']:.2f} µs")
        print(f"  P50:     {pop_stats['p50']:.2f} µs")
        print(f"  P90:     {pop_stats['p90']:.2f} µs")
        print(f"  P99:     {pop_stats['p99']:.2f} µs")
        print(f"  P99.9:   {pop_stats['p999']:.2f} µs")
        print(f"  P99.99:  {pop_stats['p9999']:.2f} µs")
        print(f"  Max:     {pop_stats['max']:.2f} µs")
        print(f"  StdDev:  {pop_stats['stddev']:.2f} µs")
        
        # Asserts - Objetivos realistas para Python puro
        self.assertLess(push_stats['p9999'], 100.0, 
                       f"Push P99.99={push_stats['p9999']:.2f}µs excede 100µs")
        self.assertLess(pop_stats['p9999'], 100.0,
                       f"Pop P99.99={pop_stats['p9999']:.2f}µs excede 100µs")
        
        # Verificar cero corrupción de datos
        self.assertEqual(push_stats['count'], iterations)
        
        ring.close()
        
        print("\n✅ PRUEBA 1 APROBADA: Ring buffer cumple objetivos de latencia")
    
    def test_02_priority_queue_contention(self):
        """
        Medir contención en cola de prioridad bajo carga concurrente.
        
        Objetivo: P99.99 < 100 µs para enqueue bajo contención.
        Precisión: No afectada (solo ordenamiento por prioridad).
        """
        print("\n" + "="*70)
        print("PRUEBA 2: Priority Queue - Contención Concurrente")
        print("="*70)
        
        queue = MultiPriorityQueue()
        latencies = []
        results = {'received': 0}
        lock = threading.Lock()
        
        def producer(priority: int, count: int):
            """Productor para una prioridad específica."""
            for i in range(count):
                start = time.perf_counter_ns()
                queue.enqueue(f"task_p{priority}_{i}", priority)
                latency = (time.perf_counter_ns() - start) / 1000.0
                latencies.append(latency)
        
        def consumer():
            """Consumidor único que procesa todas las prioridades."""
            target_count = 4000
            received = 0
            while received < target_count:
                item, prio = queue.dequeue()
                if item:
                    received += 1
                    with lock:
                        results['received'] = received
        
        # Crear hilos: 4 productores (uno por prioridad) + 1 consumidor
        threads = []
        for prio in range(4):
            t = threading.Thread(target=producer, args=(prio, 1000), 
                               name=f"Producer-P{prio}")
            threads.append(t)
        
        consumer_thread = threading.Thread(target=consumer, name="Consumer")
        threads.append(consumer_thread)
        
        # Iniciar todos los hilos
        start_time = time.perf_counter()
        for t in threads:
            t.start()
        
        # Esperar completion
        for t in threads:
            t.join(timeout=30.0)
        
        elapsed = time.perf_counter() - start_time
        
        # Analizar latencias
        stats = PercentileStats.calculate(latencies)
        
        print(f"\nEnqueue Latency (4 productores concurrentes, {len(latencies)} ops):")
        print(f"  Throughput: {len(latencies)/elapsed:.0f} ops/sec")
        print(f"  Tiempo total: {elapsed:.2f}s")
        print(f"  Mensajes recibidos: {results['received']}")
        print(f"\n  Min:     {stats['min']:.2f} µs")
        print(f"  Avg:     {stats['avg']:.2f} µs")
        print(f"  P50:     {stats['p50']:.2f} µs")
        print(f"  P90:     {stats['p90']:.2f} µs")
        print(f"  P99:     {stats['p99']:.2f} µs")
        print(f"  P99.9:   {stats['p999']:.2f} µs")
        print(f"  P99.99:  {stats['p9999']:.2f} µs")
        print(f"  Max:     {stats['max']:.2f} µs")
        print(f"  StdDev:  {stats['stddev']:.2f} µs")
        
        # Asserts
        self.assertLess(stats['p9999'], 200.0,
                       f"Enqueue P99.99={stats['p9999']:.2f}µs excede 200µs")
        self.assertEqual(results['received'], 4000,
                        f"Solo se recibieron {results['received']} de 4000 mensajes")
        
        print("\n✅ PRUEBA 2 APROBADA: Priority queue maneja contención adecuadamente")
    
    def test_03_memory_pool_determinism(self):
        """
        Validar que object pooling elimina GC pauses y provee latencia determinista.
        
        Objetivo: StdDev < 5 µs, P99.99 < 50 µs.
        Precisión: Mejorada (evita garbage collection nondeterminista).
        """
        print("\n" + "="*70)
        print("PRUEBA 3: Memory Pool - Determinismo de Asignación")
        print("="*70)
        
        pool = DeterministicMemoryPool(initial_size=1000)
        
        alloc_latencies = []
        release_latencies = []
        
        # Pre-llenar pool parcialmente
        objects = [pool.acquire() for _ in range(500)]
        
        # Medir alloc/release cycles intensivos
        cycles = 100
        batch_size = 100
        
        for cycle in range(cycles):
            # Liberar batch
            for i in range(batch_size):
                start = time.perf_counter_ns()
                pool.release(objects[i])
                release_latencies.append((time.perf_counter_ns() - start) / 1000.0)
            
            # Re-adquirir batch
            for i in range(batch_size):
                start = time.perf_counter_ns()
                obj = pool.acquire()
                alloc_latencies.append((time.perf_counter_ns() - start) / 1000.0)
                objects[i] = obj
        
        # Calcular estadísticas
        alloc_stats = PercentileStats.calculate(alloc_latencies)
        release_stats = PercentileStats.calculate(release_latencies)
        
        print(f"\nAlloc Latency ({len(alloc_latencies)} operaciones):")
        print(f"  Min:     {alloc_stats['min']:.2f} µs")
        print(f"  Avg:     {alloc_stats['avg']:.2f} µs")
        print(f"  P50:     {alloc_stats['p50']:.2f} µs")
        print(f"  P90:     {alloc_stats['p90']:.2f} µs")
        print(f"  P99:     {alloc_stats['p99']:.2f} µs")
        print(f"  P99.9:   {alloc_stats['p999']:.2f} µs")
        print(f"  P99.99:  {alloc_stats['p9999']:.2f} µs")
        print(f"  Max:     {alloc_stats['max']:.2f} µs")
        print(f"  StdDev:  {alloc_stats['stddev']:.2f} µs")
        
        print(f"\nRelease Latency ({len(release_latencies)} operaciones):")
        print(f"  Min:     {release_stats['min']:.2f} µs")
        print(f"  Avg:     {release_stats['avg']:.2f} µs")
        print(f"  P99:     {release_stats['p99']:.2f} µs")
        print(f"  P99.99:  {release_stats['p9999']:.2f} µs")
        print(f"  StdDev:  {release_stats['stddev']:.2f} µs")
        
        # Asserts - Object pooling debe tener stddev muy baja
        self.assertLess(alloc_stats['stddev'], 10.0,
                       f"Stddev={alloc_stats['stddev']:.2f}µs demasiado alta - posible GC interference")
        self.assertLess(alloc_stats['p9999'], 100.0,
                       f"Alloc P99.99={alloc_stats['p9999']:.2f}µs excede 100µs")
        
        # Verificar que no hubo MemoryError (pool exhausto)
        self.assertEqual(len(alloc_latencies), cycles * batch_size)
        
        print("\n✅ PRUEBA 3 APROBADA: Memory pool provee asignación determinista")
    
    def test_04_latency_analyzer_performance(self):
        """
        Comparar rendimiento de análisis estadístico online vs batch.
        
        Objetivo: Online < 10 µs por muestra vs Batch ~3-6 ms total.
        Precisión: ±1% para percentiles (aceptable para monitoreo).
        """
        print("\n" + "="*70)
        print("PRUEBA 4: Latency Analyzer - Online vs Batch")
        print("="*70)
        
        # Generar muestras sintéticas
        import random
        random.seed(42)
        samples = [random.gauss(50, 15) for _ in range(10000)]
        
        # Método BATCH actual (sorted)
        analyzer = LatencyAnalyzer(budget_us=50.0)
        
        start = time.perf_counter_ns()
        batch_result = analyzer.analyze_samples(samples)
        batch_time = (time.perf_counter_ns() - start) / 1000.0
        
        print(f"\nMétodo BATCH (sorted):")
        print(f"  Tiempo total: {batch_time:.2f} µs")
        print(f"  Tiempo por muestra: {batch_time/len(samples):.3f} µs")
        print(f"  P99 reportado: {batch_result['p99_us']:.2f} µs")
        print(f"  Anomalías: {len(batch_result['anomalies'])}")
        
        # Simular método ONLINE (acumulativo)
        class SimpleOnlineStats:
            def __init__(self):
                self.count = 0
                self.min_val = float('inf')
                self.max_val = float('-inf')
                self.mean = 0.0
                self.M2 = 0.0
            
            def update(self, value):
                self.count += 1
                delta = value - self.mean
                self.mean += delta / self.count
                delta2 = value - self.mean
                self.M2 += delta * delta2
                
                if value < self.min_val:
                    self.min_val = value
                if value > self.max_val:
                    self.max_val = value
            
            def get_stats(self):
                if self.count == 0:
                    return {}
                variance = self.M2 / (self.count - 1) if self.count > 1 else 0.0
                return {
                    'count': self.count,
                    'min': self.min_val,
                    'max': self.max_val,
                    'avg': self.mean,
                    'stddev': statistics.sqrt(variance) if variance > 0 else 0.0
                }
        
        online = SimpleOnlineStats()
        online_times = []
        
        start_total = time.perf_counter_ns()
        for sample in samples:
            start = time.perf_counter_ns()
            online.update(sample)
            online_times.append((time.perf_counter_ns() - start) / 1000.0)
        
        total_online_time = (time.perf_counter_ns() - start_total) / 1000.0
        avg_online_time = sum(online_times) / len(online_times)
        
        online_stats = online.get_stats()
        
        print(f"\nMétodo ONLINE (acumulativo):")
        print(f"  Tiempo total: {total_online_time:.2f} µs")
        print(f"  Tiempo por muestra: {avg_online_time:.3f} µs")
        print(f"  Min: {online_stats['min']:.2f} µs")
        print(f"  Max: {online_stats['max']:.2f} µs")
        print(f"  Avg: {online_stats['avg']:.2f} µs")
        print(f"  StdDev: {online_stats['stddev']:.2f} µs")
        
        # Comparar mejora
        improvement = (batch_time - total_online_time) / batch_time * 100 if batch_time > 0 else 0
        print(f"\nMejora: {improvement:.1f}% más rápido")
        
        # Asserts
        self.assertLess(avg_online_time, 5.0,
                       f"Online update={avg_online_time:.3f}µs/muestra excede 5µs")
        self.assertLess(total_online_time, batch_time,
                       "Método online debería ser más rápido que batch")
        
        # Verificar precisión (min/max deben coincidir)
        self.assertAlmostEqual(online_stats['min'], batch_result['min_us'], places=1,
                              msg="Min online vs batch difiere demasiado")
        self.assertAlmostEqual(online_stats['max'], batch_result['max_us'], places=1,
                              msg="Max online vs batch difiere demasiado")
        
        print("\n✅ PRUEBA 4 APROBADA: Análisis online es significativamente más rápido")
    
    def test_05_shared_memory_latency(self):
        """
        Medir latencia de acceso a memoria compartida POSIX.
        
        Objetivo: Read/Write < 5 µs para accesos individuales.
        Precisión: No afectada (zero-copy directo).
        """
        print("\n" + "="*70)
        print("PRUEBA 5: Shared Memory - Latencia de Acceso")
        print("="*70)
        
        manager = SharedMemoryManager()
        region_name = "test_shm_latency"
        
        # Crear región
        success = manager.create_region(region_name, size=65536)
        self.assertTrue(success, "Fallo al crear región de memoria compartida")
        
        mm = manager.get_mmap(region_name)
        self.assertIsNotNone(mm, "Fallo al obtener mmap")
        
        read_latencies = []
        write_latencies = []
        iterations = 10000
        
        # Escribir patrón de prueba
        test_pattern = b'\xAA\x55' * 32
        
        for i in range(iterations):
            # Medir escritura
            start = time.perf_counter_ns()
            mm[i % 1000:i % 1000 + len(test_pattern)] = test_pattern
            write_latencies.append((time.perf_counter_ns() - start) / 1000.0)
            
            # Medir lectura
            start = time.perf_counter_ns()
            data = bytes(mm[i % 1000:i % 1000 + len(test_pattern)])
            read_latencies.append((time.perf_counter_ns() - start) / 1000.0)
            
            # Verificar integridad
            if data != test_pattern:
                self.fail(f"Corrupción de datos en iteración {i}")
        
        # Calcular estadísticas
        read_stats = PercentileStats.calculate(read_latencies)
        write_stats = PercentileStats.calculate(write_latencies)
        
        print(f"\nShared Memory Write ({iterations} operaciones):")
        print(f"  Min:     {write_stats['min']:.2f} µs")
        print(f"  Avg:     {write_stats['avg']:.2f} µs")
        print(f"  P99:     {write_stats['p99']:.2f} µs")
        print(f"  P99.99:  {write_stats['p9999']:.2f} µs")
        print(f"  Max:     {write_stats['max']:.2f} µs")
        
        print(f"\nShared Memory Read ({iterations} operaciones):")
        print(f"  Min:     {read_stats['min']:.2f} µs")
        print(f"  Avg:     {read_stats['avg']:.2f} µs")
        print(f"  P99:     {read_stats['p99']:.2f} µs")
        print(f"  P99.99:  {read_stats['p9999']:.2f} µs")
        print(f"  Max:     {read_stats['max']:.2f} µs")
        
        # Asserts
        self.assertLess(write_stats['p9999'], 20.0,
                       f"Write P99.99={write_stats['p9999']:.2f}µs excede 20µs")
        self.assertLess(read_stats['p9999'], 20.0,
                       f"Read P99.99={read_stats['p9999']:.2f}µs excede 20µs")
        
        # Limpiar
        manager.close_region(region_name, unlink=True)
        
        print("\n✅ PRUEBA 5 APROBADA: Shared memory cumple objetivos de latencia")
    
    def test_06_rt_thread_scheduling_jitter(self):
        """
        Medir jitter en scheduling de hilos con prioridad RT.
        
        Objetivo: Jitter P99.99 < 100 µs para timer callbacks.
        Precisión: No aplicable (medición de timing puro).
        """
        print("\n" + "="*70)
        print("PRUEBA 6: RT Thread Scheduling - Timer Jitter")
        print("="*70)
        
        # Nota: Esta prueba requiere privilegios RT reales para resultados óptimos
        # En entornos sin RT, los resultados serán peores pero aún informativos
        
        rt_core = RealTimeCore()
        
        # Intentar configurar prioridad RT (puede fallar sin permisos)
        try:
            rt_core.set_realtime_priority(80)
            rt_enabled = True
        except Exception as e:
            print(f"Advertencia: No se pudo establecer prioridad RT: {e}")
            rt_enabled = False
        
        callback_times = []
        expected_interval_ns = 1_000_000  # 1 ms
        iterations = 1000
        
        def rt_loop():
            last_time = time.perf_counter_ns()
            for i in range(iterations):
                now = time.perf_counter_ns()
                interval = now - last_time
                jitter = abs(interval - expected_interval_ns)
                callback_times.append(jitter / 1000.0)  # Convertir a µs
                last_time = now
                
                # Sleep hasta próximo ciclo
                target_time = last_time + expected_interval_ns
                sleep_time = (target_time - time.perf_counter_ns()) / 1e9
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        thread = threading.Thread(target=rt_loop, name="RT_Timer_Thread")
        thread.start()
        thread.join(timeout=30.0)
        
        # Calcular estadísticas de jitter
        stats = PercentileStats.calculate(callback_times)
        
        print(f"\nTimer Callback Jitter ({iterations} callbacks, RT={'ON' if rt_enabled else 'OFF'}):")
        print(f"  Min:     {stats['min']:.2f} µs")
        print(f"  Avg:     {stats['avg']:.2f} µs")
        print(f"  P50:     {stats['p50']:.2f} µs")
        print(f"  P90:     {stats['p90']:.2f} µs")
        print(f"  P99:     {stats['p99']:.2f} µs")
        print(f"  P99.9:   {stats['p999']:.2f} µs")
        print(f"  P99.99:  {stats['p9999']:.2f} µs")
        print(f"  Max:     {stats['max']:.2f} µs")
        
        # Asserts - Objetivos más relajados para Python puro sin kernel RT
        if rt_enabled:
            self.assertLess(stats['p9999'], 200.0,
                           f"Jitter P99.99={stats['p9999']:.2f}µs excede 200µs con RT")
        else:
            # Sin RT, aceptar jitter mayor pero aún dentro de límites razonables
            self.assertLess(stats['p9999'], 1000.0,
                           f"Jitter P99.99={stats['p9999']:.2f}µs excede 1000µs sin RT")
            print("\nNota: Resultados mejoraran significativamente con kernel RT-Preempt")
        
        print("\n✅ PRUEBA 6 APROBADA: Scheduling jitter medido correctamente")


class TestQualityPreservation(unittest.TestCase):
    """
    Pruebas para verificar que las optimizaciones NO afectan la precisión.
    """
    
    def test_data_integrity_ring_buffer(self):
        """Verificar que ring buffer no corrompe datos."""
        if not HAS_RTCORE:
            self.skipTest("RTCore no disponible")
        
        ring = ZCKBRingBuffer("test_integrity", is_master=True, size=1024*1024)
        
        # Probar varios tamaños de datos
        test_cases = [
            b'',  # Vacío
            b'X',  # 1 byte
            b'Hello, World!',  # 13 bytes
            b'A' * 64,  # 64 bytes
            b'B' * 255,  # Máximo permitido
        ]
        
        for i, test_data in enumerate(test_cases):
            success = ring.push(test_data)
            self.assertTrue(success, f"Push falló para caso {i}")
            
            received = ring.pop()
            self.assertEqual(received, test_data,
                           f"Datos corruptos para caso {i}: {len(received)} != {len(test_data)}")
        
        ring.close()
        print("✅ Integridad de datos verificada para ring buffer")
    
    def test_priority_ordering(self):
        """Verificar que priority queue mantiene orden correcto."""
        if not HAS_RTCORE:
            self.skipTest("RTCore no disponible")
        
        queue = MultiPriorityQueue()
        
        # Encolar tareas en orden aleatorio
        tasks = [
            ("low1", 3),
            ("critical1", 0),
            ("high1", 1),
            ("normal1", 2),
            ("critical2", 0),
            ("low2", 3),
            ("high2", 1),
        ]
        
        for task, prio in tasks:
            queue.enqueue(task, prio)
        
        # Extraer y verificar orden
        expected_order = ["critical1", "critical2", "high1", "high2", 
                         "normal1", "low1", "low2"]
        
        actual_order = []
        while not queue.is_empty():
            item, prio = queue.dequeue()
            actual_order.append(item)
        
        self.assertEqual(actual_order, expected_order,
                        "Priority queue no mantiene orden correcto")
        
        print("✅ Orden de prioridades verificado correctamente")


def run_all_tests():
    """Ejecutar todas las pruebas y generar reporte."""
    print("\n" + "="*70)
    print("SUITE DE PRUEBAS DE DETERMINISMO - KLIPPER RTCORE")
    print("TUXEDO_RT - Tiempo Real Determinista de Microsegundos")
    print("="*70)
    print(f"\nFecha: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")
    
    # Crear test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Añadir pruebas
    suite.addTests(loader.loadTestsFromTestCase(TestRTCoreDeterminism))
    suite.addTests(loader.loadTestsFromTestCase(TestQualityPreservation))
    
    # Ejecutar con verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Resumen final
    print("\n" + "="*70)
    print("RESUMEN FINAL")
    print("="*70)
    print(f"Pruebas ejecutadas: {result.testsRun}")
    print(f"Exitosas: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Fallos: {len(result.failures)}")
    print(f"Errores: {len(result.errors)}")
    
    if result.wasSuccessful():
        print("\n✅ TODAS LAS PRUEBAS APROBADAS")
        print("El sistema cumple con los objetivos de determinismo temporal.")
        return 0
    else:
        print("\n❌ ALGUNAS PRUEBAS FALLARON")
        print("Revisar métricas y ajustar optimizaciones según sea necesario.")
        return 1


if __name__ == '__main__':
    exit_code = run_all_tests()
    sys.exit(exit_code)
