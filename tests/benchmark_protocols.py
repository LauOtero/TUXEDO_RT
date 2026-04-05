# -*- coding: utf-8 -*-
import os
import sys
import time
import socket
import threading
import statistics
import logging
import signal
import queue
import struct
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

KLIPPER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
sys.path.insert(0, KLIPPER_DIR)

import chelper
ffi_main, ffi_lib = chelper.get_ffi()
import msgproto
import serialhdl

# Constantes CAN
CAN_MTU = 16
CANFD_MTU = 72
CANXL_MAX_DLC = 2048 # From serialqueue_can.c
CANXL_MTU_BASE = 12 # prio (4) + flags (1) + sdt (1) + len (2) + af (4)
CANXL_MTU = CANXL_MTU_BASE + CANXL_MAX_DLC

AUTONEG_PROBE_ID = 0x3f0
CAN_ERR_MASK = 0x1FFFFFFF

# Modos CAN (de sq_backend.h)
CANBUS_MODE_CLASSIC = 0
CANBUS_MODE_FD_NO_BRS = 1
CANBUS_MODE_FD_BRS = 2
CANBUS_MODE_XL = 3

# Flags CAN (de serialqueue_can.c)
CANFD_BRS = 0x01

class ShutdownController:
    """Controlador de apagado que garantiza interrupción inmediata"""
    def __init__(self):
        self.event = threading.Event()
        self.sockets = []
        self.lock = threading.Lock()
    
    def register_socket(self, sock: socket.socket):
        with self.lock:
            if sock not in self.sockets:
                self.sockets.append(sock)
    
    def trigger(self):
        self.event.set()
        with self.lock:
            for sock in self.sockets[:]:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except (OSError, Exception):
                    pass
                try:
                    sock.close()
                except (OSError, Exception):
                    pass
            self.sockets.clear()
    
    def is_shutdown(self) -> bool:
        return self.event.is_set()

shutdown = ShutdownController()

aborted = False

def signal_handler(sig, frame):
    global aborted
    aborted = True
    if not shutdown.is_shutdown():
        logging.warning(f"\n🛑 Señal {sig} recibida. Deteniendo pruebas...")
        shutdown.trigger()

def create_mock_serial(is_can=False):
    """Crea par de sockets para simular comunicación serial full-duplex o CAN"""
    if is_can:
        # Usar SOCK_DGRAM para preservar límites de mensajes, similar a CAN_RAW
        s1, s2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    else:
        # Usar SOCK_STREAM para UART (flujo de bytes continuo)
        s1, s2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    
    s1.setblocking(False)
    s2.setblocking(False)
    # Increase buffer to avoid ENOBUFS
    s1.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*1024)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*1024)
    return s1, s2

class BenchmarkResults:
    def __init__(self):
        self.latencies = []
        self.bytes_sent = 0
        self.messages_sent = 0
        self.start_time = 0
        self.end_time = 0

def pack_can_frame(can_id, data, can_mode=CANBUS_MODE_CLASSIC, brs_flag=0):
    """Empaqueta datos en un struct can_frame, canfd_frame o canxl_frame"""
    dlc = len(data)
    if can_mode == CANBUS_MODE_CLASSIC:
        padded_data = data.ljust(8, b'\x00')
        # canid_t (4), can_dlc (1), pad (1), res0 (1), res1 (1), data (8)
        return struct.pack("=IB3x8s", can_id, dlc, padded_data)
    elif can_mode in [CANBUS_MODE_FD_NO_BRS, CANBUS_MODE_FD_BRS]:
        padded_data = data.ljust(64, b'\x00')
        flags = CANFD_BRS if brs_flag else 0
        # canid_t (4), len (1), flags (1), res0 (1), res1 (1), data (64)
        return struct.pack("=IBB2x64s", can_id, dlc, flags, padded_data)
    elif can_mode == CANBUS_MODE_XL:
        prio = 0 # No usado en este test
        flags = 0 # No usado en este test
        sdt = 0x01 # Usamos 0x01 para SDU type
        af = can_id # Usamos can_id como Arbitration Field
        # prio (I), flags (B), sdt (B), len (H), af (I)
        header = struct.pack("=IBBHI", prio, flags, sdt, dlc, af)
        return header + data # No rellenar con ceros en DGRAM
    return b''

