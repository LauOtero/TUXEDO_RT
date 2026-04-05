#!/usr/bin/env python3
# Klipper MCU Pin Management & Homing Logic
# Handles digital outputs, PWM, ADC, endstops, and trsync homing coordination.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging, math, collections
import chelper, pins

MAX_SCHEDULE_TICKS = 0x7fffffff

class MCU_trsync:
    """
    Gestiona la sincronización de disparo (trsync) para homing y sensorless.
    Coordina múltiples MCUs para detener steppers simultáneamente tras detectar un endstop.
    """
    REASON_ENDSTOP_HIT = 1
    REASON_HOST_REQUEST = 2
    REASON_PAST_END_TIME = 3
    REASON_COMMS_TIMEOUT = 4

    def __init__(self, mcu, trdispatch):
        self._mcu = mcu
        self._trdispatch = trdispatch
        self._reactor = mcu.get_printer().get_reactor()
        self._steppers = []
        self._trdispatch_mcu = None
        self._oid = mcu.create_oid()
        self._cmd_queue = mcu.alloc_command_queue()
        self._response_trsync = None
        self._trsync_start_cmd = self._trsync_set_timeout_cmd = None
        self._trsync_trigger_cmd = self._trsync_query_cmd = None
        self._stepper_stop_cmd = None
        self._trigger_completion = None
        self._home_end_clock = None
        mcu.register_config_callback(self._build_config)
        printer = mcu.get_printer()
        printer.register_event_handler("klippy:shutdown", self._shutdown)

    def get_mcu(self): return self._mcu
    def get_oid(self): return self._oid
    def get_command_queue(self): return self._cmd_queue
    def add_stepper(self, stepper):
        if stepper not in self._steppers: self._steppers.append(stepper)
    def get_steppers(self): return list(self._steppers)

    def _build_config(self):
        mcu = self._mcu
        mcu.add_config_cmd("config_trsync oid=%d" % (self._oid,))
        mcu.add_config_cmd("trsync_start oid=%d report_clock=0 report_ticks=0 expire_reason=0" % (self._oid,), on_restart=True)
        self._trsync_start_cmd = mcu.lookup_command("trsync_start oid=%c report_clock=%u report_ticks=%u expire_reason=%c", cq=self._cmd_queue)
        self._trsync_set_timeout_cmd = mcu.lookup_command("trsync_set_timeout oid=%c clock=%u", cq=self._cmd_queue)
        self._trsync_trigger_cmd = mcu.lookup_command("trsync_trigger oid=%c reason=%c", cq=self._cmd_queue)
        self._trsync_query_cmd = mcu.lookup_query_command("trsync_trigger oid=%c reason=%c", "trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u", oid=self._oid, cq=self._cmd_queue)
        self._stepper_stop_cmd = mcu.lookup_command("stepper_stop_on_trigger oid=%c trsync_oid=%c", cq=self._cmd_queue)
        
        ffi_main, ffi_lib = chelper.get_ffi()
        set_timeout_tag = mcu.lookup_command("trsync_set_timeout oid=%c clock=%u").get_command_tag()
        trigger_tag = mcu.lookup_command("trsync_trigger oid=%c reason=%c").get_command_tag()
        state_tag = mcu.lookup_command("trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u").get_command_tag()
        self._trdispatch_mcu = ffi_main.gc(ffi_lib.trdispatch_mcu_alloc(
            self._trdispatch, mcu._serial.get_serialqueue(), self._cmd_queue, self._oid,
            set_timeout_tag, trigger_tag, state_tag), ffi_lib.free)

    def _shutdown(self):
        tc = self._trigger_completion
        if tc is not None: self._trigger_completion = None; tc.complete(False)

    def _handle_trsync_state(self, params):
        if not params['can_trigger']:
            tc = self._trigger_completion
            if tc is not None:
                self._trigger_completion = None
                self._reactor.async_complete(tc, params['trigger_reason'] >= self.REASON_COMMS_TIMEOUT)
        elif self._home_end_clock is not None:
            clock = self._mcu.clock32_to_clock64(params['clock'])
            if clock >= self._home_end_clock:
                self._home_end_clock = None
                self._trsync_trigger_cmd.send([self._oid, self.REASON_PAST_END_TIME])

    def start(self, print_time, report_offset, trigger_completion, expire_timeout):
        self._trigger_completion = trigger_completion
        self._home_end_clock = None
        clock = self._mcu.print_time_to_clock(print_time)
        expire_ticks = self._mcu.seconds_to_clock(expire_timeout)
        expire_clock = clock + expire_ticks
        report_ticks = self._mcu.seconds_to_clock(expire_timeout * .3)
        report_clock = clock + int(report_ticks * report_offset + .5)
        min_extend_ticks = int(report_ticks * .8 + .5)
        
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_mcu_setup(self._trdispatch_mcu, clock, expire_clock, expire_ticks, min_extend_ticks)
        self._response_trsync = self._mcu.register_serial_response(self._handle_trsync_state, "trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u", self._oid)
        self._trsync_start_cmd.send([self._oid, report_clock, report_ticks, self.REASON_COMMS_TIMEOUT], reqclock=clock)
        for s in self._steppers: self._stepper_stop_cmd.send([s.get_oid(), self._oid])
        self._trsync_set_timeout_cmd.send([self._oid, expire_clock], reqclock=clock)

    def set_home_end_time(self, home_end_time): self._home_end_clock = self._mcu.print_time_to_clock(home_end_time)

    def stop(self):
        self._response_trsync.unregister()
        self._response_trsync = None
        self._trigger_completion = None
        if self._mcu.is_fileoutput(): return self.REASON_ENDSTOP_HIT
        params = self._trsync_query_cmd.send([self._oid, self.REASON_HOST_REQUEST])
        for s in self._steppers: s.note_homing_end()
        return params['trigger_reason']

