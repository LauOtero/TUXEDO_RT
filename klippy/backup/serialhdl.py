# Serial port management for firmware communication
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, threading, os, time
import msgproto, chelper, util

# TUXEDO_RT: Integración de subsistemas de tiempo real y memoria determinista desde rtcore
from rtcore.memory_manager import DeterministicMemoryPool
from rtcore.ring_buffer import LockFreeRingBuffer
from rtcore.rt_core import SCHED_FIFO, RealTimeCore
from rtcore.transport.factory import TransportFactory

from typing import Dict, Any, Optional, Callable, List, Tuple

class error(Exception):
    pass

class SerialReader:
    """
    TUXEDO_RT: Optimized Serial port management for high-performance firmware 
    communication. Implements zero-allocation parsing and decoupled dispatching.
    """
    def __init__(self, reactor: Any, mcu_name: str = "") -> None:
        """
        Initialize the SerialReader.
        
        :param reactor: The Klipper reactor instance.
        :param mcu_name: Optional name of the MCU for logging purposes.
        """
        self.reactor = reactor
        self.warn_prefix = ""
        self.mcu_name = mcu_name
        if self.mcu_name:
            self.warn_prefix = "mcu '%s': " % (self.mcu_name)
        sq_name = ("serialq %s" % (self.mcu_name))[:15]
        self.sq_name = sq_name.encode("utf-8")
        # TUXEDO_RT: Transporte y Factory
        self.transport = None
        self.transport_factory = TransportFactory()
        self.msgparser = msgproto.MessageParser(warn_prefix=self.warn_prefix)
        # C interface
        self.ffi_main, self.ffi_lib = chelper.get_ffi()
        self.serialqueue: Any = None
        self.default_cmd_queue = self.alloc_command_queue()
        self.stats_buf = self.ffi_main.new('char[4096]')
        # Threading
        self.lock = threading.Lock()
        self.background_thread: Optional[threading.Thread] = None
        self.dispatch_thread: Optional[threading.Thread] = None
        # Message handlers (TUXEDO_RT: Use COW for lock-free reads in bg_thread)
        self.handlers: Dict[Tuple[str, Optional[int]], Callable[[Dict[str, Any]], None]] = {}
        self._handlers_lock = threading.Lock()
        self.register_response(self._handle_unknown_init, '#unknown')
        self.register_response(self.handle_output, '#output')
        
        # TUXEDO_RT: Advanced Performance Statistics
        self._stats: Dict[str, Any] = {
            'msg_count': 0,
            'bytes_received': 0,
            'total_parse_time': 0.0,
            'max_parse_time': 0.0,
            'total_hdl_time': 0.0,
            'max_hdl_time': 0.0,
            'notification_count': 0,
            'error_count': 0
        }
        self._watchdog_threshold = 0.010 # 10ms for handler execution
        # Sent message notification tracking
        self.last_notify_id = 0
        self.pending_notifications: Dict[int, Any] = {}
        
        # TUXEDO_RT: Pools de memoria para respuestas seriales para evitar latencia de GC
        self.msg_pool = DeterministicMemoryPool(initial_size=2000)
        self.response_ring = LockFreeRingBuffer(capacity=1024)
    
    def _bg_thread(self) -> None:
        """
        High-priority thread for pulling and parsing serial messages.
        TUXEDO_RT: Decouples parsing from dispatching using a ring buffer.
        """
        name_short = ("serialhdl %s" % (self.mcu_name))[:15]
        self.ffi_lib.set_thread_name(name_short.encode('utf-8'))
        
        # TUXEDO_RT: Escalar prioridad del hilo serial a Tiempo Real (Crítico)
        rt_helper = RealTimeCore()
        rt_helper.set_realtime_priority(priority=80)
        
        response = self.ffi_main.new('struct pull_queue_message *')
        monotonic = self.reactor.monotonic
        
        # Localize functions for speed
        pull_func = self.ffi_lib.serialqueue_pull
        parse_into_func = self.msgparser.parse_into
        msg_pool_acquire = self.msg_pool.acquire
        msg_pool_release = self.msg_pool.release
        ring_push = self.response_ring.push
        
        while 1:
            pull_func(self.serialqueue, response)
            count = response.len
            if count < 0:
                break
            
            self._stats['msg_count'] += 1
            self._stats['bytes_received'] += count
            
            # TUXEDO_RT: Measure parsing performance
            start_parse = monotonic()
            params = msg_pool_acquire()
            params.clear()
            
            # TUXEDO_RT: Parsing message with zero dictionary allocations
            try:
                parse_into_func(params, response.msg[0:count])
            except Exception:
                self._stats['error_count'] += 1
                msg_pool_release(params)
                continue
            
            end_parse = monotonic()
            parse_duration = end_parse - start_parse
            self._stats['total_parse_time'] += parse_duration
            if parse_duration > self._stats['max_parse_time']:
                self._stats['max_parse_time'] = parse_duration
            
            params['#sent_time'] = response.sent_time
            params['#receive_time'] = response.receive_time
            params['#notify_id'] = response.notify_id
            
            # TUXEDO_RT: Push to ring buffer for decoupled dispatching
            if not ring_push(params):
                # Buffer overflow: handle critical failure or drop message
                self._stats['error_count'] += 1
                msg_pool_release(params)
                logging.error("%sSerial response ring overflow! Dropping message %s",
                              self.warn_prefix, params.get('#name', 'unknown'))
    
    def _dispatch_thread(self) -> None:
        """
        Secondary thread for dispatching parsed messages to their handlers.
        TUXEDO_RT: Reduces reader thread load by offloading execution.
        """
        # High priority for dispatching, but slightly lower than reader
        rt_helper = RealTimeCore()
        rt_helper.set_realtime_priority(priority=75)
        
        monotonic = self.reactor.monotonic
        ring_pop = self.response_ring.pop
        msg_pool_release = self.msg_pool.release
        handle_default = self.handle_default
        
        while 1:
            params = ring_pop()
            if params is None:
                # Use a small sleep to avoid busy-waiting if idle
                # In a high-throughput scenario, this won't be called often
                time.sleep(0.001)
                if self.serialqueue is None: # Exit condition
                    break
                continue
            
            notify_id = params.pop('#notify_id', 0)
            if notify_id:
                self._stats['notification_count'] += 1
                completion = self.pending_notifications.pop(notify_id, None)
                if completion is not None:
                    self.reactor.async_complete(completion, params)
                msg_pool_release(params)
                continue
            
            # TUXEDO_RT: Lock-free lookup using COW dictionary
            handlers = self.handlers
            # TUXEDO_RT: Dynamic priority adjustment for critical messages
            hdl_key = (params['#name'], params.get('oid'))
            is_critical = params['#name'] in ['shutdown', 'emergency_stop', 'trsync_state']
            if is_critical:
                rt_helper.set_realtime_priority(priority=90)
            
            hdl = handlers.get(hdl_key, handle_default)
            
            start_hdl = monotonic()
            try:
                hdl(params)
            except Exception:
                self._stats['error_count'] += 1
                logging.exception("%sException in serial callback", self.warn_prefix)
            finally:
                end_hdl = monotonic()
                if is_critical:
                    rt_helper.set_realtime_priority(priority=75)
                hdl_duration = end_hdl - start_hdl
                self._stats['total_hdl_time'] += hdl_duration
                if hdl_duration > self._stats['max_hdl_time']:
                    self._stats['max_hdl_time'] = hdl_duration
                
                # Watchdog for slow handlers
                if hdl_duration > self._watchdog_threshold:
                    logging.warning("%sSlow serial handler for %s: %.3fms", 
                                    self.warn_prefix, params['#name'], hdl_duration * 1000)
                
                msg_pool_release(params)

    def _error(self, msg: str, *params: Any) -> Any:
        """
        Raise a SerialReader error.
        
        :param msg: The error message format.
        :param params: The format parameters.
        """
        raise error(self.warn_prefix + (msg % params))
    def _get_identify_data(self, eventtime: float) -> Optional[bytes]:
        """
        Fetch the data dictionary from the MCU.
        
        :param eventtime: The time the event occurred.
        :return: The identify data as bytes, or None if failed.
        """
        # Query the "data dictionary" from the micro-controller
        identify_data = b""
        while 1:
            msg = "identify offset=%d count=%d" % (len(identify_data), 40)
            try:
                params = self.send_with_response(msg, 'identify_response')
            except error as e:
                logging.exception("%sWait for identify_response",
                                  self.warn_prefix)
                return None
            if params['offset'] == len(identify_data):
                msgdata = params['data']
                if not msgdata:
                    # Done
                    return identify_data
                identify_data += msgdata
    def _start_session(self, transport: Any) -> bool:
        """
        Start a new communication session with the given transport.
        
        :param transport: The transport instance to use.
        :return: True if the session started successfully, False otherwise.
        """
        self.transport = transport
        serial_fd_type = transport.get_type()
        client_id = transport.get_client_id()
        serial_fd = transport.get_fd()
        logging.info("TUXEDO_RT: serialqueue_alloc con serial_fd=%d, serial_fd_type=%s, client_id=%d",
                     serial_fd, serial_fd_type.decode('utf-8'), client_id)
        self.serialqueue = self.ffi_main.gc(
            self.ffi_lib.serialqueue_alloc(serial_fd,
                                           serial_fd_type, client_id,
                                           self.sq_name),
            self.ffi_lib.serialqueue_free)
        
        # TUXEDO_RT: Configuración adicional de la cola (ej. payload USB, params CAN)
        if hasattr(transport, 'setup_serialqueue'):
            transport.setup_serialqueue(self.ffi_lib, self.serialqueue)

        self.background_thread = threading.Thread(target=self._bg_thread)
        self.background_thread.start()
        self.dispatch_thread = threading.Thread(target=self._dispatch_thread)
        self.dispatch_thread.start()

        # TUXEDO_RT: Si el transporte ya tiene el diccionario (FileTransport)
        if serial_fd_type == b'f':
            self.msgparser.process_identify(transport.dictionary,
                                            decompress=False)
            return True

        # Obtain and load the data dictionary from the firmware
        completion = self.reactor.register_callback(self._get_identify_data)
        identify_data = completion.wait(self.reactor.monotonic() + 5.)
        if identify_data is None:
            logging.info("%sTimeout on connect (%s)", self.warn_prefix,
                         transport.get_info())
            self.disconnect()
            return False
        msgparser = msgproto.MessageParser(warn_prefix=self.warn_prefix)
        msgparser.process_identify(identify_data)
        self.msgparser = msgparser
        self.register_response(self.handle_unknown, '#unknown')
        # Setup baud adjust
        if serial_fd_type == b'c':
            wire_freq = msgparser.get_constant_float('CANBUS_FREQUENCY', None)
        else:
            wire_freq = msgparser.get_constant_float('SERIAL_BAUD', None)
        if wire_freq is not None:
            self.ffi_lib.serialqueue_set_wire_frequency(self.serialqueue,
                                                        wire_freq)
        receive_window = msgparser.get_constant_int('RECEIVE_WINDOW', None)
        if receive_window is not None:
            self.ffi_lib.serialqueue_set_receive_window(
                self.serialqueue, receive_window)
        return True

    def _connect_transport(self, transport: Any, verify_callback: Optional[Callable[[Any], bool]] = None) -> None:
        """
        TUXEDO_RT: Unified logic to connect any transport.
        
        :param transport: The transport instance to connect.
        :param verify_callback: Optional callback to verify the connection.
        """
        self.transport = transport
        while 1:
            try:
                transport.open()
                ret = self._start_session(transport)
                if ret:
                    if verify_callback is None or verify_callback(transport):
                        break
            except:
                logging.exception("%sError in %s connect",
                                  self.warn_prefix, transport.get_type())

            logging.info("%sFailed %s connect - retrying..",
                         self.warn_prefix, transport.get_type())
            self.disconnect()
            self.reactor.pause(self.reactor.monotonic() + 5.)


    def connect_generic(self, transport: Any, verify_callback: Optional[Callable[[Any], bool]] = None) -> None:
        """
        TUXEDO_RT: Connect any externally instantiated transport.
        
        :param transport: The transport instance.
        :param verify_callback: Optional verification callback.
        """
        self._connect_transport(transport, verify_callback)

    def connect_canbus(self, canbus_uuid: str, canbus_nodeid: int, canbus_iface: str = "can0") -> None:
        """
        Connect to the MCU via CAN bus.
        
        :param canbus_uuid: The CAN bus UUID of the MCU.
        :param canbus_nodeid: The CAN bus node ID.
        :param canbus_iface: The CAN interface (e.g., 'can0').
        """
        # TUXEDO_RT: Usar CANTransport
        transport = self.transport_factory.create_can(
            self.reactor, canbus_uuid, canbus_nodeid, canbus_iface, self.mcu_name)

        def verify_can(t: Any) -> bool:
            params = self.send_with_response('get_canbus_id', 'canbus_id')
            got_uuid = bytearray(params['canbus_uuid'])
            expected_uuid = bytearray(t.uuid)
            return got_uuid == expected_uuid

        self._connect_transport(transport, verify_can)

    def connect_pipe(self, filename: str) -> None:
        """
        Connect to the MCU via a Unix pipe.
        
        :param filename: The path to the pipe file.
        """
        # TUXEDO_RT: Usar PipeTransport
        transport = self.transport_factory.create_pipe(
            self.reactor, filename, self.mcu_name)
        self._connect_transport(transport)

    def connect_uart(self, serialport: str, baud: int, rts: bool = True) -> None:
        """
        Connect to the MCU via a UART (serial port).
        
        :param serialport: The serial port device path.
        :param baud: The baud rate.
        :param rts: Whether to enable RTS/CTS hardware flow control.
        """
        # TUXEDO_RT: Usar UARTTransport
        transport = self.transport_factory.create_uart(
            self.reactor, serialport, baud, rts, self.mcu_name)
        self._connect_transport(transport)

    def connect_file(self, debugoutput: Any, dictionary: bytes, pace: bool = False) -> None:
        """
        Connect to the MCU via a file (debug/simulation mode).
        
        :param debugoutput: The file object or path for debug output.
        :param dictionary: The MCU data dictionary bytes.
        :param pace: Whether to pace the output to real-time.
        """
        # TUXEDO_RT: Usar FileTransport
        transport = self.transport_factory.create_file(
            self.reactor, debugoutput, dictionary, self.mcu_name)
        self._connect_transport(transport)
    def set_clock_est(self, freq: float, conv_time: float, conv_clock: int, last_clock: int) -> None:
        """
        Update the clock estimation for the MCU.
        """
        self.ffi_lib.serialqueue_set_clock_est(
            self.serialqueue, freq, conv_time, conv_clock, last_clock)
    def disconnect(self) -> None:
        """
        Disconnect from the MCU and stop background threads.
        """
        if self.serialqueue is not None:
            sq = self.serialqueue
            self.serialqueue = None
            self.ffi_lib.serialqueue_exit(sq)
            if self.background_thread is not None:
                self.background_thread.join()
                self.background_thread = None
            if self.dispatch_thread is not None:
                self.dispatch_thread.join()
                self.dispatch_thread = None
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        for pn in self.pending_notifications.values():
            pn.complete(None)
        self.pending_notifications.clear()
    def stats(self, eventtime: float) -> str:
        """
        Return performance statistics as a string.
        """
        if self.serialqueue is None:
            return ""
        self.ffi_lib.serialqueue_get_stats(self.serialqueue,
                                           self.stats_buf, len(self.stats_buf))
        c_stats = self.ffi_main.string(self.stats_buf).decode()
        
        # TUXEDO_RT: Append Python-side performance metrics
        msg_count = self._stats['msg_count']
        if msg_count > 0:
            avg_parse = (self._stats['total_parse_time'] / msg_count) * 1000
            avg_hdl = (self._stats['total_hdl_time'] / msg_count) * 1000
        else:
            avg_parse = avg_hdl = 0.0
            
        py_stats = ("py_msgs=%d py_bytes=%d py_avg_parse=%.3fms py_max_parse=%.3fms "
                    "py_avg_hdl=%.3fms py_max_hdl=%.3fms py_errors=%d" % (
                        msg_count, self._stats['bytes_received'],
                        avg_parse, self._stats['max_parse_time'] * 1000,
                        avg_hdl, self._stats['max_hdl_time'] * 1000,
                        self._stats['error_count']))
        return "%s %s" % (c_stats, py_stats)
    def get_reactor(self) -> Any:
        """Return the reactor instance."""
        return self.reactor
    def get_msgparser(self) -> msgproto.MessageParser:
        """Return the message parser instance."""
        return self.msgparser
    def get_serialqueue(self) -> Any:
        """Return the serial queue instance."""
        return self.serialqueue
    def get_default_command_queue(self) -> Any:
        """Return the default command queue instance."""
        return self.default_cmd_queue
    def get_stats(self) -> Dict[str, Any]:
        """Return the performance statistics dictionary."""
        return dict(self._stats)

    # Serial response callbacks
    def register_response(self, callback: Optional[Callable[[Dict[str, Any]], None]], name: str, oid: Optional[int] = None) -> None:
        """
        Register a callback for a specific serial response.
        
        :param callback: The callback function, or None to unregister.
        :param name: The name of the response message.
        :param oid: Optional object ID.
        """
        # TUXEDO_RT: Thread-safe COW (Copy-On-Write) for handlers
        with self._handlers_lock:
            new_handlers = self.handlers.copy()
            if callback is None:
                new_handlers.pop((name, oid), None)
            else:
                new_handlers[name, oid] = callback
            self.handlers = new_handlers
    # Command sending
    def raw_send(self, cmd: List[int], minclock: int, reqclock: int, cmd_queue: Any) -> None:
        """
        Send a raw command to the MCU.
        
        :param cmd: List of bytes representing the command.
        :param minclock: Minimum clock for sending.
        :param reqclock: Required clock for sending.
        :param cmd_queue: The command queue to use.
        """
        self.ffi_lib.serialqueue_send(self.serialqueue, cmd_queue,
                                      cmd, len(cmd), minclock, reqclock, 0)
    def raw_send_wait_ack(self, cmd: List[int], minclock: int, reqclock: int, cmd_queue: Any) -> Dict[str, Any]:
        """
        Send a raw command and wait for an acknowledgment.
        
        :param cmd: List of bytes representing the command.
        :param minclock: Minimum clock for sending.
        :param reqclock: Required clock for sending.
        :param cmd_queue: The command queue to use.
        :return: The received response parameters.
        """
        self.last_notify_id += 1
        nid = self.last_notify_id
        completion = self.reactor.completion()
        self.pending_notifications[nid] = completion
        self.ffi_lib.serialqueue_send(self.serialqueue, cmd_queue,
                                      cmd, len(cmd), minclock, reqclock, nid)
        params = completion.wait()
        if params is None:
            self._error("Serial connection closed")
        return params
    def send(self, msg: str, minclock: int = 0, reqclock: int = 0) -> None:
        """
        Send a text command to the MCU.
        
        :param msg: The command string.
        :param minclock: Minimum clock for sending.
        :param reqclock: Required clock for sending.
        """
        cmd = self.msgparser.create_command(msg)
        self.raw_send(cmd, minclock, reqclock, self.default_cmd_queue)
    def send_with_response(self, msg: str, response: str) -> Dict[str, Any]:
        """
        Send a command and wait for a specific response.
        
        :param msg: The command string.
        :param response: The name of the expected response.
        :return: The received response parameters.
        """
        cmd = self.msgparser.create_command(msg)
        src = SerialRetryCommand(self, response)
        return src.get_response([cmd], self.default_cmd_queue)
    def alloc_command_queue(self) -> Any:
        """
        Allocate a new command queue.
        
        :return: The allocated command queue instance.
        """
        return self.ffi_main.gc(self.ffi_lib.serialqueue_alloc_commandqueue(),
                                self.ffi_lib.serialqueue_free_commandqueue)
    # Dumping debug lists
    def dump_debug(self) -> str:
        """
        Return debug information about the serial queues.
        
        :return: A string containing the debug output.
        """
        out = []
        out.append("Dumping serial stats: %s" % (
            self.stats(self.reactor.monotonic()),))
        sdata = self.ffi_main.new('struct pull_queue_message[1024]')
        rdata = self.ffi_main.new('struct pull_queue_message[1024]')
        scount = self.ffi_lib.serialqueue_extract_old(self.serialqueue, 1,
                                                      sdata, len(sdata))
        rcount = self.ffi_lib.serialqueue_extract_old(self.serialqueue, 0,
                                                      rdata, len(rdata))
        out.append("Dumping send queue %d messages" % (scount,))
        for i in range(scount):
            msg = sdata[i]
            cmds = self.msgparser.dump(msg.msg[0:msg.len])
            out.append("Sent %d %f %f %d: %s" % (
                i, msg.receive_time, msg.sent_time, msg.len, ', '.join(cmds)))
        out.append("Dumping receive queue %d messages" % (rcount,))
        for i in range(rcount):
            msg = rdata[i]
            cmds = self.msgparser.dump(msg.msg[0:msg.len])
            out.append("Receive: %d %f %f %d: %s" % (
                i, msg.receive_time, msg.sent_time, msg.len, ', '.join(cmds)))
        return '\n'.join(out)
    # Default message handlers
    def _handle_unknown_init(self, params: Dict[str, Any]) -> None:
        """
        Internal handler for unknown messages during the identification phase.
        
        :param params: The message parameters.
        """
        logging.debug("%sUnknown message %d (len %d) while identifying",
                      self.warn_prefix, params['#msgid'], len(params['#msg']))
    def handle_unknown(self, params: Dict[str, Any]) -> None:
        """
        Default handler for unknown messages.
        
        :param params: The message parameters.
        """
        logging.warning("%sUnknown message type %d: %s",
                     self.warn_prefix, params['#msgid'], repr(params['#msg']))
    def handle_output(self, params: Dict[str, Any]) -> None:
        """
        Default handler for MCU output messages.
        
        :param params: The message parameters.
        """
        logging.info("%s%s: %s", self.warn_prefix,
                     params['#name'], params['#msg'])
    def handle_default(self, params: Dict[str, Any]) -> None:
        """
        Default handler for all other messages.
        
        :param params: The message parameters.
        """
        logging.warning("%sgot %s", self.warn_prefix, params)


