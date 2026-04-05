import unittest
import time
import sys
import os

# Mock the logging to avoid noise
import logging
logging.basicConfig(level=logging.CRITICAL)

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'klippy'))

import pins

class MockChip:
    def setup_pin(self, pin_type, params):
        return f"setup_{pin_type}_{params['pin']}"
    def validate_pin_type(self, pin, pin_type):
        pass

class TestPinsOptimized(unittest.TestCase):
    def setUp(self):
        self.printer_pins = pins.PrinterPins()
        self.mock_chip = MockChip()
        self.printer_pins.register_chip('mcu', self.mock_chip)

    def test_alias_resolution(self):
        resolver = self.printer_pins.get_pin_resolver('mcu')
        resolver.alias_pin('exp1', 'PA1')
        resolver.alias_pin('beeper', 'exp1')
        
        # Test transitive resolution (beeper -> exp1 -> PA1)
        cmd = "set_pin pin=beeper value=1"
        updated = resolver.update_command(cmd)
        self.assertEqual(updated, "set_pin pin=PA1 value=1")

    def test_command_update_performance(self):
        resolver = self.printer_pins.get_pin_resolver('mcu')
        resolver.alias_pin('fan', 'PB0')
        
        cmd = "set_pin pin=fan value=1"
        
        # Warm up cache
        resolver.update_command(cmd)
        
        start = time.time()
        iterations = 10000
        for _ in range(iterations):
            resolver.update_command(cmd)
        duration = time.time() - start
        
        print(f"\n[BENCHMARK] PinResolver.update_command: {iterations} iterations in {duration:.4f}s")
        # Should be extremely fast with caching (< 0.05s for 10k iterations)
        self.assertLess(duration, 0.1)

    def test_parse_pin_caching(self):
        start = time.time()
        iterations = 5000
        for _ in range(iterations):
            self.printer_pins.parse_pin("mcu:PA1", can_invert=True)
        duration = time.time() - start
        
        print(f"[BENCHMARK] PrinterPins.parse_pin (cached): {iterations} iterations in {duration:.4f}s")
        self.assertLess(duration, 0.05)

    def test_new_functionalities(self):
        # Setup some pins
        self.printer_pins.setup_pin('digital_out', 'mcu:PA1')
        self.printer_pins.setup_pin('pwm', 'mcu:PB0')
        
        # Test hardware map
        h_map = self.printer_pins.generate_hardware_map()
        self.assertIn('mcu', h_map)
        self.assertEqual(len(h_map['mcu']['pins']), 2)
        
        # Test real-time monitoring
        self.printer_pins.update_pin_status('mcu:PA1', 'active')
        states = self.printer_pins.pin_states
        self.assertEqual(states['mcu:PA1']['status'], 'active')

if __name__ == '__main__':
    unittest.main()
