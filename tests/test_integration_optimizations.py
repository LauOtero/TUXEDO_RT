#!/usr/bin/env python3
"""
Suite de Tests de Integración para TUXEDO_RT.

Valida la interacción entre componentes optimizados:
- msgblock batch ↔ msgproto
- get_monotonic ↔ reactor
- monitor_performance ↔ AlertThresholds
- RollbackManager ↔ chelper state

Ejecutar con:
    python3 tests/test_integration_optimizations.py -v
"""
import os
import sys
import time
import unittest
import threading
import tempfile
import shutil
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chelper
    HAS_CHELPER = True
except ImportError:
    HAS_CHELPER = False


class TestMsgBlockBatchIntegration(unittest.TestCase):
    """Tests de integración entre msgblock batch y msgproto."""

    @classmethod
    def setUpClass(cls):
        if not HAS_CHELPER:
            raise unittest.SkipTest("chelper not available")
        cls._ffi, cls._lib = chelper.get_ffi()
        cls._has_batch = (
            hasattr(cls._lib, 'msgblock_encode_int_batch') and
            hasattr(cls._lib, 'msgblock_parse_int_batch')
        )

    def test_encode_decode_message_integration(self):
        """Test de integración: codificar mensaje → transmitir → decodificar."""
        if not self._has_batch:
            self.skipTest("Batch functions not available")

        test_values = [100, 200, 300, 400, 500]

        out = bytearray(128)
        vals = self._ffi.new(f'uint32_t[{len(test_values)}]')
        for i, v in enumerate(test_values):
            vals[i] = v

        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, len(test_values))
        self.assertGreater(bytes_written, 0)

        p = self._ffi.addressof(out, 0)
        decoded = self._ffi.new(f'uint32_t[{len(test_values)}]')
        result = self._lib.msgblock_parse_int_batch(decoded, p, len(test_values))

        self.assertEqual(result, 0)
        self.assertEqual(list(decoded), test_values)

    def test_large_batch_integration(self):
        """Test con batch grande (simula trama CAN completa)."""
        if not self._has_batch:
            self.skipTest("Batch functions not available")

        BATCH_SIZE = 256
        vals = self._ffi.new(f'uint32_t[{BATCH_SIZE}]')
        for i in range(BATCH_SIZE):
            vals[i] = i * 100

        out = bytearray(BATCH_SIZE * 5)
        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, BATCH_SIZE)

        self.assertGreater(bytes_written, 0)

        p = self._ffi.addressof(out, 0)
        decoded = self._ffi.new(f'uint32_t[{BATCH_SIZE}]')
        result = self._lib.msgblock_parse_int_batch(decoded, p, BATCH_SIZE)

        self.assertEqual(result, 0)
        for i in range(BATCH_SIZE):
            self.assertEqual(decoded[i], i * 100)


class TestGetMonotonicIntegration(unittest.TestCase):
    """Tests de integración de get_monotonic con reactor."""

    def test_monotonic_reactor_integration(self):
        """Test de integración: get_monotonic usado en reactor loop."""
        from util import get_monotonic

        iterations = 1000
        times = []

        for _ in range(iterations):
            t = get_monotonic()
            times.append(t)

        for i in range(1, len(times)):
            self.assertGreaterEqual(times[i], times[i-1])

    def test_monotonic_timer_accuracy(self):
        """Test de precisión: el timer debe detectar intervals de 1ms."""
        from util import get_monotonic

        intervals_detected = 0
        num_tests = 100

        for _ in range(num_tests):
            t1 = get_monotonic()
            time.sleep(0.001)
            t2 = get_monotonic()
            diff = t2 - t1

            if 0.0009 < diff < 0.002:
                intervals_detected += 1

        detection_rate = intervals_detected / num_tests
        self.assertGreater(detection_rate, 0.95)

    def test_monotonic_concurrent_access(self):
        """Test de acceso concurrente desde múltiples threads."""
        from util import get_monotonic

        results: List[List[float]] = [[] for _ in range(4)]
        threads: List[threading.Thread] = []

        def read_times(results_list: List[float]):
            for _ in range(500):
                t = get_monotonic()
                results_list.append(t)

        for i in range(4):
            t = threading.Thread(target=read_times, args=(results[i],))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        for r in results:
            self.assertEqual(len(r), 500)
            for i in range(1, len(r)):
                self.assertGreaterEqual(r[i], r[i-1])


