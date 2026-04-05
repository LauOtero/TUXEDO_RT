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
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Set path to import Klipper modules
KLIPPER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
sys.path.insert(0, KLIPPER_DIR)

import chelper
ffi_main, ffi_lib = chelper.get_ffi()
import msgproto
import serialhdl

# ─────────────────────────────────────────────────────────────
# SISTEMA DE APAGADO GLOBAL - CRÍTICO PARA INTERRUPCIÓN CTR+C
# ─────────────────────────────────────────────────────────────
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
        """Activa apagado y CIERRA todos los sockets para desbloquear llamadas C"""
        logging.info("⚡ SHUTDOWN TRIGGERED - Cerrando sockets para desbloquear I/O...")
        self.event.set()
        with self.lock:
            for sock in self.sockets[:]:  # Copia para iterar segura
                try:
                    # Shutdown primero para forzar error en llamadas bloqueantes
                    sock.shutdown(socket.SHUT_RDWR)
                except (OSError, Exception):
                    pass  # Ya cerrado o en estado inválido
                try:
                    sock.close()
                except (OSError, Exception):
                    pass
            self.sockets.clear()
    
    def is_shutdown(self) -> bool:
        return self.event.is_set()

shutdown = ShutdownController()

def signal_handler(sig, frame):
    """Handler para SIGINT (CTRL+C) y SIGTERM"""
    if not shutdown.is_shutdown():
        logging.warning(f"\n🛑 Señal {sig} recibida. Deteniendo pruebas...")
        shutdown.trigger()

# ─────────────────────────────────────────────────────────────
# FUNCIONES AUXILIARES
# ─────────────────────────────────────────────────────────────
def create_mock_serial():
    """Crea par de sockets para simular comunicación serial full-duplex"""
    s1, s2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    # Configurar como NO bloqueante para mayor control
    s1.setblocking(False)
    s2.setblocking(False)
    return s1, s2

class BenchmarkResults:
    def __init__(self):
        self.latencies = []
        self.bytes_sent = 0
        self.messages_sent = 0
        self.start_time = 0
        self.end_time = 0

# ─────────────────────────────────────────────────────────────
# HILO LECTOR "MOCK MCU" - Genera ACKs y Echoes
# ─────────────────────────────────────────────────────────────
def mock_mcu_reader_thread(s2: socket.socket, payload_parser, shutdown_ctrl: ShutdownController):
    """Hilo que simula el MCU: recibe mensajes y envía un 'Echo' V1 con secuencia independiente"""
    buf = bytearray()
    mp = payload_parser
    next_sequence = msgproto.MESSAGE_DEST | 0x02  # Iniciar en 2 porque Klipper init receive_seq=1# Inicializa en 0x11 para que coincida con receive_seq=1 de Klipper C
    
    while not shutdown_ctrl.is_shutdown():
        try:
            data = s2.recv(4096)
            if data:
                buf.extend(data)
                # Procesar todos los mensajes completos en buffer
                while buf and not shutdown_ctrl.is_shutdown():
                    msglen = mp.check_packet(bytes(buf))
                    if msglen > 0:
                        packet = buf[:msglen]
                        buf = buf[msglen:]
                        
                        # Construir ECHO (longitud 6 para despertar a serialqueue_pull)
                        # V1 header: [LEN, SEQ, DUMMY, CRC0, CRC1, SYNC]
                        ack = bytearray([
                            6,
                            next_sequence,
                            0x42, # dummy payload (1 byte)
                            0, 0,
                            msgproto.MESSAGE_SYNC
                        ])
                        next_sequence = ((next_sequence + 1) & msgproto.MESSAGE_SEQ_MASK) | msgproto.MESSAGE_DEST
                        
                        crc = msgproto.crc16_ccitt(ack[:3]) # CRC sobre LEN, SEQ, PAYLOAD
                        ack[3] = crc[0]
                        ack[4] = crc[1]
                        
                        # Enviar ACK (reintentar si EWOULDBLOCK)
                        try:
                            s2.sendall(ack)
                        except BlockingIOError:
                            time.sleep(0.0001)  # Micro-sleep y reintentar en siguiente iteración
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break  # Conexión cerrada
                    elif msglen == 0:
                        break  # Mensaje incompleto, esperar más datos
                    else:  # msglen < 0: error de protocolo
                        discard = abs(msglen)
                        logging.warning("⚠️ Protocol error en mock MCU, descartando %d bytes. Buffer: %s", discard, buf[:20].hex())
                        del buf[:discard]
            else:
                # recv() retornó vacío = conexión cerrada
                break
        except BlockingIOError:
            time.sleep(0.001)  # No hay datos, esperar un poco
        except (ConnectionResetError, BrokenPipeError, OSError):
            break  # Socket cerrado
        except Exception as e:
            logging.exception("❌ Error inesperado en mock_mcu_reader: %s", e)
            break
    
    logging.debug("🧵 mock_mcu_reader finalizado.")

