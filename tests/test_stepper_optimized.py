import unittest
import time
import sys
import os

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

import stepper

class MockConfig:
    def __init__(self, name):
        self.name = name
    def get_name(self):
        return self.name
    def lookup_object(self, name, default=None):
        return None
    def load_object(self, config, name):
        if name == 'motion_queuing':
            return MockMotionQueuing()
        return None

class MockMotionQueuing:
    def allocate_syncemitter(self, mcu, sname):
        return 1
    def register_flush_callback(self, cb):
        pass

class MockMCU:
    def __init__(self):
        self.printer = MockPrinter()
    def get_printer(self):
        return self.printer
    def create_oid(self):
        return 1
    def register_config_callback(self, cb):
        pass
    def register_event_handler(self, event, cb):
        pass
    def seconds_to_clock(self, s):
        return int(s * 1000000)
    def get_constants(self):
        return {}

class MockPrinter:
    def lookup_object(self, name, default=None):
        return None
    def load_object(self, config, name):
        if name == 'motion_queuing':
            return MockMotionQueuing()
        return None
    def register_event_handler(self, event, cb):
        pass

class TestStepperOptimized(unittest.TestCase):
    def test_mcu_stepper_new_features(self):
        # Mock initialization
        config = MockConfig("stepper_x")
        mcu = MockMCU()
        step_params = {'chip': mcu, 'pin': 'PA1', 'invert': False}
        dir_params = {'chip': mcu, 'pin': 'PA2', 'invert': False}
        
        old_get_ffi = stepper.chelper.get_ffi
        class MockFFI:
            NULL = None
            def gc(self, obj, free): return obj
            def new(self, t, n): return [0]*n
        class MockLib:
            def syncemitter_get_stepcompress(self, se): return 1
            def stepcompress_set_invert_sdir(self, sq, inv): pass
            def itersolve_get_commanded_pos(self, sk): return 10.5
            def itersolve_check_active(self, sk, ft): return 0.1
            def free(self, p): pass
            itersolve_check_active = None
        stepper.chelper.get_ffi = lambda: (MockFFI(), MockLib())

        try:
            ms = stepper.MCU_stepper(config, step_params, dir_params, 40., 200.)
            ms._stepper_kinematics = 1 # Dummy
            
            # Test FFI caching
            self.assertIsNotNone(ms._ffi_lib)
            self.assertIsNotNone(ms._ffi_main)
            
            # Test get_mcu_position
            pos1 = ms.get_mcu_position()
            self.assertEqual(pos1, 53)
            
            # Test new features
            # Predict
            next_pos = ms.predict_next_position(10.0, 1.0, 0.5, 2.0)
            # p = 10 + 1*2 + 0.5*0.5*2^2 = 10 + 2 + 1 = 13
            self.assertEqual(next_pos, 13.0)
            
            # EMA
            ema1 = ms.get_smoothed_velocity(100.0, alpha=0.1)
            self.assertEqual(ema1, 10.0) # (0.1*100) + (0.9*0)
            ema2 = ms.get_smoothed_velocity(100.0, alpha=0.1)
            self.assertEqual(ema2, 19.0) # (0.1*100) + (0.9*10)
            
            # Batch calculation
            positions = [10.0, 20.0, 30.0]
            mcu_positions = ms.batch_calc_mcu_positions(positions)
            # (10+0)/0.2 = 50, (20+0)/0.2 = 100, (30+0)/0.2 = 150
            self.assertEqual(mcu_positions, [50, 100, 150])
            
        finally:
            stepper.chelper.get_ffi = old_get_ffi

    def test_stepper_performance(self):
        # Benchmark FFI call vs member access
        config = MockConfig("stepper_x")
        mcu = MockMCU()
        step_params = {'chip': mcu, 'pin': 'PA1', 'invert': False}
        dir_params = {'chip': mcu, 'pin': 'PA2', 'invert': False}
        
        old_get_ffi = stepper.chelper.get_ffi
        class MockFFI:
            NULL = None
            def gc(self, obj, free): return obj
        class MockLib:
            def syncemitter_get_stepcompress(self, se): return 1
            def stepcompress_set_invert_sdir(self, sq, inv): pass
            def itersolve_get_commanded_pos(self, sk): return 10.5
            itersolve_check_active = None
            def free(self, p): pass
        stepper.chelper.get_ffi = lambda: (MockFFI(), MockLib())
        
        try:
            ms = stepper.MCU_stepper(config, step_params, dir_params, 40., 200.)
            ms._stepper_kinematics = 1
            
            count = 500000
            
            # Test "Before" (simulated by calling get_ffi repeatedly)
            start = time.time()
            for _ in range(count):
                stepper.chelper.get_ffi()
                ms.get_mcu_position()
            duration_before = time.time() - start

            # Test "After" (Single call using cached FFI)
            start = time.time()
            for _ in range(count):
                ms.get_mcu_position()
            duration_after = time.time() - start
            
            # Test "Batch"
            start = time.time()
            positions = [float(i % 1000) for i in range(1000)]
            for _ in range(count // 1000):
                ms.batch_calc_mcu_positions(positions)
            duration_batch = time.time() - start
            
            print(f"\nStepper Perf Results (count={count}):")
            print(f"  Before (Repeated FFI call): {duration_before:.4f}s")
            print(f"  After (Cached FFI access):  {duration_after:.4f}s")
            print(f"  Batch (Vectorized):         {duration_batch:.4f}s")
            print(f"Improvement (FFI Cache):      {(duration_before/duration_after - 1)*100:.1f}%")
            print(f"Total Improvement (Batch):    {(duration_before/duration_batch - 1)*100:.1f}%")
            
        finally:
            stepper.chelper.get_ffi = old_get_ffi

if __name__ == '__main__':
    unittest.main()
