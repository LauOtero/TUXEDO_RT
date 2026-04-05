import unittest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {
    'serial': MagicMock(),
    'termios': MagicMock(),
    'pty': MagicMock(),
    'fcntl': MagicMock(),
    'tty': MagicMock(),
}):
    from rtcore.transport.transport_uart import UARTTransport

class TestMCUTransportScenarios(unittest.TestCase):
    def setUp(self):
        self.reactor_mock = MagicMock()
        self.ffi_lib_mock = MagicMock()
        self.serialqueue_mock = MagicMock()

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_scenario_usb_2_0_high_speed(self, mock_detect_speed):
        """Escenario: Conexión USB 2.0 (480 Mbps) - Optimización activa"""
        mock_detect_speed.return_value = "480"
        
        # Inicializar transporte con optimización habilitada (default)
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM0", 250000, usb_optimize=True)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_usb_profile con 8 bloques (512 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 8)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_scenario_usb_3_x_superspeed(self, mock_detect_speed):
        """Escenario: Conexión USB 3.0/3.1 (5000+ Mbps) - Optimización activa"""
        mock_detect_speed.return_value = "5000"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM1", 250000, usb_optimize=True)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_usb_profile con 16 bloques (1024 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 16)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_scenario_usb_1_1_full_speed(self, mock_detect_speed):
        """Escenario: Conexión USB 1.1 (12 Mbps) - Optimización activa"""
        mock_detect_speed.return_value = "12"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM2", 250000, usb_optimize=True)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_usb_profile con 1 bloque (64 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 1)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_scenario_native_uart(self, mock_detect_speed):
        """Escenario: Conexión UART pura (GPIO) - Optimización activa (Fallback)"""
        mock_detect_speed.return_value = "unknown"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyAMA0", 250000, usb_optimize=True)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_usb_profile con 12 bloques (Fallback seguro)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 12)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_scenario_usb_optimize_disabled(self, mock_detect_speed):
        """Escenario: USB detectado pero optimización desactivada por el usuario"""
        # Aunque detecte 480 (USB 2.0)
        mock_detect_speed.return_value = "480"
        
        # Usuario desactiva explícitamente la optimización
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM0", 250000, usb_optimize=False)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe ignorar la velocidad detectada y usar 12 bloques (Original Klipper)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 12)
        # detect_usb_speed NO debería haber sido llamado si usb_optimize es False
        mock_detect_speed.assert_not_called()

if __name__ == '__main__':
    unittest.main()
