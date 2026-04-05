# Micro-controller clock synchronization
#
# Copyright (C) 2016-2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math
 
RTT_AGE = .000010 / (60. * 60.)
DECAY = 1. / 30.
K1 = 1. - DECAY
K2 = K1 * DECAY
TRANSMIT_EXTRA = .001

class ClockSync:
    __slots__ = [
        'reactor', 'serial', 'get_clock_timer', 'get_clock_cmd', 'cmd_queue',
        'queries_pending', 'mcu_freq', 'last_clock',
        'est_time', 'est_clock', 'est_freq',
        'min_half_rtt', 'min_rtt_time', 'time_avg', 'time_variance',
        'clock_avg', 'clock_covariance', 'prediction_variance',
        'last_prediction_time', 'history', 'max_history_size',
        'history_pos', 'history_count', 'history_rtt_sum',
        'recovery_count', 'is_recovering', 'rt_core_get_time',
        'serial_set_clock_est',
        'recovery_variance', 'outlier_clock2'
    ]
    def __init__(self, reactor):
        self.reactor = reactor
        self.serial = None
        self.get_clock_timer = reactor.register_timer(self._get_clock_event)
        self.get_clock_cmd = self.cmd_queue = None
        self.queries_pending = 0
        self.mcu_freq = 1.
        self.last_clock = 0
        # self.clock_est is now (est_time, est_clock, est_freq)
        self.est_time = 0.
        self.est_clock = 0.
        self.est_freq = 0.
        # Minimum round-trip-time tracking
        self.min_half_rtt = 999999999.9
        self.min_rtt_time = 0.
        # Linear regression of mcu clock and system sent_time
        self.time_avg = self.time_variance = 0.
        self.clock_avg = self.clock_covariance = 0.
        self.prediction_variance = 0.
        self.last_prediction_time = 0.
        self.history = []
        self.max_history_size = 100
        self.history_pos = 0
        self.history_count = 0
        self.history_rtt_sum = 0.
        self.recovery_count = 0
        self.is_recovering = False
        self.rt_core_get_time = None
        self.serial_set_clock_est = None
        self.recovery_variance = (.001 * self.mcu_freq)**2
        self.outlier_clock2 = (.000500 * self.mcu_freq)**2
    def connect(self, serial):
        self.serial = serial
        self.serial_set_clock_est = serial.set_clock_est
        self.mcu_freq = serial.msgparser.get_constant_float('CLOCK_FREQ')
        self.recovery_variance = (.001 * self.mcu_freq)**2
        self.outlier_clock2 = (.000500 * self.mcu_freq)**2
        rt_core = getattr(self.reactor, 'rt_core', None)
        self.rt_core_get_time = rt_core.get_high_res_time if rt_core else None
        # Load initial clock and frequency
        params = serial.send_with_response('get_uptime', 'uptime')
        self.last_clock = (params['high'] << 32) | params['clock']
        self.clock_avg = self.last_clock
        self.time_avg = params['#sent_time']
        self.est_time, self.est_clock, self.est_freq = (
            self.time_avg, self.clock_avg, self.mcu_freq)
        self.prediction_variance = (.001 * self.mcu_freq)**2
        # Enable periodic get_clock timer
        for i in range(8):
            self.reactor.pause(self.reactor.monotonic() + 0.050)
            self.last_prediction_time = -9999.
            params = serial.send_with_response('get_clock', 'clock')
            self._handle_clock(params)
        self.get_clock_cmd = serial.get_msgparser().create_command('get_clock')
        self.cmd_queue = serial.alloc_command_queue()
        serial.register_response(self._handle_clock, 'clock')
        self.reactor.update_timer(self.get_clock_timer, self.reactor.NOW)
    def connect_file(self, serial, pace=False):
        self.serial = serial
        self.serial_set_clock_est = serial.set_clock_est
        self.mcu_freq = serial.msgparser.get_constant_float('CLOCK_FREQ')
        self.recovery_variance = (.001 * self.mcu_freq)**2
        self.outlier_clock2 = (.000500 * self.mcu_freq)**2
        self.est_time, self.est_clock, self.est_freq = (
            0., 0., self.mcu_freq)
        freq = 1000000000000.
        if pace:
            freq = self.mcu_freq
        serial.set_clock_est(freq, self.reactor.monotonic(), 0, 0)
    # MCU clock querying (_handle_clock is invoked from background thread)
    def _get_clock_event(self, eventtime):
        self.serial.raw_send(self.get_clock_cmd, 0, 0, self.cmd_queue)
        self.queries_pending += 1
        # Use an unusual time for the next event so clock messages
        # don't resonate with other periodic events.
        return eventtime + .9839
    def _handle_clock(self, params):
        self.queries_pending = 0
        receive_time = params['#receive_time']
        rt_core_get_time = self.rt_core_get_time
        if rt_core_get_time is not None:
            receive_time = rt_core_get_time()
        last_clock = self.last_clock
        clock_delta = (params['clock'] - last_clock) & 0xffffffff
        self.last_clock = clock = last_clock + clock_delta
        sent_time = params['#sent_time']
        if not sent_time:
            return
        half_rtt = .5 * (receive_time - sent_time)
        history = self.history
        max_history_size = self.max_history_size
        history_count = self.history_count
        min_half_rtt = self.min_half_rtt
        min_rtt_time = self.min_rtt_time
        if half_rtt < min_half_rtt + (sent_time - min_rtt_time) * RTT_AGE:
            self.min_half_rtt = min_half_rtt = half_rtt
            self.min_rtt_time = min_rtt_time = sent_time
        time_avg = self.time_avg
        clock_avg = self.clock_avg
        est_freq = self.est_freq
        exp_clock = (sent_time - time_avg) * est_freq + clock_avg
        diff = clock - exp_clock
        clock_diff2 = diff * diff
        prediction_variance = self.prediction_variance
        if (clock_diff2 > 25. * prediction_variance
            and clock_diff2 > self.outlier_clock2):
            if clock > exp_clock and sent_time < self.last_prediction_time + 10.:
                return
            self.recovery_count += 1
            self.is_recovering = True
            self.prediction_variance = prediction_variance = self.recovery_variance
        else:
            self.last_prediction_time = sent_time
            self.is_recovering = False
            self.prediction_variance = prediction_variance = (
                K1 * (prediction_variance + clock_diff2 * DECAY))
        diff_sent_time = sent_time - time_avg
        self.time_avg = time_avg = time_avg + DECAY * diff_sent_time
        time_variance = self.time_variance
        self.time_variance = time_variance = (
            K1 * (time_variance + (diff_sent_time * diff_sent_time) * DECAY))
        diff_clock = clock - clock_avg
        self.clock_avg = clock_avg = clock_avg + DECAY * diff_clock
        clock_covariance = self.clock_covariance
        self.clock_covariance = clock_covariance = (
            K1 * (clock_covariance + (diff_sent_time * diff_clock) * DECAY))
        new_freq = clock_covariance / time_variance
        pred_stddev = math.sqrt(prediction_variance)
        self.serial_set_clock_est(new_freq, time_avg + TRANSMIT_EXTRA,
                                  int(clock_avg - 3. * pred_stddev), clock)
        self.est_time = time_avg + min_half_rtt
        self.est_clock = clock_avg
        self.est_freq = new_freq
        if history_count < max_history_size:
            history.append(half_rtt)
            history_count += 1
            self.history_count = history_count
            self.history_rtt_sum += half_rtt
        else:
            history_pos = self.history_pos
            self.history_rtt_sum += half_rtt - history[history_pos]
            history[history_pos] = half_rtt
            history_pos += 1
            if history_pos >= max_history_size:
                history_pos = 0
            self.history_pos = history_pos
    # clock frequency conversions
    def print_time_to_clock(self, print_time):
        return int(print_time * self.mcu_freq)
    def clock_to_print_time(self, clock):
        return clock / self.mcu_freq
    # system time conversions
    def get_clock(self, eventtime):
        return int(self.est_clock + (eventtime - self.est_time) * self.est_freq)
    
    def get_status(self):
        history_count = self.history_count
        avg_rtt = 0.
        if history_count:
            avg_rtt = self.history_rtt_sum / history_count
        return {
            'mcu_freq': self.mcu_freq,
            'last_clock': self.last_clock,
            'freq': self.est_freq,
            'min_half_rtt': self.min_half_rtt,
            'avg_half_rtt': avg_rtt,
            'recovery_count': self.recovery_count,
            'is_recovering': self.is_recovering,
            'prediction_variance': self.prediction_variance,
            'history_size': history_count
        }

    def estimate_clock_systime(self, reqclock):
        return float(reqclock - self.est_clock) / self.est_freq + self.est_time
    def estimated_print_time(self, eventtime):
        return self.clock_to_print_time(self.get_clock(eventtime))
    # misc commands
    def clock32_to_clock64(self, clock32):
        last_clock = self.last_clock
        clock_diff = (clock32 - last_clock) & 0xffffffff
        clock_diff -= (clock_diff & 0x80000000) << 1
        return last_clock + clock_diff
    def is_active(self):
        return self.queries_pending <= 4
    def dump_debug(self):
        return ("clocksync state: mcu_freq=%d last_clock=%d"
                " clock_est=(%.3f %d %.3f) min_half_rtt=%.6f min_rtt_time=%.3f"
                " time_avg=%.3f(%.3f) clock_avg=%.3f(%.3f)"
                " pred_variance=%.3f" % (
                    self.mcu_freq, self.last_clock,
                    self.est_time, self.est_clock, self.est_freq,
                    self.min_half_rtt, self.min_rtt_time,
                    self.time_avg, self.time_variance,
                    self.clock_avg, self.clock_covariance,
                    self.prediction_variance))
    def stats(self, eventtime):
        return "freq=%d" % (self.est_freq,)
    def calibrate_clock(self, print_time, eventtime):
        return (0., self.mcu_freq)

