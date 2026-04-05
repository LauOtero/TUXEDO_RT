import unittest
import sys
import os
import json
import collections

# Ensure we can import klippy modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../klippy')))
import webhooks

class MockPrinter:
    def __init__(self):
        self.objects = {}
        self.start_args = {'apiserver': '/tmp/klipper_uds_test'}
        self.event_handlers = {}
        self.command_error = Exception

    def get_reactor(self):
        return MockReactor()

    def get_start_args(self):
        return self.start_args

    def register_event_handler(self, event, handler):
        self.event_handlers[event] = handler

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)
    
    def lookup_objects(self):
        return self.objects.items()

    def get_state_message(self):
        return "Ready", "ready"

    def invoke_shutdown(self, msg):
        pass

    def set_rollover_info(self, name, msg, log=True):
        pass

class MockReactor:
    def __init__(self):
        self.fds = {}
        self.callbacks = []

    def register_fd(self, fd, callback, write_callback=None):
        self.fds[fd] = (callback, write_callback)
        return fd

    def unregister_fd(self, handle):
        if handle in self.fds:
            del self.fds[handle]

    def register_timer(self, callback, waketime=100.0, priority=2):
        self.callbacks.append((callback, waketime, priority))
        class MockTimer:
            def __init__(self): self.waketime = waketime
        return MockTimer()

    def register_callback(self, callback):
        self.callbacks.append((callback, 100.0, 2))

    def NEVER(self):
        return 9999999999.0
    
    NEVER = 9999999999.0

    def monotonic(self):
        return 100.0
    
    def set_fd_wake(self, handle, read, write):
        pass
    
    def assert_no_pause(self):
        class NoPause:
            def __enter__(self): pass
            def __exit__(self, *args): pass
        return NoPause()

class TestWebHooks(unittest.TestCase):
    def setUp(self):
        self.printer = MockPrinter()
        self.wh = webhooks.WebHooks(self.printer)

    def test_webrequest_basic(self):
        class MockClient:
            def __init__(self): self.uid = 1
        client = MockClient()
        req_data = json.dumps({"id": 123, "method": "test", "params": {"a": 1}}).encode()
        wr = webhooks.WebRequest(client, req_data)
        self.assertEqual(wr.id, 123)
        self.assertEqual(wr.method, "test")
        self.assertEqual(wr.get_int("a"), 1)

    def test_compression_logic(self):
        class MockServer:
            def __init__(self):
                self.printer = MockPrinter()
                self.webhooks = None
                self.reactor = MockReactor()
                self.uid = 1
            def pop_client(self, uid): pass
        
        class MockSocket:
            def __init__(self):
                self.sent_data = b""
            def fileno(self): return 1
            def close(self): pass
            def send(self, data):
                # Simulate partial send or just return 0 to keep data in buffer
                return 0

        server = MockServer()
        conn = webhooks.ClientConnection(server, MockSocket())
        
        # Enable compression
        conn.set_client_info({"compression": "zlib"})
        self.assertTrue(conn.use_compression)
        
        # Test sending large data triggers compression (simulated)
        # Use a very large string to ensure it exceeds the 512 byte threshold
        large_data = {"data": "x" * 2000}
        conn.send(large_data)
        print(f"DEBUG: use_compression={conn.use_compression}, buffer_len={len(conn.send_buffer)}, startswith_02={conn.send_buffer.startswith(b'\x02')}")
        self.assertTrue(conn.send_buffer.startswith(b"\x02"))

    def test_status_caching(self):
        status1 = self.wh.get_status(100.0)
        status2 = self.wh.get_status(100.05) # Within 100ms
        self.assertIs(status1, status2)
        
        status3 = self.wh.get_status(100.2) # Outside 100ms
        self.assertEqual(status1, status3) # Values same, but might be different dict if it was actually updated

if __name__ == '__main__':
    unittest.main()
