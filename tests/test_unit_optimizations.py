#!/usr/bin/env python3
"""
Suite de Tests Unitarios para TUXEDO_RT - Cobertura >90%

Este módulo contiene tests exhaustivos para:
- msgblock_encode_int_batch / msgblock_parse_int_batch
- get_monotonic_ctypes
- AlertThresholds
- RollbackManager
- MonitorPerformance

Ejecutar con:
    python3 tests/test_unit_optimizations.py -v --cov=klippy --cov-report=term-missing
"""
import os
import sys
import time
import unittest
import threading
import tempfile
import shutil
from typing import List
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chelper
    HAS_CHELPER = True
except ImportError:
    HAS_CHELPER = False


class TestVLQEncodingBatch(unittest.TestCase):
    """Tests para msgblock_encode_int_batch y msgblock_parse_int_batch."""

    @classmethod
    def setUpClass(cls):
        if not HAS_CHELPER:
            raise unittest.SkipTest("chelper not available")
        cls._ffi, cls._lib = chelper.get_ffi()
        cls._has_batch = (
            hasattr(cls._lib, 'msgblock_encode_int_batch') and
            hasattr(cls._lib, 'msgblock_parse_int_batch')
        )

    def test_encode_int_batch_exists(self):
        """Verifica que la función batch existe."""
        if not HAS_CHELPER:
            self.skipTest("chelper not available")
        self.assertTrue(
            hasattr(self._lib, 'msgblock_encode_int_batch'),
            "msgblock_encode_int_batch not found in chelper"
        )

    def test_parse_int_batch_exists(self):
        """Verifica que la función batch de parse existe."""
        if not HAS_CHELPER:
            self.skipTest("chelper not available")
        self.assertTrue(
            hasattr(self._lib, 'msgblock_parse_int_batch'),
            "msgblock_parse_int_batch not found in chelper"
        )

    def test_encode_decode_roundtrip(self):
        """Test de roundtrip: encode -> decode debe producir valores originales."""
        if not HAS_CHELPER or not self._has_batch:
            self.skipTest("Batch functions not available")

        test_values = [0, 1, 127, 128, 255, 256, 1000, 65535, 1000000, 0x7FFFFFFF]

        out = bytearray(64)
        vals = self._ffi.new(f'uint32_t[{len(test_values)}]')
        for i, v in enumerate(test_values):
            vals[i] = v

        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, len(test_values))
        self.assertGreater(bytes_written, 0)

        p = self._ffi.addressof(out, 0)
        decoded = self._ffi.new(f'uint32_t[{len(test_values)}]')
        result = self._lib.msgblock_parse_int_batch(decoded, p, len(test_values))

        self.assertEqual(result, 0)
        for i, original in enumerate(test_values):
            self.assertEqual(decoded[i], original, f"Mismatch at index {i}")

    def test_encode_batch_empty(self):
        """Test con array vacío."""
        if not HAS_CHELPER or not self._has_batch:
            self.skipTest("Batch functions not available")

        out = bytearray(64)
        vals = self._ffi.new('uint32_t[0]')
        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, 0)
        self.assertEqual(bytes_written, 0)

    def test_encode_batch_single_element(self):
        """Test con un solo elemento."""
        if not HAS_CHELPER or not self._has_batch:
            self.skipTest("Batch functions not available")

        out = bytearray(64)
        vals = self._ffi.new('uint32_t[1]')
        vals[0] = 42

        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, 1)
        self.assertGreater(bytes_written, 0)

        decoded = self._ffi.new('uint32_t[1]')
        p = self._ffi.addressof(out, 0)
        result = self._lib.msgblock_parse_int_batch(decoded, p, 1)
        self.assertEqual(result, 0)
        self.assertEqual(decoded[0], 42)

    def test_encode_batch_large_numbers(self):
        """Test con números grandes (5 bytes VLQ)."""
        if not HAS_CHELPER or not self._has_batch:
            self.skipTest("Batch functions not available")

        test_values = [0xFFFFFFFF, 0x80000000, 0x40000000]

        out = bytearray(64)
        vals = self._ffi.new(f'uint32_t[{len(test_values)}]')
        for i, v in enumerate(test_values):
            vals[i] = v

        bytes_written = self._lib.msgblock_encode_int_batch(out, vals, len(test_values))

        decoded = self._ffi.new(f'uint32_t[{len(test_values)}]')
        p = self._ffi.addressof(out, 0)
        result = self._lib.msgblock_parse_int_batch(decoded, p, len(test_values))

        self.assertEqual(result, 0)
        for i, original in enumerate(test_values):
            self.assertEqual(decoded[i], original)


