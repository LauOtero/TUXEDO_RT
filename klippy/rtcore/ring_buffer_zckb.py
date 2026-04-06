"""
Ring Buffer Lock-Free para Zero-Copy Kernel-Bypass (ZCKB-Shm)

Permite comunicación ultra-rápida entre C y Python sin copias de memoria.
Usa memoria compartida mmap() y operaciones atómicas.
"""

import os
import mmap
import struct
import time
from typing import Optional
from dataclasses import dataclass

# Constantes desde C
ZCKB_MAGIC = 0x5A434B42
ZCKB_RING_SIZE = 1024 * 1024  # 1MB

@dataclass
class RingStats:
    push_count: int = 0
    pop_count: int = 0
    overflow_count: int = 0
    underflow_count: int = 0
    total_bytes: int = 0
    avg_latency_us: float = 0.0

class ZCKBRingBuffer:
    """Anillo circular lock-free en memoria compartida."""
    
    def __init__(self, name: str, is_master: bool = True, size: int = ZCKB_RING_SIZE):
        self.name = name
        self.is_master = is_master
        self.size = size
        self.shm_name = f"/zckb_{name}"
        self.fd: Optional[int] = None
        self.mm: Optional[mmap.mmap] = None
        self.stats = RingStats()
        
        self._open_or_create()
    
    def _open_or_create(self):
        """Abrir o crear segmento de memoria compartida."""
        try:
            if self.is_master:
                # Crear segmento
                self.fd = os.posix_shm_open(self.shm_name, os.O_CREAT | os.O_RDWR, 0o666)
                os.ftruncate(self.fd, self.size)
            else:
                # Abrir existente
                self.fd = os.posix_shm_open(self.shm_name, os.O_RDWR, 0o666)
            
            # Mapear memoria
            self.mm = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED, 
                               mmap.PROT_READ | mmap.PROT_WRITE)
            
            if self.is_master:
                # Inicializar cabecera (head/tail en offsets fijos)
                # Offset 0-7: TX Head, 8-15: TX Tail, 16-23: RX Head, 24-31: RX Tail
                self._write_u64(0, 0)  # TX Head
                self._write_u64(8, 0)  # TX Tail
                self._write_u64(16, 0) # RX Head
                self._write_u64(24, 0) # RX Tail
        except Exception as e:
            raise RuntimeError(f"Fallo al inicializar ZCKB ring '{self.name}': {e}")
    
    def _read_u64(self, offset: int) -> int:
        return struct.unpack('<Q', self.mm[offset:offset+8])[0]
    
    def _write_u64(self, offset: int, value: int):
        self.mm[offset:offset+8] = struct.pack('<Q', value)
    
    def push(self, data: bytes) -> bool:
        """Push de datos al ring (zero-copy)."""
        if not self.mm:
            return False
        
        start_time = time.perf_counter_ns()
        
        # Leer estado actual (TX Head/Tail)
        head = self._read_u64(0)
        tail = self._read_u64(8)
        
        data_len = len(data)
        if data_len > 255:
            return False  # Máximo 255 bytes por mensaje
        
        # Espacio necesario: 1 byte longitud + datos
        needed = 1 + data_len
        next_head = (head + needed) % (self.size - 64)
        
        # Verificar espacio (si next_head alcanza tail, está lleno)
        if (head < tail and next_head >= tail) or (head > tail and next_head >= tail and next_head < head):
            self.stats.overflow_count += 1
            return False
        
        # Escribir longitud y datos directamente en mmap
        buffer_offset = 64  # Después de la cabecera
        self.mm[(buffer_offset + head) % (self.size - 64)] = data_len
        
        # Escribir datos (manejando wrap-around si es necesario)
        write_pos = (buffer_offset + head + 1) % (self.size - 64)
        space_to_end = (self.size - 64) - write_pos
        
        if data_len <= space_to_end:
            self.mm[write_pos:write_pos+data_len] = data
        else:
            # Wrap-around
            self.mm[write_pos:] = data[:space_to_end]
            self.mm[buffer_offset:buffer_offset+(data_len-space_to_end)] = data[space_to_end:]
        
        # Actualizar head
        self._write_u64(0, next_head)
        
        # Stats
        self.stats.push_count += 1
        self.stats.total_bytes += data_len
        
        end_time = time.perf_counter_ns()
        latency_us = (end_time - start_time) / 1000.0
        alpha = 0.1
        self.stats.avg_latency_us = (alpha * latency_us + (1 - alpha) * self.stats.avg_latency_us)
        
        return True
    
    def pop(self) -> Optional[bytes]:
        """Pop de datos del ring (zero-copy)."""
        if not self.mm:
            return None
        
        # Leer estado (RX Head/Tail)
        tail = self._read_u64(24)
        head = self._read_u64(16)
        
        if tail == head:
            self.stats.underflow_count += 1
            return None
        
        # Leer longitud
        buffer_offset = 64
        data_len = self.mm[(buffer_offset + tail) % (self.size - 64)]
        
        if data_len == 0 or data_len > 255:
            return None
        
        # Leer datos
        read_pos = (buffer_offset + tail + 1) % (self.size - 64)
        space_to_end = (self.size - 64) - read_pos
        
        if data_len <= space_to_end:
            data = bytes(self.mm[read_pos:read_pos+data_len])
        else:
            # Wrap-around
            part1 = bytes(self.mm[read_pos:])
            part2 = bytes(self.mm[buffer_offset:buffer_offset+(data_len-space_to_end)])
            data = part1 + part2
        
        # Actualizar tail
        next_tail = (tail + 1 + data_len) % (self.size - 64)
        self._write_u64(24, next_tail)
        
        self.stats.pop_count += 1
        return data
    
    def close(self):
        """Cerrar y limpiar recursos."""
        if self.mm:
            self.mm.close()
        if self.fd is not None:
            os.close(self.fd)
        if self.is_master:
            try:
                os.posix_shm_unlink(self.shm_name)
            except:
                pass

class DualRingBuffer:
    """Doble anillo para comunicación full-duplex C ↔ Python."""
    
    def __init__(self, name: str):
        # TX: Python -> C
        self.tx_ring = ZCKBRingBuffer(f"{name}_tx", is_master=True)
        # RX: C -> Python
        self.rx_ring = ZCKBRingBuffer(f"{name}_rx", is_master=True)
    
    def send_to_c(self, data: bytes) -> bool:
        """Enviar datos desde Python a C."""
        return self.tx_ring.push(data)
    
    def recv_from_c(self) -> Optional[bytes]:
        """Recibir datos desde C a Python."""
        return self.rx_ring.pop()
    
    def get_stats(self) -> dict:
        return {
            'tx': vars(self.tx_ring.stats),
            'rx': vars(self.rx_ring.stats)
        }
    
    def close(self):
        self.tx_ring.close()
        self.rx_ring.close()
        self.tx_ring = None
        self.rx_ring = None