# ─────────────────────────────────────────────────────────────
# HILO "PULLER" - Ejecuta serialqueue_pull (LLAMADA C BLOQUEANTE)
# ─────────────────────────────────────────────────────────────
def puller_thread(sq, cq, result_queue: queue.Queue, shutdown_ctrl: ShutdownController):
    """
    Hilo dedicado para ejecutar serialqueue_pull (llamada C bloqueante).
    Cuando shutdown se activa, el socket se cierra desde fuera, forzando 
    que esta llamada retorne con error y el hilo pueda terminar.
    """
    while not shutdown_ctrl.is_shutdown():
        pqm = ffi_main.new("struct pull_queue_message *")
        try:
            # ⚠️ ESTA ES LA LLAMADA C BLOQUEANTE CRÍTICA
            # Se desbloqueará cuando:
            # 1. Llegue un mensaje (Eco de longitud > 5), O
            # 2. El socket se cierre (shutdown.trigger()), generando error
            ffi_lib.serialqueue_pull(sq, pqm)
            
            if shutdown_ctrl.is_shutdown():
                break  # Salir si se activó shutdown durante la llamada
                
            # Poner resultado en queue para el hilo principal
            result_queue.put({
                'len': pqm.len,
                'success': True
            })
        except Exception as e:
            # Error en llamada C (ej: socket cerrado) = señal para terminar
            if not shutdown_ctrl.is_shutdown():
                logging.warning("⚠️ serialqueue_pull generó excepción: %s", e)
            result_queue.put({
                'error': str(e),
                'success': False
            })
            break
    
    # Señal de finalización
    result_queue.put({'done': True})
    logging.debug("🧵 puller_thread finalizado.")

# ─────────────────────────────────────────────────────────────
# FUNCIÓN PRINCIPAL DE PRUEBA
# ─────────────────────────────────────────────────────────────
def run_stress_test(duration_sec: float, payload_size: int, is_v2: bool = False) -> BenchmarkResults:
    # Resetear estado de shutdown para esta ejecución
    shutdown.event.clear()
    
    # Crear sockets de prueba
    s1, s2 = create_mock_serial()
    shutdown.register_socket(s1)
    shutdown.register_socket(s2)
    
    # Inicializar serialqueue de Klipper
    sq = ffi_lib.serialqueue_alloc(s1.fileno(), b'u', 1, b"mock")
    cq = ffi_lib.serialqueue_alloc_commandqueue()
    
    results = BenchmarkResults()
    results.start_time = time.perf_counter()
    
    payload = b'A' * payload_size
    c_payload = ffi_main.new("uint8_t[]", payload)
    mp = msgproto.MessageParser()
    
    # Queue para comunicación entre hilo puller y principal
    pull_queue = queue.Queue(maxsize=100)
    
    # Iniciar hilo mock MCU (genera ACKs)
    mcu_thread = threading.Thread(
        target=mock_mcu_reader_thread,
        args=(s2, mp, shutdown),
        daemon=True,
        name="mock_mcu"
    )
    mcu_thread.start()
    
    # Iniciar hilo puller (ejecuta llamada C bloqueante)
    pull_thread = threading.Thread(
        target=puller_thread,
        args=(sq, cq, pull_queue, shutdown),
        daemon=True,
        name="puller"
    )
    pull_thread.start()
    
    end_time = results.start_time + duration_sec
    
    try:
        while time.perf_counter() < end_time and not shutdown.is_shutdown():
            t0 = time.perf_counter()
            
            # Enviar mensaje (esto es NO bloqueante en Klipper)
            ffi_lib.serialqueue_send(sq, cq, c_payload, payload_size, 0, 0, 0)
            
            # Esperar respuesta del puller (bloqueante hasta que haya respuesta o shutdown)
            pull_result = None
            while not shutdown.is_shutdown():
                try:
                    pull_result = pull_queue.get(timeout=0.05)
                    break
                except queue.Empty:
                    pass
            
            if shutdown.is_shutdown() or pull_result is None:
                break
            
            # Verificar si el puller terminó
            if pull_result.get('done'):
                break
            
            # Verificar errores del puller
            if not pull_result.get('success', False):
                logging.warning("⚠️ Puller reportó error: %s", pull_result.get('error'))
                break
            
            # Procesar respuesta exitosa
            if pull_result.get('len', 0) < 0:
                logging.error("❌ serialqueue_pull retornó len negativo: %d", pull_result['len'])
                break
            
            t1 = time.perf_counter()
            latency_us = (t1 - t0) * 1e6
            
            results.latencies.append(latency_us)
            results.bytes_sent += payload_size
            results.messages_sent += 1
            
            # Yield periódico para no monopolizar CPU
            if results.messages_sent % 500 == 0:
                time.sleep(0.001)
                
    except KeyboardInterrupt:
        # Captura adicional por seguridad (aunque signal_handler ya se activa)
        logging.info("⌨️  Interrupción por teclado detectada.")
    finally:
        # 🛡️ BLOQUE CRÍTICO DE LIMPIEZA - SE EJECUTA SIEMPRE
        logging.debug("🧹 Iniciando limpieza de recursos...")
        
        # 1. Activar shutdown (cierra sockets, desbloquea llamadas C)
        shutdown.trigger()
        
        # 2. Esperar hilos con timeout razonable
        for thread_name, thread in [("mcu", mcu_thread), ("puller", pull_thread)]:
            if thread.is_alive():
                thread.join(timeout=1.0)
                if thread.is_alive():
                    logging.warning(f"⚠️ Hilo {thread_name} no terminó a tiempo.")
        
        # 3. Liberar recursos C de Klipper
        try:
            # Esperar un poco para que los mensajes pendientes terminen de procesarse o descartarse
            time.sleep(0.01)
            ffi_lib.serialqueue_free_commandqueue(cq)
            ffi_lib.serialqueue_free(sq)
        except Exception as e:
            logging.warning("⚠️ Error liberando recursos C: %s", e)
        
        # 4. Cerrar sockets (redundante pero seguro)
        for sock in [s1, s2]:
            try:
                sock.close()
            except:
                pass
        
        results.end_time = time.perf_counter()
    
    return results

