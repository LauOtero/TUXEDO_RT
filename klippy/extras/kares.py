"""
KARES — Deterministic Recovery System with UPS monitoring.

Architecture:
  - NUT I/O runs in an isolated daemon thread with its own asyncio loop.
  - The Klipper reactor never touches sockets; it reads a lock-protected
    AtomicSnapshot written by the NUT thread.
  - Hot path (_sample_power_state) is a single integer flag read — O(1), no GIL
    contention beyond the one-word acquire/release of the lock.
"""

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# NUT protocol exceptions
# ---------------------------------------------------------------------------

class NUTProtocolError(Exception):
    """Unexpected NUT protocol response."""

class NUTAuthError(NUTProtocolError):
    """Authentication failure."""

class NUTConnectionError(NUTProtocolError):
    """TCP connection failure."""

class NUTTimeoutError(NUTProtocolError):
    """Operation timed out."""

class NUTDataError(NUTProtocolError):
    """Malformed VAR line."""


# ---------------------------------------------------------------------------
# AtomicSnapshot — lock-free-ish handoff between NUT thread and reactor
# ---------------------------------------------------------------------------

# Fault flag bitmask (matches legacy flags field)
_FLAG_OB = 0x1   # On battery
_FLAG_LB = 0x2   # Low battery


class AtomicSnapshot:
    """
    Thread-safe container for the latest UPS snapshot.

    The NUT thread writes; the Klipper reactor reads.  A short-lived
    threading.Lock protects the reference swap — never held during I/O.
    """
    __slots__ = ("_lock", "_snapshot", "_fault_flags")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, Any]] = None
        # Pre-computed integer bitmask so the reactor hot-path does one
        # integer comparison instead of a dict traversal.
        self._fault_flags: int = 0

    def write(self, snapshot: Dict[str, Any], fault_flags: int) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._fault_flags = fault_flags

    def read(self) -> Tuple[Optional[Dict[str, Any]], int]:
        """Return (snapshot, fault_flags) atomically."""
        with self._lock:
            return self._snapshot, self._fault_flags

    @property
    def snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._snapshot

    @property
    def fault_flags(self) -> int:
        with self._lock:
            return self._fault_flags


# ---------------------------------------------------------------------------
# NUTMonitor — async NUT client (runs inside NUTThread, never in the reactor)
# ---------------------------------------------------------------------------

