import unittest
import time
import sys
import os
import math

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

import toolhead
import mcu
import chelper

class MockPrinter:
    def __init__(self):
        self.objects = {}
        self.reactor = MockReactor()
    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)
    def get_reactor(self):
        return self.reactor
    def load_object(self, config, name):
        if name == 'motion_queuing':
            return MockMotionQueuing()
        if name == 'gcode':
            return MockGCode()
        return None
    def add_object(self, name, obj):
        self.objects[name] = obj
    def send_event(self, event, *args):
        pass
    def register_event_handler(self, event, cb):
        pass

class MockReactor:
    def monotonic(self):
        return time.time()
    def assert_no_pause(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def register_timer(self, cb):
        return "timer"
    def update_timer(self, timer, wtime):
        pass
    def unregister_timer(self, timer):
        pass
    def pause(self, t):
        return t
    NOW = 0
    NEVER = 9999999999.9

class MockMCU:
    def estimated_print_time(self, t):
        return t
    def is_fileoutput(self):
        return False
    def lookup_command(self, cmd, proto):
        return MockCommand()

class MockCommand:
    def get_command_tag(self):
        return 1

class MockMotionQueuing:
    def register_flush_callback(self, cb, can_add_trapq=False):
        pass
    def allocate_trapq(self):
        return 1
    def lookup_trapq_append(self):
        def trapq_append(*args):
            pass
        return trapq_append
    def get_kin_flush_delay(self):
        return 0.050
    def flush_all_steps(self):
        pass
    def wipe_trapq(self, trapq):
        pass
    def calc_step_gen_restart(self, t):
        return t
    def note_mcu_movequeue_activity(self, t):
        pass

class MockGCode:
    def __init__(self):
        self.Coord = list

class MockConfig:
    def __init__(self, printer):
        self.printer = printer
    def get_printer(self):
        return self.printer
    def getfloat(self, name, default=None, above=None, below=None, minval=None, maxval=None):
        if name == 'max_velocity': return 300.
        if name == 'max_accel': return 3000.
        if name == 'minimum_cruise_ratio': return 0.5
        if name == 'square_corner_velocity': return 5.
        return default
    def getboolean(self, name, default=None):
        return default
    def get(self, name, default=None):
        if name == 'kinematics': return 'cartesian'
        return default
    def error(self, msg):
        return Exception(msg)

class MockKinematics:
    def __init__(self, toolhead, config):
        pass
    def set_position(self, pos, homing_axes):
        pass
    def check_move(self, move):
        pass
    def get_status(self, eventtime):
        return {}

def load_kinematics(toolhead, config):
    return MockKinematics(toolhead, config)

# Monkey patch importlib to return our mock kinematics
import importlib
old_import_module = importlib.import_module
def mock_import_module(name):
    if name == 'kinematics.cartesian':
        class MockMod:
            def load_kinematics(self, toolhead, config):
                return MockKinematics(toolhead, config)
        return MockMod()
    return old_import_module(name)
importlib.import_module = mock_import_module

class BenchmarkToolHead(unittest.TestCase):
    def setUp(self):
        self.printer = MockPrinter()
        self.config = MockConfig(self.printer)
        self.printer.add_object('mcu', MockMCU())
        self.printer.add_object('gcode', MockGCode())
        # Mock chelper.get_ffi
        self.old_get_ffi = chelper.get_ffi
        class MockFFI:
            NULL = None
        class MockLib:
            def trapq_set_position(self, *args): pass
        chelper.get_ffi = lambda: (MockFFI(), MockLib())

    def tearDown(self):
        chelper.get_ffi = self.old_get_ffi

    def test_move_performance(self):
        th = toolhead.ToolHead(self.config)
        th.set_position([0., 0., 0., 0.])
        
        count = 10000
        start_pos = [0., 0., 0., 0.]
        
        # Benchmark many small moves (typical for complex prints)
        print(f"\nBenchmarking {count} small moves...")
        start_time = time.time()
        for i in range(count):
            new_pos = [i * 0.1, (i % 2) * 0.1, 0., 0.]
            th.move(new_pos, 100.)
        duration = time.time() - start_time
        print(f"Time for {count} moves: {duration:.4f}s ({duration/count*1000000:.2f} us/move)")

        # Benchmark flushing
        print("Benchmarking lookahead flush...")
        start_time = time.time()
        th.flush_step_generation()
        duration_flush = time.time() - start_time
        print(f"Time for flush: {duration_flush:.4f}s")

if __name__ == '__main__':
    unittest.main()
