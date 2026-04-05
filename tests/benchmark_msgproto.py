import sys
import os
import time
import json
import zlib

# Añadir el path de klippy
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

import msgproto

def benchmark_crc16():
    data = bytes(range(64))
    start = time.time()
    for _ in range(100000):
        msgproto.crc16_ccitt(data)
    end = time.time()
    print(f"CRC16 CCITT (64 bytes, 100k calls): {(end - start) * 1000:.3f} ms")

def benchmark_uint32():
    pt = msgproto.PT_uint32()
    values = [0, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000]
    
    start = time.time()
    for _ in range(100000):
        for v in values:
            out = []
            pt.encode(out, v)
    end = time.time()
    print(f"PT_uint32 encode (9 values, 100k calls): {(end - start) * 1000:.3f} ms")

    encoded_values = []
    for v in values:
        out = []
        pt.encode(out, v)
        encoded_values.append(bytes(out))

    start = time.time()
    for _ in range(100000):
        for ev in encoded_values:
            pt.parse(ev, 0)
    end = time.time()
    print(f"PT_uint32 parse (9 values, 100k calls): {(end - start) * 1000:.3f} ms")

def benchmark_message_format():
    msgid_bytes = [1]
    msgformat = "config_digital_out oid=%d pin=%s value=%d default_value=%d max_duration=%d"
    # Note: Using %d as placeholders to match msgproto's convert_msg_format if needed
    # but the internal MessageFormat uses lookup_params which expects things like %u, %s etc.
    msgformat_real = "config_digital_out oid=%u pin=%s value=%c default_value=%c max_duration=%u"
    mf = msgproto.MessageFormat(msgid_bytes, msgformat_real)
    
    params = [10, b"PA1", 1, 0, 1000]
    
    start = time.time()
    for _ in range(100000):
        mf.encode(params)
    end = time.time()
    print(f"MessageFormat encode (5 params, 100k calls): {(end - start) * 1000:.3f} ms")

    encoded = mf.encode(params)
    start = time.time()
    for _ in range(100000):
        mf.parse(encoded, 0)
    end = time.time()
    print(f"MessageFormat parse (5 params, 100k calls): {(end - start) * 1000:.3f} ms")

def benchmark_message_parser():
    mp = msgproto.MessageParser()
    # Mock some data for identify
    identify_data = {
        "commands": {"config_digital_out oid=%u pin=%c value=%c default_value=%c max_duration=%u": 10},
        "responses": {"status_digital_out oid=%u value=%c": 11},
        "enumerations": {"pin": {"PA1": 1, "PA2": 2}},
        "config": {"SERIAL_BAUD": 250000},
        "version": "1.0",
        "build_versions": "1.0"
    }
    json_data = json.dumps(identify_data).encode()
    mp.process_identify(json_data, decompress=False)
    
    cmd_str = "config_digital_out oid=10 pin=PA1 value=1 default_value=0 max_duration=1000"
    
    start = time.time()
    for _ in range(10000):
        mp.create_command(cmd_str)
    end = time.time()
    print(f"MessageParser create_command (10k calls): {(end - start) * 1000:.3f} ms")

    # Full packet check
    raw_cmd = mp.create_command(cmd_str)
    packet = mp.encode_msgblock(1, raw_cmd)
    # The packet from encode_msgblock can have lists inside it (CRC is a list)
    # Let's flatten it for bytes()
    flattened_packet = []
    for item in packet:
        if isinstance(item, list):
            flattened_packet.extend(item)
        else:
            flattened_packet.append(item)
    packet_bytes = bytes(flattened_packet)
    
    start = time.time()
    for _ in range(100000):
        mp.check_packet(packet_bytes)
    end = time.time()
    print(f"MessageParser check_packet (100k calls): {(end - start) * 1000:.3f} ms")

if __name__ == "__main__":
    print("Starting msgproto benchmarks...")
    benchmark_crc16()
    benchmark_uint32()
    benchmark_message_format()
    benchmark_message_parser()
    print("Benchmarks finished.")
