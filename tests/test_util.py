import os, sys, unittest, tempfile, shutil, socket
from unittest.mock import patch, MagicMock

# Ensure we can import klippy modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../klippy')))
import util

class TestUtil(unittest.TestCase):
    def setUp(self):
        util.clear_info_cache()

    def test_cpu_info(self):
        info = util.get_cpu_info()
        self.assertIsInstance(info, str)
        self.assertNotEqual(info, "")

    def test_device_info(self):
        info = util.get_device_info()
        self.assertIsInstance(info, str)

    def test_linux_version(self):
        info = util.get_linux_version()
        self.assertIsInstance(info, str)

    def test_ip_addresses(self):
        ips = util.get_ip_addresses()
        self.assertIsInstance(ips, list)
        for ip in ips:
            # Basic IP format check
            self.assertTrue("." in ip or ":" in ip)

    def test_uptime(self):
        uptime = util.get_uptime()
        self.assertIsInstance(uptime, float)
        self.assertGreaterEqual(uptime, 0)

    def test_git_version(self):
        info = util.get_git_version()
        self.assertIn("version", info)
        self.assertIn("branch", info)

    def test_fast_crc(self):
        crc_calc = util.FastCrc()
        data = b"123456789"
        # Standard CRC-16-CCITT for "123456789" is 0x29B1
        result = crc_calc.crc16(data)
        self.assertEqual(result, 0x29B1)
        
        # Test with initial value
        result2 = crc_calc.crc16(b"456789", crc_calc.crc16(b"123"))
        self.assertEqual(result2, 0x29B1)

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            content = "hello world"
            util.atomic_write(test_file, content)
            
            with open(test_file, 'r') as f:
                self.assertEqual(f.read(), content)
            
            # Test binary mode
            util.atomic_write(test_file, b"\x00\x01\x02", mode='wb')
            with open(test_file, 'rb') as f:
                self.assertEqual(f.read(), b"\x00\x01\x02")

    def test_high_res_timer(self):
        timer = util.HighResTimer()
        timer.start()
        time_start = timer.start_time
        elapsed = timer.elapsed()
        self.assertGreaterEqual(elapsed, 0)
        
        # Test sleep_until
        target = util.time.monotonic() + 0.01
        timer.sleep_until(target)
        self.assertGreaterEqual(util.time.monotonic(), target)

    def test_system_monitor(self):
        monitor = util.SystemMonitor()
        stats = monitor.get_stats()
        self.assertIn("cpu_usage", stats)
        self.assertIn("memory_usage_mb", stats)
        self.assertIsInstance(stats["cpu_usage"], float)

    def test_low_level_placeholders(self):
        util.set_nonblock(1)
        util.clear_hupcl(1)
        pty_fd = util.create_pty("test_pty")
        if os.name == 'nt':
            self.assertEqual(pty_fd, -1)
        else:
            self.assertIsInstance(pty_fd, int)
            self.assertGreaterEqual(pty_fd, 0)
            os.close(pty_fd)
        util.fix_sigint()

if __name__ == '__main__':
    unittest.main()
