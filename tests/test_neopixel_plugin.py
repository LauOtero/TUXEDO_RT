import unittest
from unittest.mock import MagicMock, call, patch
import sys
import os

# Añadir el path para importar los módulos de klippy
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))

from extras.neopixel import PrinterNeoPixel
from extra_manager import ExtraInterface

class TestNeoPixelLifecycle(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.get_name.return_value = "neopixel my_led"
        self.config.get.side_effect = lambda k, default=None: {
            'pin': 'ar10',
        }.get(k, default)
        self.config.getint.side_effect = lambda k, default=None, minval=None: {
            'chain_count': 3
        }.get(k, default)
        self.config.getlist.side_effect = lambda k, default=None: {
            'color_order': ['GRB']
        }.get(k, default)
        
        self.printer = MagicMock()
        self.config.get_printer.return_value = self.printer
        
        self.pins = MagicMock()
        self.mcu = MagicMock()
        self.mcu.create_oid.return_value = 1
        self.pins.lookup_pin.return_value = {'chip': self.mcu, 'pin': 'ar10'}
        
        self.printer.lookup_object.side_effect = lambda name, default=None: {
            'pins': self.pins,
        }.get(name, default)

        # Mock start args
        self.printer.get_start_args.return_value = {}
        
        # Patch LEDHelper to avoid complex display dependencies
        self.led_patcher = patch('extras.neopixel.led.LEDHelper')
        self.mock_led_helper_class = self.led_patcher.start()
        self.mock_led_helper = MagicMock()
        self.mock_led_helper.get_status.return_value = {'color_data': [[0.0, 0.0, 0.0, 0.0]] * 3}
        self.mock_led_helper_class.return_value = self.mock_led_helper

    def tearDown(self):
        self.led_patcher.stop()

    def test_initialization_and_inheritance(self):
        # Asegurarse de que hereda de ExtraInterface
        self.assertTrue(issubclass(PrinterNeoPixel, ExtraInterface))
        
        plugin = PrinterNeoPixel(self.config)
        self.assertEqual(plugin.section_name, "neopixel my_led")
        
        # Validar on_load
        plugin.on_load()
        self.assertEqual(plugin.pin, 'ar10')
        self.assertEqual(len(plugin.color_map), 9) # 3 LEDs * 3 colores (GRB)
        self.assertEqual(len(plugin.color_data), 9)
        
        # Verificar que se registró el evento de Klipper fallback
        self.printer.register_event_handler.assert_any_call("klippy:connect", plugin._handle_connect)

    def test_lifecycle_shutdown(self):
        plugin = PrinterNeoPixel(self.config)
        plugin.on_load()
        
        # Simular algunos colores encendidos
        plugin.color_data = bytearray([255] * 9)
        plugin.neopixel_update_cmd = MagicMock()
        plugin.neopixel_send_cmd = MagicMock()
        plugin.neopixel_send_cmd.send.return_value = {'success': True}
        
        # Simular shutdown
        plugin.on_shutdown()
        
        # Verificar que todos los colores se pusieron a 0
        self.assertEqual(plugin.color_data, bytearray([0] * 9))
        
        # Verificar que send_data transmitió el apagado
        plugin.neopixel_update_cmd.send.assert_called()

    def test_send_data_optimization(self):
        plugin = PrinterNeoPixel(self.config)
        plugin.on_load()
        
        # Sincronizar old_data y new_data para simular estado estable
        plugin.old_color_data[:] = plugin.color_data
        
        plugin.neopixel_update_cmd = MagicMock()
        plugin.neopixel_send_cmd = MagicMock()
        plugin.neopixel_send_cmd.send.return_value = {'success': True}
        
        # Cambiar solo el primer LED (3 bytes)
        plugin.color_data[0] = 100
        plugin.color_data[1] = 150
        plugin.color_data[2] = 200
        
        plugin.send_data()
        
        # update_cmd.send debería ser llamado para transmitir solo el chunk modificado
        plugin.neopixel_update_cmd.send.assert_called()
        args = plugin.neopixel_update_cmd.send.call_args[0][0]
        self.assertEqual(args[0], 1) # OID
        self.assertEqual(args[1], 0) # Posición inicial
        self.assertEqual(list(args[2]), [100, 150, 200]) # Datos enviados

if __name__ == '__main__':
    unittest.main()
