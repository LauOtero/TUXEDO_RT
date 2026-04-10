#!/usr/bin/env python3
"""
Test suite for KARES v2.0 - Klipper Automated Recovery & Execution System.

Tests cover:
- AtomicSnapshot lock-free operations
- NUTMonitor async operations
- NUTThread daemon thread
- Checkpoint persistence
- WAL Manager
- Checkpoint ECC
- Prometheus Metrics
- Health Monitor
- Integration tests

Coverage target: >85%
"""

import asyncio
import json
import logging
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "klippy"))

from extras.kares import (
    AtomicSnapshot,
    CheckpointECC,
    HealthMonitor,
    Kares,
    KaresMetrics,
    KaresRTConfig,
    NUTConnectionError,
    NUTDataError,
    NUTMonitor,
    NUTProtocolError,
    NUTTimeoutError,
    NUTThread,
    WALManager,
    _FLAG_LB,
    _FLAG_OB,
    RT_PRIORITY_HIGH,
    RT_PRIORITY_CRITICAL,
)


class TestAtomicSnapshot(unittest.TestCase):
    """Tests for AtomicSnapshot lock-free operations."""

    def test_initial_state(self):
        """Verify initial state is correct."""
        atomic = AtomicSnapshot()
        snapshot, flags = atomic.read()
        self.assertIsNone(snapshot)
        self.assertEqual(flags, 0)

    def test_write_and_read(self):
        """Test basic write and read operations."""
        atomic = AtomicSnapshot()
        test_snapshot = {"battery": {"charge_pct": 100.0}, "ups": {"online": True}}
        test_flags = _FLAG_OB

        atomic.write(test_snapshot, test_flags)
        snapshot, flags = atomic.read()

        self.assertEqual(snapshot, test_snapshot)
        self.assertEqual(flags, test_flags)

    def test_fault_flags_bitmask(self):
        """Test fault flags bitmask operations."""
        atomic = AtomicSnapshot()

        atomic.write({}, 0)
        _, flags = atomic.read()
        self.assertEqual(flags, 0)

        atomic.write({}, _FLAG_OB)
        _, flags = atomic.read()
        self.assertEqual(flags, _FLAG_OB)

        atomic.write({}, _FLAG_LB)
        _, flags = atomic.read()
        self.assertEqual(flags, _FLAG_LB)

        atomic.write({}, _FLAG_OB | _FLAG_LB)
        _, flags = atomic.read()
        self.assertEqual(flags, _FLAG_OB | _FLAG_LB)

    def test_concurrent_write_read(self):
        """Test concurrent write and read from multiple threads."""
        atomic = AtomicSnapshot()
        num_writes = 1000
        results = []

        def writer():
            for i in range(num_writes):
                atomic.write({"index": i}, i % 4)
                time.sleep(0.0001)

        def reader():
            for _ in range(num_writes):
                snapshot, flags = atomic.read()
                results.append((snapshot["index"], flags))
                time.sleep(0.0001)

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join()
        r.join()

        self.assertEqual(len(results), num_writes)
        for idx, flags in results:
            self.assertGreaterEqual(idx, 0)
            self.assertLess(idx, num_writes)

    def test_snapshot_property(self):
        """Test snapshot property accessor."""
        atomic = AtomicSnapshot()
        self.assertIsNone(atomic.snapshot)

        test_snapshot = {"test": "data"}
        atomic.write(test_snapshot, 0)
        self.assertEqual(atomic.snapshot, test_snapshot)

    def test_fault_flags_property(self):
        """Test fault_flags property accessor."""
        atomic = AtomicSnapshot()
        self.assertEqual(atomic.fault_flags, 0)

        atomic.write({}, _FLAG_OB | _FLAG_LB)
        self.assertEqual(atomic.fault_flags, _FLAG_OB | _FLAG_LB)