def unpack_can_frame(frame_bytes):
    """Desempaqueta un struct can_frame, canfd_frame o canxl_frame"""
    if len(frame_bytes) == CAN_MTU:
        can_id, dlc, data = struct.unpack("=IB3x8s", frame_bytes)
        return can_id, dlc, data[:dlc], CANBUS_MODE_CLASSIC
    elif len(frame_bytes) == CANFD_MTU:
        can_id, dlc, flags, data = struct.unpack("=IBB2x64s", frame_bytes)
        can_mode = CANBUS_MODE_FD_BRS if (flags & CANFD_BRS) else CANBUS_MODE_FD_NO_BRS
        return can_id, dlc, data[:dlc], can_mode
    elif len(frame_bytes) >= CANXL_MTU_BASE: # CAN XL tiene longitud variable de datos
        # prio (I), flags (B), sdt (B), len (H), af (I)
        prio, flags, sdt, dlc_xl, af = struct.unpack_from("=IBBH I", frame_bytes, 0)
        # Asegurarse de que no intentamos leer más allá de los bytes disponibles
        data_start_idx = struct.calcsize("=IBBH I")
        data_end_idx = data_start_idx + dlc_xl
        if data_end_idx > len(frame_bytes):
            logging.warning(f"unpack_can_frame: Datos insuficientes para CAN XL (esperado {data_end_idx}, recibido {len(frame_bytes)})")
            return 0, 0, b'', CANBUS_MODE_CLASSIC # O manejar como error
        data = frame_bytes[data_start_idx : data_end_idx]
        return af, dlc_xl, data, CANBUS_MODE_XL
    return 0, 0, b'', CANBUS_MODE_CLASSIC

