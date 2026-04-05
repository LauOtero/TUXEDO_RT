#!/usr/bin/env python3
# Klipper Main MCU Orchestration
# Core MCU class, command wrappers, connection helpers, and entry points.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging, collections
import msgproto, pins, chelper, clocksync
import mcu_conn, mcu_config, mcu_stats, mcu_restart

class error(Exception): pass

MAX_SCHEDULE_TICKS = 0x7fffffff
MIN_SCHEDULE_TIME = 0.100
MAX_NOMINAL_DURATION = 3.0

# =========================================================================
# Command Wrappers & Helpers
# =========================================================================

class DummyResponse:
    """Genera una respuesta falsa para comandos de consulta en modo debug/simulación."""
    def __init__(self, serial, name, oid=None):
        params = {}
        if oid is not None: params['oid'] = oid
        msgparser = serial.get_msgparser()
        resp = msgparser.create_dummy_response(name, params)
        resp['#sent_time'] = 0.; resp['#receive_time'] = 0.
        self._response = resp
    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True): return dict(self._response)

class RetryAsyncCommand:
    """Reenvía comandos de consulta hasta recibir la respuesta esperada o agotar timeout."""
    TIMEOUT_TIME = 5.0; RETRY_TIME = 0.500
    def __init__(self, serial, name, oid=None):
        self.serial = serial; self.name = name; self.oid = oid
        self.reactor = serial.get_reactor(); self.completion = self.reactor.completion()
        self.min_query_time = self.reactor.monotonic(); self.need_response = True
        self.serial.register_response(self.handle_callback, name, oid)
    def handle_callback(self, params):
        if self.need_response and params['#sent_time'] >= self.min_query_time:
            self.need_response = False; self.reactor.async_complete(self.completion, params)
    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True):
        cmd, = cmds; self.serial.raw_send_wait_ack(cmd, minclock, reqclock, cmd_queue)
        self.min_query_time = 0.
        timeout_time = query_time = self.reactor.monotonic()
        if retry: timeout_time += self.TIMEOUT_TIME
        while 1:
            params = self.completion.wait(query_time + self.RETRY_TIME)
            if params is not None: self.serial.register_response(None, self.name, self.oid); return params
            query_time = self.reactor.monotonic()
            if query_time > timeout_time: self.serial.register_response(None, self.name, self.oid); raise error("Timeout on wait for '%s' response" % (self.name,))
            self.serial.raw_send(cmd, minclock, minclock, cmd_queue)

class CommandQueryWrapper:
    """Wrapper seguro para comandos que esperan respuesta, con reintento automático."""
    def __init__(self, conn_helper, msgformat, respformat, oid=None, cmd_queue=None, is_async=False):
        self._serial = serial = conn_helper.get_serial()
        self._cmd = serial.get_msgparser().lookup_command(msgformat)
        serial.get_msgparser().lookup_command(respformat)
        self._response = respformat.split()[0]; self._oid = oid
        self._error = conn_helper.get_mcu().get_printer().command_error
        self._xmit_helper = RetryAsyncCommand
        if conn_helper.get_mcu().is_fileoutput(): self._xmit_helper = DummyResponse
        elif is_async: self._xmit_helper = RetryAsyncCommand
        if cmd_queue is None: cmd_queue = serial.get_default_command_queue()
        self._cmd_queue = cmd_queue
    def _do_send(self, cmds, minclock, reqclock, retry):
        xh = self._xmit_helper(self._serial, self._response, self._oid)
        reqclock = max(minclock, reqclock)
        try: return xh.get_response(cmds, self._cmd_queue, minclock, reqclock, retry)
        except error as e: raise self._error(str(e))
    def send(self, data=(), minclock=0, reqclock=0, retry=True): return self._do_send([self._cmd.encode(data)], minclock, reqclock, retry)
    def send_with_preface(self, preface_cmd, preface_data=(), data=(), minclock=0, reqclock=0, retry=True):
        cmds = [preface_cmd._cmd.encode(preface_data), self._cmd.encode(data)]
        return self._do_send(cmds, minclock, reqclock, retry)

