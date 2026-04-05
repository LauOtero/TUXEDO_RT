
import time
import sys
import os
import io
import collections
from unittest.mock import MagicMock

# Mock Klipper modules
sys.modules['util'] = MagicMock()
sys.modules['reactor'] = MagicMock()
sys.modules['serialhdl'] = MagicMock()
sys.modules['msgproto'] = MagicMock()
sys.modules['clocksync'] = MagicMock()

import importlib.util
spec = importlib.util.spec_from_file_location("gcode", "klippy/gcode.py")
gcode_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gcode_module)

def run_benchmark():
    # Setup mock printer and dependencies
    printer = MagicMock()
    reactor = MagicMock()
    # Mock monotonic to return incrementing time
    monotonic_time = [0.0]
    def get_monotonic():
        monotonic_time[0] += 0.000001
        return monotonic_time[0]
    reactor.monotonic.side_effect = get_monotonic
    printer.get_reactor.return_value = reactor
    printer.get_start_args.return_value = {}
    
    # Initialize GCodeDispatch
    dispatch = gcode_module.GCodeDispatch(printer)
    dispatch._handle_ready() # Set to ready state
    
    # Register some dummy commands
    def cmd_dummy(gcmd):
        gcmd.get_float('X', 0.0)
        gcmd.get_float('Y', 0.0)
        gcmd.get_float('Z', 0.0)
        gcmd.get_float('E', 0.0)
        gcmd.get_float('F', 0.0)
    
    dispatch.register_command('G1', cmd_dummy)
    dispatch.register_command('G0', cmd_dummy)
    
    # Benchmark lines
    lines = [
        "G1 X100.0 Y100.0 Z0.2 E0.1 F3000",
        "G1 X110.0 Y100.0 E0.2",
        "G1 X110.0 Y110.0 E0.3",
        "G1 X100.0 Y110.0 E0.4",
        "M105",
        "SET_VELOCITY_LIMIT VELOCITY=300 ACCEL=3000", # Extended command style
        "G1 X100.0 Y100.0 E0.5 ; This is a comment",
        "N123 G1 X100 Y100*45" # Line number and checksum
    ]
    
    iterations = 5000
    total_lines = iterations * len(lines)
    
    print(f"Benchmarking G-Code dispatch with {total_lines} lines...")
    
    start_time = time.time()
    for _ in range(iterations):
        dispatch._process_commands(lines, need_ack=False)
    end_time = time.time()
    
    duration = end_time - start_time
    throughput = total_lines / duration
    avg_time = (duration / total_lines) * 1000000
    
    print(f"Total time: {duration:.4f}s")
    print(f"Throughput: {throughput:.2f} lines/sec")
    print(f"Average time per line: {avg_time:.2f}us")

if __name__ == "__main__":
    run_benchmark()
