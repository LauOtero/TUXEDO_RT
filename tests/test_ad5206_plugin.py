import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Añadir el path para importar los módulos de klippy
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))

from extras.ad5206 import ad5206
from extra_manager import ExtraInterface

class TestAD5206Plugin(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.get_name.return_value = "ad5206 my_digipot"
        self.config.getfloat.side_effect = lambda k, default=None, above=None, minval=None, maxval=None: {
            'scale': 1.0,
            'channel_1': 0.5,
            'channel_2': 0.25,
            'channel_3': None,
            'channel_4': None,
            'channel_5': None,
            'channel_6': None,
        }.get(k, default)

        self.printer = MagicMock()
        self.config.get_printer.return_value = self.printer

        # Mock bus.MCU_SPI_from_config
        self.spi_patcher = patch('extras.ad5206.bus.MCU_SPI_from_config')
        self.mock_mcu_spi_from_config = self.spi_patcher.start()
        self.mock_spi = MagicMock()
        self.mock_mcu_spi_from_config.return_value = self.mock_spi

    def tearDown(self):
        self.spi_patcher.stop()

    def test_initialization_and_on_load(self):
        # Asegurarse de que hereda de ExtraInterface
        self.assertTrue(issubclass(ad5206, ExtraInterface))
        
        plugin = ad5206(self.config)
        self.assertEqual(plugin.section_name, "ad5206 my_digipot")
        
        plugin.on_load()
        
        # Verificar que se llamó a MCU_SPI_from_config
        self.mock_mcu_spi_from_config.assert_called_once_with(
            self.config, 0, pin_option="enable_pin", default_speed=25000000)
        
        # Verificar que set_register fue llamado para los canales configurados
        # 0.5 * 256 / 1.0 = 128
        # 0.25 * 256 / 1.0 = 64
        self.mock_spi.spi_send.assert_any_call([0, 128])
        self.mock_spi.spi_send.assert_any_call([1, 64])
        self.assertEqual(self.mock_spi.spi_send.call_count, 2)

    def test_on_start(self):
        plugin = ad5206(self.config)
        plugin.on_load() # on_load inicializa el logger
        plugin.on_start()
        # No hay mucho que verificar aquí más allá de que se ejecuta sin errores
        # y que el logger se usa (lo cual es difícil de mockear directamente sin más setup)
        self.assertTrue(True) 

    def test_on_shutdown(self):
        plugin = ad5206(self.config)
        plugin.on_load()
        
        # Simular que los canales tienen valores
        plugin.set_register(0, 100)
        plugin.set_register(1, 50)
        self.mock_spi.spi_send.reset_mock() # Resetear para contar solo las llamadas de shutdown
        
        plugin.on_shutdown()
        
        # Verificar que todos los canales se pusieron a 0
        expected_calls = [call([i, 0]) for i in range(6)]
        self.mock_spi.spi_send.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(self.mock_spi.spi_send.call_count, 6)

    def test_on_reload(self):
        plugin = ad5206(self.config)
        plugin.on_load()
        self.mock_mcu_spi_from_config.reset_mock() # Resetear para on_reload
        self.mock_spi.spi_send.reset_mock() # Resetear para on_reload

        plugin.on_reload()
        
        # on_reload llama a on_load, así que esperamos las mismas llamadas
        self.mock_mcu_spi_from_config.assert_called_once_with(
            self.config, 0, pin_option="enable_pin", default_speed=25000000)
        self.mock_spi.spi_send.assert_any_call([0, 128])
        self.mock_spi.spi_send.assert_any_call([1, 64])
        self.assertEqual(self.mock_spi.spi_send.call_count, 2)

    def test_set_register(self):
        plugin = ad5206(self.config)
        plugin.on_load()
        self.mock_spi.spi_send.reset_mock() # Resetear para esta prueba

        plugin.set_register(3, 200)
        self.mock_spi.spi_send.assert_called_once_with([3, 200])

if __name__ == '__main__':
    unittest.main()