# Class to send a query command and return the received response
class SerialRetryCommand:
    """
    Class to send a query command and return the received response with retries.
    """
    def __init__(self, serial: SerialReader, name: str, oid: Optional[int] = None) -> None:
        """
        Initialize the retry command.
        
        :param serial: The SerialReader instance.
        :param name: The name of the expected response.
        :param oid: Optional object ID.
        """
        self.serial = serial
        self.name = name
        self.oid = oid
        self.last_params: Optional[Dict[str, Any]] = None
        self.serial.register_response(self.handle_callback, name, oid)
    def handle_callback(self, params: Dict[str, Any]) -> None:
        """Callback for the expected response."""
        self.last_params = params
    def get_response(self, cmds: List[List[int]], cmd_queue: Any, minclock: int = 0, reqclock: int = 0,
                     retry: bool = True) -> Dict[str, Any]:
        """
        Send commands and wait for the expected response.
        
        :param cmds: List of raw commands to send.
        :param cmd_queue: The command queue to use.
        :param minclock: Minimum clock for sending.
        :param reqclock: Required clock for sending.
        :param retry: Whether to retry on failure.
        :return: The received response parameters.
        """
        retries = 5
        retry_delay = .010
        if not retry:
            retries = 0
        while 1:
            for cmd in cmds[:-1]:
                self.serial.raw_send(cmd, minclock, reqclock, cmd_queue)
            self.serial.raw_send_wait_ack(cmds[-1], minclock, reqclock,
                                          cmd_queue)
            params = self.last_params
            if params is not None:
                self.serial.register_response(None, self.name, self.oid)
                return params
            if retries <= 0:
                self.serial.register_response(None, self.name, self.oid)
                self.serial._error("Timeout on wait for '%s' response",
                                   self.name)
            retries -= 1
            self.serial.reactor.pause(self.serial.reactor.monotonic()
                                      + retry_delay)
            retry_delay *= 2.

# TUXEDO_RT: Las funciones de reset y stk500v2_leave se han movido a 
# rtcore.transport.utils y UARTTransport respectivamente para mayor modularidad.