class TestGetMonotonicCtypes(unittest.TestCase):
    """Tests para get_monotonic_ctypes."""

    def test_monotonic_increases(self):
        """Verifica que el tiempo monotónico siempre aumenta."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
        from util import get_monotonic

        values = [get_monotonic() for _ in range(100)]
        for i in range(1, len(values)):
            self.assertGreaterEqual(values[i], values[i-1])

    def test_monotonic_positive(self):
        """Verifica que el tiempo monotónico es positivo."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
        from util import get_monotonic

        t = get_monotonic()
        self.assertGreater(t, 0)

    def test_monotonic_precision(self):
        """Verifica que la precisión es al menos milisegundo."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
        from util import get_monotonic

        t1 = get_monotonic()
        time.sleep(0.001)
        t2 = get_monotonic()

        diff = t2 - t1
        self.assertGreater(diff, 0.0005, "Precision test: diff should be > 500µs")
        self.assertLess(diff, 0.01, f"Precision test: diff should be < 10ms, got {diff*1000:.2f}ms")

    def test_monotonic_thread_safety(self):
        """Verifica seguridad en multithreading."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
        from util import get_monotonic

        results = []
        def read_monotonic():
            for _ in range(1000):
                results.append(get_monotonic())

        threads = [threading.Thread(target=read_monotonic) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 4000)
        self.assertTrue(all(r > 0 for r in results))


