import unittest
import time
import os
import sys
import threading
from typing import Dict, Any

# Add current directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'klippy'))

import reactor
import serialhdl
import msgproto

class MockMCU:
    def __init__(self, msgparser):
        self.msgparser = msgparser
        self.seq = 0
        
    def generate_packet(self, msgname, **params):
        mf = self.msgparser.messages_by_name[msgname]
        cmd = mf.encode_by_name(**params)
        packet = self.msgparser.encode_msgblock(self.seq, cmd)
        self.seq = (self.seq + 1) % 16
        # Flatten packet
        res = []
        for item in packet:
            if isinstance(item, list): res.extend(item)
            else: res.append(item)
        return bytes(res)

class MockFFI:
    class new:
        def __init__(self, type_str, val=None):
            self.len = 0
            self.msg = bytearray(64)
            self.notify_id = 0
            self.sent_time = 0.0
            self.receive_time = 0.0
            self._type = type_str
            
        def __len__(self):
            if 'char' in self._type: return 4096
            return 64
            
    def string(self, buf):
        return b"mock_stats"
        
    def gc(self, obj, free_func):
        return obj

class MockFFILib:
    def serialqueue_alloc(self, fd, type, client_id, name): return 1
    def serialqueue_free(self, sq): pass
    def serialqueue_alloc_commandqueue(self): return 1
    def serialqueue_free_commandqueue(self, cq): pass
    def serialqueue_exit(self, sq): pass
    def set_thread_name(self, name): pass
    def serialqueue_get_stats(self, sq, buf, len): pass
    def get_monotonic(self): return time.time()
    
    def __init__(self):
        self.queue = []
        self.cond = threading.Condition()
        self.exiting = False
        
    def serialqueue_pull(self, sq, response):
        with self.cond:
            while not self.queue and not self.exiting:
                self.cond.wait(0.1)
            if self.exiting:
                response.len = -1
                return
            data = self.queue.pop(0)
            response.len = len(data)
            response.msg[0:len(data)] = data
            response.notify_id = 0
            response.receive_time = time.time()
            response.sent_time = response.receive_time - 0.001

class TestSerialFunctional(unittest.TestCase):
    def setUp(self):
        self.r = reactor.Reactor()
        self.old_get_ffi = serialhdl.chelper.get_ffi
        self.mock_ffi_lib = MockFFILib()
        serialhdl.chelper.get_ffi = lambda: (MockFFI(), self.mock_ffi_lib)
        
        self.sh = serialhdl.SerialReader(self.r, "mcu")
        # Initialize with some messages
        self.mp = self.sh.msgparser
        identify_data = {
            "commands": {"test_cmd arg1=%u arg2=%c": 10},
            "responses": {"test_resp arg1=%u arg2=%u": 11},
            "enumerations": {},
            "version": "1.0",
            "build_versions": "1.0"
        }
        import json
        self.mp.process_identify(json.dumps(identify_data).encode(), decompress=False)
        
        self.mcu = MockMCU(self.mp)
        # Start bg threads
        self.sh.serialqueue = 1
        self.sh.background_thread = threading.Thread(target=self.sh._bg_thread)
        self.sh.background_thread.start()
        self.sh.dispatch_thread = threading.Thread(target=self.sh._dispatch_thread)
        self.sh.dispatch_thread.start()
        
        self.last_params = None

    def tearDown(self):
        self.mock_ffi_lib.exiting = True
        with self.mock_ffi_lib.cond:
            self.mock_ffi_lib.cond.notify_all()
        self.sh.disconnect()
        self.r.finalize()
        serialhdl.chelper.get_ffi = self.old_get_ffi

    def test_handler_cow(self):
        def cb1(params):
            self.last_params = ("cb1", params['arg1'])
        def cb2(params):
            self.last_params = ("cb2", params['arg1'])
            
        # Register first handler
        self.sh.register_response(cb1, "test_resp")
        
        packet = self.mcu.generate_packet("test_resp", arg1=100, arg2=200)
        with self.mock_ffi_lib.cond:
            self.mock_ffi_lib.queue.append(packet)
            self.mock_ffi_lib.cond.notify_all()
            
        # Wait for cb1
        timeout = 2.0
        while self.last_params is None and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1
        self.assertEqual(self.last_params, ("cb1", 100))
        
        # Swap handler while running (COW test)
        self.last_params = None
        self.sh.register_response(cb2, "test_resp")
        
        packet = self.mcu.generate_packet("test_resp", arg1=200, arg2=300)
        with self.mock_ffi_lib.cond:
            self.mock_ffi_lib.queue.append(packet)
            self.mock_ffi_lib.cond.notify_all()
            
        # Wait for cb2
        timeout = 2.0
        while self.last_params is None and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1
        self.assertEqual(self.last_params, ("cb2", 200))

    def test_stats_reporting(self):
        # Process some messages
        packet = self.mcu.generate_packet("test_resp", arg1=1, arg2=2)
        with self.mock_ffi_lib.cond:
            for _ in range(5):
                self.mock_ffi_lib.queue.append(packet)
            self.mock_ffi_lib.cond.notify_all()
            
        # Wait for processing
        time.sleep(0.5)
        
        stats_str = self.sh.stats(time.time())
        print(f"\nStats output: {stats_str}")
        self.assertIn("py_msgs=5", stats_str)
        self.assertIn("py_avg_parse", stats_str)
        self.assertIn("py_avg_hdl", stats_str)

if __name__ == '__main__':
    unittest.main()
