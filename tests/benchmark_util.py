import time, sys, os
import logging
from typing import Dict, Any

# Ensure we can import klippy modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../klippy')))
import util

def benchmark_function(name, func, iterations=100, *args, **kwargs):
    start = time.time()
    for _ in range(iterations):
        func(*args, **kwargs)
    duration = time.time() - start
    avg = (duration / iterations) * 1000 # in ms
    print(f"Benchmark: {name:20} | Iterations: {iterations:5} | Total: {duration:8.4f}s | Avg: {avg:10.6f}ms")
    return avg

def run_benchmarks():
    print("=== Klippy Util Optimized Benchmarks ===")
    
    # clear cache before benchmarks
    util.clear_info_cache()
    
    # System info functions (first call - uncached)
    print("\n--- Uncached System Info (Now with Windows support) ---")
    benchmark_function("get_cpu_info", util.get_cpu_info, 1)
    util.clear_info_cache()
    benchmark_function("get_device_info", util.get_device_info, 1)
    util.clear_info_cache()
    benchmark_function("get_linux_version", util.get_linux_version, 1)
    util.clear_info_cache()
    benchmark_function("get_ip_addresses", util.get_ip_addresses, 1)
    util.clear_info_cache()
    benchmark_function("get_git_version", util.get_git_version, 1)
    
    # Cached system info
    print("\n--- Cached System Info (1000 iterations, using lru_cache) ---")
    benchmark_function("get_cpu_info (cached)", util.get_cpu_info, 1000)
    benchmark_function("get_device_info (cached)", util.get_device_info, 1000)
    benchmark_function("get_linux_version (cached)", util.get_linux_version, 1000)
    benchmark_function("get_ip_addresses (cached)", util.get_ip_addresses, 1000)
    benchmark_function("get_git_version (cached)", util.get_git_version, 1000)
    
    # New utilities
    print("\n--- New Utilities ---")
    crc = util.FastCrc()
    data = b"Hello world, testing CRC calculation performance"
    benchmark_function("FastCrc.crc16 (48b)", crc.crc16, 1000, data)
    
    monitor = util.SystemMonitor()
    benchmark_function("SystemMonitor.get_stats", monitor.get_stats, 100)
    
    timer = util.HighResTimer()
    benchmark_function("HighResTimer.elapsed", timer.elapsed, 1000)

    # File operations
    print("\n--- Atomic Write ---")
    benchmark_function("atomic_write", util.atomic_write, 10, "test_atomic.tmp", "test data")
    if os.path.exists("test_atomic.tmp"):
        os.unlink("test_atomic.tmp")
    
if __name__ == "__main__":
    # Disable logging for benchmarks
    logging.disable(logging.CRITICAL)
    run_benchmarks()
