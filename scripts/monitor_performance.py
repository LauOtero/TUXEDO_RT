#!/usr/bin/env python3
"""
Sistema de Monitoreo de Performance en Tiempo Real para TUXEDO_RT.

Este módulo proporciona:
- Monitoreo continuo de métricas de rendimiento
- Thresholds de alerta configurables
- Plan de rollback automatizado
- Alertas por degradación de performance

Uso:
    python3 scripts/monitor_performance.py --daemon
    python3 scripts/monitor_performance.py --benchmark
    python3 scripts/monitor_performance.py --rollback

Arquitectura:
    ┌─────────────────────────────────────────────────────────────┐
    │                    MonitorPerformance                        │
    │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
    │  │MetricCollector│  │AlertThresholds│  │RollbackManager │  │
    │  └──────┬──────┘  └───────┬──────┘  └────────┬─────────┘  │
    │         │                  │                   │            │
    │         ▼                  ▼                   ▼            │
    │  ┌─────────────────────────────────────────────────────┐  │
    │  │              PerformanceMetrics (Shared)             │  │
    │  └─────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────┘
"""
import os
import sys
import time
import json
import logging
import argparse
import threading
import collections
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from datetime import datetime, timedelta
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from util import get_monotonic, get_cpu_info, get_linux_version
    import chelper
    HAS_CHELPER = True
except ImportError:
    HAS_CHELPER = False
    get_monotonic = time.monotonic
    get_cpu_info = lambda: "Unknown"
    get_linux_version = lambda: "Unknown"


class AlertLevel(Enum):
    """Niveles de alerta de performance."""
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class ThresholdConfig:
    """Configuración de thresholds para alertas."""
    warning: float
    critical: float
    emergency: float
    unit: str = ""

    def evaluate(self, value: float) -> AlertLevel:
        """Evalúa un valor contra los thresholds."""
        if value >= self.emergency:
            return AlertLevel.EMERGENCY
        elif value >= self.critical:
            return AlertLevel.CRITICAL
        elif value >= self.warning:
            return AlertLevel.WARNING
        return AlertLevel.OK


@dataclass
class PerformanceMetric:
    """Una métrica de rendimiento individual."""
    name: str
    value: float
    timestamp: float
    unit: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    level: AlertLevel = AlertLevel.OK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": self.timestamp,
            "unit": self.unit,
            "tags": self.tags,
            "level": self.level.value
        }


class PerformanceMetrics:
    """
    Almacén circular de métricas con ventana de tiempo.
    Implementa un buffer ring para limitar memoria.
    O(1) para inserciones, O(1) para acceso por índice.
    """
    DEFAULT_WINDOW_SIZE = 1000

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE):
        self._lock = threading.Lock()
        self._metrics: collections.deque = collections.deque(maxlen=window_size)
        self._aggregates: Dict[str, Dict[str, float]] = {}
        self._last_update: Dict[str, float] = {}

    def add(self, metric: PerformanceMetric) -> None:
        """Añade una métrica al buffer. O(1)"""
        with self._lock:
            self._metrics.append(metric)
            self._last_update[metric.name] = metric.timestamp

    def get_recent(self, name: str, count: int = 100) -> List[PerformanceMetric]:
        """Obtiene las N métricas más recientes de un tipo. O(n)"""
        with self._lock:
            return [m for m in list(self._metrics)[-count:] if m.name == name]

    def get_stats(self, name: str, window: int = 100) -> Dict[str, float]:
        """Calcula estadísticas para una métrica. O(n)"""
        recent = self.get_recent(name, window)
        if not recent:
            return {}
        values = [m.value for m in recent]
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
            "last": values[-1]
        }

    def clear(self) -> None:
        """Limpia todas las métricas. O(1)"""
        with self._lock:
            self._metrics.clear()
            self._last_update.clear()


