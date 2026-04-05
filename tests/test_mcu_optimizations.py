import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Añadir el path de klippy
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

with patch.dict(sys.modules, {
    'serialhdl': MagicMock(),
    'msgproto': MagicMock(),
    'pins': MagicMock(),
    'chelper': MagicMock(),
    'clocksync': MagicMock(),
    'transport_utils': MagicMock(),
    'serial': MagicMock(),
    'termios': MagicMock(),
    'fcntl': MagicMock(),
    'pty': MagicMock(),
}):
    import mcu

class TestMCUOptimizations(unittest.TestCase):
    def setUp(self):
        self.mock_printer = MagicMock()
        self.mock_printer.command_error = RuntimeError
        self.mock_config = MagicMock()
        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_clocksync = MagicMock()
        self.mock_mcu_obj = MagicMock()
        self.mock_mcu_obj.get_printer.return_value = self.mock_printer
        self.mock_mcu_obj.get_name.return_value = "mcu"
        self.mock_mcu_obj.is_fileoutput.return_value = False
        self.mock_serial = MagicMock()
        self.mock_msgparser = MagicMock()
        self.mock_cmd = MagicMock()
        self.mock_cmd.encode.return_value = b"cmd"
        self.mock_msgparser.lookup_command.return_value = self.mock_cmd
        self.mock_msgparser.lookup_msgid.return_value = 1
        self.mock_serial.get_msgparser.return_value = self.mock_msgparser
        self.mock_serial.get_default_command_queue.return_value = MagicMock()
        self.mock_serial.transport_factory.create_transport.return_value = MagicMock()
        
        with patch.object(mcu.serialhdl, 'SerialReader', return_value=self.mock_serial):
            self.conn_helper = mcu.MCUConnectHelper(self.mock_config, self.mock_mcu_obj, self.mock_clocksync)

    def test_command_caching(self):
        msgformat = "test_cmd param=%d"
        # First call creates the command
        cmd1 = self.conn_helper.lookup_command(msgformat)
        # Second call should return the same instance
        cmd2 = self.conn_helper.lookup_command(msgformat)
        self.assertIs(cmd1, cmd2)
        self.assertEqual(len(self.conn_helper._command_cache), 1)

    def test_query_command_caching(self):
        msgformat = "test_query param=%d"
        respformat = "test_resp value=%d"
        # First call creates the query
        q1 = self.conn_helper.lookup_query_command(msgformat, respformat)
        # Second call should return the same instance
        q2 = self.conn_helper.lookup_query_command(msgformat, respformat)
        self.assertIs(q1, q2)
        self.assertEqual(len(self.conn_helper._query_command_cache), 1)

    def test_latency_tracking(self):
        self.conn_helper.note_latency(0.010) # 10ms
        self.conn_helper.note_latency(0.020) # 20ms
        avg, max_lat, cur_avg = self.conn_helper.get_latency_stats()
        self.assertEqual(avg, 0.015)
        self.assertEqual(max_lat, 0.020)
        self.assertEqual(cur_avg, 0.015)

    def test_command_batcher(self):
        mcu_inst = MagicMock()
        batcher = mcu.CommandBatcher(mcu_inst)
        cmd_wrapper = MagicMock()
        batcher.add(cmd_wrapper, (1, 2), 100, 200)
        batcher.send()
        cmd_wrapper.send.assert_called_with((1, 2), 100, 200)

    def test_pin_cache_optimization(self):
        mock_pins = MagicMock()
        self.mock_printer.lookup_object.return_value = mock_pins
        mock_resolver = MagicMock()
        mock_pins.get_pin_resolver.return_value = mock_resolver
        mock_resolver.update_command.side_effect = lambda x: x + "_resolved"
        
        cfg_helper = mcu.MCUConfigHelper(self.mock_config, self.conn_helper)
        # Add identical commands with pins
        cfg_helper.add_config_cmd("config_pwm pin=PA1")
        cfg_helper.add_config_cmd("config_pwm pin=PA1")
        
        cfg_helper._finalize_config()
        
        # update_command should only be called ONCE due to cache
        self.assertEqual(mock_resolver.update_command.call_count, 1)
        self.assertEqual(cfg_helper._config_cmds[1], "config_pwm pin=PA1_resolved")
        self.assertEqual(cfg_helper._config_cmds[2], "config_pwm pin=PA1_resolved")

if __name__ == '__main__':
    unittest.main()