# ─────────────────────────────────────────────────────────────
# REPORTE DE RESULTADOS
# ─────────────────────────────────────────────────────────────
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
    print(f" 📊 REPORTE: {name}")
    print(f"{'='*60}")
    print(f" ✅ Mensajes  : {results.messages_sent:,}")
    print(f" 📦 Bytes    : {results.bytes_sent:,}")
    print(f" 🚀 Throughput: {throughput:,.2f} KB/s")
    print(f" ⏱️  Latencia Media : {avg:7.2f} µs")
    print(f" ⬇️  Latencia Mínima: {min_lat:7.2f} µs")
    print(f" ⬆️  Latencia Máxima: {max_lat:7.2f} µs")
    print(f" 📈 P99.9     : {p99:7.2f} µs")
    
    wcet_status = "✅ PASSED" if max_lat < 10.0 else "⚠️ WARNING"
    print(f" 🎯 WCET (<10µs): [{wcet_status}] {max_lat:.2f}µs")
    print(f"{'='*60}\n")

# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Registrar handlers de señal ANTES de cualquier operación
    signal.signal(signal.SIGINT, signal_handler)   # CTRL+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill / systemd
    
    print("🚀 TUXEDO_RT MSGBLOCK - Suite de Benchmarks")
    print("🛡️  Sistema de interrupción ACTIVADO (CTRL+C)")
    print("💡 Presiona CTRL+C en cualquier momento para detener.\n")
    
    try:
        # Test 1: Protocolo V1 - Payload pequeño
        if not shutdown.is_shutdown():
            print("🔄 [1/2] Test V1: 40 bytes...")
            res_v1 = run_stress_test(duration_sec=2.0, payload_size=40)
            print_report("Protocolo V1 (40B)", res_v1)
        
        # Test 2: Protocolo V2 - Payload grande
        if not shutdown.is_shutdown():
            print("🔄 [2/2] Test V2: 1024 bytes...")
            res_v2 = run_stress_test(duration_sec=2.0, payload_size=1024)
            print_report("Protocolo V2 (1024B)", res_v2)
            
    except Exception as e:
        logging.exception("❌ Error inesperado en ejecución principal:")
    finally:
        # Limpieza final garantizada
        shutdown.trigger()
        print("✅ Ejecución finalizada. Recursos liberados.")
    
    print("📝 Nota: Para prueba de 24h usar duration_sec=86400")