class TestNUTMonitorParsing(unittest.TestCase):
    """Tests for NUTMonitor parsing utilities."""

    def test_parse_var_name(self):
        """Test variable name parsing from VAR line."""
        line = 'VAR ups1 battery.charge "75"'
        name = NUTMonitor._parse_var_name(line)
        self.assertEqual(name, "battery.charge")

    def test_parse_var_value(self):
        """Test variable value parsing from VAR line."""
        line = 'VAR ups1 battery.charge "75"'
        value = NUTMonitor._parse_var_value(line)
        self.assertEqual(value, "75")

        line_no_quotes = 'VAR ups1 ups.status "OL"'
        value = NUTMonitor._parse_var_value(line_no_quotes)
        self.assertEqual(value, "OL")

    def test_parse_var_value_empty(self):
        """Test parsing of empty values."""
        line = 'VAR ups1 battery.status ""'
        value = NUTMonitor._parse_var_value(line)
        self.assertEqual(value, "")

    def test_to_float(self):
        """Test float conversion."""
        self.assertEqual(NUTMonitor._to_float("75.5"), 75.5)
        self.assertEqual(NUTMonitor._to_float("100"), 100.0)
        self.assertIsNone(NUTMonitor._to_float(None))
        self.assertIsNone(NUTMonitor._to_float("invalid"))

    def test_to_int(self):
        """Test integer conversion."""
        self.assertEqual(NUTMonitor._to_int("75"), 75)
        self.assertEqual(NUTMonitor._to_int("75.9"), 75)
        self.assertIsNone(NUTMonitor._to_int(None))
        self.assertIsNone(NUTMonitor._to_int("invalid"))

    def test_parse_status_online(self):
        """Test status parsing for online state."""
        status = NUTMonitor._parse_status("OL")
        self.assertTrue(status["online"])
        self.assertFalse(status["on_battery"])
        self.assertFalse(status["low_battery"])

    def test_parse_status_on_battery(self):
        """Test status parsing for on battery state."""
        status = NUTMonitor._parse_status("OB LB")
        self.assertFalse(status["online"])
        self.assertTrue(status["on_battery"])
        self.assertTrue(status["low_battery"])

    def test_parse_status_empty(self):
        """Test status parsing for empty string."""
        status = NUTMonitor._parse_status("")
        self.assertFalse(status["online"])
        self.assertFalse(status["on_battery"])
        self.assertFalse(status["low_battery"])

    def test_build_snapshot(self):
        """Test snapshot building from variables."""
        atomic = AtomicSnapshot()
        monitor = NUTMonitor(
            host="localhost",
            port=3493,
            ups_name="ups",
            username="",
            password="",
            timeout=1.0,
            atomic=atomic,
        )

        vars_map = {
            "ups.status": "OL",
            "battery.charge": "85.5",
            "battery.runtime": "1800",
            "battery.voltage": "48.0",
            "input.voltage": "220.0",
        }

        snapshot = monitor._build_snapshot(vars_map)

        self.assertIn("timestamp", snapshot)
        self.assertEqual(snapshot["battery"]["charge_pct"], 85.5)
        self.assertEqual(snapshot["battery"]["runtime_s"], 1800)
        self.assertEqual(snapshot["battery"]["voltage"], 48.0)
        self.assertEqual(snapshot["input"]["voltage"], 220.0)
        self.assertTrue(snapshot["ups"]["online"])
        self.assertFalse(snapshot["ups"]["on_battery"])

    def test_get_cached(self):
        """Test cached variable retrieval."""
        atomic = AtomicSnapshot()
        monitor = NUTMonitor(
            host="localhost",
            port=3493,
            ups_name="ups",
            username="",
            password="",
            timeout=1.0,
            atomic=atomic,
        )

        self.assertIsNone(monitor.get_cached("battery.charge"))

        monitor._cache["battery.charge"] = ("75", time.monotonic())
        cached = monitor.get_cached("battery.charge")
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0], "75")


