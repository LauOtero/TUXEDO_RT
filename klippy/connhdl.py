#!/usr/bin/env python3
# Klipper Connection Handler (conn_manager wrapper)
# Replaces serialhdl.py to support Serial, CAN, RS485, and EtherCAT.
# Copyright (C) 2016-2026  Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import threading
import os
import time
import collections
from typing import Dict, Any, Optional, Callable, List, Tuple

import msgproto
import chelper
import util

# TUXEDO_RT: Integración de subsistemas de tiempo real y memoria determinista
from rtcore.memory_manager import DeterministicMemoryPool
from rtcore.ring_buffer import LockFreeRingBuffer
from rtcore.rt_core import RealTimeCore
from rtcore.connections.factory import ConnectionFactory

class error(Exception):
    pass

# =========================================================================
# Connection Type Constants (Must match C-side conn_backend.h)
# =========================================================================
CONN_TYPE_SERIAL    = b's'
CONN_TYPE_CAN       = b'c'
CONN_TYPE_ETHERTUX  = b'e'
CONN_TYPE_DEBUGFILE = b'f'
CONN_TYPE_RS485     = b'r'

class ConnectionHandler:
    """
    High-level connection handler for Klipper MCUs.
    Manages the lifecycle of the C 'conn_manager' structure.
    Handles zero-copy parsing, RT dispatching, and connection stats.
    """
    
    def __init__(self, reactor: Any, mcu_name: str = "") -> None:
        self.reactor = reactor
        self.mcu_name = mcu_name
        self.warn_prefix = "mcu '%s': " % mcu_name if mcu_name else ""
        
        # Name for the C backend thread (max 15 chars)
        self.conn_name = ("conn %s" % (self.mcu_name))[:15].encode('utf-8')
        
        # Connection Factory (Abstracts UART, CAN, EtherCAT setup)
        self.connection_factory = ConnectionFactory()
        self.connection = None
        
        # C Interface (conn_manager)
        self.ffi_main, self.ffi_lib = chelper.get_ffi()
        self.conn_mgr: Any = None
        self.default_cmd_queue = self.alloc_command_queue()
        self.stats_buf = self.ffi_main.new('char[4096]')
        
        # Message Parser
        self.msgparser = msgproto.MessageParser(warn_prefix=self.warn_prefix)
        
        # Threading
        self.lock = threading.Lock()
        self.bg_thread: Optional[threading.Thread] = None
        self.dispatch_thread: Optional[threading.Thread] = None
        
        # Handlers (COW pattern for lock-free reads in RT thread)
        self.handlers: Dict[Tuple[str, Optional[int]], Callable[[Dict[str, Any]], None]] = {}
        self._handlers_lock = threading.Lock()
        self.register_response(self._handle_unknown_init, '#unknown')
        self.register_response(self.handle_output, '#output')
        
        # TUXEDO_RT: Performance Stats & Memory Pools
        self._stats = {
            'msg_count': 0, 'bytes_received': 0,
            'total_parse_time': 0.0, 'max_parse_time': 0.0,
            'total_hdl_time': 0.0, 'max_hdl_time': 0.0,
            'notification_count': 0, 'error_count': 0
        }
        self.msg_pool = DeterministicMemoryPool(initial_size=2000)
        self.response_ring = LockFreeRingBuffer(capacity=1024)

    # =========================================================================
    # Background Thread (High Priority RT)
    # =========================================================================
    def _bg_thread(self) -> None:
        """Pulls messages from conn_manager and pushes to ring buffer."""
        name_short = ("conn_read %s" % self.mcu_name)[:15].encode('utf-8')
        try:
            self.ffi_lib.set_thread_name(name_short)
        except Exception:
            pass

        # Set Real-Time Priority
        try:
            rt_helper = RealTimeCore()
            rt_helper.set_realtime_priority(priority=80)
        except Exception:
            logging.warning("Failed to set RT priority for connection thread")

        response = self.ffi_main.new('struct pull_queue_message *')
        monotonic = self.reactor.monotonic
        
        # Localize C functions for speed
        pull_func = self.ffi_lib.conn_pull
        parse_into_func = self.msgparser.parse_into
        msg_pool_acquire = self.msg_pool.acquire
        msg_pool_release = self.msg_pool.release
        ring_push = self.response_ring.push
        
        while 1:
            # Blocking pull from conn_manager
            pull_func(self.conn_mgr, response)
            count = response.len
            
            if count < 0:
                # Connection closed or error
                break
            
            self._stats['msg_count'] += 1
            self._stats['bytes_received'] += count
            
            # TUXEDO_RT: Zero-copy parsing
            start_parse = monotonic()
            params = msg_pool_acquire()
            params.clear()
            
            try:
                # Pass raw bytes directly to parser
                parse_into_func(params, response.msg[0:count])
            except Exception:
                self._stats['error_count'] += 1
                msg_pool_release(params)
                continue
            
            # Add metadata
            params['#sent_time'] = response.sent_time
            params['#receive_time'] = response.receive_time
            params['#notify_id'] = response.notify_id
            
            end_parse = monotonic()
            parse_duration = end_parse - start_parse
            self._stats['total_parse_time'] += parse_duration
            if parse_duration > self._stats['max_parse_time']:
                self._stats['max_parse_time'] = parse_duration
            
            # Push to dispatch ring
            if not ring_push(params):
                self._stats['error_count'] += 1
                msg_pool_release(params)
                logging.error("%sResponse ring overflow! Dropping message.", self.warn_prefix)

    # =========================================================================
    # Dispatch Thread
    # =========================================================================
    def _dispatch_thread(self) -> None:
        """Dispatches parsed messages to Python handlers."""
        try:
            rt_helper = RealTimeCore()
            rt_helper.set_realtime_priority(priority=75)
        except Exception:
            pass
            
        monotonic = self.reactor.monotonic
        ring_pop = self.response_ring.pop
        msg_pool_release = self.msg_pool.release
        
        while 1:
            params = ring_pop()
            if params is None:
                if self.conn_mgr is None:
                    break
                time.sleep(0.001)
                continue
            
            notify_id = params.get('#notify_id', 0)
            if notify_id:
                # Wake up raw_send_wait_ack
                self._stats['notification_count'] += 1
                completion = self.pending_notifications.pop(notify_id, None)
                if completion is not None:
                    self.reactor.async_complete(completion, params)
                msg_pool_release(params)
                continue
            
            # Handler dispatch
            hdl_key = (params['#name'], params.get('oid'))
            handlers = self.handlers
            hdl = handlers.get(hdl_key, self.handle_default)
            
            start_hdl = monotonic()
            try:
                hdl(params)
            except Exception:
                self._stats['error_count'] += 1
                logging.exception("%sException in connection callback.", self.warn_prefix)
            finally:
                hdl_duration = monotonic() - start_hdl
                self._stats['total_hdl_time'] += hdl_duration
                if hdl_duration > self._stats['max_hdl_time']:
                    self._stats['max_hdl_time'] = hdl_duration
                msg_pool_release(params)

    # =========================================================================
    # Connection Management
    # =========================================================================
    def _start_session(self, connection: Any) -> bool:
        """Initializes C conn_manager with connection parameters."""
        self.connection = connection
        
        # Get connection details
        conn_type = connection.get_type()  # b's', b'c', b'e', b'r'
        client_id = connection.get_client_id()  # 0 for serial, node_id for can
        fd = connection.get_fd()                # File descriptor or -1 for EtherCAT
        
        logging.info("TUXEDO_RT: conn_alloc(type=%s, fd=%d, client_id=%d)",
                     conn_type.decode('utf-8'), fd, client_id)

        # Create C conn_manager instance
        self.conn_mgr = self.ffi_main.gc(
            self.ffi_lib.conn_alloc(fd, conn_type, client_id, self.conn_name),
            self.ffi_lib.conn_free
        )
        
        if self.conn_mgr is None:
            raise error("Failed to allocate connection manager")

        # Apply connection specific configs (e.g., CAN params)
        if hasattr(connection, 'setup_serialqueue'):
            connection.setup_serialqueue(self.ffi_lib, self.conn_mgr)

        # Load Dictionary / Identify
        self.msgparser = msgproto.MessageParser(warn_prefix=self.warn_prefix)
        
        if conn_type == CONN_TYPE_DEBUGFILE:
            # Debug mode loads dictionary immediately
            if hasattr(connection, 'dictionary'):
                self.msgparser.process_identify(connection.dictionary, decompress=False)
            return True

        # Normal mode: Identify sequence
        completion = self.reactor.register_callback(self._get_identify_data)
        identify_data = completion.wait(self.reactor.monotonic() + 5.)
        
        if identify_data is None:
            logging.error("%sTimeout during identify.", self.warn_prefix)
            self.disconnect()
            return False

        self.msgparser.process_identify(identify_data)
        self.register_response(self.handle_unknown, '#unknown')
        
        # Configure wire frequency (Baudrate or CAN freq)
        wire_freq = self.msgparser.get_constant('SERIAL_BAUD', None) or \
                    self.msgparser.get_constant('CANBUS_FREQUENCY', None)
        if wire_freq:
            self.ffi_lib.conn_set_wire_frequency(self.conn_mgr, wire_freq)

        # Start Threads
        self.bg_thread = threading.Thread(target=self._bg_thread)
        self.bg_thread.start()
        self.dispatch_thread = threading.Thread(target=self._dispatch_thread)
        self.dispatch_thread.start()
        
        return True

    def _connect_connection(self, connection: Any, verify_callback=None) -> None:
        """Loop to connect connection and start session."""
        self.connection = connection
        while 1:
            try:
                connection.open()
                ret = self._start_session(connection)
                if ret:
                    if verify_callback is None or verify_callback(connection):
                        break
            except Exception:
                logging.exception("%sError connecting connection.", self.warn_prefix)
            
            self.disconnect()
            self.reactor.pause(self.reactor.monotonic() + 2.0)

    # Public Connection Methods
    def connect_serial(self, serialport: str, baud: int, rts: bool = True) -> None:
        c = self.connection_factory.create_uart(self.reactor, serialport, baud, rts, self.mcu_name)
        self._connect_connection(c)

    def connect_canbus(self, canbus_uuid: str, node_id: int, iface: str) -> None:
        c = self.connection_factory.create_can(self.reactor, canbus_uuid, node_id, iface, self.mcu_name)
        def verify_can(conn):
            params = self.send_with_response('get_canbus_id', 'canbus_id')
            return bytearray(params['canbus_uuid']) == bytearray(conn.uuid)
        self._connect_connection(c, verify_can)

    def connect_rs485(self, serialport: str, baud: int, delay: int = 0) -> None:
        c = self.connection_factory.create_rs485(self.reactor, serialport, baud, delay, self.mcu_name)
        self._connect_connection(c)

    def connect_ethertux(self, alias: int, position: int, vendor: int, product: int, cycle_ns: int) -> None:
        c = self.connection_factory.create_ethertux(self.reactor, alias, position, vendor, product, cycle_ns, self.mcu_name)
        self._connect_connection(c)

    def connect_file(self, filename: str, dictionary: bytes) -> None:
        c = self.connection_factory.create_file(self.reactor, filename, dictionary, self.mcu_name)
        self._connect_connection(c)

    def disconnect(self) -> None:
        if self.conn_mgr is not None:
            cm = self.conn_mgr
            self.conn_mgr = None
            self.ffi_lib.conn_exit(cm) # Stops background thread
            if self.bg_thread:
                self.bg_thread.join()
            if self.dispatch_thread:
                self.dispatch_thread.join()
        if self.connection:
            self.connection.close()
            self.connection = None

    # =========================================================================
    # Command Sending
    # =========================================================================
    def raw_send(self, cmd: List[int], minclock: int, reqclock: int, cmd_queue: Any) -> None:
        if not self.conn_mgr:
            raise error("Not connected")
        self.ffi_lib.conn_send(self.conn_mgr, cmd_queue, cmd, len(cmd), minclock, reqclock, 0)

    pending_notifications: Dict[int, Any] = {}
    last_notify_id = 0

    def raw_send_wait_ack(self, cmd: List[int], minclock: int, reqclock: int, cmd_queue: Any) -> Dict[str, Any]:
        self.last_notify_id += 1
        nid = self.last_notify_id
        completion = self.reactor.completion()
        self.pending_notifications[nid] = completion
        
        self.ffi_lib.conn_send(self.conn_mgr, cmd_queue, cmd, len(cmd), minclock, reqclock, nid)
        
        params = completion.wait()
        if params is None:
            self._error("Connection closed while waiting for ACK")
        return params

    # =========================================================================
    # Helpers
    # =========================================================================
    def _get_identify_data(self, eventtime: float) -> Optional[bytes]:
        identify_data = b""
        while 1:
            msg = "identify offset=%d count=%d" % (len(identify_data), 50)
            try:
                params = self.send_with_response(msg, 'identify_response')
            except error:
                return None
            data = params['data']
            if not data:
                return identify_data
            identify_data += data

    def alloc_command_queue(self) -> Any:
        return self.ffi_main.gc(self.ffi_lib.conn_alloc_commandqueue(), 
                                self.ffi_lib.conn_free_commandqueue)

    def register_response(self, callback: Optional[Callable], name: str, oid: Optional[int] = None) -> None:
        with self._handlers_lock:
            new_handlers = self.handlers.copy()
            if callback is None:
                new_handlers.pop((name, oid), None)
            else:
                new_handlers[(name, oid)] = callback
            self.handlers = new_handlers

    def send_with_response(self, msg: str, response: str) -> Dict[str, Any]:
        cmd = self.msgparser.create_command(msg)
        src = RetryCommand(self, response)
        return src.get_response([cmd], self.default_cmd_queue)

    def get_serialqueue(self): # Compatibility alias for mcu.py
        return self.conn_mgr

    def get_msgparser(self):
        return self.msgparser

    def stats(self, eventtime: float) -> str:
        if not self.conn_mgr: return ""
        self.ffi_lib.conn_get_stats(self.conn_mgr, self.stats_buf, len(self.stats_buf))
        c_stats = self.ffi_main.string(self.stats_buf).decode()
        
        # Add Python side stats
        if self._stats['msg_count'] > 0:
            avg_parse = (self._stats['total_parse_time'] / self._stats['msg_count']) * 1000
            return "%s py_parse_avg=%.2fms py_max=%.2fms" % (c_stats, avg_parse, self._stats['max_parse_time']*1000)
        return c_stats

    # Default Handlers
    def _handle_unknown_init(self, params):
        pass # Ignore unknowns during init
    def handle_unknown(self, params):
        logging.warning("%sUnknown message: %s", self.warn_prefix, params.get('#name', '?'))
    def handle_output(self, params):
        logging.info("%sOutput: %s", self.warn_prefix, params.get('#msg', ''))
    def handle_default(self, params):
        pass

class RetryCommand:
    """Helper to retry commands until a response is received."""
    def __init__(self, conn: ConnectionHandler, name: str, oid: int = None):
        self.conn = conn
        self.name = name
        self.oid = oid
        self.params = None
        self.reactor = conn.reactor
        conn.register_response(self._cb, name, oid)

    def _cb(self, params): self.params = params

    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True):
        timeout = self.reactor.monotonic() + 5.0
        while self.reactor.monotonic() < timeout:
            self.params = None
            for cmd in cmds[:-1]:
                self.conn.raw_send(cmd, minclock, reqclock, cmd_queue)
            self.conn.raw_send_wait_ack(cmds[-1], minclock, reqclock, cmd_queue)
            
            # Wait briefly for callback
            end = self.reactor.monotonic() + 0.100
            while self.params is None and self.reactor.monotonic() < end:
                self.reactor.pause(self.reactor.monotonic() + 0.005)
            
            if self.params is not None:
                self.conn.register_response(None, self.name, self.oid)
                return self.params
            
            if not retry: break
            
        self.conn.register_response(None, self.name, self.oid)
        raise error("Timeout on '%s'" % self.name)

    def _error(self, msg): raise error(msg)