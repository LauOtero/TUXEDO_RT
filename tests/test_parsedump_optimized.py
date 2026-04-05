import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os
import io

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {'msgproto': MagicMock()}):
    import msgproto
    import parsedump

class TestParsedumpOptimized(unittest.TestCase):
    def setUp(self):
        self.mp_mock = MagicMock()
        msgproto.MessageParser.return_value = self.mp_mock

    @patch('builtins.open', new_callable=mock_open, read_data=b"dict_data")
    @patch('os.read')
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_basic_parsing(self, mock_stdout, mock_os_read, mock_file):
        # Setup mocks
        mock_os_read.side_effect = [b"packet1", b""]
        self.mp_mock.check_packet.side_effect = [7, 0] # l=7 for "packet1", then 0
        self.mp_mock.dump.return_value = ["header", "msg1"]
        
        # Run main with args
        with patch('sys.argv', ['parsedump.py', 'dict', 'data']):
            parsedump.main()
            
        self.assertIn("msg1\n", mock_stdout.getvalue())

    @patch('builtins.open', new_callable=mock_open, read_data=b"dict_data")
    @patch('os.read')
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_filter_functionality(self, mock_stdout, mock_os_read, mock_file):
        # Setup mocks
        mock_os_read.side_effect = [b"packet1", b""]
        self.mp_mock.check_packet.side_effect = [7, 0]
        self.mp_mock.dump.return_value = ["header", "stepper_msg", "heater_msg"]
        
        # Run with filter for 'stepper'
        with patch('sys.argv', ['parsedump.py', 'dict', 'data', '--filter', 'stepper']):
            parsedump.main()
            
        output = mock_stdout.getvalue()
        self.assertIn("stepper_msg", output)
        self.assertNotIn("heater_msg", output)

    @patch('builtins.open', new_callable=mock_open, read_data=b"dict_data")
    @patch('os.read')
    @patch('sys.stderr', new_callable=io.StringIO)
    def test_stats_functionality(self, mock_stderr, mock_os_read, mock_file):
        mock_os_read.side_effect = [b"packet1", b""]
        self.mp_mock.check_packet.side_effect = [7, 0]
        self.mp_mock.dump.return_value = ["header", "msg1"]
        
        with patch('sys.argv', ['parsedump.py', 'dict', 'data', '--stats']):
            parsedump.main()
            
        stderr_output = mock_stderr.getvalue()
        self.assertIn("Statistics", stderr_output)
        self.assertIn("Messages parsed: 1", stderr_output)

if __name__ == '__main__':
    unittest.main()
