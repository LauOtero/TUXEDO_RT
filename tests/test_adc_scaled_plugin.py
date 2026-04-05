import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Añadir el path para importar los módulos de klippy
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))

from extras.adc_scaled import PrinterADCScaled, MCU_scaled_adc
from extra_manager import ExtraInterface

class TestADCScaledPlugin(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.get_name.return_value = "adc_scaled my_adc"
        self.config.getfloat.side_effect = lambda k, default=None, above=None: {
            'smooth_time': 2.0,
        }.get(k, default)
        self.config.get.side_effect = lambda k, default=None: {
            'vref_pin': 'PA0',
            'vssa_pin': 'PA1',
        }.get(k, default)

        self.printer = MagicMock()
        self.config.get_printer.return_value = self.printer
        self.printer.config_error = ValueError # Mock config_error to be ValueError

        self.mcu = MagicMock()
        self.mcu.setup_pin.return_value = MagicMock() # Mock for 'adc' pin setup
        self.mcu.setup_pin.return_value.get_mcu.return_value = self.mcu # Ensure mcu is returned

        self.pins = MagicMock()
        self.pins.register_chip.return_value = None
        self.pins.setup_pin.return_value = self.mcu.setup_pin.return_value # Mock for ppins.setup_pin

        self.query_adc = MagicMock()
        self.config.get_printer.return_value.load_object.return_value = self.query_adc
        
        self.printer.lookup_object.side_effect = lambda name, default=None: {
            'pins': self.pins,
            'query_adc': self.query_adc,
        }.get(name, default)

        # Patch MCU_scaled_adc to avoid deeper dependencies during PrinterADCScaled tests
        self.mcu_scaled_adc_patcher = patch('extras.adc_scaled.MCU_scaled_adc')
        self.mock_mcu_scaled_adc_class = self.mcu_scaled_adc_patcher.start()
        self.mock_mcu_scaled_adc = MagicMock()
        self.mock_mcu_scaled_adc_class.return_value = self.mock_mcu_scaled_adc

        self.config.error.side_effect = ValueError # Mock config.error to raise ValueError

    def tearDown(self):
        self.mcu_scaled_adc_patcher.stop()

    def test_initialization_and_on_load(self):
        self.assertTrue(issubclass(PrinterADCScaled, ExtraInterface))
        
        plugin = PrinterADCScaled(self.config)
        self.assertEqual(plugin.section_name, "adc_scaled my_adc")
        
        plugin.on_load()
        
        self.printer.lookup_object.assert_any_call('pins')
        self.pins.register_chip.assert_called_once_with("my_adc", plugin)
        
        # Verify _config_pin calls
        self.assertEqual(self.pins.setup_pin.call_count, 2)
        self.pins.setup_pin.assert_any_call('adc', 'PA0')
        self.pins.setup_pin.assert_any_call('adc', 'PA1')
        
        self.assertEqual(self.query_adc.register_adc.call_count, 2)
        self.query_adc.register_adc.assert_any_call("my_adc:vref", self.mcu.setup_pin.return_value)
        self.query_adc.register_adc.assert_any_call("my_adc:vssa", self.mcu.setup_pin.return_value)

    def test_on_start(self):
        plugin = PrinterADCScaled(self.config)
        plugin.on_load()
        plugin.on_start()
        # Just check if it runs without error and logs (difficult to mock logger directly)
        self.assertTrue(True)

    def test_on_shutdown(self):
        plugin = PrinterADCScaled(self.config)
        plugin.on_load()
        plugin.on_shutdown()
        # Just check if it runs without error and logs
        self.assertTrue(True)

    def test_on_reload(self):
        plugin = PrinterADCScaled(self.config)
        plugin.on_load()
        
        # Reset mocks to check calls during reload
        self.pins.register_chip.reset_mock()
        self.pins.setup_pin.reset_mock()
        self.query_adc.register_adc.reset_mock()
        
        plugin.on_reload()
        
        # on_reload calls on_load, so expect similar calls
        self.pins.register_chip.assert_called_once_with("my_adc", plugin)
        self.assertEqual(self.pins.setup_pin.call_count, 2)
        self.assertEqual(self.query_adc.register_adc.call_count, 2)

    def test_vref_vssa_callbacks(self):
        plugin = PrinterADCScaled(self.config)
        plugin.on_load()

        # Simulate vref callback
        samples_vref = [(1.0, 1000), (1.1, 1010)]
        plugin.vref_callback(samples_vref)
        # Expected smoothed value: 0 + (1010 - 0) * min(1.1 * 0.5, 1) = 1010 * 0.55 = 555.5
        self.assertAlmostEqual(plugin.last_vref[0], 1.1)
        self.assertAlmostEqual(plugin.last_vref[1], 555.5)

        # Simulate vssa callback
        samples_vssa = [(1.0, 100), (1.1, 110)]
        plugin.vssa_callback(samples_vssa)
        # Expected smoothed value: 0 + (110 - 0) * min(1.1 * 0.5, 1) = 110 * 0.55 = 60.5
        self.assertAlmostEqual(plugin.last_vssa[0], 1.1)
        self.assertAlmostEqual(plugin.last_vssa[1], 60.5)

    def test_mcu_scaled_adc_setup_pin(self):
        # Unpatch MCU_scaled_adc to test its interaction
        self.mcu_scaled_adc_patcher.stop() 

        plugin = PrinterADCScaled(self.config)
        plugin.on_load()

        mock_pin_params = {'pin': 'PB0'}
        mcu_scaled_adc_instance = plugin.setup_pin('adc', mock_pin_params)
        
        self.assertIsInstance(mcu_scaled_adc_instance, MCU_scaled_adc)
        self.mcu.setup_pin.assert_called_with('adc', mock_pin_params)
        
        # Test unsupported pin type
        with self.assertRaisesRegex(ValueError, "adc_scaled only supports adc pins"):
            plugin.setup_pin('digital_out', mock_pin_params)

if __name__ == '__main__':
    unittest.main()