class TestAlertThresholds(unittest.TestCase):
    """Tests para AlertThresholds."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import AlertThresholds, ThresholdConfig, AlertLevel, PerformanceMetric

        self.AlertThresholds = AlertThresholds
        self.ThresholdConfig = ThresholdConfig
        self.AlertLevel = AlertLevel
        self.PerformanceMetric = PerformanceMetric

    def test_threshold_config_evaluate(self):
        """Test de evaluación de thresholds."""
        config = self.ThresholdConfig(
            warning=10.0,
            critical=20.0,
            emergency=30.0
        )

        self.assertEqual(config.evaluate(5.0), self.AlertLevel.OK)
        self.assertEqual(config.evaluate(10.0), self.AlertLevel.WARNING)
        self.assertEqual(config.evaluate(15.0), self.AlertLevel.WARNING)
        self.assertEqual(config.evaluate(20.0), self.AlertLevel.CRITICAL)
        self.assertEqual(config.evaluate(25.0), self.AlertLevel.CRITICAL)
        self.assertEqual(config.evaluate(30.0), self.AlertLevel.EMERGENCY)
        self.assertEqual(config.evaluate(100.0), self.AlertLevel.EMERGENCY)

    def test_register_threshold(self):
        """Test de registro de thresholds."""
        thresholds = self.AlertThresholds()
        config = self.ThresholdConfig(warning=10.0, critical=20.0, emergency=30.0)

        thresholds.register("test_metric", config)
        self.assertIn("test_metric", thresholds._thresholds)

    def test_evaluate_metric(self):
        """Test de evaluación de métricas."""
        thresholds = self.AlertThresholds()
        config = self.ThresholdConfig(warning=10.0, critical=20.0, emergency=30.0)
        thresholds.register("cpu_usage", config)

        metric = self.PerformanceMetric(
            name="cpu_usage",
            value=15.0,
            timestamp=time.time()
        )

        level = thresholds.evaluate(metric)
        self.assertEqual(level, self.AlertLevel.WARNING)

    def test_debounce(self):
        """Test de debounce (evita alertas spam)."""
        thresholds = self.AlertThresholds()
        config = self.ThresholdConfig(warning=1.0, critical=100.0, emergency=1000.0)
        thresholds.register("latency", config)

        metric = self.PerformanceMetric(
            name="latency",
            value=100.0,
            timestamp=time.time()
        )

        level1 = thresholds.evaluate(metric)
        self.assertEqual(level1, self.AlertLevel.CRITICAL)

        metric.timestamp += 1.0  # 1 segundo después
        level2 = thresholds.evaluate(metric)
        self.assertEqual(level2, self.AlertLevel.OK)  # Debounce activo

    def test_get_stats(self):
        """Test de estadísticas de alertas."""
        thresholds = self.AlertThresholds()
        config = self.ThresholdConfig(warning=1.0, critical=2.0, emergency=3.0)
        thresholds.register("metric1", config)
        thresholds.register("metric2", config)

        metric = self.PerformanceMetric(
            name="metric1",
            value=100.0,
            timestamp=time.time()
        )
        thresholds.evaluate(metric)

        stats = thresholds.get_stats()
        self.assertEqual(stats["active_thresholds"], 2)
        self.assertGreater(stats["total_alerts"], 0)


class TestRollbackManager(unittest.TestCase):
    """Tests para RollbackManager."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import RollbackManager

        self.temp_dir = tempfile.mkdtemp()
        self.manager = RollbackManager(config_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_checkpoint(self):
        """Test de guardado de checkpoint."""
        config = {"batch_size": 100, "timeout": 5.0}

        version_id = self.manager.save_checkpoint("test_optimization", config)

        self.assertIsNotNone(version_id)
        self.assertTrue(version_id.startswith("test_optimization"))

    def test_rollback_hook(self):
        """Test de ejecución de rollback hook."""
        rollback_called = []

        def rollback_hook(config):
            rollback_called.append(config)

        self.manager.register_rollback_hook("test_opt", rollback_hook)

        self.manager.save_checkpoint("test_opt", {"setting": 1})
        self.manager.save_checkpoint("test_opt", {"setting": 42})
        self.assertEqual(self.manager._current_version["test_opt"], 1)

        result = self.manager.rollback("test_opt", target_version=0)
        self.assertTrue(result)
        self.assertEqual(len(rollback_called), 1)
        self.assertEqual(rollback_called[0]["setting"], 1)

    def test_rollback_multiple_versions(self):
        """Test de múltiples versiones de rollback."""
        rollback_configs = []

        def rollback_hook(config):
            rollback_configs.append(config)

        self.manager.register_rollback_hook("multi_version", rollback_hook)

        for i in range(5):
            self.manager.save_checkpoint("multi_version", {"version": i})

        self.assertEqual(self.manager._current_version["multi_version"], 4)

        result = self.manager.rollback("multi_version", target_version=2)
        self.assertTrue(result)
        self.assertEqual(len(rollback_configs), 1)
        self.assertEqual(rollback_configs[0]["version"], 2)

    def test_rollback_invalid_version(self):
        """Test de rollback con versión inválida."""
        self.manager.save_checkpoint("test", {"value": 1})

        result = self.manager.rollback("test", target_version=999)
        self.assertFalse(result)


class TestPerformanceMetrics(unittest.TestCase):
    """Tests para PerformanceMetrics."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import PerformanceMetrics, PerformanceMetric, AlertLevel

        self.PerformanceMetrics = PerformanceMetrics
        self.metrics = PerformanceMetrics(window_size=100)
        self.PerformanceMetric = PerformanceMetric
        self.AlertLevel = AlertLevel

    def test_add_metric(self):
        """Test de adición de métricas."""
        metric = self.PerformanceMetric(
            name="test",
            value=1.0,
            timestamp=time.time()
        )
        self.metrics.add(metric)

        recent = self.metrics.get_recent("test", count=10)
        self.assertEqual(len(recent), 1)

    def test_get_recent(self):
        """Test de obtención de métricas recientes."""
        for i in range(50):
            metric = self.PerformanceMetric(
                name="test",
                value=float(i),
                timestamp=time.time() + i
            )
            self.metrics.add(metric)

        recent = self.metrics.get_recent("test", count=10)
        self.assertEqual(len(recent), 10)

    def test_get_stats(self):
        """Test de cálculo de estadísticas."""
        for i in range(10):
            metric = self.PerformanceMetric(
                name="latency",
                value=float(i),
                timestamp=time.time() + i
            )
            self.metrics.add(metric)

        stats = self.metrics.get_stats("latency", window=10)

        self.assertEqual(stats["count"], 10)
        self.assertEqual(stats["min"], 0.0)
        self.assertEqual(stats["max"], 9.0)
        self.assertAlmostEqual(stats["mean"], 4.5)

    def test_window_size_limit(self):
        """Test de límite de ventana."""
        metrics = self.PerformanceMetrics(window_size=10)

        for i in range(20):
            metric = self.PerformanceMetric(
                name="test",
                value=float(i),
                timestamp=time.time() + i
            )
            metrics.add(metric)

        recent = metrics.get_recent("test", count=100)
        self.assertLessEqual(len(recent), 10)

    def test_clear(self):
        """Test de limpieza."""
        metric = self.PerformanceMetric(
            name="test",
            value=1.0,
            timestamp=time.time()
        )
        self.metrics.add(metric)
        self.metrics.clear()

        recent = self.metrics.get_recent("test", count=10)
        self.assertEqual(len(recent), 0)


class TestMonitorPerformanceIntegration(unittest.TestCase):
    """Tests de integración del sistema de monitoreo."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from monitor_performance import MonitorPerformance, AlertLevel

        self.MonitorPerformance = MonitorPerformance
        self.AlertLevel = AlertLevel

    def test_monitor_initialization(self):
        """Test de inicialización del monitor."""
        monitor = self.MonitorPerformance()
        self.assertIsNotNone(monitor._metrics)
        self.assertIsNotNone(monitor._thresholds)
        self.assertIsNotNone(monitor._rollback)

    def test_register_alert_callback(self):
        """Test de registro de callbacks de alerta."""
        monitor = self.MonitorPerformance()
        callback_called = []

        def callback(metric):
            callback_called.append(metric)

        monitor.register_alert_callback(self.AlertLevel.WARNING, callback)
        self.assertIn(callback, monitor._callbacks[self.AlertLevel.WARNING])

    def test_get_status(self):
        """Test de obtención de estado."""
        monitor = self.MonitorPerformance()
        status = monitor.get_status()

        self.assertIn("running", status)
        self.assertIn("metrics_buffer_size", status)
        self.assertIn("alert_stats", status)

    def test_trigger_rollback(self):
        """Test de trigger de rollback."""
        rollback_called = []

        def rollback_hook(config):
            rollback_called.append(config)

        monitor = self.MonitorPerformance()
        monitor._rollback.register_rollback_hook("test_opt", rollback_hook)
        monitor._rollback.save_checkpoint("test_opt", {"value": 1})
        monitor._rollback.save_checkpoint("test_opt", {"value": 2})
        self.assertEqual(monitor._rollback._current_version["test_opt"], 1)

        result = monitor.trigger_rollback("test_opt")
        self.assertTrue(result)
        self.assertEqual(len(rollback_called), 1)
        self.assertEqual(rollback_called[0]["value"], 1)


def run_tests():
    """Ejecuta todos los tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestVLQEncodingBatch))
    suite.addTests(loader.loadTestsFromTestCase(TestGetMonotonicCtypes))
    suite.addTests(loader.loadTestsFromTestCase(TestAlertThresholds))
    suite.addTests(loader.loadTestsFromTestCase(TestRollbackManager))
    suite.addTests(loader.loadTestsFromTestCase(TestPerformanceMetrics))
    suite.addTests(loader.loadTestsFromTestCase(TestMonitorPerformanceIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