def mock_mcu_reader_thread(s2: socket.socket, payload_parser, shutdown_ctrl: ShutdownController, is_can=False, client_id=1):
    """Hilo que simula el MCU: recibe mensajes y envía un 'Echo' V1"""
    buf = bytearray()
    mp = payload_parser
    next_sequence = msgproto.MESSAGE_DEST | 0x02  # Iniciar en 2 porque Klipper init receive_seq=1
    last_recv_seq_num = -1
    last_ack = None
    
    # Buffer para datos entrantes del socket
    socket_recv_buf = bytearray()
    
    while not shutdown_ctrl.is_shutdown():
        try:
            # Leer del socket en un buffer temporal
            data = s2.recv(4096)
            mcu_recv_py = time.perf_counter()
            if not data:
                break

            if is_can:
                socket_recv_buf.extend(data)
                # Procesar frames CAN del buffer del socket
                idx = 0
                while idx < len(socket_recv_buf):
                    frame_chunk = None
                    can_id = 0
                    dlc = 0
                    frame_data = b''
                    received_can_mode = CANBUS_MODE_CLASSIC
                    frame_len = 0

                    actual_datagram_len = len(socket_recv_buf)

                    if actual_datagram_len == CAN_MTU:
                        frame_chunk = socket_recv_buf[idx : idx + CAN_MTU]
                        can_id, dlc, frame_data, received_can_mode = unpack_can_frame(frame_chunk)
                        frame_len = CAN_MTU
                    elif actual_datagram_len == CANFD_MTU:
                        frame_chunk = socket_recv_buf[idx : idx + CANFD_MTU]
                        can_id, dlc, frame_data, received_can_mode = unpack_can_frame(frame_chunk)
                        frame_len = CANFD_MTU
                    elif actual_datagram_len >= CANXL_MTU_BASE:
                        frame_chunk = socket_recv_buf[idx:]
                        can_id, dlc, frame_data, received_can_mode = unpack_can_frame(frame_chunk)
                        frame_len = len(frame_chunk)

                    if frame_len == 0:
                        break
                    idx += frame_len

                    if received_can_mode != CANBUS_MODE_XL:
                        can_id &= CAN_ERR_MASK
                    if can_id == AUTONEG_PROBE_ID:
                        continue
                    if can_id != client_id:
                        continue
                    buf.extend(frame_data)
                del socket_recv_buf[:idx]
            else:
                buf.extend(data)

            # Procesar todos los mensajes completos en buffer
            while buf and not shutdown_ctrl.is_shutdown():
                msglen = mp.check_packet(bytes(buf))
                if msglen > 0:
                    packet = buf[:msglen]
                    buf = buf[msglen:]
                    
                    # Extraer seq del mensaje recibido para log
                    is_v2_received = (packet[0] == msgproto.MESSAGE_SYNC_V2)
                    recv_seq = packet[1] if not is_v2_received else packet[3]
                    recv_seq_num = recv_seq & msgproto.MESSAGE_SEQ_MASK
                    
                    if recv_seq_num == last_recv_seq_num and last_ack is not None:
                        # Retransmisión
                        ack = last_ack
                        logging.debug(f"Mock MCU received RETRANSMISSION len={msglen}, seq={recv_seq:02x}, resending ACK seq={ack[1]:02x}")
                    else:
                        last_recv_seq_num = recv_seq_num
                        # Construir ACK (V1 o V2 según el mensaje recibido)
                        if is_v2_received:
                            msg_min_v2 = msgproto.MESSAGE_HEADER_SIZE_V2 + msgproto.MESSAGE_TRAILER_SIZE
                            ack = bytearray([msgproto.MESSAGE_SYNC_V2])
                            ack.extend([(msg_min_v2 >> 8) & 0xFF, msg_min_v2 & 0xFF])
                            ack.extend([next_sequence])
                            ack.extend(b'\x00\x00') # CRC placeholder
                            ack.extend([msgproto.MESSAGE_SYNC])
                            crc_start_idx = 0
                            crc_len = msg_min_v2 - msgproto.MESSAGE_TRAILER_SIZE
                        else:
                            ack = bytearray([msgproto.MESSAGE_MIN]) # len = 5
                            ack.extend([next_sequence])
                            ack.extend(b'\x00\x00') # CRC placeholder
                            ack.extend([msgproto.MESSAGE_SYNC])
                            crc_start_idx = 0
                            crc_len = msgproto.MESSAGE_MIN - msgproto.MESSAGE_TRAILER_SIZE
                        
                        crc = msgproto.crc16_ccitt(ack[crc_start_idx : crc_len])
                        ack[crc_len] = crc[0]
                        ack[crc_len + 1] = crc[1]
                        last_ack = ack
                        
                        logging.debug(f"Mock MCU received msg len={msglen}, seq={recv_seq:02x}, sending ACK seq={next_sequence:02x} (V{'2' if is_v2_received else '1'})")
                        next_sequence = ((next_sequence + 1) & msgproto.MESSAGE_SEQ_MASK) | msgproto.MESSAGE_DEST
                    
                    try:
                        mcu_ack_send_py = time.perf_counter()
                        if is_can:
                            ack_bytes = bytes(ack)
                            ack_can_mode = received_can_mode # Usar el modo del mensaje recibido para el ACK
                            ack_brs_flag = CANFD_BRS if ack_can_mode == CANBUS_MODE_FD_BRS else 0
                            
                            if ack_can_mode == CANBUS_MODE_XL:
                                logging.debug(f"Mock MCU: Enviando ACK CAN XL (len={len(ack_bytes)})")
                                frame = pack_can_frame(client_id, ack_bytes, can_mode=ack_can_mode)
                                s2.sendall(frame)
                            else:
                                # Para Classic y FD, fragmentar si es necesario
                                fragment_size = 8 if ack_can_mode == CANBUS_MODE_CLASSIC else 64
                                for i in range(0, len(ack_bytes), fragment_size):
                                    chunk = ack_bytes[i:i+fragment_size]
                                    logging.debug(f"Mock MCU: Enviando ACK CAN (mode={ack_can_mode}, chunk_len={len(chunk)})")
                                    frame = pack_can_frame(client_id, chunk, can_mode=ack_can_mode, brs_flag=ack_brs_flag)
                                    s2.sendall(frame)
                        else:
                            logging.debug(f"Mock MCU: Enviando ACK UART (len={len(ack)})")
                            s2.sendall(ack)
                    except BlockingIOError:
                        time.sleep(0.0001)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                elif msglen == 0:
                    break  # Mensaje incompleto
                else:  # msglen < 0: error de protocolo
                    discard = abs(msglen)
                    del buf[:discard]
        except BlockingIOError:
            time.sleep(0.001)
        except (ConnectionResetError, BrokenPipeError, OSError):
            break
        except Exception as e:
            logging.exception("Error en mock_mcu_reader: %s", e)
            break

def puller_thread(sq, cq, result_queue: queue.Queue, shutdown_ctrl: ShutdownController):
    """Hilo dedicado para ejecutar serialqueue_pull"""
    while not shutdown_ctrl.is_shutdown():
        pqm = ffi_main.new("struct pull_queue_message *")
        pull_start_py = time.perf_counter()
        try:
            ffi_lib.serialqueue_pull(sq, pqm)
            pull_end_py = time.perf_counter()
            if shutdown_ctrl.is_shutdown():
                break
            if pqm.len >= 0: # Procesar mensajes válidos y ACKs (len=0)
                logging.debug(f"Puller: Recibido mensaje len={pqm.len}, notify_id={pqm.notify_id}, receive_time={pqm.receive_time}, pull_duration={pull_end_py - pull_start_py:.6f}s")
                result_queue.put({'len': pqm.len, 'success': True, 'notify_id': pqm.notify_id, 'receive_time': pqm.receive_time, 'pull_duration': pull_end_py - pull_start_py})
            elif pqm.len == -2: # No hay mensajes disponibles, esperar un poco para evitar busy-waiting
                time.sleep(0.001)
            else:
                logging.debug(f"Puller: Recibido mensaje de error len={pqm.len}")
                result_queue.put({'len': pqm.len, 'success': False}) # Mensaje de error (len=-1)
        except Exception as e:
            if not shutdown_ctrl.is_shutdown():
                logging.warning("serialqueue_pull generó excepción: %s", e)
            result_queue.put({'error': str(e), 'success': False})
            break
    result_queue.put({'done': True})