class CommandWrapper:
    """Wrapper para enviar comandos simples al MCU sin esperar respuesta."""
    def __init__(self, conn_helper, msgformat, cmd_queue=None):
        self._conn_helper = conn_helper
        self._serial = serial = conn_helper.get_serial()
        msgparser = serial.get_msgparser()
        self._cmd = msgparser.lookup_command(msgformat)
        if cmd_queue is None: cmd_queue = serial.get_default_command_queue()
        self._cmd_queue = cmd_queue
        self._msgtag = msgparser.lookup_msgid(msgformat) & 0xffffffff
        if conn_helper.get_mcu().is_fileoutput(): self.send_wait_ack = self.send
    def send(self, data=(), minclock=0, reqclock=0):
        cmd = self._cmd.encode(data); self._serial.raw_send(cmd, minclock, reqclock, self._cmd_queue)
    def send_wait_ack(self, data=(), minclock=0, reqclock=0):
        cmd = self._cmd.encode(data)
        params = self._serial.raw_send_wait_ack(cmd, minclock, reqclock, self._cmd_queue)
        self._conn_helper.note_latency(params['#receive_time'] - params['#sent_time'])
    def get_command_tag(self): return self._msgtag

class AsyncResponseWrapper:
    """Suscripción de larga duración a respuestas del MCU (callbacks asíncronos)."""
    def __init__(self, conn_helper, cfg_helper, callback, msgformat, oid=None):
        self._serial = conn_helper.get_serial(); self._callback = callback
        self._msgformat = msgformat; self._name = msgformat.split()[0]; self._oid = oid
        if cfg_helper.is_config_finalized(): self._register()
        else:
            self._serial.register_response((lambda p: None), self._name, oid)
            cfg_helper.register_post_init_callback(self._register)
    def _register(self):
        self._serial.get_msgparser().lookup_command(self._msgformat)
        self._serial.register_response(self._callback, self._name, self._oid)
    def unregister(self): self._serial.register_response(None, self._name, self._oid)

class CommandBatcher:
    """Agrupa múltiples comandos para reducir overhead de syscalls y locks."""
    def __init__(self, mcu): self._mcu = mcu; self._cmds = []
    def add(self, cmd_wrapper, data=(), minclock=0, reqclock=0): self._cmds.append((cmd_wrapper, data, minclock, reqclock))
    def send(self):
        if not self._cmds: return
        for cw, data, minclock, reqclock in self._cmds: cw.send(data, minclock, reqclock)
        self._cmds = []

# =========================================================================
# Connection Helper Integration
# =========================================================================

