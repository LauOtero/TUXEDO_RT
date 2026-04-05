import unittest
from unittest.mock import MagicMock
import sys
import os

# Mock printer and other objects
class MockPrinter:
    def __init__(self):
        self.objects = {}
    def add_object(self, name, obj):
        self.objects[name] = obj
    def lookup_object(self, name):
        return self.objects.get(name)

class MockChip:
    def __init__(self, name):
        self.name = name
        self.setup_calls = []
        self.validated_pins = []
    def setup_pin(self, pin_type, pin_params):
        self.setup_calls.append((pin_type, pin_params))
        return "pin_obj"
    def validate_pin_type(self, pin, pin_type):
        self.validated_pins.append((pin, pin_type))

# Import pins.py
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
import pins

class TestPinsOptimized(unittest.TestCase):
    def setUp(self):
        self.printer = MockPrinter()
        self.pins_service = pins.PrinterPins()
        self.chip = MockChip("mcu")
        self.pins_service.register_chip("mcu", self.chip)

    def test_parse_pin_caching(self):
        # Primer parseo
        params1 = self.pins_service.parse_pin("PA1")
        self.assertEqual(params1['pin'], "PA1")
        
        # Segundo parseo (debe venir del cache)
        params2 = self.pins_service.parse_pin("PA1")
        self.assertEqual(params1, params2)
        self.assertIsNot(params1, params2) # Debe ser una copia

    def test_setup_pin_validation_hook(self):
        # Configurar un pin y verificar que se llama a validate_pin_type
        self.pins_service.setup_pin("pwm", "PA2")
        self.assertEqual(self.chip.validated_pins, [("PA2", "pwm")])

    def test_debug_info(self):
        self.pins_service.setup_pin("digital_out", "PA3")
        debug_info = self.pins_service.get_debug_info()
        self.assertIn("mcu", debug_info['chips'])
        self.assertIn("mcu:PA3", debug_info['active_pins'])
        self.assertEqual(debug_info['active_pins']['mcu:PA3']['share_type'], "digital_out")

    def test_invalid_pins(self):
        with self.assertRaises(pins.error):
            self.pins_service.parse_pin("PA 1") # Espacio inválido
        with self.assertRaises(pins.error):
            self.pins_service.parse_pin("PA!1") # Caracter inválido

    def test_pin_sharing(self):
        # Registrar pin
        self.pins_service.lookup_pin("PA4", share_type="stepper")
        
        # Intentar registrar con distinto tipo (debe fallar)
        with self.assertRaises(pins.error):
            self.pins_service.lookup_pin("PA4", share_type="endstop")
            
        # Intentar registrar con distinto pullup (debe fallar)
        with self.assertRaises(pins.error):
            self.pins_service.lookup_pin("^PA4", can_pullup=True, share_type="stepper")

    def test_reset_pin_sharing(self):
        params = self.pins_service.lookup_pin("PA5", share_type="temp")
        self.assertIn("mcu:PA5", self.pins_service.active_pins)
        
        self.pins_service.reset_pin_sharing(params)
        self.assertNotIn("mcu:PA5", self.pins_service.active_pins)
        self.assertEqual(self.pins_service._parse_cache, {})

if __name__ == '__main__':
    unittest.main()
