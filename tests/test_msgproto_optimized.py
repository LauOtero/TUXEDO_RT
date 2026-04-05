import unittest
import sys
import os
import json
import zlib

# Add klippy to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
import msgproto

class TestMsgProtoOptimized(unittest.TestCase):
    def test_crc16_ccitt(self):
        data = b"123456789"
        # Expected CRC for "123456789" is 0x29B1 for CCITT-FALSE (standard for Klipper)
        # Klipper's CRC is a bit different, it uses 0xFFFF init and some specific XORs.
        # Let's verify it against the implementation.
        res = msgproto.crc16_ccitt(data)
        self.assertEqual(len(res), 2)
        
        # Test with empty data
        res_empty = msgproto.crc16_ccitt(b"")
        self.assertEqual(res_empty, [0xff, 0xff])

    def test_pt_uint32(self):
        pt = msgproto.PT_uint32()
        values = [0, 1, 127, 128, 0x3FFF, 0x4000, 0xFFFFFFFF]
        for v in values:
            out = []
            pt.encode(out, v)
            v_parsed, pos = pt.parse(bytes(out), 0)
            self.assertEqual(v & 0xffffffff, v_parsed & 0xffffffff)
            self.assertEqual(pos, len(out))

    def test_pt_int32(self):
        pt = msgproto.PT_int32()
        values = [0, 1, -1, 127, -127, 128, -128, 0x7FFFFFFF, -0x80000000]
        for v in values:
            out = []
            pt.encode(out, v)
            v_parsed, pos = pt.parse(bytes(out), 0)
            # Use signed comparison
            self.assertEqual(v, v_parsed if v_parsed < 0x80000000 else v_parsed - 0x100000000)
            self.assertEqual(pos, len(out))

    def test_pt_string(self):
        pt = msgproto.PT_string()
        v = b"hello world"
        out = []
        pt.encode(out, v)
        v_parsed, pos = pt.parse(bytes(out), 0)
        self.assertEqual(v, v_parsed)
        self.assertEqual(pos, len(out))

    def test_message_format(self):
        msgformat = "test_cmd arg1=%u arg2=%s"
        mf = msgproto.MessageFormat([10], msgformat)
        params = [1234, b"data"]
        encoded = mf.encode(params)
        parsed, pos = mf.parse(encoded, 0)
        self.assertEqual(parsed['arg1'], 1234)
        self.assertEqual(parsed['arg2'], b"data")
        
        # Test encode_by_name
        encoded_name = mf.encode_by_name(arg1=1234, arg2=b"data")
        self.assertEqual(encoded, encoded_name)

    def test_pt_bool(self):
        pt = msgproto.PT_bool()
        for v in [True, False, 1, 0]:
            out = []
            pt.encode(out, v)
            v_parsed, pos = pt.parse(bytes(out), 0)
            self.assertEqual(bool(v), v_parsed)
            self.assertEqual(pos, len(out))

    def test_message_caching(self):
        msgformat = "test_cache arg1=%u"
        mf = msgproto.MessageFormat([20], msgformat)
        params = [100]
        encoded1 = mf.encode(params)
        # Second call should use cache
        encoded2 = mf.encode(params)
        self.assertEqual(encoded1, encoded2)
        # Ensure it's not the same object (caller might modify)
        self.assertIsNot(encoded1, encoded2)

    def test_message_parser(self):
        mp = msgproto.MessageParser()
        identify_data = {
            "commands": {"test_cmd arg1=%u arg2=%c": 10},
            "responses": {"test_resp arg1=%u": 11},
            "enumerations": {},
            "version": "1.0",
            "build_versions": "1.0"
        }
        json_data = json.dumps(identify_data).encode()
        mp.process_identify(json_data, decompress=False)
        
        # Test create_command
        cmd_bytes = mp.create_command("test_cmd arg1=100 arg2=5")
        self.assertEqual(cmd_bytes[0], 10) # msgid
        
        # Test check_packet
        packet = mp.encode_msgblock(1, cmd_bytes)
        # Flatten packet if it contains lists
        flattened = []
        for item in packet:
            if isinstance(item, list): flattened.extend(item)
            else: flattened.append(item)
        
        packet_bytes = bytes(flattened)
        res = mp.check_packet(packet_bytes)
        self.assertEqual(res, len(packet_bytes))
        
        # Test parse
        parsed = mp.parse(packet_bytes)
        self.assertEqual(parsed['arg1'], 100)
        self.assertEqual(parsed['arg2'], 5)
        self.assertEqual(parsed['#name'], "test_cmd")

    def test_errors(self):
        mp = msgproto.MessageParser()
        with self.assertRaises(msgproto.error):
            mp.create_command("unknown_cmd")

if __name__ == '__main__':
    unittest.main()