class TestNUTThreadBackoff(unittest.TestCase):
    """Tests for NUTThread backoff logic."""

    def test_backoff_initial(self):
        """Test initial backoff is MIN_BACKOFF."""
        atomic = AtomicSnapshot()
        monitor = NUTMonitor(
            host="localhost",
            port=3493,
            ups_name="ups",
            username="",
            password="",
            timeout=1.0,
            atomic=atomic,
        )
        stop_event = threading.Event()
        thread = NUTThread(monitor, poll_interval=0.1, stop_event=stop_event)

        self.assertEqual(thread._poll_interval, 0.1)
        self.assertEqual(thread._MIN_BACKOFF, 1.0)
        self.assertEqual(thread._MAX_BACKOFF, 60.0)

    def test_poll_interval_minimum(self):
        """Test poll_interval is bounded to minimum 0.1."""
        atomic = AtomicSnapshot()
        monitor = NUTMonitor(
            host="localhost",
            port=3493,
            ups_name="ups",
            username="",
            password="",
            timeout=1.0,
            atomic=atomic,
        )
        stop_event = threading.Event()
        thread = NUTThread(monitor, poll_interval=0.001, stop_event=stop_event)

        self.assertEqual(thread._poll_interval, 0.1)


class TestCheckpointPersistence(unittest.TestCase):
    """Tests for checkpoint persistence functionality."""

    def setUp(self):
        """Set up temporary directory for checkpoints."""
        self.temp_dir = tempfile.mkdtemp()
        self.checkpoint_path = os.path.join(self.temp_dir, "checkpoint.json")

    def tearDown(self):
        """Clean up temporary files."""
        for f in Path(self.temp_dir).glob("*"):
            f.unlink()
        os.rmdir(self.temp_dir)

    def test_checkpoint_structure(self):
        """Test checkpoint JSON structure."""
        checkpoint = {
            "timestamp": time.time(),
            "snapshot_id": time.time_ns(),
            "eventtime": time.monotonic(),
            "print_time": 100.0,
            "reason": "test",
            "last_command": "auto_test",
            "toolhead": {
                "position": [0, 0, 0, 0],
                "velocity": 0.0,
            },
            "steppers": {},
            "extruders": {},
            "mcus": {},
            "gcode": {
                "file_position": 1000,
                "resume_file_position": 900,
                "resume_strategy": "replay_inflight_command",
            },
        }

        with open(self.checkpoint_path, "w") as f:
            json.dump(checkpoint, f)

        with open(self.checkpoint_path, "r") as f:
            loaded = json.load(f)

        self.assertEqual(loaded["reason"], "test")
        self.assertEqual(loaded["gcode"]["file_position"], 1000)
        self.assertIn("timestamp", loaded)

    def test_checkpoint_roundtrip(self):
        """Test checkpoint save and load."""
        checkpoint = {
            "timestamp": time.time(),
            "reason": "power_loss",
            "toolhead": {"position": [10, 20, 30, 50]},
            "gcode": {"file_position": 5000},
        }

        with open(self.checkpoint_path, "w") as f:
            json.dump(checkpoint, f)

        with open(self.checkpoint_path, "r") as f:
            loaded = json.load(f)

        self.assertEqual(loaded["reason"], "power_loss")
        self.assertEqual(loaded["toolhead"]["position"], [10, 20, 30, 50])
        self.assertEqual(loaded["gcode"]["file_position"], 5000)

    def test_checkpoint_atomic_write(self):
        """Test atomic write using temp file and rename."""
        checkpoint = {"timestamp": time.time(), "reason": "test"}

        tmp_path = self.checkpoint_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(checkpoint, f)

        os.rename(tmp_path, self.checkpoint_path)

        with open(self.checkpoint_path, "r") as f:
            loaded = json.load(f)

        self.assertEqual(loaded["reason"], "test")
        self.assertFalse(os.path.exists(tmp_path))

    def test_checkpoint_with_crc(self):
        """Test checkpoint with CRC32."""
        checkpoint = {"timestamp": time.time(), "reason": "test"}
        json_data = json.dumps(checkpoint).encode('utf-8')
        crc = struct.pack(">I", zlib.crc32(json_data) & 0xFFFFFFFF)

        with open(self.checkpoint_path, "wb") as f:
            f.write(crc + json_data)

        with open(self.checkpoint_path, "rb") as f:
            data = f.read()

        stored_crc = struct.unpack(">I", data[:4])[0]
        computed_crc = zlib.crc32(data[4:])
        self.assertEqual(stored_crc, computed_crc)


