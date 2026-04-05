
import time
import sys
import os
import io
from unittest.mock import MagicMock

# Mock Klipper modules
sys.modules['util'] = MagicMock()
sys.modules['reactor'] = MagicMock()
sys.modules['serialhdl'] = MagicMock()
sys.modules['msgproto'] = MagicMock()
sys.modules['clocksync'] = MagicMock()

import importlib.util
spec = importlib.util.spec_from_file_location("console", "klippy/console.py")
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)

def run_benchmark():
    # Setup mock reactor and dependencies
    reactor = MagicMock()
    reactor.monotonic.side_effect = lambda: time.time()
    
    # Initialize KeyboardReader
    # Note: We need to bypass some actual I/O for benchmarking
    reader = console.KeyboardReader(reactor, "/dev/null", 250000, None, 64)
    reader.mcu_freq = 100000000.0
    
    # Benchmark 1: Line translation and evaluation
    print("Benchmarking line translation and evaluation...")
    lines = [
        "get_uptime",
        "SET myvar {123.4 * 2}",
        "reset_step_clock oid=4 clock={clock + 1000}",
        "DELAY {clock + 5000} get_status",
        "FLOOD 10 .01 get_uptime",
        "# This is a comment that should be ignored",
        "  LIST  "
    ]
    
    iterations = 10000
    start_time = time.time()
    for i in range(iterations):
        for line in lines:
            reader.translate(line, time.time())
    end_time = time.time()
    
    total_lines = iterations * len(lines)
    avg_time = (end_time - start_time) / total_lines
    print(f"Average time per line translation: {avg_time*1000000:.2f}us")

    # Benchmark 2: DUMP command logic (simulated)
    print("Benchmarking DUMP command logic (100 values)...")
    parts = ["DUMP", "0x12345678", "100", "32"]
    
    # Mock ser.send_with_response for DUMP
    reader.ser.send_with_response.return_value = {'val': 0xDEADBEEF}
    
    start_time = time.time()
    for i in range(100): # Run 100 times to get stable average
        # We redirect stdout to avoid console spam during benchmark
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        reader.command_DUMP(parts)
        sys.stdout = old_stdout
    end_time = time.time()
    
    avg_dump = (end_time - start_time) / 100
    print(f"Average time for DUMP (100 values): {avg_dump*1000:.2f}ms")

if __name__ == "__main__":
    run_benchmark()
