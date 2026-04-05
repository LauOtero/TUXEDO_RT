import ctypes
import logging
import os
import struct
import threading
import time
from collections import deque
from multiprocessing import shared_memory
from typing import Any, Callable, Dict, List, Optional

SCHED_FIFO = 1
MCL_CURRENT = 1
MCL_FUTURE = 2
REALTIME_PRIORITY_CLASS = 0x00000100
THREAD_PRIORITY_TIME_CRITICAL = 15
THREAD_PRIORITY_HIGHEST = 2
LATENCY_RECORD_SIZE = 8
LATENCY_HEADER_FORMAT = "QQ"
LATENCY_HEADER_SIZE = struct.calcsize(LATENCY_HEADER_FORMAT)

class SchedParam(ctypes.Structure):
    _fields_ = [("sched_priority", ctypes.c_int)]

class RealTimeCore:
    def __init__(self):
        self.threads = {}
        self.running = False
        self._mlock_buffers = []
        self._latency_ring = deque(maxlen=4096)
        self._latency_lock = threading.Lock()
        self._shared_latency = None
        self._shared_latency_capacity = 0
        self._shared_latency_name = None
        self._setup_logging()
        self._lock_memory()

    def _setup_logging(self):
        self.logger = logging.getLogger('RTCore')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[%(asctime)s] [RT] %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _load_libc(self):
        return ctypes.CDLL("libc.so.6", use_errno=True)

    def _load_kernel32(self):
        return ctypes.windll.kernel32

    def _lock_memory(self):
        if os.name == 'posix':
            try:
                libc = self._load_libc()
                res = libc.mlockall(MCL_CURRENT | MCL_FUTURE)
                if res != 0:
                    err = ctypes.get_errno()
                    self.logger.warning("Failed to lock memory (mlockall). Error: %s",
                                        os.strerror(err))
                else:
                    self.logger.info("Memory locked successfully (mlockall).")
            except Exception as e:
                self.logger.error("Error during memory locking: %s", e)
        else:
            self.preallocate_buffer(1024 * 1024)

    def preallocate_buffer(self, size: int):
        size = max(4096, int(size))
        buf = ctypes.create_string_buffer(size)
        stride = 4096
        for offset in range(0, size, stride):
            buf[offset] = 0
        if os.name == 'nt':
            try:
                kernel32 = self._load_kernel32()
                kernel32.VirtualLock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
                kernel32.VirtualLock.restype = ctypes.c_int
                kernel32.VirtualLock(ctypes.addressof(buf), ctypes.c_size_t(size))
            except Exception as e:
                self.logger.warning("VirtualLock unavailable: %s", e)
        self._mlock_buffers.append(buf)
        return buf

    def create_shared_latency_buffer(self, name: str = "klipper_rt_latency",
                                     capacity: int = 4096):
        self.close_shared_latency_buffer()
        capacity = max(64, int(capacity))
        shm = shared_memory.SharedMemory(
            name=name, create=True,
            size=LATENCY_HEADER_SIZE + capacity * LATENCY_RECORD_SIZE)
        struct.pack_into(LATENCY_HEADER_FORMAT, shm.buf, 0, 0, 0)
        self._shared_latency = shm
        self._shared_latency_capacity = capacity
        self._shared_latency_name = shm.name
        return shm.name

    def close_shared_latency_buffer(self):
        if self._shared_latency is None:
            return
        try:
            self._shared_latency.close()
            self._shared_latency.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._shared_latency = None
            self._shared_latency_capacity = 0
            self._shared_latency_name = None

    def record_latency(self, latency_us: float):
        latency_us = float(latency_us)
        with self._latency_lock:
            self._latency_ring.append(latency_us)
            if self._shared_latency is not None:
                count, write_index = struct.unpack_from(
                    LATENCY_HEADER_FORMAT, self._shared_latency.buf, 0)
                slot = write_index % self._shared_latency_capacity
                offset = LATENCY_HEADER_SIZE + slot * LATENCY_RECORD_SIZE
                struct.pack_into("d", self._shared_latency.buf, offset, latency_us)
                struct.pack_into(
                    LATENCY_HEADER_FORMAT, self._shared_latency.buf, 0,
                    count + 1, write_index + 1)

    def get_latency_samples(self) -> List[float]:
        with self._latency_lock:
            return list(self._latency_ring)

    def get_latency_stats(self) -> Dict[str, float]:
        samples = self.get_latency_samples()
        if not samples:
            return {
                "count": 0,
                "min_us": 0.0,
                "max_us": 0.0,
                "avg_us": 0.0,
                "jitter_us": 0.0,
            }
        return {
            "count": len(samples),
            "min_us": min(samples),
            "max_us": max(samples),
            "avg_us": sum(samples) / len(samples),
            "jitter_us": max(samples) - min(samples),
        }

    def set_cpu_affinity(self, cpus: List[int]) -> bool:
        cpus = sorted({int(cpu) for cpu in cpus if int(cpu) >= 0})
        if not cpus:
            return False
        try:
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, cpus)
                return True
            if os.name == 'nt':
                mask = 0
                for cpu in cpus:
                    mask |= 1 << cpu
                kernel32 = self._load_kernel32()
                kernel32.SetProcessAffinityMask.argtypes = [
                    ctypes.c_void_p, ctypes.c_size_t]
                kernel32.SetProcessAffinityMask.restype = ctypes.c_int
                handle = kernel32.GetCurrentProcess()
                return bool(kernel32.SetProcessAffinityMask(handle, mask))
        except Exception as e:
            self.logger.warning("Unable to set CPU affinity: %s", e)
        return False

    def get_platform_profile(self, profile: str) -> Dict[str, Any]:
        profiles = {
            "sbc": {
                "scheduler": "SCHED_FIFO",
                "priority": 95,
                "cpu_affinity": [1, 2, 3],
                "isolated_cpus": "1-3",
                "latency_budget_us": 50.0,
                "buffer_bytes": 2 * 1024 * 1024,
            },
            "windows": {
                "scheduler": "RTX64/Win32-REALTIME",
                "priority": 95,
                "cpu_affinity": [2, 3],
                "isolated_cpus": "2-3",
                "latency_budget_us": 50.0,
                "buffer_bytes": 2 * 1024 * 1024,
            },
            "kvm": {
                "scheduler": "SCHED_FIFO",
                "priority": 90,
                "cpu_affinity": [2, 3, 4, 5],
                "isolated_cpus": "2-5",
                "latency_budget_us": 50.0,
                "buffer_bytes": 4 * 1024 * 1024,
            },
        }
        return dict(profiles.get(profile, profiles["sbc"]))

    def configure_platform_profile(self, profile: str,
                                   cpu_affinity: Optional[List[int]] = None,
                                   buffer_bytes: Optional[int] = None,
                                   latency_capacity: int = 4096) -> Dict[str, Any]:
        selected = self.get_platform_profile(profile)
        cpus = cpu_affinity or selected["cpu_affinity"]
        if buffer_bytes is None:
            buffer_bytes = selected["buffer_bytes"]
        selected["affinity_applied"] = self.set_cpu_affinity(cpus)
        selected["buffer_bytes"] = buffer_bytes
        self.preallocate_buffer(buffer_bytes)
        if self._shared_latency is None:
            self.create_shared_latency_buffer(capacity=latency_capacity)
        return selected

    def set_realtime_priority(self, priority: int = 99):
        if os.name == 'posix':
            try:
                libc = self._load_libc()
                param = SchedParam(priority)
                res = libc.sched_setscheduler(0, SCHED_FIFO, ctypes.byref(param))
                if res != 0:
                    err = ctypes.get_errno()
                    self.logger.warning(
                        "Failed to set SCHED_FIFO (prio=%s). Error: %s",
                        priority, os.strerror(err))
                else:
                    self.logger.info("Thread priority SCHED_FIFO set to %s.",
                                     priority)
            except Exception as e:
                self.logger.error("Error setting RT priority: %s", e)
        elif os.name == 'nt':
            try:
                kernel32 = self._load_kernel32()
                process = kernel32.GetCurrentProcess()
                thread = kernel32.GetCurrentThread()
                kernel32.SetPriorityClass(process, REALTIME_PRIORITY_CLASS)
                thread_prio = THREAD_PRIORITY_TIME_CRITICAL
                if priority < 90:
                    thread_prio = THREAD_PRIORITY_HIGHEST
                kernel32.SetThreadPriority(thread, thread_prio)
            except Exception as e:
                self.logger.warning("Unable to set Windows RT priority: %s", e)
        else:
            self.logger.warning("Real-time scheduling not supported on this OS.")

    def set_priority(self, priority: int = 99):
        self.set_realtime_priority(priority)

    def register_thread(self, name: str, target: Callable, priority: int = 50,
                        is_critical: bool = False,
                        cpu_affinity: Optional[List[int]] = None,
                        prealloc_bytes: int = 0):
        def wrapped_target():
            if is_critical:
                self.set_realtime_priority(priority)
            if cpu_affinity:
                self.set_cpu_affinity(cpu_affinity)
            if prealloc_bytes:
                self.preallocate_buffer(prealloc_bytes)
            try:
                target()
            except Exception as e:
                self.logger.error("Critical thread '%s' died: %s", name, e)
                
        thread = threading.Thread(target=wrapped_target, name=name, daemon=True)
        self.threads[name] = {
            'thread': thread,
            'priority': priority,
            'is_critical': is_critical,
            'cpu_affinity': cpu_affinity,
            'prealloc_bytes': prealloc_bytes,
        }
        self.logger.info("Registered thread '%s' (Priority: %s, Critical: %s).",
                         name, priority, is_critical)

    def start_all(self):
        """Starts all registered RT threads."""
        self.running = True
        for name, t_info in self.threads.items():
            if not t_info['thread'].is_alive():
                t_info['thread'].start()
                self.logger.info(f"Thread '{name}' started.")

    def stop_all(self):
        self.running = False
        self.logger.info("Stopping RT Core services...")

    def get_high_res_time(self) -> float:
        return time.perf_counter()

    def map_klipper_priority(self, klipper_prio: int) -> int:
        mapping = {0: 99, 1: 90, 2: 85, 3: 80}
        return mapping.get(klipper_prio, 85)
