import unittest
import time
import os
import sys
import logging

# Add current directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'klippy'))

import reactor

class TestReactorPerformance(unittest.TestCase):
    def setUp(self):
        self.r = reactor.Reactor()
        self.timer_count = 0
        self.execution_times = []

    def timer_callback(self, eventtime):
        self.timer_count += 1
        self.execution_times.append(eventtime)
        return self.r.NEVER

    def test_timer_scaling_performance(self):
        # Register 10,000 timers and see how long it takes to process them
        num_timers = 10000
        now = self.r.monotonic()
        
        # Registration performance
        start_reg = time.time()
        for i in range(num_timers):
            self.r.register_timer(self.timer_callback, now + (i * 0.00001))
        duration_reg = time.time() - start_reg
        print(f"\n[BENCHMARK] Registration of {num_timers} timers: {duration_reg:.4f}s")
        
        # Dispatch performance
        # We simulate the passage of time
        future_time = now + 1.0
        start_disp = time.time()
        self.r._g_dispatch = None # Mock to avoid greenlet switching logic in this simple test
        while self.timer_count < num_timers:
            self.r._check_timers(future_time, False)
        duration_disp = time.time() - start_disp
        
        print(f"[BENCHMARK] Dispatch of {num_timers} timers: {duration_disp:.4f}s")
        print(f"[BENCHMARK] Total processed: {self.timer_count}")
        
        avg, max_lat = self.r.get_latency_stats()
        print(f"[BENCHMARK] Avg Latency: {avg*1000000:.2f} us, Max Latency: {max_lat*1000000:.2f} us")
        
        self.assertEqual(self.timer_count, num_timers)
        # Heap insertion O(log N) should be very fast
        self.assertLess(duration_reg, 0.1) 
        # Heap pop/process O(log N) should be very fast
        self.assertLess(duration_disp, 0.2)

    def test_lazy_unregistration(self):
        # Test that unregistering doesn't require searching the whole list (O(1) mark)
        num_timers = 5000
        now = self.r.monotonic()
        handlers = []
        for i in range(num_timers):
            h = self.r.register_timer(self.timer_callback, now + 100.0)
            handlers.append(h)
        
        start_unreg = time.time()
        for h in handlers:
            self.r.unregister_timer(h)
        duration_unreg = time.time() - start_unreg
        
        print(f"[BENCHMARK] Unregistration (lazy) of {num_timers} timers: {duration_unreg:.4f}s")
        # Should be almost instantaneous since it's just a variable assignment
        self.assertLess(duration_unreg, 0.01)

if __name__ == '__main__':
    unittest.main()