def run_stress_test(protocol: str, duration_sec: float, klipper_data_payload_size: int, can_mode: int = CANBUS_MODE_CLASSIC) -> BenchmarkResults:
    shutdown.event.clear()
    is_can = (protocol == 'CAN')
    client_id = 1
    
    s1, s2 = create_mock_serial(is_can=is_can)
    
    # Aumentar buffers de socket drásticamente para evitar ENOBUFS en test de alto throughput
    s1.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 10 * 1024 * 1024)
    s1.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 10 * 1024 * 1024)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 10 * 1024 * 1024)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 10 * 1024 * 1024)
    
    shutdown.register_socket(s1)
    shutdown.register_socket(s2)
    
    fd_type = b'c' if is_can else b'u'
    logging.info("Benchmark: Llamando a serialqueue_alloc con fd_type=%s, client_id=%d", fd_type.decode('utf-8'), client_id)
    sq = ffi_lib.serialqueue_alloc(s1.fileno(), fd_type, client_id, b"mock")
    cq = ffi_lib.serialqueue_alloc_commandqueue()
    
    # Configurar modo CAN si es necesario
    if is_can:
        ffi_lib.serialqueue_set_can_params(sq, can_mode, 0, 0)
        max_payload = 2048 if can_mode == CANBUS_MODE_XL else (64 if can_mode != CANBUS_MODE_CLASSIC else 8)
    else:
        max_payload = 64
        
    results = BenchmarkResults()
    results.start_time = time.perf_counter()

    if klipper_data_payload_size > max_payload:
        klipper_data_payload_size = max_payload
        
    # Generar payload de datos ('A's)
    payload_bytes = b'A' * klipper_data_payload_size
    c_payload = ffi_main.new("uint8_t[]", klipper_data_payload_size)
    ffi_main.memmove(c_payload, payload_bytes, klipper_data_payload_size)
    
    mp = msgproto.MessageParser()
    
    pull_queue = queue.Queue(maxsize=100)
    
    # Iniciar hilos
    t_puller = threading.Thread(target=puller_thread, args=(sq, cq, pull_queue, shutdown))
    t_puller.start()
    
    t_mcu = threading.Thread(target=mock_mcu_reader_thread, 
                             args=(s2, mp, shutdown, is_can, client_id))
    t_mcu.start()
    
    time.sleep(0.1) # Dejar que los hilos arranquen
    
    msg_count = 0
    start_ts = time.perf_counter()
    current_notify_id = 100
    
    sent_times = {}
    
    # Sincronizar bases de tiempo C y Python
    c_monotonic_start_time = ffi_lib.get_monotonic()
    py_perf_counter_start_time = time.perf_counter()
    time_offset = py_perf_counter_start_time - c_monotonic_start_time
    
    try:
        while time.perf_counter() - start_ts < duration_sec and not shutdown.is_shutdown():
            # Enviar payload. serialqueue se encarga de envolverlo en un mensaje Klipper V1 o V2
            send_start_py = time.perf_counter()
            ffi_lib.serialqueue_send(sq, cq, c_payload, klipper_data_payload_size, 0, 0, current_notify_id)
            sent_times[current_notify_id] = send_start_py
            msg_count += 1
            current_notify_id += 1
            if current_notify_id > 1000000: current_notify_id = 100
            
            # Control de flujo simple para no saturar la cola en el test
            if msg_count % 100 == 0:
                time.sleep(0.001)
                
            # Procesar respuestas del puller
            while not pull_queue.empty():
                msg = pull_queue.get_nowait()
                if msg.get('success'):
                    results.messages_sent += 1
                    notify_id = msg['notify_id']
                    receive_time_c = msg['receive_time']
                    
                    # Ajustar receive_time a la base de tiempo de Python
                    adjusted_receive_time = receive_time_c + time_offset
                    
                    receive_end_py = time.perf_counter()
                    if notify_id in sent_times:
                        sent_time = sent_times.pop(notify_id)
                        latency_us = (receive_end_py - sent_time) * 1_000_000
                        results.latencies.append(latency_us)
        
        t1_total = time.perf_counter()
        
        # Obtener estadísticas reales del serialqueue
        stats_buf = ffi_main.new("char[]", 256)
        ffi_lib.serialqueue_get_stats(sq, stats_buf, 256)
        stats_str = ffi_main.string(stats_buf).decode('utf-8')
        
        # Parsear bytes_write
        bytes_write = 0
        for part in stats_str.split():
            if part.startswith("bytes_write="):
                bytes_write = int(part.split("=")[1])
                break
                
        results.bytes_sent = bytes_write
        
    except KeyboardInterrupt:
        pass
    finally:
        # Ensure C-side cleanup happens before Python closes sockets
        try:
            # Wait for the command queue to empty before freeing
            timeout_start = time.perf_counter()
            while not ffi_lib.serialqueue_commandqueue_is_empty(cq):
                if time.perf_counter() - timeout_start > 5.0: # 5 second timeout
                    logging.warning("Timeout waiting for commandqueue to empty. Forcing free.")
                    break
                time.sleep(0.01) # Small delay to avoid busy-waiting
            ffi_lib.serialqueue_free_commandqueue(cq)
            ffi_lib.serialqueue_free(sq)
        except Exception as e:
            logging.warning("Error during C-side cleanup: %s", e)

        shutdown.trigger()
        
        for thread in [t_mcu, t_puller]:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        results.end_time = time.perf_counter()
    
    return results

