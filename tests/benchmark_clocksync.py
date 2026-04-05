import time
import math
import sys
import os
from collections import deque
from unittest.mock import MagicMock

# Import the real production class
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
try:
    import clocksync
    ClockSyncProduction = clocksync.ClockSync
except ImportError:
    ClockSyncProduction = None

# Constants from clocksync.py
RTT_AGE = .000010 / (60. * 60.)
DECAY = 1. / 30.
K1 = 1. - DECAY
K2 = K1 * DECAY
TRANSMIT_EXTRA = .001

class ClockSyncOptimized:
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
        self.serial = MagicMock()
        self.get_clock_timer = None
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
        
        # Local cache
        min_half_rtt = self.min_half_rtt
        min_rtt_time = self.min_rtt_time
        prediction_variance = self.prediction_variance
        mcu_freq = self.mcu_freq
        est_freq = self.est_freq
        time_avg = self.time_avg
        clock_avg = self.clock_avg
        time_variance = self.time_variance
        clock_covariance = self.clock_covariance

        if half_rtt < min_half_rtt + (sent_time - min_rtt_time) * RTT_AGE:
            self.min_half_rtt = min_half_rtt = half_rtt
            self.min_rtt_time = min_rtt_time = sent_time
        
        exp_clock = (sent_time - time_avg) * est_freq + clock_avg
        diff = clock - exp_clock
        clock_diff2 = diff * diff
        
        if (clock_diff2 > 25. * prediction_variance
            and clock_diff2 > (.000500 * mcu_freq)**2):
            if clock > exp_clock and sent_time < self.last_prediction_time + 10.:
                return
            self.recovery_count += 1
            self.is_recovering = True
            prediction_variance = (.001 * mcu_freq)**2
        else:
            self.last_prediction_time = sent_time
            self.is_recovering = False
            prediction_variance = K1 * (prediction_variance + clock_diff2 * DECAY)
        
        diff_sent_time = sent_time - time_avg
        time_avg = time_avg + DECAY * diff_sent_time
        time_variance = K1 * (time_variance + (diff_sent_time * diff_sent_time) * DECAY)
        
        diff_clock = clock - clock_avg
        clock_avg = clock_avg + DECAY * diff_clock
        clock_covariance = K1 * (clock_covariance + (diff_sent_time * diff_clock) * DECAY)
        
        new_freq = clock_covariance / time_variance
        pred_stddev = math.sqrt(prediction_variance)
        self.serial.set_clock_est(new_freq, time_avg + TRANSMIT_EXTRA,
                                  int(clock_avg - 3. * pred_stddev), clock)
        
        self.est_time = time_avg + min_half_rtt
        self.est_clock = clock_avg
        self.est_freq = new_freq
        self.prediction_variance = prediction_variance
        self.time_avg = time_avg
        self.clock_avg = clock_avg
        self.time_variance = time_variance
        self.clock_covariance = clock_covariance
        
        history = self.history
        history.append((sent_time, half_rtt, diff, new_freq))
        if len(history) > self.max_history_size:
            del history[0]

    def get_clock(self, eventtime):
        return int(self.est_clock + (eventtime - self.est_time) * self.est_freq)

class ClockSyncOriginal:
    def __init__(self, reactor):
        self.reactor = reactor
        self.serial = MagicMock()
        self.get_clock_timer = None
        self.get_clock_cmd = self.cmd_queue = None
        self.queries_pending = 0
        self.mcu_freq = 1.
        self.last_clock = 0
        self.clock_est = (0., 0., 0.)
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
        if half_rtt < self.min_half_rtt + aged_rtt:
            self.min_half_rtt = half_rtt
            self.min_rtt_time = sent_time
        exp_clock = ((sent_time - self.time_avg) * self.clock_est[2]
                     + self.clock_avg)
        diff = clock - exp_clock
        clock_diff2 = diff**2
        if (clock_diff2 > 25. * self.prediction_variance
            and clock_diff2 > (.000500 * self.mcu_freq)**2):
            if clock > exp_clock and sent_time < self.last_prediction_time+10.:
                return
            self.prediction_variance = (.001 * self.mcu_freq)**2
            self.recovery_count += 1
            self.is_recovering = True
        else:
            self.last_prediction_time = sent_time
            self.is_recovering = False
            self.prediction_variance = (
                (1. - DECAY) * (self.prediction_variance + clock_diff2 * DECAY))
        diff_sent_time = sent_time - self.time_avg
        self.time_avg += DECAY * diff_sent_time
        self.time_variance = (1. - DECAY) * (
            self.time_variance + diff_sent_time**2 * DECAY)
        diff_clock = clock - self.clock_avg
        self.clock_avg += DECAY * diff_clock
        self.clock_covariance = (1. - DECAY) * (
            self.clock_covariance + diff_sent_time * diff_clock * DECAY)
        new_freq = self.clock_covariance / self.time_variance
        pred_stddev = math.sqrt(self.prediction_variance)
        self.serial.set_clock_est(new_freq, self.time_avg + TRANSMIT_EXTRA,
                                  int(self.clock_avg - 3. * pred_stddev), clock)
        self.clock_est = (self.time_avg + self.min_half_rtt,
                          self.clock_avg, new_freq)
        if len(self.history) >= self.max_history_size:
            self.history.pop(0)
        self.history.append((sent_time, half_rtt, diff, new_freq))

    def get_clock(self, eventtime):
        sample_time, clock, freq = self.clock_est
        return int(clock + (eventtime - sample_time) * freq)

