#!/usr/bin/env python3
# Klipper MCU Configuration Helper
# Handles pin resolution, config command batching, CRC calculation, and move queue setup.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging, zlib, pins
import msgproto
import mcu_pins

MAX_SCHEDULE_TICKS = 0x7fffffff
MAX_NOMINAL_DURATION = 3.0

class MCUConfigHelper:
    """
    Gestiona la fase de configuración del MCU.
    Recopila comandos de config, resuelve nombres de pines, calcula CRC y valida la sincronización con el firmware.
    """
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._conn_helper = conn_helper
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        self._config_finalized = False
        self._oid_count = 0
        self._config_callbacks = []
        self._post_init_callbacks = []
        self._config_cmds = []
        self._restart_cmds = []
        self._init_cmds = []
        self._config_crc = 0
        self._mcu_freq = 0.
        self._reserved_move_slots = 0
        printer.lookup_object('pins').register_chip(self._name, mcu)
        printer.register_event_handler("klippy:mcu_identify", self._mcu_identify)
        printer.register_event_handler("klippy:connect", self._connect)

    def _finalize_config(self):
        """Ejecuta callbacks, resuelve pines y calcula CRC de configuración."""
        for cb in self._config_callbacks: cb()
        self._config_finalized = True
        self._config_cmds.insert(0, "allocate_oids count=%d" % (self._oid_count,))
        ppins = self._printer.lookup_object('pins')
        pin_resolver = ppins.get_pin_resolver(self._name)
        pin_cache = {}
        for cmdlist in (self._config_cmds, self._restart_cmds, self._init_cmds):
            for i, cmd in enumerate(cmdlist):
                if 'pin=' in cmd:
                    res = pin_cache.get(cmd)
                    if res is None: res = pin_cache[cmd] = pin_resolver.update_command(cmd)
                    cmdlist[i] = res
        crc = 0
        for i, cmd in enumerate(self._config_cmds):
            if i > 0: crc = zlib.crc32(b'\n', crc)
            crc = zlib.crc32(cmd.encode(), crc)
        self._config_crc = crc & 0xffffffff
        self._config_cmds.append("finalize_config crc=%d" % (self._config_crc,))

    def _send_cfg_init_commands(self, cmds):
        try:
            for c in cmds: self._serial.send(c)
        except msgproto.enumeration_error as e:
            enum_name, enum_value = e.get_enum_params()
            if enum_name == 'pin':
                raise self._printer.config_error("Pin '%s' is not a valid pin name on mcu '%s'" % (enum_value, self._name))
            raise

    def _send_get_config(self):
        get_config_cmd = self._mcu.lookup_query_command("get_config", "config is_config=%c crc=%u is_shutdown=%c move_count=%hu")
        if self._mcu.is_fileoutput(): return {'is_config': 0, 'move_count': 500, 'crc': 0}
        config_params = get_config_cmd.send()
        if self._conn_helper.is_shutdown(): raise self._conn_helper.get_mcu().error("MCU '%s' error during config: %s" % (self._name, self._conn_helper.get_shutdown_msg()))
        if config_params['is_shutdown']: raise self._conn_helper.get_mcu().error("Can not update MCU '%s' config as it is shutdown" % (self._name,))
        return config_params

    def _connect(self):
        """Conecta, valida CRC, envía config y setup movequeue."""
        restart_helper = self._conn_helper.get_restart_helper()
        config_params = self._send_get_config()
        if not config_params['is_config']:
            restart_helper.check_restart_on_send_config()
            self._finalize_config()
            cfg_init_cmds = self._config_cmds + self._init_cmds
            logging.info("Sending MCU '%s' printer configuration...", self._name)
        else:
            start_reason = self._printer.get_start_args().get("start_reason")
            if start_reason == 'firmware_restart': raise self._conn_helper.get_mcu().error("Failed automated reset of MCU '%s'" % (self._name,))
            self._finalize_config()
            if self._config_crc != config_params['crc']:
                restart_helper.check_restart_on_crc_mismatch()
                raise self._conn_helper.get_mcu().error("MCU '%s' CRC does not match config" % (self._name,))
            cfg_init_cmds = self._restart_cmds + self._init_cmds
            
        self._send_cfg_init_commands(cfg_init_cmds)
        config_params = self._send_get_config()
        if not config_params['is_config'] and not self._mcu.is_fileoutput(): raise self._conn_helper.get_mcu().error("Unable to configure MCU '%s'" % (self._name,))
        for cb in self._post_init_callbacks: cb()
        
        move_count = config_params['move_count']
        if move_count < self._reserved_move_slots: raise self._conn_helper.get_mcu().error("Too few moves available on MCU '%s'" % (self._name,))
        ss_move_count = move_count - self._reserved_move_slots
        motion_queuing = self._printer.lookup_object('motion_queuing')
        motion_queuing.setup_mcu_movequeue(self._mcu, self._serial.get_serialqueue(), ss_move_count)
        move_msg = "Configured MCU '%s' (%d moves)" % (self._name, move_count)
        logging.info(move_msg)
        log_info = self._conn_helper.log_info() + "\n" + move_msg
        self._printer.set_rollover_info(self._name, log_info, log=False)

    def _mcu_identify(self):
        self._mcu_freq = self._mcu.get_constant_float('CLOCK_FREQ')
        ppins = self._printer.lookup_object('pins')
        pin_resolver = ppins.get_pin_resolver(self._name)
        for cname, value in self._mcu.get_constants().items():
            if cname.startswith("RESERVE_PINS"):
                for pin in value.split(','): pin_resolver.reserve_pin(pin, cname[13:])
        if MAX_NOMINAL_DURATION * self._mcu_freq > MAX_SCHEDULE_TICKS:
            max_possible = MAX_SCHEDULE_TICKS * 1 / self._mcu_freq
            raise self._conn_helper.get_mcu().error("Too high clock speed for MCU '%s' to be able to resolve a maximum nominal duration of %ds. Max possible duration: %ds" % (self._name, MAX_NOMINAL_DURATION, max_possible))

    def _verify_not_finalized(self):
        if self._config_finalized: raise self._conn_helper.get_mcu().error("Internal error! MCU already configured")

    def is_config_finalized(self): return self._config_finalized
    def setup_pin(self, pin_type, pin_params):
        self._verify_not_finalized()
        pcs = {'endstop': mcu_pins.MCU_endstop, 'digital_out': mcu_pins.MCU_digital_out, 'pwm': mcu_pins.MCU_pwm, 'adc': mcu_pins.MCU_adc}
        if pin_type not in pcs: raise pins.error("pin type %s not supported on mcu" % (pin_type,))
        return pcs[pin_type](self._mcu, pin_params)
    def create_oid(self): self._verify_not_finalized(); self._oid_count += 1; return self._oid_count - 1
    def register_config_callback(self, cb): self._verify_not_finalized(); self._config_callbacks.append(cb)
    def add_config_cmd(self, cmd, is_init=False, on_restart=False):
        self._verify_not_finalized()
        if is_init: self._init_cmds.append(cmd)
        elif on_restart: self._restart_cmds.append(cmd)
        else: self._config_cmds.append(cmd)
    def register_post_init_callback(self, cb): self._verify_not_finalized(); self._post_init_callbacks.append(cb)
    def get_query_slot(self, oid):
        slot = self.seconds_to_clock(oid * .01)
        t = int(self._mcu.estimated_print_time(self._reactor.monotonic()) + 1.5)
        return self._mcu.print_time_to_clock(t) + slot
    def seconds_to_clock(self, time): return int(time * self._mcu_freq)
    def request_move_queue_slot(self): self._reserved_move_slots += 1