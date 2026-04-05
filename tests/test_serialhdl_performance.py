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

class TestSerialPerformance(unittest.TestCase):
    def setUp(self):
        self.r = reactor.Reactor()
        # Mock chelper
        import chelper
        self.old_get_ffi = chelper.get_ffi
        self.mock_ffi_lib = MockFFILib()
        chelper.get_ffi = lambda: (MockFFI(), self.mock_ffi_lib)
        
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

    def tearDown(self):
        self.mock_ffi_lib.exiting = True
        with self.mock_ffi_lib.cond:
            self.mock_ffi_lib.cond.notify_all()
        self.sh.disconnect()
        self.r.finalize()
        import chelper
        chelper.get_ffi = self.old_get_ffi

    def test_processing_throughput(self):
        # Stress test: send 10,000 messages
        count = 10000
        packet = self.mcu.generate_packet("test_resp", arg1=1234, arg2=5678)
        
        start_time = time.time()
        with self.mock_ffi_lib.cond:
            for _ in range(count):
                self.mock_ffi_lib.queue.append(packet)
            self.mock_ffi_lib.cond.notify_all()
            
        # Wait for processing
        timeout = 5.0
        while timeout > 0:
            stats = self.sh.get_stats()
            if stats['msg_count'] >= count:
                break
            time.sleep(0.1)
            timeout -= 0.1
            
        end_time = time.time()
        duration = end_time - start_time
        
        stats = self.sh.get_stats()
        print(f"\nProcessed {stats['msg_count']} messages in {duration:.3f}s")
        print(f"Avg parse time: {stats['total_parse_time']/count*1e6:.3f}us")
        print(f"Avg handle time: {stats['total_hdl_time']/count*1e6:.3f}us")
        print(f"Max parse time: {stats['max_parse_time']*1e6:.3f}us")
        print(f"Max handle time: {stats['max_hdl_time']*1e6:.3f}us")
        
        self.assertGreaterEqual(stats['msg_count'], count)

if __name__ == '__main__':
    unittest.main()
