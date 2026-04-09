#!/usr/bin/env python3
# Klipper MCU Statistics & Performance Monitoring
# Tracks MCU load, clock sync accuracy, latency, and RT metrics.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging, math

class MCUStatsHelper:
    """
    Recopila y reporta estadísticas de rendimiento del MCU.
    Incluye carga de tareas, precisión de reloj, latencia de comandos y métricas RT de trdispatch.
    """
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._conn_helper = conn_helper
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        self._mcu_freq = 0.
        self._get_status_info = {}
        self._stats_sumsq_base = 0.
        self._mcu_tick_avg = 0.
        self._mcu_tick_stddev = 0.
        self._mcu_tick_awake = 0.
        self._last_latency_warning = 0.
        self._last_load_warning = 0.
        self._rtt_stats = {'srtt': 0.0, 'rttvar': 0.0, 'rto': 0.0}
        self._c_rtt_func = None
        printer.register_event_handler("klippy:ready", self._ready)
        printer.register_event_handler("klippy:mcu_identify", self._mcu_identify)

    def _handle_mcu_stats(self, params):
        count = params['count']
        tick_sum = params['sum']
        c = 1.0 / (count * self._mcu_freq)
        self._mcu_tick_avg = tick_sum * c
        tick_sumsq = params['sumsq'] * self._stats_sumsq_base
        diff = count * tick_sumsq - tick_sum**2
        self._mcu_tick_stddev = c * math.sqrt(max(0., diff))
        self._mcu_tick_awake = tick_sum / self._mcu_freq

    def _mcu_identify(self):
        self._mcu_freq = self._mcu.get_constant_float('CLOCK_FREQ')
        self._stats_sumsq_base = self._mcu.get_constant_float('STATS_SUMSQ_BASE')
        msgparser = self._serial.get_msgparser()
        version, build_versions = msgparser.get_version_info()
        self._get_status_info['mcu_version'] = version
        self._get_status_info['mcu_build_versions'] = build_versions
        self._get_status_info['mcu_constants'] = msgparser.get_constants()
        self._serial.register_response(self._handle_mcu_stats, 'stats')
        # TUXEDO_RT: Load RTT stats function from C (unified metrics)
        self._load_c_rtt_func()

    def _load_c_rtt_func(self):
        try:
            import chelper
            ffi, lib = chelper.get_ffi()
            if ffi and hasattr(lib, 'conn_get_rtt_stats'):
                self._c_rtt_func = lib.conn_get_rtt_stats
                self._ffi = ffi
                logging.info("TUXEDO_RT: RTT stats loaded from C (srtt/rttvar/rto)")
            else:
                logging.info("TUXEDO_RT: C RTT function not available, using Python fallback")
        except Exception as e:
            logging.info("TUXEDO_RT: Failed to load C RTT function: %s", e)

    def _ready(self):
        if self._mcu.is_fileoutput(): return
        mcu_freq = self._mcu_freq
        systime = self._reactor.monotonic()
        get_clock = self._clocksync.get_clock
        calc_freq = get_clock(systime + 1) - get_clock(systime)
        freq_diff = abs(mcu_freq - calc_freq)
        mcu_freq_mhz = int(mcu_freq / 1000000. + 0.5)
        calc_freq_mhz = int(calc_freq / 1000000. + 0.5)
        if freq_diff > mcu_freq * 0.01 and mcu_freq_mhz != calc_freq_mhz:
            pconfig = self._printer.lookup_object('configfile')
            msg = ("MCU '%s' configured for %dMhz but running at %dMhz!" % (self._name, mcu_freq_mhz, calc_freq_mhz))
            pconfig.runtime_warning(msg)

    def get_status(self, eventtime=None): return dict(self._get_status_info)

    def stats(self, eventtime):
        mcu_awake = self._mcu_tick_awake
        mcu_task_avg = self._mcu_tick_avg
        mcu_task_stddev = self._mcu_tick_stddev
        load = "mcu_awake=%.03f mcu_task_avg=%.06f mcu_task_stddev=%.06f" % (mcu_awake, mcu_task_avg, mcu_task_stddev)
        s_stats = self._serial.stats(eventtime)
        c_stats = self._clocksync.stats(eventtime)
        stats = load + ' ' + s_stats + ' ' + c_stats
        last_stats = {'mcu_awake': mcu_awake, 'mcu_task_avg': mcu_task_avg, 'mcu_task_stddev': mcu_task_stddev}
        for token in s_stats.split():
            key, value = token.split('=', 1)
            last_stats[key] = float(value) if '.' in value else int(value)
        for token in c_stats.split():
            key, value = token.split('=', 1)
            last_stats[key] = float(value) if '.' in value else int(value)

        avg_lat, max_lat, cur_avg_lat = self._conn_helper.get_latency_stats()
        last_stats['latency_avg'] = avg_lat
        last_stats['latency_max'] = max_lat
        last_stats['latency_cur'] = cur_avg_lat

        # TUXEDO_RT: Consume RTT stats from C (unified metrics)
        if self._c_rtt_func is not None and hasattr(self, '_ffi'):
            try:
                srtt_ptr = self._ffi.new('double *')
                rttvar_ptr = self._ffi.new('double *')
                rto_ptr = self._ffi.new('double *')
                self._c_rtt_func(self._serial.get_serialqueue(), srtt_ptr, rttvar_ptr, rto_ptr)
                srtt = srtt_ptr[0]
                rttvar = rttvar_ptr[0]
                rto = rto_ptr[0]
                self._rtt_stats['srtt'] = srtt
                self._rtt_stats['rttvar'] = rttvar
                self._rtt_stats['rto'] = rto
                stats += ' rtt_srtt=%.3f rtt_rttvar=%.3f rtt_rto=%.3f' % (srtt, rttvar, rto)
                last_stats['rtt_srtt'] = srtt
                last_stats['rtt_rttvar'] = rttvar
                last_stats['rtt_rto'] = rto
            except Exception:
                pass

        if max_lat > 0.100 and eventtime >= self._last_latency_warning + 5.:
            self._last_latency_warning = eventtime
            logging.warning("High latency detected on MCU '%s': max=%.03fms", self._name, max_lat * 1000.0)
        if mcu_task_avg > 0.000050 and eventtime >= self._last_load_warning + 5.:
            self._last_load_warning = eventtime
            logging.warning("MCU '%s' high task load: avg=%.06fs", self._name, mcu_task_avg)

        stats += ' latency_avg=%.06f latency_max=%.06f latency_cur_avg=%.06f' % (avg_lat, max_lat, cur_avg_lat)
        self._get_status_info['last_stats'] = last_stats
        return False, '%s: %s' % (self._name, stats)