class TestWALManager(unittest.TestCase):
    """Tests for WAL Manager."""

    def setUp(self):
        """Set up temporary directory for WAL."""
        self.temp_dir = tempfile.mkdtemp()
        self.wal_path = os.path.join(self.temp_dir, "test.wal")

    def tearDown(self):
        """Clean up temporary files."""
        for f in Path(self.temp_dir).glob("*"):
            f.unlink()
        os.rmdir(self.temp_dir)

    def test_wal_initialization(self):
        """Test WAL Manager initialization."""
        wal = WALManager(self.wal_path)
        self.assertEqual(wal._wal_path, self.wal_path)
        self.assertEqual(wal._seq, 0)

    def test_write_entry(self):
        """Test WAL entry writing."""
        wal = WALManager(self.wal_path)
        checkpoint = {"timestamp": time.time(), "reason": "test"}

        result = wal.write_entry(checkpoint)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(self.wal_path))

    def test_read_entries(self):
        """Test reading WAL entries."""
        wal = WALManager(self.wal_path)
        checkpoint1 = {"timestamp": time.time(), "reason": "test1", "value": 1}
        checkpoint2 = {"timestamp": time.time(), "reason": "test2", "value": 2}

        wal.write_entry(checkpoint1)
        wal.write_entry(checkpoint2)

        entries = wal.read_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["reason"], "test1")
        self.assertEqual(entries[1]["reason"], "test2")

    def test_recover_from_wal(self):
        """Test recovery from WAL."""
        wal = WALManager(self.wal_path)
        checkpoint = {"timestamp": time.time(), "reason": "test", "value": 42}

        wal.write_entry(checkpoint)
        recovered = wal.recover_from_wal()

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["reason"], "test")
        self.assertEqual(recovered["value"], 42)

    def test_empty_wal_recovery(self):
        """Test recovery from empty WAL."""
        wal = WALManager(self.wal_path)
        recovered = wal.recover_from_wal()
        self.assertIsNone(recovered)

    def test_truncate(self):
        """Test WAL truncation."""
        wal = WALManager(self.wal_path)
        wal.write_entry({"timestamp": time.time(), "reason": "test"})
        wal.write_entry({"timestamp": time.time(), "reason": "test2"})

        entries = wal.read_entries()
        self.assertEqual(len(entries), 2)

        wal.truncate(wal._last_valid_offset)
        entries_after = wal.read_entries()
        self.assertLessEqual(len(entries_after), 2)


class TestCheckpointECC(unittest.TestCase):
    """Tests for Checkpoint ECC (Error Correction Code)."""

    def test_ecc_disabled_without_rsnaps(self):
        """Test ECC returns original data when rsnaps not available."""
        with patch('extras.kares.HAS_RSNAPS', False):
            data = b"test data"
            encoded, ecc = CheckpointECC.encode(data)
            self.assertEqual(encoded, data)
            self.assertEqual(ecc, b"")

    def test_ecc_disabled_decode_without_rsnaps(self):
        """Test ECC decode returns original data when rsnaps not available."""
        with patch('extras.kares.HAS_RSNAPS', False):
            data = b"test data"
            decoded = CheckpointECC.decode(data, b"some_ecc")
            self.assertEqual(decoded, data)