class TestMonitorPerformanceIntegration(unittest.TestCase):
    """Tests de integración del sistema de monitoreo."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import (
            MonitorPerformance, PerformanceMetrics, AlertThresholds,
            RollbackManager, MetricCollector, AlertLevel, PerformanceMetric
        )
        self.MonitorPerformance = MonitorPerformance
        self.PerformanceMetrics = PerformanceMetrics
        self.AlertThresholds = AlertThresholds
        self.RollbackManager = RollbackManager
        self.AlertLevel = AlertLevel
        self.PerformanceMetric = PerformanceMetric

        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_monitor_thresholds_integration(self):
        """Test de integración: Monitor → Thresholds → Alerts."""
        monitor = self.MonitorPerformance()
        thresholds = self.AlertThresholds()
        metrics = self.PerformanceMetrics()

        from monitor_performance import ThresholdConfig
        thresholds.register("test_metric", ThresholdConfig(
            warning=10.0, critical=20.0, emergency=30.0
        ))

        for value in [5.0, 15.0, 25.0, 35.0]:
            metric = self.PerformanceMetric(
                name="test_metric",
                value=value,
                timestamp=time.time()
            )
            metrics.add(metric)
            level = thresholds.evaluate(metric)

            if value < 10:
                self.assertEqual(level, self.AlertLevel.OK)
            elif value < 20:
                self.assertEqual(level, self.AlertLevel.WARNING)
            elif value < 30:
                self.assertEqual(level, self.AlertLevel.CRITICAL)
            else:
                self.assertEqual(level, self.AlertLevel.EMERGENCY)

    def test_monitor_rollback_integration(self):
        """Test de integración: Monitor → RollbackManager."""
        rollback_mgr = self.RollbackManager(config_dir=self.temp_dir)

        rollback_called = []
        rollback_mgr.register_rollback_hook("test_opt", lambda c: rollback_called.append(c))

        rollback_mgr.save_checkpoint("test_opt", {"batch_size": 100})
        rollback_mgr.save_checkpoint("test_opt", {"batch_size": 200})

        rollback_mgr.rollback("test_opt", target_version=0)

        self.assertEqual(len(rollback_called), 1)
        self.assertEqual(rollback_called[0]["batch_size"], 100)

    def test_full_monitoring_pipeline(self):
        """Test del pipeline completo: Collect → Store → Evaluate → Alert."""
        monitor = self.MonitorPerformance()

        alerts_received = []
        def alert_handler(metric):
            alerts_received.append(metric)

        monitor.register_alert_callback(self.AlertLevel.WARNING, alert_handler)

        from monitor_performance import ThresholdConfig, PerformanceMetric
        monitor._thresholds.register("cpu_usage", ThresholdConfig(
            warning=10.0, critical=50.0, emergency=90.0
        ))

        metric = PerformanceMetric(
            name="cpu_usage",
            value=15.0,
            timestamp=time.time()
        )

        level = monitor.check_and_alert(metric)

        self.assertEqual(level, self.AlertLevel.WARNING)
        self.assertEqual(len(alerts_received), 1)
        self.assertEqual(alerts_received[0].value, 15.0)


class TestChelperStateIntegration(unittest.TestCase):
    """Tests de integración con estado de chelper."""

    @classmethod
    def setUpClass(cls):
        if not HAS_CHELPER:
            raise unittest.SkipTest("chelper not available")
        cls._ffi, cls._lib = chelper.get_ffi()

    def test_rtt_stats_connection_integration(self):
        """Test: conn_get_rtt_stats retorna valores consistentes."""
        if not hasattr(self._lib, 'conn_get_rtt_stats'):
            self.skipTest("conn_get_rtt_stats not available")

        srtt = self._ffi.new('double *')
        rttvar = self._ffi.new('double *')
        rto = self._ffi.new('double *')

        self._lib.conn_get_rtt_stats(None, srtt, rttvar, rto)

        self.assertIsInstance(srtt[0], float)
        self.assertIsInstance(rttvar[0], float)
        self.assertIsInstance(rto[0], float)

        self.assertGreaterEqual(srtt[0], 0.0)
        self.assertGreaterEqual(rttvar[0], 0.0)
        self.assertGreaterEqual(rto[0], 0.0)


class TestStressScenarios(unittest.TestCase):
    """Tests de escenarios de estrés (simulan condiciones extremas)."""

    def test_high_frequency_metric_updates(self):
        """Test: métricas actualizadas a alta frecuencia (1000/seg)."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import PerformanceMetrics, PerformanceMetric

        metrics = PerformanceMetrics(window_size=10000)
        ITERATIONS = 10000

        start = time.time()
        for i in range(ITERATIONS):
            metric = PerformanceMetric(
                name="stress_test",
                value=float(i),
                timestamp=start + i * 0.001
            )
            metrics.add(metric)
        elapsed = time.time() - start

        recent = metrics.get_recent("stress_test", count=ITERATIONS)
        self.assertEqual(len(recent), ITERATIONS)

        print(f"\nHigh frequency test: {ITERATIONS} updates in {elapsed:.3f}s")
        self.assertLess(elapsed, 5.0)

    def test_concurrent_threshold_evaluation(self):
        """Test: múltiples threads evaluando thresholds simultáneamente."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import AlertThresholds, ThresholdConfig, AlertLevel, PerformanceMetric

        thresholds = AlertThresholds()
        thresholds.register("shared_metric", ThresholdConfig(
            warning=10.0, critical=20.0, emergency=30.0
        ))

        results: List[AlertLevel] = []
        lock = threading.Lock()

        def eval_thread():
            for i in range(100):
                metric = PerformanceMetric(
                    name="shared_metric",
                    value=float(i % 35),
                    timestamp=time.time()
                )
                level = thresholds.evaluate(metric)
                with lock:
                    results.append(level)

        threads = [threading.Thread(target=eval_thread) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 400)


def run_tests():
    """Ejecuta todos los tests de integración."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestMsgBlockBatchIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestGetMonotonicIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestMonitorPerformanceIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestChelperStateIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestStressScenarios))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
