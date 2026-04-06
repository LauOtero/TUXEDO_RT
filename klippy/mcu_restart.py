#!/usr/bin/env python3
# Klipper MCU Restart & Recovery Logic
# Handles automatic MCU resets, firmware restarts, and USB power cycling.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging, os
import chelper, mcu_conn
from rtcore.connections import utils as connection_utils

class MCURestartHelper:
    """
    Gestiona la recuperación automática y reinicio de firmware del MCU.
    Soporta métodos: arduino, cheetah, command, rpi_usb.
    """
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._conn_helper = conn_helper
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        self._restart_method = config.getchoice('restart_method', {None: None, 'arduino': 'arduino', 'cheetah': 'cheetah', 'command': 'command', 'rpi_usb': 'rpi_usb'}, None)
        conn_id, baud = conn_helper.get_connection_id()
        if baud: self._restart_method = config.getchoice('restart_method', {None: None, 'arduino': 'arduino', 'cheetah': 'cheetah', 'command': 'command', 'rpi_usb': 'rpi_usb'}, None)
        self._reset_cmd = self._config_reset_cmd = None
        self._is_mcu_bridge = False
        printer.register_event_handler("klippy:firmware_restart", self._firmware_restart)
        printer.register_event_handler("klippy:disconnect", self._disconnect)
        printer.register_event_handler("klippy:mcu_identify", self._mcu_identify)

    def _check_restart(self, reason):
        start_reason = self._printer.get_start_args().get("start_reason")
        if start_reason == 'firmware_restart': return
        logging.info("Attempting automated MCU '%s' restart: %s", self._name, reason)
        self._printer.request_exit('firmware_restart')
        self._reactor.pause(self._reactor.monotonic() + 2.000)
        raise self._mcu.error("Attempt MCU '%s' restart failed" % (self._name,))

    def check_restart_on_crc_mismatch(self): self._check_restart("CRC mismatch")
    def check_restart_on_send_config(self):
        if self._restart_method == 'rpi_usb': self._check_restart("full reset before config")
    def check_restart_on_attach(self):
        resmeth = self._restart_method
        conn_id, baud = self._conn_helper.get_connection_id()
        if resmeth == 'rpi_usb' and not os.path.exists(conn_id): self._check_restart("enable power")
    def lookup_attach_uart_rts(self): return (self._restart_method != "cheetah")

    def _mcu_identify(self):
        self._reset_cmd = self._mcu.try_lookup_command("reset")
        self._config_reset_cmd = self._mcu.try_lookup_command("config_reset")
        ext_only = self._reset_cmd is None and self._config_reset_cmd is None
        msgparser = self._serial.get_msgparser()
        mbaud = msgparser.get_constant('SERIAL_BAUD', None)
        if self._restart_method is None and mbaud is None and not ext_only: self._restart_method = 'command'
        if msgparser.get_constant('CANBUS_BRIDGE', 0):
            self._is_mcu_bridge = True
            self._printer.register_event_handler("klippy:firmware_restart", self._firmware_restart_bridge)

    def _disconnect(self): self._serial.disconnect()

    def _restart_arduino(self):
        logging.info("Attempting MCU '%s' reset", self._name)
        self._disconnect()
        conn_id, baud = self._conn_helper.get_connection_id()
        transport_utils.arduino_reset(conn_id, self._reactor)

    def _restart_cheetah(self):
        logging.info("Attempting MCU '%s' Cheetah-style reset", self._name)
        self._disconnect()
        conn_id, baud = self._conn_helper.get_connection_id()
        transport_utils.cheetah_reset(conn_id, self._reactor)

    def _restart_via_command(self):
        if ((self._reset_cmd is None and self._config_reset_cmd is None) or not self._clocksync.is_active()):
            logging.info("Unable to issue reset command on MCU '%s'", self._name); return
        if self._reset_cmd is None:
            logging.info("Attempting MCU '%s' config_reset command", self._name)
            self._conn_helper.force_local_shutdown()
            self._reactor.pause(self._reactor.monotonic() + 0.015)
            self._config_reset_cmd.send()
        else:
            logging.info("Attempting MCU '%s' reset command", self._name)
            self._reset_cmd.send()
            self._reactor.pause(self._reactor.monotonic() + 0.015)
        self._disconnect()

    def _restart_rpi_usb(self):
        logging.info("Attempting MCU '%s' reset via rpi usb power", self._name)
        self._disconnect()
        chelper.run_hub_ctrl(0)
        self._reactor.pause(self._reactor.monotonic() + 2.)
        chelper.run_hub_ctrl(1)

    def _firmware_restart(self, force=False):
        if self._is_mcu_bridge and not force: return
        if self._restart_method == 'rpi_usb': self._restart_rpi_usb()
        elif self._restart_method == 'command': self._restart_via_command()
        elif self._restart_method == 'cheetah': self._restart_cheetah()
        else: self._restart_arduino()

    def _firmware_restart_bridge(self): self._firmware_restart(True)