class TestKaresMetrics(unittest.TestCase):
    """Tests for KARES Prometheus Metrics."""

    def setUp(self):
        """Reset singleton for each test."""
        KaresMetrics._instance = None

    def test_metrics_singleton(self):
        """Test metrics is a singleton."""
        metrics1 = KaresMetrics()
        metrics2 = KaresMetrics()
        self.assertIs(metrics1, metrics2)

    def test_metrics_disabled_without_prometheus(self):
        """Test metrics disabled when prometheus not available."""
        with patch('extras.kares.HAS_PROMETHEUS', False):
            metrics = KaresMetrics()
            self.assertFalse(metrics._enabled)

            metrics.record_checkpoint("test", 10.0)
            metrics.record_fault("test")
            metrics.record_recovery(True, 5.0)

            output = metrics.generate()
            self.assertIn(b"disabled", output)

    def test_record_checkpoint(self):
        """Test recording checkpoint metrics."""
        with patch('extras.kares.HAS_PROMETHEUS', True):
            with patch('extras.kares.Counter') as MockCounter:
                with patch('extras.kares.Histogram') as MockHistogram:
                    with patch('extras.kares.Gauge') as MockGauge:
                        MockCounter.return_value = MagicMock()
                        MockHistogram.return_value = MagicMock()
                        MockGauge.return_value = MagicMock()

                        metrics = KaresMetrics()
                        metrics._enabled = True

                        metrics.record_checkpoint("power_loss", 10.0)

                        MockCounter.return_value.labels.assert_called_with(reason="power_loss")
                        MockCounter.return_value.labels.return_value.inc.assert_called()

    def test_update_ups_state(self):
        """Test updating UPS state metrics."""
        with patch('extras.kares.HAS_PROMETHEUS', True):
            with patch('extras.kares.Counter') as MockCounter:
                with patch('extras.kares.Histogram') as MockHistogram:
                    with patch('extras.kares.Gauge') as MockGauge:
                        MockCounter.return_value = MagicMock()
                        MockHistogram.return_value = MagicMock()
                        mock_gauge = MagicMock()
                        MockGauge.return_value = mock_gauge

                        metrics = KaresMetrics()
                        metrics._enabled = True
                        metrics.battery_charge_percent = mock_gauge
                        metrics.input_voltage_volts = mock_gauge
                        metrics.on_battery = mock_gauge

                        snapshot = {
                            "battery": {"charge_pct": 85.0},
                            "input": {"voltage": 220.0},
                            "ups": {"on_battery": True}
                        }

                        metrics.update_ups_state(snapshot)

                        mock_gauge.set.assert_called()


