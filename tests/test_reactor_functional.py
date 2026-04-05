import unittest
import time
import os
import sys
import threading
import socket
import greenlet

# Add current directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'klippy'))

import reactor

class TestReactorFunctional(unittest.TestCase):
    def setUp(self):
        self.r = reactor.Reactor()
        self.callback_called = False
        self.callback_result = None

    def tearDown(self):
        self.r.finalize()

    def test_basic_callback(self):
        def cb(eventtime):
            self.callback_called = True
            return self.r.NEVER
        
        self.r.register_timer(cb, self.r.monotonic())
        # Run a few iterations
        self.r._g_dispatch = None
        self.r._check_timers(self.r.monotonic() + 0.1, False)
        self.assertTrue(self.callback_called)

    def test_completion(self):
        completion = self.r.completion()
        self.assertFalse(completion.test())
        
        completion.complete("success")
        self.assertTrue(completion.test())
        self.assertEqual(completion.wait(), "success")

    def test_mutex(self):
        mutex = self.r.mutex()
        self.assertFalse(mutex.test())
        
        with mutex:
            self.assertTrue(mutex.test())
        
        self.assertFalse(mutex.test())

    def test_async_callback(self):
        # We need to setup async callbacks which uses pipes
        self.r._setup_async_callbacks()
        
        def cb(eventtime):
            self.callback_called = True
            self.callback_result = "async_done"
            return self.r.NEVER
        
        # Call from "another thread" (simulated)
        self.r.register_async_callback(cb)
        
        # Process the pipe signal
        self.r._got_pipe_signal(self.r.monotonic())
        
        # Check timers to run the registered callback
        self.r._check_timers(self.r.monotonic() + 0.1, False)
        
        self.assertTrue(self.callback_called)
        self.assertEqual(self.callback_result, "async_done")

    def test_stats_collection(self):
        def cb(eventtime):
            return self.r.NEVER
        
        self.r.register_timer(cb, self.r.monotonic())
        self.r._check_timers(self.r.monotonic() + 0.1, False)
        
        stats = self.r.get_stats()
        self.assertGreater(stats['timers_processed'], 0)
        self.assertIn('avg_latency', stats)
        self.assertIn('heap_size', stats)

    def test_timer_priority(self):
        results = []
        def cb_low(eventtime):
            results.append("low")
            return self.r.NEVER
        def cb_high(eventtime):
            results.append("high")
            return self.r.NEVER
            
        now = self.r.monotonic()
        # Same waketime, different priority
        self.r.register_timer(cb_low, now, priority=3)
        self.r.register_timer(cb_high, now, priority=1)
        
        self.r._check_timers(now + 0.1, False)
        # High priority should come first if waketime is identical
        self.assertEqual(results, ["high", "low"])

if __name__ == '__main__':
    unittest.main()
