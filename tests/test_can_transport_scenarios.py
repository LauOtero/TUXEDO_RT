import unittest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {
    'can': MagicMock(),
    'socket': MagicMock(),
    'termios': MagicMock(),
    'pty': MagicMock(),
    'fcntl': MagicMock(),
    'tty': MagicMock(),
}):
    from rtcore.transport.transport_can import CANTransport

class TestCANTransportScenarios(unittest.TestCase):
    def setUp(self):
        self.reactor_mock = MagicMock()
        self.ffi_lib_mock = MagicMock()
        self.serialqueue_mock = MagicMock()

    def test_scenario_can_classic(self):
        """Escenario: CAN Classic - Modo 0"""
        transport = CANTransport(self.reactor_mock, "1234567890ab", 0x10, 
                                 iface="can0", canbus_mode="classic")
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_can_params con mode=0
        self.ffi_lib_mock.serialqueue_set_can_params.assert_called_once_with(
            self.serialqueue_mock, 0, 3, 1)

    def test_scenario_can_fd_no_brs(self):
        """Escenario: CAN FD (sin BRS) - Modo 1"""
        transport = CANTransport(self.reactor_mock, "1234567890ab", 0x10, 
                                 iface="can0", canbus_mode="fd_no_brs")
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_can_params con mode=1
        self.ffi_lib_mock.serialqueue_set_can_params.assert_called_once_with(
            self.serialqueue_mock, 1, 3, 1)

    def test_scenario_can_fd_brs(self):
        """Escenario: CAN FD (con BRS) - Modo 2"""
        transport = CANTransport(self.reactor_mock, "1234567890ab", 0x10, 
                                 iface="can0", canbus_mode="fd_brs")
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_can_params con mode=2
        self.ffi_lib_mock.serialqueue_set_can_params.assert_called_once_with(
            self.serialqueue_mock, 2, 3, 1)

    def test_scenario_can_xl(self):
        """Escenario: CAN XL - Modo 3"""
        transport = CANTransport(self.reactor_mock, "1234567890ab", 0x10, 
                                 iface="can0", canbus_mode="xl", xl_sdt=2)
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_can_params con mode=3 y xl_sdt=2
        self.ffi_lib_mock.serialqueue_set_can_params.assert_called_once_with(
            self.serialqueue_mock, 3, 3, 2)

    def test_scenario_invalid_mode_fallback(self):
        """Escenario: Modo inválido - Fallback a Classic (Modo 0)"""
        transport = CANTransport(self.reactor_mock, "1234567890ab", 0x10, 
                                 iface="can0", canbus_mode="invalid_mode")
        transport.setup_serialqueue(self.ffi_lib_mock, self.serialqueue_mock)
        
        # Debe llamar a serialqueue_set_can_params con mode=0 (fallback)
        self.ffi_lib_mock.serialqueue_set_can_params.assert_called_once_with(
            self.serialqueue_mock, 0, 3, 1)

if __name__ == '__main__':
    unittest.main()