def run_benchmark(iterations=1000000):
    reactor = MagicMock()
    
    # 1. Benchmark Original
    cs_orig = ClockSyncOriginal(reactor)
    cs_orig.mcu_freq = 10000.0
    cs_orig.clock_est = (100.0, 1000000.0, 10000.0)
    cs_orig.time_avg = 100.0
    cs_orig.clock_avg = 1000000.0
    cs_orig.prediction_variance = 1.0
    cs_orig.time_variance = 1.0
    cs_orig.clock_covariance = 10000.0
    
    # 2. Benchmark Optimized
    cs_opt = ClockSyncOptimized(reactor)
    cs_opt.mcu_freq = 10000.0
    cs_opt.est_time = 100.0
    cs_opt.est_clock = 1000000.0
    cs_opt.est_freq = 10000.0
    cs_opt.time_avg = 100.0
    cs_opt.clock_avg = 1000000.0
    cs_opt.prediction_variance = 1.0
    cs_opt.time_variance = 1.0
    cs_opt.clock_covariance = 10000.0
    
    # 3. Benchmark Production
    if ClockSyncProduction:
        cs_prod = ClockSyncProduction(reactor)
        cs_prod.serial = MagicMock()
        cs_prod.serial_set_clock_est = cs_prod.serial.set_clock_est
        cs_prod.mcu_freq = 10000.0
        cs_prod.est_time = 100.0
        cs_prod.est_clock = 1000000.0
        cs_prod.est_freq = 10000.0
        cs_prod.time_avg = 100.0
        cs_prod.clock_avg = 1000000.0
        cs_prod.prediction_variance = 1.0
        cs_prod.time_variance = 1.0
        cs_prod.clock_covariance = 10000.0
        cs_prod.recovery_variance = (.001 * cs_prod.mcu_freq) ** 2
        cs_prod.outlier_clock2 = (.000500 * cs_prod.mcu_freq) ** 2
    else:
        cs_prod = None
    
    print(f"Benchmarking with {iterations} iterations...")
    
    # --- get_clock benchmark ---
    start = time.time()
    for i in range(iterations):
        cs_orig.get_clock(100.0 + i * 0.001)
    orig_gc = time.time() - start
    
    start = time.time()
    for i in range(iterations):
        cs_opt.get_clock(100.0 + i * 0.001)
    opt_gc = time.time() - start
    
    print(f"get_clock: Original {orig_gc:.4f}s, Optimized {opt_gc:.4f}s (Improvement: {(orig_gc-opt_gc)/orig_gc*100:.1f}%)")
    
    if cs_prod:
        start = time.time()
        for i in range(iterations):
            cs_prod.get_clock(100.0 + i * 0.001)
        prod_gc = time.time() - start
        print(f"get_clock: Production {prod_gc:.4f}s (Improvement vs Orig: {(orig_gc-prod_gc)/orig_gc*100:.1f}%)")
    
    # --- _handle_clock benchmark ---
    hc_iters = iterations // 10
    params = {'clock': 1010000, '#sent_time': 101.0, '#receive_time': 101.01}
    
    start = time.time()
    for i in range(hc_iters):
        params['clock'] += 10000
        params['#sent_time'] += 1.0
        params['#receive_time'] += 1.0
        cs_orig._handle_clock(params)
    orig_hc = time.time() - start
    
    params = {'clock': 1010000, '#sent_time': 101.0, '#receive_time': 101.01}
    start = time.time()
    for i in range(hc_iters):
        params['clock'] += 10000
        params['#sent_time'] += 1.0
        params['#receive_time'] += 1.0
        cs_opt._handle_clock(params)
    opt_hc = time.time() - start
    
    print(f"_handle_clock: Original {orig_hc:.4f}s, Optimized {opt_hc:.4f}s (Improvement: {(orig_hc-opt_hc)/orig_hc*100:.1f}%)")

    if cs_prod:
        params = {'clock': 1010000, '#sent_time': 101.0, '#receive_time': 101.01}
        start = time.time()
        for i in range(hc_iters):
            params['clock'] += 10000
            params['#sent_time'] += 1.0
            params['#receive_time'] += 1.0
            cs_prod._handle_clock(params)
        prod_hc = time.time() - start
        print(f"_handle_clock: Production {prod_hc:.4f}s (Improvement vs Orig: {(orig_hc-prod_hc)/orig_hc*100:.1f}%, vs Opt: {(opt_hc-prod_hc)/opt_hc*100:.1f}%)")

if __name__ == '__main__':
    run_benchmark()