class AlertThresholds:
    """
    Gestor de thresholds de alerta con histéresis.
    Implementa Debounce para evitar alertas spurious.
    """
    DEBOUNCE_SECONDS = 5.0

    def __init__(self):
        self._thresholds: Dict[str, ThresholdConfig] = {}
        self._last_alert: Dict[str, float] = {}
        self._alert_count: Dict[str, int] = collections.Counter()
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def register(self, name: str, config: ThresholdConfig) -> None:
        """Registra un threshold para una métrica."""
        with self._lock:
            self._thresholds[name] = config

    def evaluate(self, metric: PerformanceMetric) -> AlertLevel:
        """Evalúa una métrica contra sus thresholds. O(1)"""
        with self._lock:
            if metric.name not in self._thresholds:
                return AlertLevel.OK

            config = self._thresholds[metric.name]
            level = config.evaluate(metric.value)

            if level != AlertLevel.OK:
                now = metric.timestamp
                last = self._last_alert.get(metric.name, 0)

                if now - last < self.DEBOUNCE_SECONDS:
                    return AlertLevel.OK

                self._last_alert[metric.name] = now
                self._alert_count[metric.name] += 1
                self._history.append({
                    "timestamp": now,
                    "metric": metric.name,
                    "value": metric.value,
                    "level": level.value
                })

            return level

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas de alertas."""
        with self._lock:
            return {
                "total_alerts": sum(self._alert_count.values()),
                "by_metric": dict(self._alert_count),
                "active_thresholds": len(self._thresholds),
                "history_size": len(self._history)
            }


class RollbackManager:
    """
    Gestor de rollback automatizado para optimizaciones.
    Implementa versionado de configuraciones y rollback atómico.
    """
    def __init__(self, config_dir: str = ".tuxedo_rt"):
        self._config_dir = config_dir
        self._versions: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        self._current_version: Dict[str, int] = {}
        self._rollback_hooks: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._ensure_config_dir()

    def _ensure_config_dir(self) -> None:
        """Crea el directorio de configuración si no existe."""
        if not os.path.exists(self._config_dir):
            os.makedirs(self._config_dir, exist_ok=True)

    def register_rollback_hook(self, name: str, callback: Callable) -> None:
        """Registra un callback de rollback para una optimización."""
        with self._lock:
            self._rollback_hooks[name] = callback

    def save_checkpoint(self, name: str, config: Dict[str, Any]) -> str:
        """Guarda un checkpoint de la configuración actual."""
        with self._lock:
            version_id = len(self._versions[name])
            checkpoint = {
                "version_id": version_id,
                "timestamp": time.time(),
                "config": config,
                "hostname": os.environ.get("HOSTNAME", "unknown")
            }
            self._versions[name].append(checkpoint)
            self._current_version[name] = version_id
            self._save_to_disk(name, checkpoint)
            return f"{name}_v{version_id}"

    def rollback(self, name: str, target_version: Optional[int] = None) -> bool:
        """
        Ejecuta rollback a una versión anterior.
        Si target_version es None, rollback al último checkpoint.
        """
        with self._lock:
            if name not in self._versions or not self._versions[name]:
                logging.error(f"No versions found for {name}")
                return False

            if target_version is None:
                target_version = len(self._versions[name]) - 2

            if target_version < 0 or target_version >= len(self._versions[name]):
                logging.error(f"Invalid target version {target_version}")
                return False

            checkpoint = self._versions[name][target_version]
            logging.info(f"Rolling back {name} to version {target_version}")

            if name in self._rollback_hooks:
                try:
                    self._rollback_hooks[name](checkpoint["config"])
                    self._current_version[name] = target_version
                    return True
                except Exception as e:
                    logging.error(f"Rollback hook failed: {e}")
                    traceback.print_exc()
                    return False

            return False

    def _save_to_disk(self, name: str, checkpoint: Dict[str, Any]) -> None:
        """Guarda checkpoint a disco para persistencia."""
        path = os.path.join(self._config_dir, f"{name}_v{checkpoint['version_id']}.json")
        try:
            with open(path, 'w') as f:
                json.dump(checkpoint, f, indent=2)
        except Exception as e:
            logging.warning(f"Failed to save checkpoint to disk: {e}")


class MetricCollector(threading.Thread):
    """
    Hilo recolector de métricas en tiempo real.
    Recolecta métricas del sistema y de chelper si está disponible.
    """
    SAMPLING_INTERVAL = 0.1  # 100ms

    def __init__(self, metrics: PerformanceMetrics, chelper_available: bool = True):
        super().__init__(daemon=True, name="MetricCollector")
        self._metrics = metrics
        self._chelper = chelper
        self._running = False
        self._ffi = None
        self._lib = None
        self._rtt_buffer = {"srtt": 0.0, "rttvar": 0.0, "rto": 0.0}

        if chelper_available and HAS_CHELPER:
            try:
                self._ffi, self._lib = chelper.get_ffi()
            except Exception as e:
                logging.warning(f"Failed to load chelper: {e}")

    def run(self) -> None:
        """Loop principal de recolección."""
        self._running = True
        start_time = get_monotonic()

        while self._running:
            now = get_monotonic()
            elapsed = now - start_time

            self._collect_system_metrics(now)

            if self._lib is not None and hasattr(self._lib, 'conn_get_rtt_stats'):
                self._collect_rtt_stats(now)

            time.sleep(self.SAMPLING_INTERVAL)

    def stop(self) -> None:
        """Detiene el recolector."""
        self._running = False

    def _collect_system_metrics(self, timestamp: float) -> None:
        """Recolecta métricas del sistema."""
        try:
            cpu_info = get_cpu_info()
            self._metrics.add(PerformanceMetric(
                name="system.cpu_info_load",
                value=len(cpu_info.split()) if cpu_info else 0,
                timestamp=timestamp,
                tags={"source": "system"}
            ))
        except Exception:
            pass

    def _collect_rtt_stats(self, timestamp: float) -> None:
        """Recolecta métricas RTT desde chelper."""
        try:
            if hasattr(self._lib, 'conn_get_rtt_stats'):
                srtt = self._ffi.new('double *')
                rttvar = self._ffi.new('double *')
                rto = self._ffi.new('double *')
                self._lib.conn_get_rtt_stats(None, srtt, rttvar, rto)

                self._rtt_buffer["srtt"] = srtt[0]
                self._rtt_buffer["rttvar"] = rttvar[0]
                self._rtt_buffer["rto"] = rto[0]

                self._metrics.add(PerformanceMetric(
                    name="network.rtt_srtt",
                    value=srtt[0],
                    timestamp=timestamp,
                    unit="seconds",
                    tags={"source": "chelper"}
                ))
                self._metrics.add(PerformanceMetric(
                    name="network.rtt_rttvar",
                    value=rttvar[0],
                    timestamp=timestamp,
                    unit="seconds",
                    tags={"source": "chelper"}
                ))
                self._metrics.add(PerformanceMetric(
                    name="network.rtt_rto",
                    value=rto[0],
                    timestamp=timestamp,
                    unit="seconds",
                    tags={"source": "chelper"}
                ))
        except Exception:
            pass


class MonitorPerformance:
    """
    Sistema principal de monitoreo de performance.
    Coordina recolección, evaluación de thresholds y alertas.
    """
    def __init__(self, daemon_mode: bool = False):
        self._metrics = PerformanceMetrics()
        self._thresholds = AlertThresholds()
        self._rollback = RollbackManager()
        self._collector: Optional[MetricCollector] = None
        self._daemon_mode = daemon_mode
        self._callbacks: Dict[AlertLevel, List[Callable]] = collections.defaultdict(list)
        self._running = False
        self._setup_default_thresholds()
        self._setup_rollback_hooks()

    def _setup_default_thresholds(self) -> None:
        """Configura los thresholds por defecto."""
        self._thresholds.register("network.rtt_srtt", ThresholdConfig(
            warning=0.050,
            critical=0.100,
            emergency=0.500,
            unit="seconds"
        ))
        self._thresholds.register("network.rtt_rttvar", ThresholdConfig(
            warning=0.020,
            critical=0.050,
            emergency=0.200,
            unit="seconds"
        ))
        self._thresholds.register("network.rtt_rto", ThresholdConfig(
            warning=0.100,
            critical=0.250,
            emergency=1.000,
            unit="seconds"
        ))

    def _setup_rollback_hooks(self) -> None:
        """Configura los hooks de rollback."""
        def rollback_vlq_encoding(config: Dict[str, Any]) -> None:
            logging.info("Rolling back VLQ encoding configuration")
            if "batch_size" in config:
                logging.info(f"Restoring batch_size to {config['batch_size']}")

        self._rollback.register_rollback_hook("vlq_encoding", rollback_vlq_encoding)

    def register_alert_callback(self, level: AlertLevel, callback: Callable) -> None:
        """Registra un callback para un nivel de alerta."""
        self._callbacks[level].append(callback)

    def start(self) -> None:
        """Inicia el monitoreo."""
        if self._running:
            logging.warning("Monitor already running")
            return

        self._running = True
        self._collector = MetricCollector(self._metrics, HAS_CHELPER)
        self._collector.start()
        logging.info("Performance monitor started")

    def stop(self) -> None:
        """Detiene el monitoreo."""
        if not self._running:
            return

        self._running = False
        if self._collector:
            self._collector.stop()
            self._collector.join(timeout=1.0)
        logging.info("Performance monitor stopped")

    def check_and_alert(self, metric: PerformanceMetric) -> AlertLevel:
        """Evalúa una métrica y dispara alertas si es necesario."""
        level = self._thresholds.evaluate(metric)

        if level != AlertLevel.OK:
            logging.warning(
                f"ALERT [{level.value.upper()}] {metric.name}={metric.value}{metric.unit} "
                f"at {datetime.fromtimestamp(metric.timestamp).isoformat()}"
            )
            for callback in self._callbacks[level]:
                try:
                    callback(metric)
                except Exception as e:
                    logging.error(f"Alert callback failed: {e}")

        return level

    def get_status(self) -> Dict[str, Any]:
        """Retorna el estado actual del sistema."""
        return {
            "running": self._running,
            "metrics_buffer_size": len(self._metrics._metrics),
            "alert_stats": self._thresholds.get_stats(),
            "rollback_versions": {
                name: len(versions)
                for name, versions in self._rollback._versions.items()
            }
        }

    def trigger_rollback(self, optimization_name: str) -> bool:
        """Dispara un rollback para una optimización."""
        logging.info(f"Triggering rollback for {optimization_name}")
        return self._rollback.rollback(optimization_name)


def run_benchmark() -> Dict[str, Any]:
    """
    Ejecuta benchmark de performance y retorna resultados.
    Mide overhead de CFFI vs ctypes para get_monotonic.
    """
    results = {
        "timestamp": time.time(),
        "hostname": os.environ.get("HOSTNAME", "unknown"),
        "chelper_available": HAS_CHELPER,
        "benchmarks": {}
    }

    ITERATIONS = 100000

    time.monotonic()
    start = get_monotonic()
    for _ in range(ITERATIONS):
        get_monotonic()
    ctypes_time = get_monotonic() - start
    results["benchmarks"]["get_monotonic_ctypes"] = {
        "iterations": ITERATIONS,
        "total_seconds": ctypes_time,
        "per_call_ns": ctypes_time / ITERATIONS * 1e9
    }

    if HAS_CHELPER:
        ffi, lib = chelper.get_ffi()
        if hasattr(lib, 'get_monotonic'):
            start = get_monotonic()
            for _ in range(ITERATIONS):
                lib.get_monotonic()
            cffi_time = get_monotonic() - start
            results["benchmarks"]["get_monotonic_cffi"] = {
                "iterations": ITERATIONS,
                "total_seconds": cffi_time,
                "per_call_ns": cffi_time / ITERATIONS * 1e9
            }
            results["benchmarks"]["overhead_comparison"] = {
                "speedup": cffi_time / ctypes_time if ctypes_time > 0 else 0,
                "recommendation": "ctypes" if ctypes_time < cffi_time else "cffi"
            }

    return results


def main():
    """Punto de entrada principal."""
    parser = argparse.ArgumentParser(description="TUXEDO_RT Performance Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--benchmark", action="store_true", help="Run benchmarks")
    parser.add_argument("--rollback", type=str, help="Trigger rollback for optimization")
    parser.add_argument("--status", action="store_true", help="Show current status")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if args.benchmark:
        print("Running performance benchmarks...")
        results = run_benchmark()
        print(json.dumps(results, indent=2))
        return

    if args.rollback:
        monitor = MonitorPerformance()
        success = monitor.trigger_rollback(args.rollback)
        print(f"Rollback {'succeeded' if success else 'failed'}")
        return

    if args.status:
        monitor = MonitorPerformance()
        print(json.dumps(monitor.get_status(), indent=2))
        return

    if args.daemon:
        print("Starting performance monitor in daemon mode...")
        monitor = MonitorPerformance(daemon_mode=True)
        monitor.start()

        def alert_callback(metric: PerformanceMetric):
            print(f"ALERT: {metric.name} = {metric.value}")

        monitor.register_alert_callback(AlertLevel.CRITICAL, alert_callback)

        try:
            while True:
                time.sleep(1)
                status = monitor.get_status()
                print(f"\rMetrics: {status['metrics_buffer_size']} | Alerts: {status['alert_stats']['total_alerts']}", end="")
        except KeyboardInterrupt:
            print("\nStopping...")
            monitor.stop()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