class NUTMonitor:
    """
    Async NUT client with cache, validation, and critical-event callbacks.

    Must be driven by its own asyncio event loop (see NUTThread).
    The public surface exposed to the Klipper reactor is entirely
    synchronisation-primitive-based (AtomicSnapshot).
    """

    # Variables polled when LIST VAR is unsupported
    _COMMON_VARS = (
        "ups.status",
        "battery.charge",
        "battery.runtime",
        "battery.voltage",
        "battery.temperature",
        "input.voltage",
        "input.frequency",
        "output.voltage",
        "output.frequency",
        "ups.temperature",
        "ups.load",
        "ups.model",
        "ups.serial",
        "ups.mfr",
    )

    def __init__(
        self,
        host: str,
        port: int,
        ups_name: str,
        username: str,
        password: str,
        timeout: float,
        atomic: AtomicSnapshot,
        on_critical: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.ups_name = ups_name
        self.username = username
        self.password = password
        self.timeout = timeout
        self.atomic = atomic
        self.on_critical = on_critical
        self.logger = logger or logging.getLogger(__name__)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        # Per-variable cache: name -> (value, timestamp)
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._last_flags: int = 0

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def _open(self) -> None:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            await self._authenticate()
        except asyncio.TimeoutError as exc:
            self._close()
            raise NUTTimeoutError(str(exc)) from exc
        except OSError as exc:
            self._close()
            raise NUTConnectionError(str(exc)) from exc

    def _close(self) -> None:
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.reader = None
        self.writer = None

    async def _ensure_connected(self) -> None:
        if self.reader is None or self.writer is None:
            await self._open()

    # ------------------------------------------------------------------
    # Low-level send / receive
    # ------------------------------------------------------------------

    async def _send_line(self, line: str) -> None:
        if self.writer is None:
            raise NUTConnectionError("writer not connected")
        try:
            self.writer.write((line + "\n").encode())
            await asyncio.wait_for(self.writer.drain(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            self._close()
            raise NUTTimeoutError(str(exc)) from exc
        except OSError as exc:
            self._close()
            raise NUTConnectionError(str(exc)) from exc

    async def _recv_line(self) -> str:
        if self.reader is None:
            raise NUTConnectionError("reader not connected")
        try:
            data = await asyncio.wait_for(
                self.reader.readline(), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            self._close()
            raise NUTTimeoutError(str(exc)) from exc
        except OSError as exc:
            self._close()
            raise NUTConnectionError(str(exc)) from exc
        if not data:
            self._close()
            raise NUTConnectionError("server closed connection")
        return data.decode("utf-8", errors="ignore").strip()

    async def _send_expect_ok(self, line: str, exc_type: type) -> None:
        await self._send_line(line)
        resp = await self._recv_line()
        if resp.startswith("ERR"):
            raise exc_type(resp)
        if not resp.startswith("OK"):
            raise NUTProtocolError(resp)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> None:
        if not self.password:
            return
        if not self.username:
            raise NUTAuthError("NUT username required when password is set")
        await self._send_expect_ok("USERNAME %s" % self.username, NUTAuthError)
        await self._send_expect_ok("PASSWORD %s" % self.password, NUTAuthError)
        await self._send_expect_ok("LOGIN %s" % self.ups_name, NUTAuthError)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def query_var(self, var_name: str) -> Optional[str]:
        """Query a single NUT variable with one retry on connection failure."""
        for attempt in range(2):
            try:
                await self._ensure_connected()
                await self._send_line("GET VAR %s %s" % (self.ups_name, var_name))
                resp = await self._recv_line()
                if resp.startswith("ERR"):
                    return None
                if not resp.startswith("VAR "):
                    raise NUTProtocolError(resp)
                value = self._parse_var_value(resp)
                self._cache[var_name] = (value, time.monotonic())
                return value
            except NUTProtocolError as exc:
                self.logger.warning("NUT protocol error querying %s: %s", var_name, exc)
                return None
            except (NUTConnectionError, NUTTimeoutError) as exc:
                self.logger.warning("NUT connection error (attempt %d): %s", attempt, exc)
                self._close()
                if attempt == 0:
                    continue
                return None
        return None

    async def list_vars(self) -> Dict[str, str]:
        """Retrieve all UPS variables in one round-trip."""
        for attempt in range(2):
            try:
                await self._ensure_connected()
                await self._send_line("LIST VAR %s" % self.ups_name)
                first = await self._recv_line()
                if first.startswith("ERR"):
                    raise NUTProtocolError(first)
                result: Dict[str, str] = {}
                if first.startswith("BEGIN LIST VAR"):
                    while True:
                        line = await self._recv_line()
                        if line.startswith("END LIST VAR"):
                            break
                        if line.startswith("VAR "):
                            result[self._parse_var_name(line)] = self._parse_var_value(line)
                elif first.startswith("VAR "):
                    result[self._parse_var_name(first)] = self._parse_var_value(first)
                else:
                    raise NUTProtocolError(first)
                now = time.monotonic()
                for k, v in result.items():
                    self._cache[k] = (v, now)
                return result
            except NUTProtocolError as exc:
                self.logger.warning("NUT protocol error listing vars: %s", exc)
                return {}
            except (NUTConnectionError, NUTTimeoutError) as exc:
                self.logger.warning("NUT connection error (attempt %d): %s", attempt, exc)
                self._close()
                if attempt == 0:
                    continue
                return {}
        return {}

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    async def get_snapshot(self) -> Optional[Dict[str, Any]]:
        """
        Fetch a complete UPS snapshot.
        Falls back to individual queries if LIST VAR fails.
        Writes result to the AtomicSnapshot and fires callbacks.
        """
        vars_map = await self.list_vars()
        if not vars_map:
            vars_map = {}
            for var in self._COMMON_VARS:
                val = await self.query_var(var)
                if val is not None:
                    vars_map[var] = val
        if not vars_map:
            return None
        snapshot = self._build_snapshot(vars_map)
        self._publish(snapshot)
        return snapshot

    def _publish(self, snapshot: Dict[str, Any]) -> None:
        """Write snapshot to the shared AtomicSnapshot and fire on_critical."""
        ups = snapshot["ups"]
        flags = (
            (_FLAG_OB if ups["on_battery"] else 0)
            | (_FLAG_LB if ups["low_battery"] else 0)
        )
        # Write atomically — the reactor reads this
        self.atomic.write(snapshot, flags)

        # Detect critical transitions and fire the callback (still in NUT thread)
        prev = self._last_flags
        self._last_flags = flags
        if flags != prev and self.on_critical is not None:
            current_crit = {
                "online": ups["online"],
                "on_battery": ups["on_battery"],
                "low_battery": ups["low_battery"],
            }
            # Reconstruct previous state from bitmask for callback parity
            prev_crit = {
                "online": not bool(prev & _FLAG_OB),
                "on_battery": bool(prev & _FLAG_OB),
                "low_battery": bool(prev & _FLAG_LB),
            }
            try:
                self.on_critical(current_crit, prev_crit)
            except Exception as exc:
                self.logger.warning("on_critical callback raised: %s", exc)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_var_name(line: str) -> str:
        # "VAR <ups_name> <var_name> \"<value>\""
        parts = line.split(" ", 3)
        if len(parts) < 3:
            raise NUTDataError(line)
        return parts[2]

    @staticmethod
    def _parse_var_value(line: str) -> str:
        idx = line.find('"')
        if idx == -1:
            return ""
        end = line.find('"', idx + 1)
        if end == -1:
            return ""
        return line[idx + 1:end]

    @staticmethod
    def _to_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_status(status: Optional[str]) -> Dict[str, Any]:
        tokens: frozenset = frozenset()
        if status:
            tokens = frozenset(t.upper() for t in status.split() if t)
        return {
            "raw": status or "",
            "online": "OL" in tokens,
            "on_battery": "OB" in tokens,
            "low_battery": "LB" in tokens,
            "charging": "CHRG" in tokens,
            "discharging": "DISCHRG" in tokens,
            "replace_battery": "RB" in tokens,
        }

    def _build_snapshot(self, vars_map: Dict[str, str]) -> Dict[str, Any]:
        get = vars_map.get
        status = self._parse_status(get("ups.status"))
        return {
            "timestamp": time.time(),
            "raw": vars_map,
            "battery": {
                "charge_pct": self._to_float(get("battery.charge")),
                "runtime_s": self._to_int(get("battery.runtime")),
                "voltage": self._to_float(get("battery.voltage")),
                "temperature_c": self._to_float(get("battery.temperature")),
            },
            "input": {
                "voltage": self._to_float(get("input.voltage")),
                "frequency": self._to_float(get("input.frequency")),
            },
            "output": {
                "voltage": self._to_float(get("output.voltage")),
                "frequency": self._to_float(get("output.frequency")),
            },
            "ups": {
                "status": status["raw"],
                "online": status["online"],
                "on_battery": status["on_battery"],
                "low_battery": status["low_battery"],
                "charging": status["charging"],
                "discharging": status["discharging"],
                "replace_battery": status["replace_battery"],
                "load_pct": self._to_float(get("ups.load")),
                "temperature_c": self._to_float(get("ups.temperature")),
                "model": get("ups.model"),
                "serial": get("ups.serial"),
                "mfr": get("ups.mfr"),
            },
        }

    def get_cached(self, name: str) -> Optional[Tuple[str, float]]:
        """Return the last cached (value, monotonic_timestamp) for a variable."""
        return self._cache.get(name)


# ---------------------------------------------------------------------------
# NUTThread — daemon thread hosting the asyncio NUT poll loop
# ---------------------------------------------------------------------------

class NUTThread:
    """
    Runs NUTMonitor in a daemon thread with its own asyncio event loop.

    The Klipper reactor must never call into this thread directly; all
    data exchange is through AtomicSnapshot.
    """

    _MIN_BACKOFF = 1.0    # seconds
    _MAX_BACKOFF = 60.0

    def __init__(
        self,
        monitor: NUTMonitor,
        poll_interval: float,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._monitor = monitor
        self._poll_interval = max(0.1, poll_interval)
        self._stop = stop_event
        self._logger = logger or logging.getLogger(__name__)
        self._thread = threading.Thread(
            target=self._thread_main,
            name="kares-nut",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout=timeout)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._poll_loop())
        finally:
            loop.close()

    async def _poll_loop(self) -> None:
        backoff = self._MIN_BACKOFF
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                await self._monitor.get_snapshot()
                backoff = self._MIN_BACKOFF  # reset on success
            except Exception as exc:
                self._logger.warning("NUT poll error: %s", exc)
                # Back off to avoid hammering a broken upsd
                await asyncio.sleep(min(backoff, self._MAX_BACKOFF))
                backoff = min(backoff * 2.0, self._MAX_BACKOFF)
                continue
            elapsed = time.monotonic() - t0
            wait = max(0.0, self._poll_interval - elapsed)
            # Use asyncio.sleep so the stop event can interrupt if needed
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.get_event_loop().run_in_executor(
                        None, self._stop.wait, wait
                    )),
                    timeout=wait + 0.1,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


# ---------------------------------------------------------------------------
# Kares — Klipper plugin entry point
# ---------------------------------------------------------------------------

class Kares:
    """
    UPS-aware deterministic checkpoint/recovery for Klipper.

    Two backends:
      gpio  — reads a sysfs GPIO value file (sync, trivial).
      nut   — polls upsd via NUTMonitor in an isolated daemon thread;
              the reactor reads only an AtomicSnapshot (zero I/O in hot path).
    """

    def __init__(self, config: Any) -> None:
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        bmap = {"gpio": "gpio", "nut": "nut"}
        self.backend = config.getchoice("backend", bmap, "gpio")
        self.poll_interval = config.getfloat("poll_interval", 0.05, minval=0.005)
        self.debounce_time = config.getfloat("debounce_ms", 25.0, minval=0.0) / 1000.0
        self.shutdown_on_fault = config.getboolean("shutdown_on_fault", True)

        # GPIO backend config
        self.gpio_value_path = config.get(
            "gpio_value_path", "/sys/class/gpio/gpio24/value"
        )
        self.gpio_active_low = config.getboolean("gpio_active_low", True)

        # NUT backend config
        self.nut_host = config.get("nut_host", "127.0.0.1")
        self.nut_port = config.getint("nut_port", 3493, minval=1, maxval=65535)
        self.nut_ups_name = config.get("nut_ups_name", "ups")
        self.nut_username = config.get("nut_username", "")
        self.nut_password = config.get("nut_password", "")
        self.nut_timeout = config.getfloat("nut_timeout", 0.20, minval=0.01)

        # Checkpoint
        default_path = os.path.expanduser(
            "~/printer_data/config/kares_checkpoint.json"
        )
        self.checkpoint_path = config.get("checkpoint_path", default_path)

        # Runtime state
        self.fault_active = False
        self.fault_count = 0
        self.last_fault_time = 0.0
        self.last_fault_reason = 0
        self.last_voltage_mv = 0
        self.last_flags = 0
        self.last_sent_mcus = 0
        self.last_error = ""
        self._fault_since: Optional[float] = None

        # NUT objects (only when backend == 'nut')
        self._nut_atomic: Optional[AtomicSnapshot] = None
        self._nut_monitor: Optional[NUTMonitor] = None
        self._nut_stop: Optional[threading.Event] = None
        self._nut_thread: Optional[NUTThread] = None

        # GCode commands
        self.gcode = self.printer.lookup_object("gcode")
        cmds = [
            ("KARES_SAVE_CHECKPOINT",   self.cmd_KARES_SAVE_CHECKPOINT,
             self.cmd_KARES_SAVE_CHECKPOINT_help),
            ("KARES_RESUME_CHECKPOINT", self.cmd_KARES_RESUME_CHECKPOINT,
             self.cmd_KARES_RESUME_CHECKPOINT_help),
            ("KARES_HELP",              self.cmd_KARES_HELP,
             self.cmd_KARES_HELP_help),
            ("KARES_NUT_STATUS",        self.cmd_KARES_NUT_STATUS,
             self.cmd_KARES_NUT_STATUS_help),
        ]
        for name, fn, desc in cmds:
            self.gcode.register_command(name, fn, desc=desc)

        # Klipper events
        self.printer.register_event_handler("klippy:connect",    self._handle_connect)
        self.printer.register_event_handler("klippy:shutdown",   self._handle_shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._handle_disconnect)

        # Reactor timer (always registered; armed in _handle_connect)
        self.sample_timer = self.reactor.register_timer(
            self._sample_power_state, self.reactor.NEVER
        )

        # Initialise NUT subsystem before connect so the thread is ready
        if self.backend == "nut":
            self._nut_atomic = AtomicSnapshot()
            self._nut_monitor = NUTMonitor(
                host=self.nut_host,
                port=self.nut_port,
                ups_name=self.nut_ups_name,
                username=self.nut_username,
                password=self.nut_password,
                timeout=self.nut_timeout,
                atomic=self._nut_atomic,
                on_critical=self._handle_nut_critical,
                logger=logging.getLogger("kares.nut"),
            )
            self._nut_stop = threading.Event()
            self._nut_thread = NUTThread(
                monitor=self._nut_monitor,
                poll_interval=self.poll_interval,
                stop_event=self._nut_stop,
                logger=logging.getLogger("kares.nut.thread"),
            )
            self._nut_thread.start()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _handle_connect(self) -> None:
        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)
        if os.path.exists(self.checkpoint_path):
            try:
                with open(self.checkpoint_path, "r") as fh:
                    ckpt = json.load(fh)
                reason = ckpt.get("reason", "unknown")
                age_min = (time.time() - ckpt.get("timestamp", 0)) / 60.0
                self.gcode.respond_info(
                    "\n"
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                    "KARES: RECOVERY CHECKPOINT DETECTED\n"
                    "  Cause : %s\n"
                    "  Age   : %.1f minutes\n"
                    "--------------------------------------------------\n"
                    "  To resume : KARES_RESUME_CHECKPOINT\n"
                    "  To discard: delete %s\n"
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                    % (reason, age_min, self.checkpoint_path)
                )
            except Exception:
                logging.exception("KARES: error reading checkpoint on connect")

    def _handle_shutdown(self) -> None:
        self.reactor.update_timer(self.sample_timer, self.reactor.NEVER)
        self._stop_nut()

    def _handle_disconnect(self) -> None:
        self._maybe_checkpoint("disconnect")
        self.reactor.update_timer(self.sample_timer, self.reactor.NEVER)
        self._stop_nut()

    def _stop_nut(self) -> None:
        if self._nut_stop is not None:
            self._nut_stop.set()
        if self._nut_thread is not None:
            self._nut_thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # NUT critical-state callback (called from NUT thread)
    # ------------------------------------------------------------------

    def _handle_nut_critical(
        self, current: Dict[str, Any], previous: Dict[str, Any]
    ) -> None:
        """
        Invoked by NUTMonitor when online/on_battery/low_battery changes.
        Runs in the NUT daemon thread — must not call Klipper internals.
        """
        logging.warning("KARES: UPS critical change %s -> %s", previous, current)

        if current.get("on_battery") and not previous.get("on_battery"):
            self._save_panic_checkpoint(
                "power_loss_nut", command="auto_power_loss_nut"
            )
        elif current.get("low_battery") and not previous.get("low_battery"):
            self._save_panic_checkpoint(
                "low_battery_nut", command="auto_low_battery_nut"
            )

    # ------------------------------------------------------------------
    # Fault reading — hot path (reactor thread)
    # ------------------------------------------------------------------

    def _read_gpio_fault(self) -> Tuple[bool, int, int, int]:
        try:
            with open(self.gpio_value_path, "r") as fh:
                raw = fh.read().strip()
            active = raw != "0"
            if self.gpio_active_low:
                active = not active
            return active, 0, 0, 1
        except Exception:
            return False, 0, 0, 0

    def _read_nut_fault(self) -> Tuple[bool, int, int, int]:
        """
        Hot path: reads pre-computed fault_flags from AtomicSnapshot.
        Zero I/O, zero asyncio, one lock acquire.
        """
        if self._nut_atomic is None:
            return False, 0, 0, 0

        snapshot, flags = self._nut_atomic.read()
        if snapshot is None:
            # NUT thread hasn't delivered a snapshot yet
            return False, 0, 0, 0

        fault = bool(flags)
        # Derive voltage_mv for logging
        voltage_mv = 0
        vin = snapshot["input"]["voltage"]
        if vin is None:
            vin = snapshot["battery"]["voltage"]
        if vin is not None:
            voltage_mv = int(vin * 1000.0)

        reason = 2 if (flags & _FLAG_LB) else 1
        return fault, voltage_mv, flags, reason

    def _read_fault_state(self) -> Tuple[bool, int, int, int]:
        if self.backend == "nut":
            return self._read_nut_fault()
        return self._read_gpio_fault()

    # ------------------------------------------------------------------
    # Reactor timer callback
    # ------------------------------------------------------------------

    def _sample_power_state(self, eventtime: float) -> float:
        try:
            fault, voltage_mv, flags, reason = self._read_fault_state()
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            logging.warning("KARES: read error: %s", exc)
            return eventtime + self.poll_interval

        if fault:
            if self._fault_since is None:
                self._fault_since = eventtime
            if (not self.fault_active
                    and eventtime - self._fault_since >= self.debounce_time):
                self.fault_active = True
                self._emit_power_fault(reason, voltage_mv, flags)
        else:
            self._fault_since = None
            self.fault_active = False

        return eventtime + self.poll_interval

    # ------------------------------------------------------------------
    # Fault emission
    # ------------------------------------------------------------------

    def _emit_power_fault(
        self, reason: int, voltage_mv: int, flags: int
    ) -> None:
        self.fault_count += 1
        self.last_fault_time = self.reactor.monotonic()
        self.last_fault_reason = int(reason) & 0xFF
        self.last_voltage_mv = int(voltage_mv) & 0xFFFFFFFF
        self.last_flags = int(flags) & 0xFFFFFFFF

        self._save_panic_checkpoint(self.last_fault_reason)

        sent_mcus = 0
        for _, mcu in self.printer.lookup_objects("mcu"):
            if hasattr(mcu, "notify_power_fault") and mcu.notify_power_fault(
                self.last_fault_reason, self.last_voltage_mv, self.last_flags
            ):
                sent_mcus += 1
        self.last_sent_mcus = sent_mcus

        if self.shutdown_on_fault:
            self.printer.invoke_shutdown(
                "UPS POWER_FAULT reason=%d voltage_mv=%d flags=0x%X sent_mcus=%d"
                % (self.last_fault_reason, self.last_voltage_mv,
                   self.last_flags, self.last_sent_mcus)
            )

    def trigger_test_fault(
        self, reason: int = 0x7F, voltage_mv: int = 0, flags: int = 0x8000
    ) -> None:
        self._emit_power_fault(reason, voltage_mv, flags)

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    def _maybe_checkpoint(self, reason: str) -> None:
        try:
            eventtime = self.reactor.monotonic()
            vsd = self.printer.lookup_object("virtual_sdcard", None)
            ps = self.printer.lookup_object("print_stats", None)
            ps_state = (ps.get_status(eventtime) if ps else {}).get("state")
            if vsd is None:
                if ps_state not in ("printing", "paused"):
                    return
                self._save_panic_checkpoint(reason, command="auto_" + reason)
                return
            status = vsd.get_status(eventtime)
            if (not status.get("is_active")
                    and not status.get("cmd_from_sd")
                    and ps_state not in ("printing", "paused")):
                return
            self._save_panic_checkpoint(reason, command="auto_" + reason)
        except Exception as exc:
            logging.error(
                "KARES: automatic checkpoint failed (%s): %s", reason, exc
            )

    def _save_panic_checkpoint(
        self, reason: Any, command: Optional[str] = None
    ) -> None:
        try:
            eventtime = self.reactor.monotonic()
            toolhead = self.printer.lookup_object("toolhead")
            print_time = toolhead.get_last_move_time()
            kin = toolhead.get_kinematics()
            vsd = self.printer.lookup_object("virtual_sdcard", None)
            vsd_status = vsd.get_status(eventtime) if vsd else {}
            cmd_from_sd = bool(vsd_status.get("cmd_from_sd", False))
            sd_file_position = int(vsd_status.get("file_position", 0) or 0)
            sd_next_file_position = int(
                vsd_status.get("next_file_position", sd_file_position) or 0
            )
            sd_current_line = vsd_status.get("current_line", "")

            if command is None and cmd_from_sd:
                command = sd_current_line or (
                    "SD@%d-%d" % (sd_file_position, sd_next_file_position)
                )

            gcode_move = self.printer.lookup_object("gcode_move", None)
            gm_status = gcode_move.get_status(eventtime) if gcode_move else {}

            checkpoint: Dict[str, Any] = {
                "timestamp": time.time(),
                "snapshot_id": time.time_ns(),
                "eventtime": eventtime,
                "print_time": print_time,
                "reason": reason,
                "last_command": command or "N/A",
                "toolhead": {
                    "position": toolhead.get_position(),
                    "velocity": toolhead.get_status(eventtime)["velocity"],
                },
                "steppers": {},
                "extruders": {},
                "manual_steppers": {},
                "extruder_steppers": {},
                "mcus": {},
                "gcode": {
                    "file_position": vsd_status.get("file_position"),
                    "next_file_position": sd_next_file_position,
                    "resume_file_position": sd_file_position,
                    "file_path": vsd_status.get("file_path"),
                    "is_active": vsd_status.get("is_active"),
                    "progress": vsd_status.get("progress"),
                    "cmd_from_sd": cmd_from_sd,
                    "current_line": sd_current_line,
                    "current_line_start": vsd_status.get("current_line_start"),
                    "current_line_end": vsd_status.get("current_line_end"),
                    "last_line": vsd_status.get("last_line"),
                    "last_line_start": vsd_status.get("last_line_start"),
                    "last_line_end": vsd_status.get("last_line_end"),
                },
                "gcode_move": {
                    "position": list(gm_status.get("position", [])),
                    "gcode_position": list(gm_status.get("gcode_position", [])),
                    "base_position": list(gm_status.get("homing_origin", [])),
                    "absolute_coordinates": gm_status.get("absolute_coordinates"),
                    "absolute_extrude": gm_status.get("absolute_extrude"),
                    "speed": gm_status.get("speed"),
                    "speed_factor": gm_status.get("speed_factor"),
                    "extrude_factor": gm_status.get("extrude_factor"),
                },
            }

            for s in kin.get_steppers():
                name = s.get_name()
                mcu_pos = s.get_past_mcu_position(print_time)
                checkpoint["steppers"][name] = {
                    "mcu_pos": mcu_pos,
                    "commanded_pos": s.mcu_to_commanded_position(mcu_pos),
                }

            for i in range(99):
                ename = "extruder" if i == 0 else "extruder%d" % i
                obj = self.printer.lookup_object(ename, None)
                if obj is None:
                    break
                htr = obj.get_heater()
                htr_status = htr.get_status(eventtime)
                checkpoint["extruders"][ename] = {
                    "pos": obj.find_past_position(print_time),
                    "target": htr_status["target"],
                    "temp": htr_status["temperature"],
                }

            for msname, ms in self.printer.lookup_objects("manual_stepper"):
                for s in ms.get_steppers():
                    name = s.get_name()
                    mcu_pos = s.get_past_mcu_position(print_time)
                    checkpoint["manual_steppers"][name] = {
                        "mcu_pos": mcu_pos,
                        "commanded_pos": s.mcu_to_commanded_position(mcu_pos),
                        "manual_stepper_parent": msname,
                    }

            for esname, es in self.printer.lookup_objects("extruder_stepper"):
                checkpoint["extruder_steppers"][esname] = {
                    "pos": es.find_past_position(print_time),
                }

            bed = self.printer.lookup_object("heater_bed", None)
            if bed:
                bed_status = bed.get_status(eventtime)
                checkpoint["heater_bed"] = {
                    "target": bed_status["target"],
                    "temp": bed_status["temperature"],
                }

            queued_pending = False
            for mcu_name, mcu in self.printer.lookup_objects("mcu"):
                mcu_ept = mcu.estimated_print_time(eventtime)
                snap_clock = mcu.print_time_to_clock(print_time)
                est_clock = mcu.print_time_to_clock(mcu_ept)
                delta = max(0, snap_clock - est_clock)
                entry: Dict[str, Any] = {
                    "estimated_print_time": mcu_ept,
                    "snapshot_clock": snap_clock,
                    "estimated_clock": est_clock,
                    "queued_clock_delta": delta,
                    "shutdown_clock": mcu.get_shutdown_clock(),
                }
                if hasattr(mcu, "get_urt_capabilities"):
                    entry["urt"] = mcu.get_urt_capabilities()
                checkpoint["mcus"][mcu_name] = entry
                if delta > 0:
                    queued_pending = True

            if cmd_from_sd:
                if queued_pending:
                    checkpoint["gcode"]["resume_strategy"] = "replay_inflight_command"
                    checkpoint["gcode"]["resume_file_position"] = int(
                        vsd_status.get("current_line_start", sd_file_position) or 0
                    )
                else:
                    checkpoint["gcode"]["resume_strategy"] = "continue_after_inflight"
                    checkpoint["gcode"]["resume_file_position"] = sd_next_file_position
            else:
                checkpoint["gcode"]["resume_strategy"] = "host_stream_or_macro"

            ckpt_dir = os.path.dirname(self.checkpoint_path)
            if ckpt_dir:
                os.makedirs(ckpt_dir, exist_ok=True)
            tmp = self.checkpoint_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(checkpoint, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.checkpoint_path)
            if ckpt_dir:
                try:
                    fd = os.open(ckpt_dir, os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except Exception:
                    pass
            logging.info("KARES: checkpoint saved → %s", self.checkpoint_path)
        except Exception as exc:
            logging.error("KARES: failed to save checkpoint: %s", exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, eventtime: Optional[float] = None) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "fault_active": self.fault_active,
            "fault_count": self.fault_count,
            "last_fault_time": self.last_fault_time,
            "last_fault_reason": self.last_fault_reason,
            "last_voltage_mv": self.last_voltage_mv,
            "last_flags": self.last_flags,
            "last_sent_mcus": self.last_sent_mcus,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # GCode commands
    # ------------------------------------------------------------------

    cmd_KARES_SAVE_CHECKPOINT_help = "Save a manual KARES recovery checkpoint"

    def cmd_KARES_SAVE_CHECKPOINT(self, gcmd: Any) -> None:
        reason = gcmd.get("REASON", "manual_macro")
        command = gcmd.get("COMMAND", gcmd.get_commandline())
        self._save_panic_checkpoint(reason, command=command)
        gcmd.respond_info("KARES: checkpoint saved (reason: %s)" % reason)

    cmd_KARES_RESUME_CHECKPOINT_help = (
        "Resume a virtual SD print from a KARES checkpoint"
    )

    def cmd_KARES_RESUME_CHECKPOINT(self, gcmd: Any) -> None:
        path = gcmd.get("PATH", self.checkpoint_path)
        if not os.path.exists(path):
            raise gcmd.error("KARES: checkpoint not found: %s" % path)
        try:
            with open(path, "r") as fh:
                ckpt = json.load(fh)
        except Exception as exc:
            raise gcmd.error("KARES: invalid checkpoint: %s" % exc)

        gstate = ckpt.get("gcode") or {}
        file_path = gstate.get("file_path")
        resume_pos = gstate.get("resume_file_position", gstate.get("file_position", 0))
        strategy = gstate.get("resume_strategy", "host_stream_or_macro")

        if not file_path:
            raise gcmd.error("KARES: checkpoint has no file_path")
        if strategy == "host_stream_or_macro":
            raise gcmd.error(
                "KARES: checkpoint is not an SD print (strategy=%s)" % strategy
            )
        try:
            resume_pos = int(resume_pos)
        except Exception:
            resume_pos = 0

        vsd = self.printer.lookup_object("virtual_sdcard", None)
        if vsd is None or not hasattr(vsd, "resume_from_checkpoint"):
            raise gcmd.error("KARES: virtual_sdcard not available")
        vsd.resume_from_checkpoint(file_path, resume_pos)
        gcmd.respond_info(
            "KARES: resuming file=%s pos=%d strategy=%s"
            % (file_path, resume_pos, strategy)
        )

    cmd_KARES_HELP_help = "Show KARES recovery system help"

    def cmd_KARES_HELP(self, gcmd: Any) -> None:
        has_ckpt = (
            "PRESENT (pending resume)"
            if os.path.exists(self.checkpoint_path)
            else "none"
        )
        gcmd.respond_info(
            "\n"
            "KARES — Deterministic Recovery System\n"
            "--------------------------------------\n"
            "  KARES_SAVE_CHECKPOINT    Save state manually\n"
            "  KARES_RESUME_CHECKPOINT  Resume from last checkpoint\n"
            "  KARES_NUT_STATUS         Show UPS metrics (NUT backend)\n"
            "  KARES_HELP               This message\n"
            "--------------------------------------\n"
            "Checkpoint: %s\n" % has_ckpt
        )

    cmd_KARES_NUT_STATUS_help = (
        "Show current UPS metrics from the NUT daemon"
    )

    def cmd_KARES_NUT_STATUS(self, gcmd: Any) -> None:
        if self.backend != "nut" or self._nut_atomic is None:
            gcmd.respond_info("KARES: NUT backend is not enabled.")
            return

        snapshot = self._nut_atomic.snapshot
        if snapshot is None:
            gcmd.respond_info(
                "KARES: no NUT data yet — waiting for first poll."
            )
            return

        b = snapshot["battery"]
        i = snapshot["input"]
        o = snapshot["output"]
        u = snapshot["ups"]
        runtime_s = b["runtime_s"] or 0

        def _f(v: Optional[float], decimals: int = 1) -> str:
            return ("%.*f" % (decimals, v)) if v is not None else "N/A"

        gcmd.respond_info(
            "KARES: UPS status (NUT)\n"
            "  Updated : %s\n"
            "  Status  : %s  online=%s  on_battery=%s  low_battery=%s\n"
            "  Battery : %s%%  runtime=%ds (%.1fmin)\n"
            "  Input   : %sV  %sHz\n"
            "  Output  : %sV  %sHz\n"
            "  Load    : %s%%  temp=%sC\n"
            "  Model   : %s  S/N: %s  Mfr: %s\n"
            % (
                time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(snapshot["timestamp"])
                ),
                u["status"],
                u["online"], u["on_battery"], u["low_battery"],
                _f(b["charge_pct"]), runtime_s, runtime_s / 60.0,
                _f(i["voltage"]), _f(i["frequency"]),
                _f(o["voltage"]), _f(o.get("frequency")),
                _f(u["load_pct"]), _f(u["temperature_c"]),
                u["model"] or "N/A",
                u["serial"] or "N/A",
                u["mfr"] or "N/A",
            )
        )


def load_config(config: Any) -> Kares:
    return Kares(config)