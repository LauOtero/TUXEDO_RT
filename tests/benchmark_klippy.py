import sys
import os
import time
import collections
from unittest.mock import MagicMock

# Mock necessary modules
sys.modules['util'] = MagicMock()
sys.modules['reactor'] = MagicMock()
sys.modules['queuelogger'] = MagicMock()
sys.modules['msgproto'] = MagicMock()
sys.modules['gcode'] = MagicMock()
sys.modules['configfile'] = MagicMock()
sys.modules['pins'] = MagicMock()
sys.modules['mcu'] = MagicMock()
sys.modules['toolhead'] = MagicMock()
sys.modules['webhooks'] = MagicMock()

# Mock rtcore package
rtcore = MagicMock()
sys.modules['rtcore'] = rtcore
sys.modules['rtcore.rt_core'] = MagicMock()
sys.modules['i18n'] = MagicMock()
sys.modules['rtcore.fault_tolerance'] = MagicMock()
sys.modules['plugin_manager'] = MagicMock()
sys.modules['rtcore.memory_manager'] = MagicMock()

import importlib.util
spec = importlib.util.spec_from_file_location("klippy", "klippy/klippy.py")
klippy_module = importlib.util.module_from_spec(spec)
# Patch i18n functions before exec
klippy_module._ = lambda x: x
spec.loader.exec_module(klippy_module)

def run_benchmark():
    reactor = MagicMock()
    # Mock monotonic to return incrementing time
    monotonic_time = [0.0]
    def get_monotonic():
        monotonic_time[0] += 0.000001
        return monotonic_time[0]
    reactor.monotonic.side_effect = get_monotonic
    
    bglogger = MagicMock()
    start_args = {'software_version': 'v0.1-test'}
    
    printer = klippy_module.Printer(reactor, bglogger, start_args)
    
    # 1. Benchmark add_object / lookup_object
    print("Benchmarking object lookup...")
    num_objects = 1000
    for i in range(num_objects):
        printer.add_object(f"test_obj_{i}", MagicMock())
    
    start_time = time.time()
    iterations = 100000
    for i in range(iterations):
        printer.lookup_object(f"test_obj_{i % num_objects}")
    end_time = time.time()
    print(f"Lookup time (100k iterations): {end_time - start_time:.4f}s")
    
    # 2. Benchmark send_event
    print("Benchmarking send_event...")
    def handler():
        pass
    for _ in range(10):
        printer.register_event_handler("test_event", handler)
    
    start_time = time.time()
    for _ in range(iterations):
        printer.send_event("test_event")
    end_time = time.time()
    print(f"Event dispatch time (100k iterations): {end_time - start_time:.4f}s")

    # 3. Benchmark lookup_objects (with prefix)
    print("Benchmarking lookup_objects with prefix...")
    start_time = time.time()
    for _ in range(10000):
        printer.lookup_objects("test_obj")
    end_time = time.time()
    print(f"Lookup prefix time (10k iterations): {end_time - start_time:.4f}s")

    # 4. Benchmark load_object with extras cache
    print("Benchmarking load_object with extras cache...")
    # Mock os.path.exists to be fast
    with MagicMock() as mock_exists:
        mock_exists.return_value = True
        import os
        orig_exists = os.path.exists
        os.path.exists = mock_exists
        
        # First call (uncached)
        printer.load_object(MagicMock(), "test_module")
        
        # Subsequence calls (cached)
        start_time = time.time()
        for _ in range(10000):
            printer.load_object(MagicMock(), "test_module")
        end_time = time.time()
        print(f"Load object time (10k iterations, cached): {end_time - start_time:.4f}s")
        
        os.path.exists = orig_exists

    # 5. Verify stats
    print("Verifying stats methods...")
    printer.set_reactor_stats(True)
    printer.send_event("test_event")
    stats = printer.get_reactor_stats()
    print(f"Reactor stats: {stats}")
    
    mem_stats = printer.get_memory_stats()
    print(f"Memory stats: {mem_stats}")

if __name__ == "__main__":
    run_benchmark()
