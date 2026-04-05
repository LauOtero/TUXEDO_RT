#!/usr/bin/env python3
"""
TUXEDO_RT: Extreme Performance Benchmark for Optimized util.py Methods
"""
import time
import sys
import os

# Add klippy to path
sys.path.insert(0, os.path.dirname(__file__))

from klippy.util import (
    get_cpu_info, get_device_info, get_linux_version, get_ip_addresses,
    get_uptime, get_version_from_file, get_git_version, FastCrc,
    HighResTimer, SystemMonitor, clear_info_cache
)

def benchmark_function(func, name, iterations=100, *args, **kwargs):
    """Benchmark a function with multiple iterations."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        times.append(end - start)

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print("20"
    return result

def benchmark_all():
    """Run comprehensive benchmarks on all optimized methods."""
    print("🚀 TUXEDO_RT: Extreme Performance Benchmark Suite")
    print("=" * 60)

    # Benchmark system info functions
    print("\n📊 System Information Functions:")
    cpu_info = benchmark_function(get_cpu_info, "get_cpu_info")
    device_info = benchmark_function(get_device_info, "get_device_info")
    linux_ver = benchmark_function(get_linux_version, "get_linux_version")
    ip_addrs = benchmark_function(get_ip_addresses, "get_ip_addresses")
    uptime = benchmark_function(get_uptime, "get_uptime")

    # Benchmark file operations
    print("\n📁 File Operations:")
    klippy_src = os.path.dirname(os.path.dirname(__file__))
    version = benchmark_function(get_version_from_file, "get_version_from_file", 50, klippy_src)

    # Benchmark git operations
    print("\n🔧 Git Operations:")
    clear_info_cache()
    git_info = benchmark_function(get_git_version, "get_git_version", 10)

    # Benchmark utility classes
    print("\n⚡ Utility Classes:")

    # FastCrc benchmark
    crc = FastCrc()
    test_data = b"Hello, World! This is a test string for CRC calculation." * 100
    crc_result = benchmark_function(crc.crc16, "FastCrc.crc16", 1000, test_data)

    # HighResTimer benchmark
    timer = HighResTimer()
    timer.start()
    time.sleep(0.001)  # Minimal sleep
    elapsed = timer.elapsed()
    print(f"HighResTimer.elapsed(): {elapsed:.6f}s (precision test)")

    # SystemMonitor benchmark
    monitor = SystemMonitor()
    stats = benchmark_function(monitor.get_stats, "SystemMonitor.get_stats", 50)

    print("\n" + "=" * 60)
    print("✅ All benchmarks completed successfully!")
    print("🎯 Extreme performance optimizations active")

    # Summary
    print("\n📈 Performance Summary:")
    print(f"   CPU Info: {cpu_info}")
    print(f"   Device: {device_info}")
    print(f"   Linux Version: {linux_ver}")
    print(f"   IP Addresses: {len(ip_addrs)} found")
    print(f"   Uptime: {uptime:.1f}s")
    print(f"   Version: {version}")
    print(f"   Git Version: {git_info.get('version', 'N/A')}")
    print(f"   CRC Result: 0x{crc_result:04X}")
    print(f"   System CPU: {stats['cpu_usage']:.1f}%")
    print(f"   System Memory: {stats['memory_usage_mb']:.1f}MB")

if __name__ == "__main__":
    try:
        benchmark_all()
    except Exception as e:
        print(f"❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()