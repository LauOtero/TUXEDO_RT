import unittest
import time
import os
import sys
import logging
import threading

# Add current directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'klippy'))

import queuelogger

class TestQueueLoggerPerformance(unittest.TestCase):
    def setUp(self):
        self.test_log = "test_performance.log"
        if os.path.exists(self.test_log):
            os.remove(self.test_log)
        self.ql = queuelogger.setup_bg_logging(self.test_log, logging.INFO)

    def tearDown(self):
        queuelogger.clear_bg_logging()
        self.ql.stop()
        if os.path.exists(self.test_log):
            # Wait a bit for the file to be released
            time.sleep(0.1)
            try:
                os.remove(self.test_log)
            except:
                pass

    def test_logging_latency(self):
        # Measure how long it takes to call log.info (main thread latency)
        logger = logging.getLogger()
        iterations = 10000
        
        start = time.time()
        for i in range(iterations):
            logger.info(f"Test message {i} with some extra data to simulate real load")
        duration = time.time() - start
        
        avg_latency = (duration / iterations) * 1000000 # in microseconds
        print(f"\n[BENCHMARK] Average Main Thread Latency: {avg_latency:.2f} us")
        
        # Original was doing formatting in-place, now it's deferred.
        # Expecting < 20us per call on modern CPU
        self.assertLess(duration, 0.5, "Logging is too slow for the main thread")

    def test_throughput_and_batching(self):
        # Stress test to see how the background thread handles bursts
        logger = logging.getLogger()
        burst_size = 5000
        
        start = time.time()
        for i in range(burst_size):
            logger.info(f"Burst message {i}")
        
        # Check if we can still log while background is busy
        logger.info("Critical check message")
        
        # Wait for background thread to catch up
        timeout = 5.0
        while self.ql.bg_queue.qsize() > 0 and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1
            
        duration = time.time() - start
        print(f"[BENCHMARK] Total time for {burst_size} logs: {duration:.4f}s")
        self.assertEqual(self.ql.bg_queue.qsize(), 0, "Queue did not drain in time")

    def test_emergency_buffer(self):
        logger = logging.getLogger()
        for i in range(10):
            logger.info(f"Emergency message {i}")
        
        # Give a small window for the bg thread
        time.sleep(0.2)
        
        logs = self.ql.get_emergency_logs()
        self.assertGreater(len(logs), 0)
        self.assertIn("Emergency message 9", logs[-1].msg)

if __name__ == '__main__':
    unittest.main()
