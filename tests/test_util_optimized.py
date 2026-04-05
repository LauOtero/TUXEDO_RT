import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {
    'pty': MagicMock(),
    'fcntl': MagicMock(),
    'termios': MagicMock(),
}):
    import util

class TestUtilOptimized(unittest.TestCase):
    def setUp(self):
        util.clear_info_cache()

    @patch('util._try_read_file')
    def test_cpu_info_caching(self, mock_read):
        mock_read.return_value = "processor : 0\nmodel name : Test CPU\n"
        
        # First call should call _try_read_file
        info1 = util.get_cpu_info()
        self.assertEqual(info1, "1 core Test CPU")
        self.assertEqual(mock_read.call_count, 1)
        
        # Second call should use cache
        info2 = util.get_cpu_info()
        self.assertEqual(info1, info2)
        self.assertEqual(mock_read.call_count, 1)

    @patch('util.socket.gethostname', return_value='mock-host')
    @patch('util.socket.getaddrinfo')
    def test_ip_addresses(self, mock_getaddrinfo, mock_gethostname):
        mock_getaddrinfo.return_value = [
            (None, None, None, None, ("192.168.1.10", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("192.168.1.10", 0)),
        ]
        ips = util.get_ip_addresses()
        self.assertEqual(ips, ["192.168.1.10", "10.0.0.5"])
        
    @patch('util._try_read_file')
    def test_uptime(self, mock_read):
        mock_read.return_value = "1234.56 7890.12\n"
        uptime = util.get_uptime()
        self.assertEqual(uptime, 1234.56)

    def test_cache_clearing(self):
        with patch('util._try_read_file') as mock_read:
            mock_read.return_value = "processor : 0\nmodel name : Test CPU\n"
            util.get_cpu_info()
            util.clear_info_cache()
            util.get_cpu_info()
            self.assertEqual(mock_read.call_count, 2)

if __name__ == '__main__':
    unittest.main()