# Clock syncing code for secondary MCUs (whose clocks are sync'ed to a
# primary MCU)
class SecondarySync(ClockSync):
    def __init__(self, reactor, main_sync):
        ClockSync.__init__(self, reactor)
        self.main_sync = main_sync
        self.clock_adj = (0., 1.)
        self.last_sync_time = 0.
    def connect(self, serial):
        ClockSync.connect(self, serial)
        self.clock_adj = (0., self.mcu_freq)
        curtime = self.reactor.monotonic()
        main_print_time = self.main_sync.estimated_print_time(curtime)
        local_print_time = self.estimated_print_time(curtime)
        self.clock_adj = (main_print_time - local_print_time, self.mcu_freq)
        self.calibrate_clock(0., curtime)
    def connect_file(self, serial, pace=False):
        ClockSync.connect_file(self, serial, pace)
        self.clock_adj = (0., self.mcu_freq)
    # clock frequency conversions
    def print_time_to_clock(self, print_time):
        adjusted_offset, adjusted_freq = self.clock_adj
        return int((print_time - adjusted_offset) * adjusted_freq)
    def clock_to_print_time(self, clock):
        adjusted_offset, adjusted_freq = self.clock_adj
        return clock / adjusted_freq + adjusted_offset
    # misc commands
    def dump_debug(self):
        adjusted_offset, adjusted_freq = self.clock_adj
        return "%s clock_adj=(%.3f %.3f)" % (
            ClockSync.dump_debug(self), adjusted_offset, adjusted_freq)
    def stats(self, eventtime):
        adjusted_offset, adjusted_freq = self.clock_adj
        return "%s adj=%d" % (ClockSync.stats(self, eventtime), adjusted_freq)
    def calibrate_clock(self, print_time, eventtime):
        # Calculate: est_print_time = main_sync.estimatated_print_time()
        main_sync = self.main_sync
        ser_time, ser_clock, ser_freq = (
            main_sync.est_time, main_sync.est_clock, main_sync.est_freq)
        main_mcu_freq = main_sync.mcu_freq
        est_main_clock = (eventtime - ser_time) * ser_freq + ser_clock
        est_print_time = est_main_clock / main_mcu_freq
        # Determine sync1_print_time and sync2_print_time
        sync1_print_time = max(print_time, est_print_time)
        sync2_print_time = max(sync1_print_time + 4., self.last_sync_time,
                               print_time + 2.5 * (print_time - est_print_time))
        # Calc sync2_sys_time (inverse of main_sync.estimatated_print_time)
        sync2_main_clock = sync2_print_time * main_mcu_freq
        sync2_sys_time = ser_time + (sync2_main_clock - ser_clock) / ser_freq
        # Adjust freq so estimated print_time will match at sync2_print_time
        sync1_clock = self.print_time_to_clock(sync1_print_time)
        sync2_clock = self.get_clock(sync2_sys_time)
        adjusted_freq = ((sync2_clock - sync1_clock)
                         / (sync2_print_time - sync1_print_time))
        adjusted_offset = sync1_print_time - sync1_clock / adjusted_freq
        # Apply new values
        self.clock_adj = (adjusted_offset, adjusted_freq)
        self.last_sync_time = sync2_print_time
        return self.clock_adj