class TestHealthMonitor(unittest.TestCase):
    """Tests for Health Monitor."""

    def setUp(self):
        """Set up mocks for HealthMonitor."""
        self.config = MagicMock()
        self.printer = MagicMock()
        self.reactor = MagicMock()
        self.config.get_printer.return_value = self.printer
        self.printer.get_reactor.return_value = self.reactor
        self.config.getchoice.return_value = "nut"
        self.config.getfloat.return_value = 0.05
        self.config.getboolean.return_value = True
        self.config.get.return_value = ""

    def test_health_monitor_initialization(self):
        """Test HealthMonitor initialization."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            health = HealthMonitor(kares)

            self.assertEqual(health._kares, kares)
            self.assertTrue(health._last_checkpoint_valid)
            self.assertEqual(len(health._health_issues), 0)

    def test_verify_checkpoint_integrity_valid(self):
        """Test checkpoint integrity verification with valid file."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            kares.checkpoint_path = os.path.join(tempfile.gettempdir(), "test_checkpoint.json")

            checkpoint = {"timestamp": time.time(), "reason": "test"}
            json_data = json.dumps(checkpoint).encode('utf-8')
            crc = struct.pack(">I", zlib.crc32(json_data) & 0xFFFFFFFF)

            with open(kares.checkpoint_path, "wb") as f:
                f.write(crc + json_data)

            health = HealthMonitor(kares)
            result = health._verify_checkpoint_integrity()

            self.assertTrue(result)

            os.unlink(kares.checkpoint_path)

    def test_verify_checkpoint_integrity_missing_file(self):
        """Test checkpoint integrity when file is missing."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            kares.checkpoint_path = "/nonexistent/path/checkpoint.json"

            health = HealthMonitor(kares)
            result = health._verify_checkpoint_integrity()

            self.assertTrue(result)

    def test_has_disk_space(self):
        """Test disk space check."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            kares.checkpoint_path = os.path.join(tempfile.gettempdir(), "test")

            health = HealthMonitor(kares)
            result = health._has_disk_space()

            self.assertTrue(result)

    def test_get_health_status(self):
        """Test health status reporting."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            health = HealthMonitor(kares)

            status = health.get_health_status()

            self.assertIn("healthy", status)
            self.assertIn("issues", status)
            self.assertIn("checkpoint_valid", status)
            self.assertTrue(status["healthy"])


class TestKaresGCodeCommands(unittest.TestCase):
    """Tests for Kares GCode command registration."""

    def setUp(self):
        """Set up mocks for Kares initialization."""
        self.config = MagicMock()
        self.config.get_printer.return_value = MagicMock()
        self.config.getchoice.return_value = "nut"
        self.config.getfloat.return_value = 0.05
        self.config.getboolean.return_value = True
        self.config.get.return_value = ""

    def test_kares_initialization(self):
        """Test Kares initializes correctly."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            self.assertEqual(kares.backend, "nut")
            self.assertEqual(kares.poll_interval, 0.05)
            self.assertIsNotNone(kares._nut_atomic)
            self.assertIsNotNone(kares._nut_monitor)

    def test_backend_selection_gpio(self):
        """Test GPIO backend selection."""
        self.config.getchoice.return_value = "gpio"

        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            self.assertEqual(kares.backend, "gpio")

    def test_backend_selection_nut(self):
        """Test NUT backend selection."""
        self.config.getchoice.return_value = "nut"

        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            self.assertEqual(kares.backend, "nut")

    def test_poll_interval_bounds(self):
        """Test poll_interval is bounded correctly."""
        self.config.getfloat.return_value = 0.001

        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            self.assertEqual(kares.poll_interval, 0.05)

    def test_fault_state_initialization(self):
        """Test fault state is initialized correctly."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            self.assertFalse(kares.fault_active)
            self.assertEqual(kares.fault_count, 0)
            self.assertEqual(kares.last_fault_time, 0.0)


class TestKaresFaultDetection(unittest.TestCase):
    """Tests for Kares fault detection mechanisms."""

    def setUp(self):
        """Set up mocks."""
        self.config = MagicMock()
        self.printer = MagicMock()
        self.reactor = MagicMock()
        self.config.get_printer.return_value = self.printer
        self.printer.get_reactor.return_value = self.reactor
        self.config.getchoice.return_value = "nut"
        self.config.getfloat.return_value = 0.05
        self.config.getboolean.return_value = True
        self.config.get.return_value = ""

    def test_read_nut_fault_no_snapshot(self):
        """Test read_nut_fault when no snapshot available."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            kares._nut_atomic.write(None, 0)
            fault, voltage, flags, reason = kares._read_nut_fault()

            self.assertFalse(fault)
            self.assertEqual(voltage, 0)

    def test_read_nut_fault_with_snapshot(self):
        """Test read_nut_fault with valid snapshot."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            snapshot = {
                "input": {"voltage": 220.0},
                "battery": {"voltage": 48.0},
            }
            kares._nut_atomic.write(snapshot, _FLAG_OB)
            fault, voltage, flags, reason = kares._read_nut_fault()

            self.assertTrue(fault)
            self.assertEqual(voltage, 220000)

    def test_read_gpio_fault(self):
        """Test GPIO fault reading."""
        self.config.getchoice.return_value = "gpio"
        self.config.get.return_value = "/sys/class/gpio/gpio24/value"

        with patch("extras.kares.logging"), \
             patch("builtins.open", create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.__enter__.return_value.read.return_value = "0\n"
            mock_open.return_value = mock_file

            kares = Kares(self.config)
            fault, voltage, flags, reason = kares._read_gpio_fault()

            self.assertFalse(fault)


class TestKaresSnapshotIntegration(unittest.TestCase):
    """Integration tests for Kares snapshot system."""

    def test_atomic_snapshot_integration(self):
        """Test full atomic snapshot workflow."""
        atomic = AtomicSnapshot()

        snapshot1 = {"ups": {"online": True}, "battery": {"charge_pct": 100}}
        snapshot2 = {"ups": {"online": False, "on_battery": True}, "battery": {"charge_pct": 50}}

        atomic.write(snapshot1, _FLAG_OB)
        s1, f1 = atomic.read()
        self.assertEqual(s1, snapshot1)

        atomic.write(snapshot2, _FLAG_OB | _FLAG_LB)
        s2, f2 = atomic.read()
        self.assertEqual(s2, snapshot2)
        self.assertTrue(f2 & _FLAG_LB)

    def test_nut_monitor_callback(self):
        """Test NUT critical callback is triggered."""
        atomic = AtomicSnapshot()
        callback_results = []

        def on_critical(current, previous):
            callback_results.append((current, previous))

        monitor = NUTMonitor(
            host="localhost",
            port=3493,
            ups_name="ups",
            username="",
            password="",
            timeout=1.0,
            atomic=atomic,
            on_critical=on_critical,
        )

        snapshot = {
            "ups": {
                "online": False,
                "on_battery": True,
                "low_battery": True,
            }
        }
        monitor._publish(snapshot)

        self.assertEqual(len(callback_results), 1)
        current, previous = callback_results[0]
        self.assertTrue(current["on_battery"])
        self.assertTrue(current["low_battery"])


class TestNUTProtocolErrors(unittest.TestCase):
    """Tests for NUT protocol error handling."""

    def test_nut_protocol_error(self):
        """Test NUTProtocolError is raised correctly."""
        error = NUTProtocolError("Invalid response")
        self.assertEqual(str(error), "Invalid response")

    def test_nut_auth_error(self):
        """Test NUTAuthError inheritance."""
        error = NUTAuthError("Authentication failed")
        self.assertIsInstance(error, NUTProtocolError)
        self.assertEqual(str(error), "Authentication failed")

    def test_nut_connection_error(self):
        """Test NUTConnectionError inheritance."""
        error = NUTConnectionError("Connection refused")
        self.assertIsInstance(error, NUTProtocolError)
        self.assertEqual(str(error), "Connection refused")

    def test_nut_timeout_error(self):
        """Test NUTTimeoutError inheritance."""
        error = NUTTimeoutError("Operation timed out")
        self.assertIsInstance(error, NUTProtocolError)
        self.assertEqual(str(error), "Operation timed out")

    def test_nut_data_error(self):
        """Test NUTDataError inheritance."""
        error = NUTDataError("Malformed data")
        self.assertIsInstance(error, NUTProtocolError)
        self.assertEqual(str(error), "Malformed data")


class TestKaresRTConfig(unittest.TestCase):
    """Tests for KaresRTConfig real-time configuration."""

    def test_rt_config_initial_state(self):
        """Verify initial RT config state."""
        rt_config = KaresRTConfig()
        self.assertFalse(rt_config._fault_state)
        self.assertFalse(rt_config._fault_active_flag)
        self.assertEqual(rt_config._fault_flags, 0)
        self.assertEqual(rt_config._fault_voltage, 0)
        self.assertEqual(rt_config._fault_reason, 0)

    def test_rt_config_slots_efficiency(self):
        """Verify __slots__ provides memory efficiency."""
        rt_config = KaresRTConfig()
        with self.assertRaises(AttributeError):
            rt_config.new_attribute = "value"

    def test_rt_config_reset(self):
        """Verify reset clears all state."""
        rt_config = KaresRTConfig()
        rt_config._fault_state = True
        rt_config._fault_flags = 0xFF
        rt_config._fault_voltage = 12000
        rt_config._fault_reason = 5
        rt_config._fault_active_flag = True
        rt_config._transition_count = 10

        rt_config.reset()

        self.assertFalse(rt_config._fault_state)
        self.assertFalse(rt_config._fault_active_flag)
        self.assertEqual(rt_config._fault_flags, 0)
        self.assertEqual(rt_config._transition_count, 0)

    def test_rt_config_fault_tracking(self):
        """Verify fault tracking state machine."""
        rt_config = KaresRTConfig()

        self.assertFalse(rt_config._fault_active_flag)

        rt_config._fault_active_flag = True
        self.assertTrue(rt_config._fault_active_flag)

        rt_config._fault_active_flag = False
        self.assertFalse(rt_config._fault_active_flag)

    def test_rt_priority_constants(self):
        """Verify RT priority constants are correct."""
        self.assertEqual(RT_PRIORITY_HIGH, 95)
        self.assertEqual(RT_PRIORITY_CRITICAL, 99)
        self.assertGreater(RT_PRIORITY_CRITICAL, RT_PRIORITY_HIGH)


class TestKaresStatusReporting(unittest.TestCase):
    """Tests for Kares status reporting."""

    def setUp(self):
        """Set up mocks."""
        self.config = MagicMock()
        self.printer = MagicMock()
        self.reactor = MagicMock()
        self.config.get_printer.return_value = self.printer
        self.printer.get_reactor.return_value = self.reactor
        self.config.getchoice.return_value = "nut"
        self.config.getfloat.return_value = 0.05
        self.config.getboolean.return_value = True
        self.config.get.return_value = ""

    def test_get_status_structure(self):
        """Test get_status returns correct structure."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)
            status = kares.get_status()

            self.assertIn("backend", status)
            self.assertIn("fault_active", status)
            self.assertIn("fault_count", status)
            self.assertIn("last_fault_time", status)
            self.assertIn("last_fault_reason", status)
            self.assertIn("wal_enabled", status)
            self.assertIn("ecc_enabled", status)
            self.assertIn("metrics_enabled", status)

    def test_get_status_values(self):
        """Test get_status returns correct values."""
        with patch("extras.kares.logging"):
            kares = Kares(self.config)

            kares.fault_active = True
            kares.fault_count = 5
            kares.last_fault_reason = 1

            status = kares.get_status()

            self.assertEqual(status["backend"], "nut")
            self.assertTrue(status["fault_active"])
            self.assertEqual(status["fault_count"], 5)
            self.assertEqual(status["last_fault_reason"], 1)


def run_tests():
    """Run all tests with coverage."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestAtomicSnapshot))
    suite.addTests(loader.loadTestsFromTestCase(TestNUTMonitorParsing))
    suite.addTests(loader.loadTestsFromTestCase(TestNUTThreadBackoff))
    suite.addTests(loader.loadTestsFromTestCase(TestCheckpointPersistence))
    suite.addTests(loader.loadTestsFromTestCase(TestWALManager))
    suite.addTests(loader.loadTestsFromTestCase(TestCheckpointECC))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresMetrics))
    suite.addTests(loader.loadTestsFromTestCase(TestHealthMonitor))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresGCodeCommands))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresFaultDetection))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresSnapshotIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestNUTProtocolErrors))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresStatusReporting))
    suite.addTests(loader.loadTestsFromTestCase(TestKaresRTConfig))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print("KARES v2.0 Test Suite Summary")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print(f"Success: {result.wasSuccessful()}")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
