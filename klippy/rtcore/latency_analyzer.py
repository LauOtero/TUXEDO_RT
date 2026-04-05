import statistics
import struct
from multiprocessing import shared_memory

from .rt_core import LATENCY_HEADER_FORMAT, LATENCY_HEADER_SIZE


class LatencyAnalyzer:
    def __init__(self, latency_budget_us=50.0):
        self.latency_budget_us = float(latency_budget_us)

    def analyze_samples(self, samples):
        samples = [float(sample) for sample in samples]
        if not samples:
            return {
                "count": 0,
                "min_us": 0.0,
                "max_us": 0.0,
                "avg_us": 0.0,
                "median_us": 0.0,
                "p99_us": 0.0,
                "jitter_us": 0.0,
                "anomalies": [],
                "within_budget": True,
            }
        ordered = sorted(samples)
        p99_index = min(len(ordered) - 1, int(len(ordered) * 0.99))
        anomalies = [value for value in samples
                     if value > self.latency_budget_us]
        return {
            "count": len(samples),
            "min_us": ordered[0],
            "max_us": ordered[-1],
            "avg_us": sum(samples) / len(samples),
            "median_us": statistics.median(ordered),
            "p99_us": ordered[p99_index],
            "jitter_us": ordered[-1] - ordered[0],
            "anomalies": anomalies,
            "within_budget": not anomalies,
        }

    def read_shared_buffer(self, name, capacity):
        shm = shared_memory.SharedMemory(name=name, create=False)
        try:
            count, write_index = struct.unpack_from(
                LATENCY_HEADER_FORMAT, shm.buf, 0)
            available = min(int(count), int(capacity), int(write_index))
            start = max(0, write_index - available)
            samples = []
            for offset_index in range(available):
                slot = (start + offset_index) % capacity
                offset = LATENCY_HEADER_SIZE + slot * 8
                samples.append(struct.unpack_from("d", shm.buf, offset)[0])
            return samples
        finally:
            shm.close()

    def render_text_report(self, samples):
        stats = self.analyze_samples(samples)
        return (
            "count={count} min_us={min_us:.3f} max_us={max_us:.3f} "
            "avg_us={avg_us:.3f} median_us={median_us:.3f} "
            "p99_us={p99_us:.3f} jitter_us={jitter_us:.3f} "
            "within_budget={within_budget} anomalies={anomaly_count}"
        ).format(
            count=stats["count"],
            min_us=stats["min_us"],
            max_us=stats["max_us"],
            avg_us=stats["avg_us"],
            median_us=stats["median_us"],
            p99_us=stats["p99_us"],
            jitter_us=stats["jitter_us"],
            within_budget="yes" if stats["within_budget"] else "no",
            anomaly_count=len(stats["anomalies"]),
        )