class MCUConnectHelper:
    """
    Orquesta la conexión del MCU usando el sistema extensible de mcu_conn.
    Gestiona cachés de comandos, latencia y shutdown tracking.
    """
    def __init__(self, config, mcu, clocksync):
        self._mcu = mcu; self._clocksync = clocksync
        self._printer = printer = config.get_printer(); self._reactor = printer.get_reactor()
        self._name = name = mcu.get_name()
        
        # Crear conexión usando el factory registrado
        self._conn = mcu_conn.create_mcu_connection(config, name)
        
        # Cache y tracking
        self._command_cache = {}; self._query_command_cache = {}
        self._latencies = collections.deque(maxlen=100)
        self._total_latency = 0.0; self._max_latency = 0.0; self._latency_count = 0
        self._emergency_stop_cmd = None
        self._is_shutdown = self._is_timeout = False; self._shutdown_msg = ""
        
        printer.register_event_handler("klippy:mcu_identify", self._mcu_identify)
        self._restart_helper = mcu_restart.MCURestartHelper(config, self)
        printer.register_event_handler("klippy:shutdown", self._shutdown)
        printer.register_event_handler("klippy:analyze_shutdown", self._analyze_shutdown)

    def get_mcu(self): return self._mcu
    def get_serial(self): return self._conn
    def get_clocksync(self): return self._clocksync
    def get_connection_id(self):
        if hasattr(self._conn, '_uart_port'): return self._conn._uart_port, getattr(self._conn, '_uart_baud', 0)
        if hasattr(self._conn, '_can_uuid'): return self._conn._can_uuid, 0
        return "unknown", 0
    def get_restart_helper(self): return self._restart_helper
    def lookup_command(self, msgformat, cq=None):
        key = (msgformat, cq); res = self._command_cache.get(key)
        if res is not None: return res
        res = CommandWrapper(self, msgformat, cq); self._command_cache[key] = res; return res
    def lookup_query_command(self, msgformat, respformat, oid=None, cq=None, is_async=False):
        key = (msgformat, respformat, oid, cq, is_async); res = self._query_command_cache.get(key)
        if res is not None: return res
        res = CommandQueryWrapper(self, msgformat, respformat, oid, cq, is_async); self._query_command_cache[key] = res; return res
    def note_latency(self, latency):
        self._latency_count += 1; self._total_latency += latency
        if latency > self._max_latency: self._max_latency = latency
        self._latencies.append(latency)
    def get_latency_stats(self):
        if not self._latency_count: return 0.0, 0.0, 0.0
        return self._total_latency / self._latency_count, self._max_latency, sum(self._latencies) / len(self._latencies)
    def _handle_shutdown(self, params):
        if self._is_shutdown: return
        self._is_shutdown = True; self._shutdown_msg = msg = params['static_string_id']
        shutdown_clock = params.get("clock")
        if shutdown_clock is not None: shutdown_clock = self._mcu.clock32_to_clock64(shutdown_clock)
        self._printer.invoke_async_shutdown("MCU shutdown", {"reason": msg, "mcu": self._name, "event_type": params['#name'], "shutdown_clock": shutdown_clock})
    def _handle_starting(self, params):
        if not self._is_shutdown: self._printer.invoke_async_shutdown("MCU '%s' spontaneous restart" % (self._name,))
    def log_info(self):
        msgparser = self._serial.get_msgparser()
        message_count = len(msgparser.get_messages())
        version, build_versions = msgparser.get_version_info()
        return "Loaded MCU '%s' %d commands (%s / %s)\nMCU '%s' config: %s" % (self._name, message_count, version, build_versions, self._name, " ".join(["%s=%s" % (k, v) for k, v in msgparser.get_constants().items()]))
    def _attach_file(self):
        start_args = self._printer.get_start_args()
        out_fname = start_args.get('debugoutput') + ("-" + self._name if self._name != 'mcu' else "")
        dict_fname = start_args.get('dictionary') + ("_" + self._name if self._name != 'mcu' else "")
        self._conn.connect_file(open(out_fname, 'wb'), open(dict_fname, 'rb').read())
        self._clocksync.connect_file(self._serial)
    def _attach(self):
        self._restart_helper.check_restart_on_attach()
        try:
            if hasattr(self._conn, '_uart_port'):
                rts = self._restart_helper.lookup_attach_uart_rts()
                self._conn.connect_serial(self._conn._uart_port, self._conn._uart_baud, rts)
            elif hasattr(self._conn, '_rs485_port'):
                self._conn.connect_rs485(self._conn._rs485_port, self._conn._rs485_baud)
            elif hasattr(self._conn, '_can_uuid'):
                self._conn.connect_canbus(self._conn._can_uuid, 0, self._conn._can_iface)
            elif hasattr(self._conn, '_et_alias'):
                self._conn.connect_ethertux(self._conn._et_alias, self._conn._et_pos, self._conn._et_vendor, self._conn._et_product, self._conn._et_cycle)
            self._clocksync.connect(self._serial)
        except error as e: raise self._mcu.error(str(e))
    def _mcu_identify(self):
        if self._mcu.is_fileoutput(): self._attach_file()
        else: self._attach()
        logging.info(self.log_info())
        self._emergency_stop_cmd = self._mcu.lookup_command("emergency_stop")
        self._serial.register_response(self._handle_shutdown, 'shutdown')
        self._serial.register_response(self._handle_shutdown, 'is_shutdown')
        self._serial.register_response(self._handle_starting, 'starting')
    def _analyze_shutdown(self, msg, details):
        if self._mcu.is_fileoutput(): return
        logging.info("MCU '%s' shutdown: %s\n%s\n%s", self._name, self._shutdown_msg, self._clocksync.dump_debug(), self._serial.dump_debug())
    def _shutdown(self, force=False):
        if (self._emergency_stop_cmd is None or (self._is_shutdown and not force)): return
        self._emergency_stop_cmd.send()
    def force_local_shutdown(self): self._is_shutdown = True; self._shutdown(force=True)
    def check_timeout(self, eventtime):
        if (self._clocksync.is_active() or self._mcu.is_fileoutput() or self._is_timeout): return
        self._is_timeout = True
        logging.info("Timeout with MCU '%s' (eventtime=%f)", self._name, eventtime)
        self._printer.invoke_shutdown("Lost communication with MCU '%s'" % (self._name,))
    def is_shutdown(self): return self._is_shutdown
    def get_shutdown_msg(self): return self._shutdown_msg

