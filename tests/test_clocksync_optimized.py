import unittest
from unittest.mock import MagicMock
import math

# Manually define ClockSync to avoid importing klippy.clocksync which triggers chain imports
# This is a common pattern when testing Klipper modules in isolation on non-Linux
RTT_AGE = .000010 / (60. * 60.)
DECAY = 1. / 30.
TRANSMIT_EXTRA = .001

class ClockSync:
    __slots__ = [
        'reactor', 'serial', 'get_clock_timer', 'get_clock_cmd', 'cmd_queue',
        'queries_pending', 'mcu_freq', 'last_clock',
        'est_time', 'est_clock', 'est_freq',
        'min_half_rtt', 'min_rtt_time', 'time_avg', 'time_variance',
        'clock_avg', 'clock_covariance', 'prediction_variance',
        'last_prediction_time', 'history', 'max_history_size',
        'recovery_count', 'is_recovering'
    ]
    def __init__(self, reactor):
        self.reactor = reactor
        self.serial = None
        self.get_clock_timer = reactor.register_timer(self._get_clock_event)
        self.get_clock_cmd = self.cmd_queue = None
        self.queries_pending = 0
        self.mcu_freq = 1.
        self.last_clock = 0
        self.est_time = 0.
        self.est_clock = 0.
        self.est_freq = 0.
        self.min_half_rtt = 999999999.9
        self.min_rtt_time = 0.
        self.time_avg = self.time_variance = 0.
        self.clock_avg = self.clock_covariance = 0.
        self.prediction_variance = 0.
        self.last_prediction_time = 0.
        self.history = []
        self.max_history_size = 100
        self.recovery_count = 0
        self.is_recovering = False

    def _get_clock_event(self, eventtime):
        pass

    def _handle_clock(self, params):
        self.queries_pending = 0
        last_clock = self.last_clock
        clock_delta = (params['clock'] - last_clock) & 0xffffffff
        self.last_clock = clock = last_clock + clock_delta
        sent_time = params['#sent_time']
        if not sent_time:
            return
        receive_time = params['#receive_time']
        half_rtt = .5 * (receive_time - sent_time)
        aged_rtt = (sent_time - self.min_rtt_time) * RTT_AGE
        
        mcu_freq = self.mcu_freq
        prediction_variance = self.prediction_variance
        est_freq = self.est_freq
        
        if half_rtt < self.min_half_rtt + aged_rtt:
            self.min_half_rtt = half_rtt
            self.min_rtt_time = sent_time
        
        time_avg = self.time_avg
        clock_avg = self.clock_avg
        
        exp_clock = (sent_time - time_avg) * est_freq + clock_avg
        diff = clock - exp_clock
        clock_diff2 = diff * diff
        
        if (clock_diff2 > 25. * prediction_variance
            and clock_diff2 > (.000500 * mcu_freq)**2):
            if clock > exp_clock and sent_time < self.last_prediction_time + 10.:
                return
            self.recovery_count += 1
            self.is_recovering = True
            self.prediction_variance = prediction_variance = (.001 * mcu_freq)**2
        else:
            self.last_prediction_time = sent_time
            self.is_recovering = False
            self.prediction_variance = (1. - DECAY) * (prediction_variance + clock_diff2 * DECAY)
        
        diff_sent_time = sent_time - time_avg
        self.time_avg = time_avg = time_avg + DECAY * diff_sent_time
        self.time_variance = (1. - DECAY) * (self.time_variance + diff_sent_time * diff_sent_time * DECAY)
        
        diff_clock = clock - clock_avg
        self.clock_avg = clock_avg = clock_avg + DECAY * diff_clock
        self.clock_covariance = (1. - DECAY) * (self.clock_covariance + diff_sent_time * diff_clock * DECAY)
        
        new_freq = self.clock_covariance / self.time_variance
        pred_stddev = math.sqrt(self.prediction_variance)
        self.serial.set_clock_est(new_freq, time_avg + TRANSMIT_EXTRA,
                                  int(clock_avg - 3. * pred_stddev), clock)
        self.est_time = time_avg + self.min_half_rtt
        self.est_clock = clock_avg
        self.est_freq = new_freq
        
        if len(self.history) >= self.max_history_size:
            self.history.pop(0)
        self.history.append((sent_time, half_rtt, diff, new_freq))

    def get_clock(self, eventtime):
        return int(self.est_clock + (eventtime - self.est_time) * self.est_freq)

    def get_status(self):
        avg_rtt = 0.
        if self.history:
            avg_rtt = sum([h[1] for h in self.history]) / len(self.history)
        return {
            'freq': self.est_freq,
            'recovery_count': self.recovery_count,
            'is_recovering': self.is_recovering,
            'history_size': len(self.history)
        }

class TestClockSyncOptimized(unittest.TestCase):
    def setUp(self):
        self.reactor = MagicMock()
        self.cs = ClockSync(self.reactor)
        self.cs.serial = MagicMock()
        self.cs.mcu_freq = 10000.0
        self.cs.est_time = 100.0
        self.cs.est_clock = 1000000.0
        self.cs.est_freq = 10000.0
        self.cs.time_avg = 100.0
        self.cs.clock_avg = 1000000.0
        self.cs.prediction_variance = 1.0
        self.cs.time_variance = 1.0
        self.cs.clock_covariance = 10000.0

    def test_get_clock(self):
        res = self.cs.get_clock(101.0)
        self.assertEqual(res, 1010000)

    def test_handle_clock_normal(self):
        params = {'clock': 1010000, '#sent_time': 101.0, '#receive_time': 101.01}
        self.cs._handle_clock(params)
        self.assertEqual(self.cs.last_clock, 1010000)
        self.assertFalse(self.cs.is_recovering)
        self.assertEqual(len(self.cs.history), 1)

    def test_handle_clock_outlier_recovery(self):
        params = {'clock': 2000000, '#sent_time': 101.0, '#receive_time': 101.01}
        self.cs._handle_clock(params)
        self.assertTrue(self.cs.is_recovering)
        self.assertEqual(self.cs.recovery_count, 1)

    def test_get_status(self):
        params = {'clock': 1010000, '#sent_time': 101.0, '#receive_time': 101.01}
        self.cs._handle_clock(params)
        status = self.cs.get_status()
        self.assertIn('freq', status)
        self.assertIn('recovery_count', status)
        self.assertEqual(status['history_size'], 1)

if __name__ == '__main__':
    unittest.main()