def print_report(name: str, results: BenchmarkResults):
    total_time = max(results.end_time - results.start_time, 0.001)
    throughput = (results.bytes_sent / total_time) / 1024  # KB/s
    
    lats = results.latencies
    if not lats:
        print(f"⚠️ Sin datos para: {name}")
        return
        
    avg = statistics.mean(lats)
    p99 = statistics.quantiles(lats, n=1000)[998] if len(lats) >= 1000 else max(lats)
    max_lat = max(lats)
    min_lat = min(lats)
    
    print(f"\n{'='*60}")
    print(f" 📊 REPORTE PROTOCOLO: {name}")
    print(f"{'='*60}")
    print(f" ✅ Mensajes OK: {results.messages_sent:,}")
    print(f" 📦 Bytes Env : {results.bytes_sent:,}")
    print(f" 🚀 Throughput: {throughput:,.2f} KB/s")
    print(f" ⏱️  Latencia Media: {avg:7.2f} µs")
    print(f" 📈 Latencia Max  : {max_lat:7.2f} µs")
    print(f" 🎯 P99.9         : {p99:7.2f} µs")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("🚀 TUXEDO_RT PROTOCOLS - Test Automatizado (UART & CAN)")
    
    try:
        if not aborted:
            print("🔄 Iniciando Test UART (40 bytes)...")
            res_uart = run_stress_test('UART', duration_sec=2.0, klipper_data_payload_size=40)
            print_report("UART (Serial)", res_uart)
        
        if not aborted:
            print("🔄 Iniciando Test CAN (40 bytes, segmentado)...")
            res_can = run_stress_test('CAN', duration_sec=2.0, klipper_data_payload_size=40, can_mode=CANBUS_MODE_CLASSIC)
            print_report("CAN (Classic 8-byte frames)", res_can)
            
        if not aborted:
            print("🔄 Iniciando Test CAN FD (64 bytes, sin BRS)...")
            res_can_fd_no_brs = run_stress_test('CAN', duration_sec=2.0, klipper_data_payload_size=64, can_mode=CANBUS_MODE_FD_NO_BRS)
            print_report("CAN FD (64-byte frames, no BRS)", res_can_fd_no_brs)

        if not aborted:
            print("🔄 Iniciando Test CAN FD (64 bytes, con BRS)...")
            res_can_fd_brs = run_stress_test('CAN', duration_sec=2.0, klipper_data_payload_size=64, can_mode=CANBUS_MODE_FD_BRS)
            print_report("CAN FD (64-byte frames, with BRS)", res_can_fd_brs)

        if not aborted:
            print("🔄 Iniciando Test CAN XL (2048 bytes)...")
            res_can_xl = run_stress_test('CAN', duration_sec=2.0, klipper_data_payload_size=2048, can_mode=CANBUS_MODE_XL)
            print_report("CAN XL (2048-byte frames)", res_can_xl)
            
    except Exception as e:
        logging.exception("Error inesperado:")
    finally:
        shutdown.trigger()
        print("✅ Ejecución finalizada.")
