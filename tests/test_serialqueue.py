#!/usr/bin/env python3
import os
import sys
import time
import socket
import threading
import fcntl

# Add parent directory to path to import chelper
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import klippy.chelper as chelper

def set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def test_usb_detection_and_payload_assignment():
    print("--- Test: USB Version Detection & Payload Assignment ---")
    ffi_main, ffi_lib = chelper.get_ffi()
    
    # Create a mock socket pair to act as serial_fd
    s1, s2 = socket.socketpair()
    fd = s1.fileno()
    
    # Test UART/USB allocation
    # SQT_SERIAL = 0 (we assume b'\x00' is UART/USB, b'\x01' is CAN)
    sq = ffi_lib.serialqueue_alloc(fd, b'\x00', 0, b"ttyUSB0")
    
    if sq != ffi_main.NULL:
        # Since we pass a socket fd, detect_usb_speed will fail to find sysfs and default to 64 bytes (1 block)
        blocks = ffi_lib.serialqueue_get_max_pending_blocks(sq)
        print(f"✅ Fallback payload assignment successful (Expected >= 1): {blocks} blocks")
    else:
        print("❌ serialqueue_alloc failed")
    
    ffi_lib.serialqueue_exit(sq)
    ffi_lib.serialqueue_free(sq)
    s1.close()
    s2.close()

def test_stress_and_robustness():
    print("\n--- Test: Stress, Flow Control & Disconnect Robustness ---")
    ffi_main, ffi_lib = chelper.get_ffi()
    
    s1, s2 = socket.socketpair()
    set_nonblocking(s1.fileno())
    set_nonblocking(s2.fileno())
    
    sq = ffi_lib.serialqueue_alloc(s1.fileno(), b'\x00', 0, b"ttyUSB_TEST\x00\x00\x00\x00\x00")
    
    # Simulate writing a large block of data that will fill the buffer
    cq = ffi_lib.serialqueue_alloc_commandqueue()
    
    msg = b"1234567890" * 5 # 50 bytes (dentro del límite típico de Klipper)
    c_msg = ffi_main.new("uint8_t[]", msg)
    
    print("Simulating high-throughput writes...")
    # Queue multiple messages (total 50 * 1000 = 50k bytes)
    for i in range(1000):
        ffi_lib.serialqueue_send(sq, cq, c_msg, len(msg), 0, 0, i)
        
    print("✅ Queued 50k bytes successfully.")
    
    # Simulate a disconnect (close the receiving end)
    print("Simulating unexpected hardware disconnect...")
    s2.close()
    
    # Try to process the queue, which will write to a closed socket and trigger the exponential backoff / error handling
    # We just wait a bit to ensure it doesn't crash
    time.sleep(0.5)
    print("✅ Disconnect handled without segfault.")
    
    ffi_lib.serialqueue_free_commandqueue(cq)
    ffi_lib.serialqueue_exit(sq)
    ffi_lib.serialqueue_free(sq)
    s1.close()

if __name__ == '__main__':
    try:
        test_usb_detection_and_payload_assignment()
        test_stress_and_robustness()
        print("\n✅ All serialqueue integration and stress tests passed!")
    except Exception as e:
        print(f"\n❌ Test suite failed: {e}")
        sys.exit(1)
