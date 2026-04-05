import time, sys, os, socket, json, threading
import logging
from typing import Dict, Any, List
from unittest.mock import MagicMock

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

    def lookup_object(self, name):
        return self.objects.get(name)

    def get_state_message(self):
        return "Ready", "ready"

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

    def register_callback(self, callback):
        self.callbacks.append(callback)

    def monotonic(self):
        return time.monotonic()

def benchmark_webhooks():
    print("=== Klippy WebHooks Baseline Benchmarks ===")
    printer = MockPrinter()
    wh = None
    try:
        wh = webhooks.WebHooks(printer)
    except (AttributeError, socket.error):
        # Fallback if AF_UNIX is not available on Windows
        print("Warning: ServerSocket initialization failed (AF_UNIX not available), proceeding with mock.")
        wh = MagicMock()
        wh._handle_info_request = webhooks.WebHooks._handle_info_request.__get__(wh, webhooks.WebHooks)
        wh.printer = printer
    
    # Benchmark 1: Request decoding and processing overhead
    # Simulate 1000 requests
    count = 1000
    requests = [json.dumps({"id": i, "method": "info", "params": {}}).encode() + b'\x03' for i in range(count)]
    
    print(f"\nBenchmarking {count} 'info' requests processing...")
    start_time = time.time()
    
    # We need to mock ClientConnection to test _process_request directly
    class MockClient:
        def __init__(self):
            self.send_count = 0
            self.uid = 1
        def send(self, data):
            self.send_count += 1
        def is_closed(self): return False

    client = MockClient()
    for req in requests:
        # Mocking the flow of process_received -> _process_request
        # We strip the \x03 and decode
        raw_req = req[:-1]
        try:
            # We use the real WebRequest class
            wr = webhooks.WebRequest(client, raw_req)
            wh._handle_info_request(wr)
        except Exception as e:
            print(f"Error: {e}")
            
    duration = time.time() - start_time
    print(f"Time for {count} requests: {duration:.4f}s ({duration/count*1000000:.2f} us/req)")

    # Benchmark 2: json_dumps vs json_loads
    print("\nBenchmarking JSON operations...")
    test_data = {"id": 123, "method": "test", "params": {"a": 1, "b": "hello", "c": [1,2,3], "d": {"e": 5}}}
    encoded = webhooks.json_dumps(test_data)
    
    start_time = time.time()
    for _ in range(count):
        webhooks.json_dumps(test_data)
    duration_enc = time.time() - start_time
    print(f"json_dumps {count} times: {duration_enc:.4f}s ({duration_enc/count*1000000:.2f} us/op)")

    start_time = time.time()
    for _ in range(count):
        webhooks.json_loads(encoded)
    duration_dec = time.time() - start_time
    print(f"json_loads {count} times: {duration_dec:.4f}s ({duration_dec/count*1000000:.2f} us/op)")

    # Clean up
    if os.path.exists('/tmp/klipper_uds_test'):
        os.unlink('/tmp/klipper_uds_test')

if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    benchmark_webhooks()
