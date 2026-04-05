
import sys
import os
import time
import timeit
import zlib
import struct
from unittest.mock import MagicMock

# AÃ±adir el path de klippy
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

# Mocking modules that mcu.py imports
mock_serialhdl = MagicMock()
mock_msgproto = MagicMock()
mock_pins = MagicMock()
mock_chelper = MagicMock()
mock_clocksync = MagicMock()

sys.modules['serialhdl'] = mock_serialhdl
sys.modules['msgproto'] = mock_msgproto
sys.modules['pins'] = mock_pins
sys.modules['chelper'] = mock_chelper
sys.modules['clocksync'] = mock_clocksync

# Mock rtcore.transport.utils
mock_transport_utils = MagicMock()
sys.modules['rtcore.transport'] = MagicMock()
sys.modules['rtcore.transport.utils'] = mock_transport_utils

import mcu

def benchmark_config_building():
    print("Benchmarking MCUConfigHelper building...")
    
    printer = MagicMock()
    config = MagicMock()
    config.get_printer.return_value = printer
    config.get_name.return_value = "mcu"
    
    conn_helper = MagicMock()
    mcu_obj = MagicMock()
    conn_helper.get_mcu.return_value = mcu_obj
    mcu_obj.get_name.return_value = "mcu"
    
    serial = MagicMock()
    conn_helper.get_serial.return_value = serial
    
    clocksync_obj = MagicMock()
    conn_helper.get_clocksync.return_value = clocksync_obj
    
    reactor = MagicMock()
    printer.get_reactor.return_value = reactor
    
    # Simulate adding 100 config commands
    def run_config_build():
        # Create a fresh helper for each call to avoid "already configured" error
        ch = mcu.MCUConfigHelper(config, conn_helper)
        for i in range(100):
            ch.add_config_cmd(f"config_command_{i} param={i}")
            ch.create_oid()
        
        # Mock pin resolver for _finalize_config
        pins_obj = MagicMock()
        printer.lookup_object.return_value = pins_obj
        pin_resolver = MagicMock()
        pins_obj.get_pin_resolver.return_value = pin_resolver
        pin_resolver.update_command.side_effect = lambda x: x
        
        ch._finalize_config()

    timer = timeit.Timer(run_config_build)
    count = 100
    duration = timer.timeit(number=count)
    print(f"  Config building (100 cmds): {duration/count*1000:.3f} ms per call")

def benchmark_command_lookup():
    print("Benchmarking Command Lookup...")
    
    printer = MagicMock()
    config = MagicMock()
    config.get_printer.return_value = printer
    config.get_name.return_value = "mcu"
    
    clocksync_obj = MagicMock()
    mcu_obj = mcu.MCU(config, clocksync_obj)
    
    def run_lookup():
        for i in range(100):
            mcu_obj.lookup_command(f"command_{i} param=%d")

    timer = timeit.Timer(run_lookup)
    count = 1000
    duration = timer.timeit(number=count)
    print(f"  CommandWrapper lookup (100 calls x {count} times): {duration/count*1000:.3f} ms per call")

def benchmark_pwm_updates():
    print("Benchmarking PWM updates...")
    
    mcu_obj = MagicMock()
    pin_params = {'pin': 'PB0', 'invert': False}
    pwm = mcu.MCU_pwm(mcu_obj, pin_params)
    
    # Mock for _build_config
    mcu_obj.alloc_command_queue.return_value = MagicMock()
    reactor = MagicMock()
    mcu_obj.get_printer().get_reactor.return_value = reactor
    reactor.monotonic.return_value = 0.
    mcu_obj.estimated_print_time.return_value = 0.
    mcu_obj.print_time_to_clock.return_value = 1000
    mcu_obj.seconds_to_clock.return_value = 100
    mcu_obj.get_constant_float.return_value = 255.
    
    set_cmd = MagicMock()
    mcu_obj.lookup_command.return_value = set_cmd
    
    pwm._build_config()
    
    def run_pwm_set():
        for i in range(100):
            pwm.set_pwm(0.1 + i*0.01, 0.5)

    timer = timeit.Timer(run_pwm_set)
    count = 100
    duration = timer.timeit(number=count)
    print(f"  PWM set_pwm (100 calls): {duration/count*1000:.3f} ms per call")

def benchmark_adc_processing():
    print("Benchmarking ADC processing...")
    
    mcu_obj = MagicMock()
    pin_params = {'pin': 'PA0'}
    adc = mcu.MCU_adc(mcu_obj, pin_params)
    
    # Mock for _build_config
    mcu_obj.create_oid.return_value = 1
    mcu_obj.get_query_slot.return_value = 1000
    mcu_obj.seconds_to_clock.return_value = 100
    mcu_obj.get_constant_float.return_value = 4095.
    mcu_obj.try_lookup_command.return_value = MagicMock()
    
    adc.setup_adc_sample(0.1, sample_count=8, batch_num=8)
    adc._build_config()
    
    # Mock parameters for _handle_analog_in_state
    # 8 samples (H = 2 bytes each)
    values_raw = struct.pack('<HHHHHHHH', 100, 200, 300, 400, 500, 600, 700, 800)
    params = {'values': values_raw, 'next_clock': 2000}
    
    mcu_obj.clock32_to_clock64.return_value = 2000
    mcu_obj.clock_to_print_time.side_effect = lambda x: x / 1000000.
    
    def run_adc_handle():
        for i in range(100):
            adc._handle_analog_in_state(params)

    timer = timeit.Timer(run_adc_handle)
    count = 100
    duration = timer.timeit(number=count)
    print(f"  ADC handle (100 reports of 8 samples): {duration/count*1000:.3f} ms per call")

def benchmark_stats_helper():
    print("Benchmarking MCUStatsHelper...")
    
    mcu_obj = MagicMock()
    conn_helper = MagicMock()
    mcu_obj.get_name.return_value = "mcu"
    
    serial = MagicMock()
    conn_helper.get_serial.return_value = serial
    serial.stats.return_value = "bytes_write=100 bytes_read=200"
    
    clocksync = MagicMock()
    conn_helper.get_clocksync.return_value = clocksync
    clocksync.stats.return_value = "freq=16000000.0 offset=0.0"
    
    # Setup for latency stats
    conn_helper.get_latency_stats.return_value = (0.001, 0.005, 0.001)
    
    stats_helper = mcu.MCUStatsHelper(mcu_obj, conn_helper)
    
    # Simulate some initial stats data
    stats_helper._mcu_tick_awake = 0.001
    stats_helper._mcu_tick_avg = 0.0001
    stats_helper._mcu_tick_stddev = 0.00001
    
    def run_stats():
        for i in range(100):
            stats_helper.stats(0.)

    timer = timeit.Timer(run_stats)
    count = 100
    duration = timer.timeit(number=count)
    print(f"  Stats helper (100 calls): {duration/count*1000:.3f} ms per call")

if __name__ == "__main__":
    benchmark_config_building()
    benchmark_command_lookup()
    benchmark_pwm_updates()
    benchmark_adc_processing()
    benchmark_stats_helper()
