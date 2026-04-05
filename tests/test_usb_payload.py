import unittest
import os
from unittest.mock import patch, MagicMock

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {
    'serial': MagicMock(),
    'termios': MagicMock(),
    'pty': MagicMock(),
    'fcntl': MagicMock(),
    'tty': MagicMock(),
}):
    from rtcore.transport.transport_uart import UARTTransport

class TestUSBPayloadOptimization(unittest.TestCase):
    def setUp(self):
        self.reactor_mock = MagicMock()
        self.ffi_lib_mock = MagicMock()
        self.serialqueue_mock = MagicMock()

    @patch('os.path.exists')
    @patch('os.path.realpath')
    @patch('os.path.basename')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_detect_usb_speed_high_speed(self, mock_open, mock_basename, mock_realpath, mock_exists):
        # Simular una conexión USB 2.0 High Speed (480 Mbps)
        mock_realpath.return_value = '/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1/1-1.2/1-1.2:1.0/tty/ttyACM0'
        mock_basename.return_value = 'ttyACM0'
        
        # Simular que los archivos sysfs existen
        def exists_side_effect(path):
            return True
        mock_exists.side_effect = exists_side_effect
        
        # Simular el contenido del archivo 'speed'
        mock_open.return_value.read.return_value = "480\n"

        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM0", 250000)
        speed = transport.detect_usb_speed()
        self.assertEqual(speed, "480")

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_setup_serialqueue_usb2(self, mock_detect_speed):
        mock_detect_speed.return_value = "480"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM0", 250000)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # USB 2.0 -> max_blocks = 8 (512 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 8)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_setup_serialqueue_usb1(self, mock_detect_speed):
        mock_detect_speed.return_value = "12"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyUSB0", 115200)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # USB 1.x -> max_blocks = 1 (64 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 1)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_setup_serialqueue_usb3(self, mock_detect_speed):
        mock_detect_speed.return_value = "5000"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyACM1", 250000)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # USB 3.x -> max_blocks = 16 (1024 bytes)
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 16)

    @patch.object(UARTTransport, 'detect_usb_speed')
    def test_setup_serialqueue_unknown(self, mock_detect_speed):
        mock_detect_speed.return_value = "unknown"
        
        transport = UARTTransport(self.reactor_mock, "/dev/ttyAMA0", 250000)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Fallback default -> max_blocks = 12
        self.ffi_lib_mock.serialqueue_set_usb_profile.assert_called_once_with(self.serialqueue_mock, 12)

if __name__ == '__main__':
    unittest.main()