class TriggerDispatch:
    """
    Coordinador maestro de homing multi-MCU.
    Inicia y detiene sincronizadamente los trsync de todos los steppers involucrados.
    """
    TRSYNC_TIMEOUT = 0.025
    TRSYNC_SINGLE_MCU_TIMEOUT = 0.250

    def __init__(self, mcu):
        self._mcu = mcu
        self._trigger_completion = None
        ffi_main, ffi_lib = chelper.get_ffi()
        self._trdispatch = ffi_main.gc(ffi_lib.trdispatch_alloc(), ffi_lib.free)
        self._trsyncs = [MCU_trsync(mcu, self._trdispatch)]

    def get_oid(self): return self._trsyncs[0].get_oid()
    def get_command_queue(self): return self._trsyncs[0].get_command_queue()
    
    def add_stepper(self, stepper):
        trsyncs = {trsync.get_mcu(): trsync for trsync in self._trsyncs}
        trsync = trsyncs.get(stepper.get_mcu())
        if trsync is None:
            trsync = MCU_trsync(stepper.get_mcu(), self._trdispatch)
            self._trsyncs.append(trsync)
        trsync.add_stepper(stepper)
        # Validación de ejes compartidos multi-MCU
        sname = stepper.get_name()
        if sname.startswith('stepper'):
            for ot in self._trsyncs:
                for s in ot.get_steppers():
                    if ot is not trsync and s.get_name().startswith(sname[:9]):
                        raise self._mcu.get_printer().config_error("Multi-mcu homing not supported on multi-mcu shared axis")

    def get_steppers(self): return [s for trsync in self._trsyncs for s in trsync.get_steppers()]
    def start(self, print_time):
        reactor = self._mcu.get_printer().get_reactor()
        self._trigger_completion = reactor.completion()
        expire_timeout = self.TRSYNC_SINGLE_MCU_TIMEOUT if len(self._trsyncs) == 1 else self.TRSYNC_TIMEOUT
        for i, trsync in enumerate(self._trsyncs):
            trsync.start(print_time, float(i) / len(self._trsyncs), self._trigger_completion, expire_timeout)
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_start(self._trdispatch, self._trsyncs[0].REASON_HOST_REQUEST)
        return self._trigger_completion

    def wait_end(self, end_time):
        self._trsyncs[0].set_home_end_time(end_time)
        if self._mcu.is_fileoutput(): self._trigger_completion.complete(True)
        self._trigger_completion.wait()

    def stop(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_stop(self._trdispatch)
        res = [trsync.stop() for trsync in self._trsyncs]
        err_res = [r for r in res if r >= MCU_trsync.REASON_COMMS_TIMEOUT]
        return err_res[0] if err_res else res[0]

    def get_rt_stats(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        stats = ffi_main.new('struct trdispatch_rt_stats[1]')
        ffi_lib.trdispatch_get_rt_stats(self._trdispatch, stats)
        d = stats[0]
        return {'sample_count': d.sample_count, 'trigger_count': d.trigger_count, 'last_latency_us': d.last_latency_us, 'max_latency_us': d.max_latency_us, 'avg_latency_us': d.avg_latency_us}

class MCU_endstop:
    """Wrapper de pin de fin de carrera (endstop). Gestiona polling, homing y trsync."""
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._pin = pin_params['pin']
        self._pullup = pin_params['pullup']
        self._invert = pin_params['invert']
        self._oid = self._mcu.create_oid()
        self._home_cmd = self._query_cmd = None
        self._mcu.register_config_callback(self._build_config)
        self._rest_ticks = 0
        self._dispatch = TriggerDispatch(mcu)
    def get_mcu(self): return self._mcu
    def add_stepper(self, stepper): self._dispatch.add_stepper(stepper)
    def get_steppers(self): return self._dispatch.get_steppers()
    def _build_config(self):
        self._mcu.add_config_cmd("config_endstop oid=%d pin=%s pull_up=%d" % (self._oid, self._pin, self._pullup))
        self._mcu.add_config_cmd("endstop_home oid=%d clock=0 sample_ticks=0 sample_count=0 rest_ticks=0 pin_value=0 trsync_oid=0 trigger_reason=0" % (self._oid,), on_restart=True)
        cmd_queue = self._dispatch.get_command_queue()
        self._home_cmd = self._mcu.lookup_command("endstop_home oid=%c clock=%u sample_ticks=%u sample_count=%c rest_ticks=%u pin_value=%c trsync_oid=%c trigger_reason=%c", cq=cmd_queue)
        self._query_cmd = self._mcu.lookup_query_command("endstop_query_state oid=%c", "endstop_state oid=%c homing=%c next_clock=%u pin_value=%c", oid=self._oid, cq=cmd_queue)
    def home_start(self, print_time, sample_time, sample_count, rest_time, triggered=True):
        clock = self._mcu.print_time_to_clock(print_time)
        rest_ticks = self._mcu.print_time_to_clock(print_time+rest_time) - clock
        self._rest_ticks = rest_ticks
        tc = self._dispatch.start(print_time)
        self._home_cmd.send([self._oid, clock, self._mcu.seconds_to_clock(sample_time), sample_count, rest_ticks, triggered ^ self._invert, self._dispatch.get_oid(), MCU_trsync.REASON_ENDSTOP_HIT], reqclock=clock)
        return tc
    def home_wait(self, home_end_time):
        self._dispatch.wait_end(home_end_time)
        self._home_cmd.send([self._oid, 0, 0, 0, 0, 0, 0, 0])
        res = self._dispatch.stop()
        if res >= MCU_trsync.REASON_COMMS_TIMEOUT: raise self._mcu.get_printer().command_error("Communication timeout during homing")
        if res != MCU_trsync.REASON_ENDSTOP_HIT: return 0.
        if self._mcu.is_fileoutput(): return home_end_time
        params = self._query_cmd.send([self._oid])
        next_clock = self._mcu.clock32_to_clock64(params['next_clock'])
        return self._mcu.clock_to_print_time(next_clock - self._rest_ticks)
    def query_endstop(self, print_time):
        clock = self._mcu.print_time_to_clock(print_time)
        if self._mcu.is_fileoutput(): return 0
        params = self._query_cmd.send([self._oid], minclock=clock)
        return params['pin_value'] ^ self._invert

class MCU_digital_out:
    """Wrapper para pines de salida digital (relés, ventiladores on/off, LEDs)."""
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._oid = None
        self._mcu.register_config_callback(self._build_config)
        self._pin = pin_params['pin']
        self._invert = pin_params['invert']
        self._start_value = self._shutdown_value = self._invert
        self._max_duration = 2.
        self._last_clock = 0
        self._set_cmd = None
    def get_mcu(self): return self._mcu
    def setup_max_duration(self, max_duration): self._max_duration = max_duration
    def setup_start_value(self, start_value, shutdown_value):
        self._start_value = (not not start_value) ^ self._invert
        self._shutdown_value = (not not shutdown_value) ^ self._invert
    def _build_config(self):
        if self._max_duration and self._start_value != self._shutdown_value:
            raise pins.error("Pin with max duration must have start value equal to shutdown value")
        mdur_ticks = self._mcu.seconds_to_clock(self._max_duration)
        if mdur_ticks > MAX_SCHEDULE_TICKS: raise pins.error("Digital pin max duration too large")
        self._mcu.request_move_queue_slot()
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd("config_digital_out oid=%d pin=%s value=%d default_value=%d max_duration=%d" % (self._oid, self._pin, self._start_value, self._shutdown_value, mdur_ticks))
        self._mcu.add_config_cmd("update_digital_out oid=%d value=%d" % (self._oid, self._start_value), on_restart=True)
        cmd_queue = self._mcu.alloc_command_queue()
        self._set_cmd = self._mcu.lookup_command("queue_digital_out oid=%c clock=%u on_ticks=%u", cq=cmd_queue)
        self._mcu_print_time_to_clock = self._mcu.print_time_to_clock
        self._set_cmd_send = self._set_cmd.send
    def set_digital(self, print_time, value):
        clock = self._mcu_print_time_to_clock(print_time)
        self._set_cmd_send([self._oid, clock, (not not value) ^ self._invert], minclock=self._last_clock, reqclock=clock)
        self._last_clock = clock

class MCU_pwm:
    """Wrapper para pines PWM (motores de extrusión, hotend, ventiladores speed-controlled)."""
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._hardware_pwm = False
        self._cycle_time = 0.100
        self._max_duration = 2.
        self._oid = None
        self._mcu.register_config_callback(self._build_config)
        self._pin = pin_params['pin']
        self._invert = pin_params['invert']
        self._start_value = self._shutdown_value = float(self._invert)
        self._last_clock = 0
        self._last_value = .0
        self._pwm_max = 0.
        self._set_cmd = None
    def get_mcu(self): return self._mcu
    def setup_max_duration(self, max_duration): self._max_duration = max_duration
    def setup_cycle_time(self, cycle_time, hardware_pwm=False):
        self._cycle_time = cycle_time
        self._hardware_pwm = hardware_pwm
    def setup_start_value(self, start_value, shutdown_value):
        if self._invert: start_value, shutdown_value = 1. - start_value, 1. - shutdown_value
        self._start_value = max(0., min(1., start_value))
        self._shutdown_value = max(0., min(1., shutdown_value))
        self._last_value = self._start_value
    def _build_config(self):
        if self._max_duration and self._start_value != self._shutdown_value: raise pins.error("Pin with max duration must have start value equal to shutdown value")
        cmd_queue = self._mcu.alloc_command_queue()
        curtime = self._mcu.get_printer().get_reactor().monotonic()
        printtime = self._mcu.estimated_print_time(curtime)
        self._last_clock = self._mcu.print_time_to_clock(printtime + 0.200)
        cycle_ticks = self._mcu.seconds_to_clock(self._cycle_time)
        mdur_ticks = self._mcu.seconds_to_clock(self._max_duration)
        if mdur_ticks > MAX_SCHEDULE_TICKS: raise pins.error("PWM pin max duration too large")
        if self._hardware_pwm:
            self._pwm_max = self._mcu.get_constant_float("PWM_MAX")
            self._mcu.request_move_queue_slot()
            self._oid = self._mcu.create_oid()
            self._mcu.add_config_cmd("config_pwm_out oid=%d pin=%s cycle_ticks=%d value=%d default_value=%d max_duration=%d" % (self._oid, self._pin, cycle_ticks, self._start_value * self._pwm_max, self._shutdown_value * self._pwm_max, mdur_ticks))
            svalue = int(self._start_value * self._pwm_max + 0.5)
            self._mcu.add_config_cmd("queue_pwm_out oid=%d clock=%d value=%d" % (self._oid, self._last_clock, svalue), on_restart=True)
            self._set_cmd = self._mcu.lookup_command("queue_pwm_out oid=%c clock=%u value=%hu", cq=cmd_queue)
            self._mcu_print_time_to_clock = self._mcu.print_time_to_clock
            self._set_cmd_send = self._set_cmd.send
            return
        if self._shutdown_value not in [0., 1.]: raise pins.error("shutdown value must be 0.0 or 1.0 on soft pwm")
        if cycle_ticks > MAX_SCHEDULE_TICKS: raise pins.error("PWM pin cycle time too large")
        self._mcu.request_move_queue_slot()
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd("config_digital_out oid=%d pin=%s value=%d default_value=%d max_duration=%d" % (self._oid, self._pin, self._start_value >= 1.0, self._shutdown_value >= 0.5, mdur_ticks))
        self._mcu.add_config_cmd("set_digital_out_pwm_cycle oid=%d cycle_ticks=%d" % (self._oid, cycle_ticks))
        self._pwm_max = float(cycle_ticks)
        svalue = int(self._start_value * cycle_ticks + 0.5)
        self._mcu.add_config_cmd("queue_digital_out oid=%d clock=%d on_ticks=%d" % (self._oid, self._last_clock, svalue), is_init=True)
        self._set_cmd = self._mcu.lookup_command("queue_digital_out oid=%c clock=%u on_ticks=%u", cq=cmd_queue)
        self._mcu_print_time_to_clock = self._mcu.print_time_to_clock
        self._set_cmd_send = self._set_cmd.send
    def next_aligned_print_time(self, print_time, allow_early=0.):
        if self._hardware_pwm or self._last_value in [1., 0.]: return print_time
        req_ptime = print_time - min(allow_early, 0.5 * self._cycle_time)
        cycle_ticks = self._mcu.seconds_to_clock(self._cycle_time)
        req_clock = self._mcu_print_time_to_clock(req_ptime)
        pulses = (req_clock - self._last_clock + cycle_ticks - 1) // cycle_ticks
        next_clock = self._last_clock + pulses * cycle_ticks
        return self._mcu.clock_to_print_time(next_clock)
    def set_pwm(self, print_time, value):
        if self._invert: value = 1. - value
        v = int(max(0., min(1., value)) * self._pwm_max + 0.5)
        clock = self._mcu_print_time_to_clock(print_time)
        self._set_cmd_send([self._oid, clock, v], minclock=self._last_clock, reqclock=clock)
        self._last_clock = clock
        self._last_value = value

class MCU_adc:
    """Wrapper para entradas analógicas (termistores, sensores de voltaje/corriente)."""
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._pin = pin_params['pin']
        self._min_sample = self._max_sample = 0.
        self._sample_time = self._report_time = 0.
        self._sample_count = self._batch_num = self._range_check_count = 0
        self._report_clock = 0
        self._last_state = (0., 0.)
        self._oid = self._callback = None
        self._mcu.register_config_callback(self._build_config)
        self._inv_max_adc = 0.
        self._fmt_cache = {}
        self._unpack_cache = {}
    def get_mcu(self): return self._mcu
    def setup_adc_sample(self, report_time, sample_time=0., sample_count=1, batch_num=1, minval=0., maxval=1., range_check_count=0):
        self._report_time = report_time; self._sample_time = sample_time
        self._sample_count = sample_count; self._batch_num = max(1, min(48 // 2, batch_num))
        self._min_sample = minval; self._max_sample = maxval; self._range_check_count = range_check_count
    def setup_adc_callback(self, callback): self._callback = callback
    def get_last_value(self): return self._last_state
    def _build_config(self):
        if not self._sample_count: return
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd("config_analog_in oid=%d pin=%s" % (self._oid, self._pin))
        clock = self._mcu.get_query_slot(self._oid)
        sample_ticks = self._mcu.seconds_to_clock(self._sample_time)
        mcu_adc_max = self._mcu.get_constant_float("ADC_MAX")
        max_adc = self._sample_count * mcu_adc_max
        if max_adc >= (1 << 16): raise self._mcu.get_printer().config_error("ADC sample_count=%d too large for MCU" % (self._sample_count,))
        self._inv_max_adc = 1.0 / max_adc
        self._report_clock = self._mcu.seconds_to_clock(self._report_time)
        min_sample = max(0, min(0xffff, int(self._min_sample * max_adc)))
        max_sample = max(0, min(0xffff, int(math.ceil(self._max_sample * max_adc))))
        self._mcu_clock32_to_clock64 = self._mcu.clock32_to_clock64
        self._mcu_clock_to_print_time = self._mcu.clock_to_print_time
        oldcmd = "query_analog_in oid=%c clock=%u sample_ticks=%u sample_count=%c rest_ticks=%u min_value=%hu max_value=%hu range_check_count=%c"
        if (self._batch_num == 1 and self._mcu.try_lookup_command(oldcmd) is not None):
            self._mcu.add_config_cmd("query_analog_in oid=%d clock=%d sample_ticks=%d sample_count=%d rest_ticks=%d min_value=%d max_value=%d range_check_count=%d" % (self._oid, clock, sample_ticks, self._sample_count, self._report_clock, min_sample, max_sample, self._range_check_count), is_init=True)
            self._mcu.register_serial_response(self._old_handle_analog_in_state, "analog_in_state oid=%c next_clock=%u value=%hu", self._oid)
            return
        BYTES_PER_SAMPLE = 2
        bytes_per_report = self._batch_num * BYTES_PER_SAMPLE
        self._mcu.add_config_cmd("query_analog_in oid=%d clock=%d sample_ticks=%d sample_count=%d rest_ticks=%d bytes_per_report=%d min_value=%d max_value=%d range_check_count=%d" % (self._oid, clock, sample_ticks, self._sample_count, self._report_clock, bytes_per_report, min_sample, max_sample, self._range_check_count), is_init=True)
        self._mcu.register_serial_response(self._handle_analog_in_state, "analog_in_state oid=%c next_clock=%u values=%*s", self._oid)
    def _old_handle_analog_in_state(self, params):
        last_value = params['value'] * self._inv_max_adc
        next_clock = self._mcu_clock32_to_clock64(params['next_clock'])
        last_read_clock = next_clock - self._report_clock
        last_read_time = self._mcu_clock_to_print_time(last_read_clock)
        self._last_state = (last_read_time, last_value)
        if self._callback is not None: self._callback([(last_read_time, last_value)])
    def _handle_analog_in_state(self, params):
        data = params['values']
        num = len(data) // 2
        unpack = self._unpack_cache.get(num)
        if unpack is None:
            fmt = self._fmt_cache.get(num)
            if fmt is None: fmt = self._fmt_cache[num] = '<' + 'H' * num
            unpack = self._unpack_cache[num] = __import__('struct').Struct(fmt).unpack
        values = unpack(data)
        next_clock = self._mcu_clock32_to_clock64(params['next_clock'])
        ctpt = self._mcu_clock_to_print_time
        inv_max = self._inv_max_adc
        rclock = self._report_clock
        sample_count = len(values)
        last_state = (ctpt(next_clock - rclock), values[-1] * inv_max)
        self._last_state = last_state
        if self._callback is None: return
        sample_clock = next_clock - sample_count * rclock
        samples = [(ctpt(sample_clock), value * inv_max) for value in values]
        self._callback(samples)