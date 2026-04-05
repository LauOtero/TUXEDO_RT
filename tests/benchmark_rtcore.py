import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../klippy')))

from rtcore.latency_analyzer import LatencyAnalyzer
from rtcore.rt_core import RealTimeCore


def run_benchmark(samples=500000):
    rt_core = RealTimeCore()
    rt_core.configure_platform_profile(
        "sbc", cpu_affinity=[0], buffer_bytes=1024 * 1024,
        latency_capacity=min(samples, 8192))
    start = time.perf_counter()
    for index in range(samples):
        rt_core.record_latency(8.0 + float(index & 31))
    duration = time.perf_counter() - start
    stats = rt_core.get_latency_stats()
    analyzer = LatencyAnalyzer(latency_budget_us=50.0)
    report = analyzer.analyze_samples(rt_core.get_latency_samples())
    throughput = samples / duration if duration else 0.0
    print("samples=%d" % (samples,))
    print("duration_s=%.6f" % (duration,))
    print("throughput_ops_s=%.2f" % (throughput,))
    print("avg_latency_us=%.3f" % (stats["avg_us"],))
    print("max_latency_us=%.3f" % (stats["max_us"],))
    print("jitter_us=%.3f" % (stats["jitter_us"],))
    print("p99_us=%.3f" % (report["p99_us"],))
    print("anomalies=%d" % (len(report["anomalies"]),))
    print("within_50us_budget=%s" % (
        "yes" if report["within_budget"] else "no",))
    print("within_500k_target=%s" % ("yes" if throughput >= 500000 else "no",))
    rt_core.close_shared_latency_buffer()


if __name__ == "__main__":
    run_benchmark()