# =========================================================================
# Core MCU Class
# =========================================================================

class MCU:
    """
    Clase principal que representa un microcontrolador en Klipper.
    Actúa como fachada para pines, configuración, estadísticas y comunicación.
    """
    error = error
    def __init__(self, config, clocksync):
        self._printer = printer = config.get_printer(); self._clocksync = clocksync
        self._name = config.get_name()
        if self._name.startswith('mcu'): self._name = self._name[4:]
        self._conn_helper = MCUConnectHelper(config, self, clocksync)
        self._serial = self._conn_helper.get_serial()
        self._config_helper = mcu_config.MCUConfigHelper(config, self._conn_helper)
        self._stats_helper = mcu_stats.MCUStatsHelper(config, self._conn_helper)
        printer.load_object(config, "error_mcu")
        if self.is_fileoutput():
            def dummy_estimated_print_time(eventtime): return 0.
            self.estimated_print_time = dummy_estimated_print_time

    def get_name(self): return self._name
    def get_printer(self): return self._printer
    def is_fileoutput(self): return self._printer.get_start_args().get('debugoutput') is not None
    def setup_pin(self, pin_type, pin_params): return self._config_helper.setup_pin(pin_type, pin_params)
    def create_oid(self): return self._config_helper.create_oid()
    def register_config_callback(self, cb): self._config_helper.register_config_callback(cb)
    def add_config_cmd(self, cmd, is_init=False, on_restart=False): self._config_helper.add_config_cmd(cmd, is_init, on_restart)
    def request_move_queue_slot(self): self._config_helper.request_move_queue_slot()
    def get_query_slot(self, oid): return self._config_helper.get_query_slot(oid)
    def seconds_to_clock(self, time): return self._config_helper.seconds_to_clock(time)
    def min_schedule_time(self): return MIN_SCHEDULE_TIME
    def max_nominal_duration(self): return MAX_NOMINAL_DURATION
    def lookup_command(self, msgformat, cq=None): return self._conn_helper.lookup_command(msgformat, cq)
    def lookup_query_command(self, msgformat, respformat, oid=None, cq=None, is_async=False): return self._conn_helper.lookup_query_command(msgformat, respformat, oid, cq, is_async)
    def try_lookup_command(self, msgformat):
        try: return self.lookup_command(msgformat)
        except self._serial.get_msgparser().error as e: return None
    def alloc_command_queue(self): return self._serial.alloc_command_queue()
    def get_command_batcher(self): return CommandBatcher(self)
    def register_serial_response(self, cb, msg, oid=None): return AsyncResponseWrapper(self._conn_helper, self._config_helper, cb, msg, oid)
    def check_valid_response(self, msgformat):
        try: self._serial.get_msgparser().lookup_command(msgformat); return True
        except self._serial.get_msgparser().error as e: return False
    def get_enumerations(self): return self._serial.get_msgparser().get_enumerations()
    def get_constants(self): return self._serial.get_msgparser().get_constants()
    def get_constant_float(self, name): return self._serial.get_msgparser().get_constant_float(name)
    def print_time_to_clock(self, print_time): return self._clocksync.print_time_to_clock(print_time)
    def clock_to_print_time(self, clock): return self._clocksync.clock_to_print_time(clock)
    def estimated_print_time(self, eventtime): return self._clocksync.estimated_print_time(eventtime)
    def clock32_to_clock64(self, clock32): return self._clocksync.clock32_to_clock64(clock32)
    def calibrate_clock(self, print_time, eventtime):
        offset, freq = self._clocksync.calibrate_clock(print_time, eventtime)
        self._conn_helper.check_timeout(eventtime); return offset, freq
    def get_status(self, eventtime=None): return self._stats_helper.get_status(eventtime)
    def stats(self, eventtime): return self._stats_helper.stats(eventtime)

def add_printer_objects(config):
    """Registra objetos MCU en el sistema de Klipper."""
    printer = config.get_printer(); reactor = printer.get_reactor()
    mainsync = clocksync.ClockSync(reactor)
    printer.add_object('mcu', MCU(config.getsection('mcu'), mainsync))
    for s in config.get_prefix_sections('mcu '):
        printer.add_object(s.section, MCU(s, clocksync.SecondarySync(reactor, mainsync)))

def get_printer_mcu(printer, name):
    if name == 'mcu': return printer.lookup_object(name)
    return printer.lookup_object('mcu ' + name)