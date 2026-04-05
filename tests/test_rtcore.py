import unittest
import sys
import os
import struct
import time
import threading

# Ensure we can import klippy modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../klippy')))
from rtcore.latency_analyzer import LatencyAnalyzer
from rtcore.rt_core import RealTimeCore
from rtcore.priority_queue import MultiPriorityQueue

class TestRTCore(unittest.TestCase):
    def setUp(self):
        self.rt_core = RealTimeCore()

    def tearDown(self):
        self.rt_core.close_shared_latency_buffer()

    def test_priority_mapping(self):
        # Test Klipper to SCHED_FIFO priority mapping
        self.assertEqual(self.rt_core.map_klipper_priority(0), 99)
        self.assertEqual(self.rt_core.map_klipper_priority(1), 90)
        self.assertEqual(self.rt_core.map_klipper_priority(2), 85)
        self.assertEqual(self.rt_core.map_klipper_priority(3), 80)
        # Default mapping for unknown priority
        self.assertEqual(self.rt_core.map_klipper_priority(10), 85)

    def test_high_res_time(self):
        # High resolution time should be monotonic and have good precision
        t1 = self.rt_core.get_high_res_time()
        time.sleep(0.01)
        t2 = self.rt_core.get_high_res_time()
        self.assertGreater(t2, t1)
        self.assertLess(t2 - t1, 0.05)

    def test_priority_queue(self):
        pq = MultiPriorityQueue()
        # Enqueue items with different priorities
        pq.enqueue("low", priority=3)
        pq.enqueue("critical", priority=0)
        pq.enqueue("high", priority=1)
        pq.enqueue("normal", priority=2)

        # Dequeue items should respect priority
        item, prio = pq.dequeue()
        self.assertEqual(item, "critical")
        self.assertEqual(prio, 0)

        item, prio = pq.dequeue()
        self.assertEqual(item, "high")
        self.assertEqual(prio, 1)

        item, prio = pq.dequeue()
        self.assertEqual(item, "normal")
        self.assertEqual(prio, 2)

        item, prio = pq.dequeue()
        self.assertEqual(item, "low")
        self.assertEqual(prio, 3)

    def test_thread_registration(self):
        execution_count = 0
        def test_target():
            nonlocal execution_count
            execution_count += 1

        self.rt_core.register_thread("test_thread", test_target, priority=80, is_critical=True)
        self.rt_core.start_all()
        
        # Give it a moment to run
        time.sleep(0.1)
        self.rt_core.stop_all()
        
        self.assertGreaterEqual(execution_count, 1)

    def test_latency_ring_and_stats(self):
        for sample in (12.5, 18.0, 9.5, 15.0):
            self.rt_core.record_latency(sample)
        stats = self.rt_core.get_latency_stats()
        self.assertEqual(stats["count"], 4)
        self.assertEqual(stats["min_us"], 9.5)
        self.assertEqual(stats["max_us"], 18.0)
        self.assertAlmostEqual(stats["avg_us"], 13.75)
        self.assertAlmostEqual(stats["jitter_us"], 8.5)

    def test_shared_latency_buffer(self):
        name = self.rt_core.create_shared_latency_buffer(
            name="klipper_rt_test_buffer", capacity=64)
        self.assertEqual(name, "klipper_rt_test_buffer")
        self.rt_core.record_latency(22.0)
        self.rt_core.record_latency(27.5)
        count, write_index = struct.unpack_from(
            "QQ", self.rt_core._shared_latency.buf, 0)
        self.assertEqual(count, 2)
        self.assertEqual(write_index, 2)

    def test_platform_profile_configuration(self):
        profile = self.rt_core.configure_platform_profile(
            "kvm", cpu_affinity=[0], buffer_bytes=65536, latency_capacity=128)
        self.assertEqual(profile["scheduler"], "SCHED_FIFO")
        self.assertEqual(profile["buffer_bytes"], 65536)
        self.assertEqual(profile["latency_budget_us"], 50.0)
        self.assertEqual(self.rt_core._shared_latency_capacity, 128)

    def test_latency_analyzer(self):
        analyzer = LatencyAnalyzer(latency_budget_us=20.0)
        samples = [8.0, 12.0, 19.5, 21.0, 18.0]
        stats = analyzer.analyze_samples(samples)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["min_us"], 8.0)
        self.assertEqual(stats["max_us"], 21.0)
        self.assertAlmostEqual(stats["median_us"], 18.0)
        self.assertFalse(stats["within_budget"])
        self.assertEqual(stats["anomalies"], [21.0])

    def test_latency_analyzer_reads_shared_buffer(self):
        self.rt_core.create_shared_latency_buffer(
            name="klipper_rt_test_reader", capacity=64)
        for sample in (10.0, 15.0, 17.0):
            self.rt_core.record_latency(sample)
        analyzer = LatencyAnalyzer()
        samples = analyzer.read_shared_buffer("klipper_rt_test_reader", 64)
        self.assertEqual(samples, [10.0, 15.0, 17.0])
        report = analyzer.render_text_report(samples)
        self.assertIn("count=3", report)
        self.assertIn("within_budget=yes", report)

if __name__ == '__main__':
    unittest.main()
