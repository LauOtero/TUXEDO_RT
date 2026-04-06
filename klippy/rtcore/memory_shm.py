#!/usr/bin/env python3
"""
Memory Manager para Zero-Copy Kernel-Bypass (ZCKB-Shm)

Gestiona el ciclo de vida de regiones de memoria compartida POSIX.
Proporciona API unificada para creación, mapeo, desmapeo y limpieza.
"""

import os
import mmap
import struct
import time
import threading
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

# Constantes desde C (deben coincidir con zckb_shm.h)
ZCKB_MAGIC = 0x5A434B42  # "ZCKB"
ZCKB_VERSION = 1
ZCKB_MAX_REGIONS = 8
ZCKB_RING_SIZE = 1024 * 1024  # 1MB por defecto

# Offsets de cabecera en la región compartida
OFFSET_MAGIC = 0
OFFSET_VERSION = 4
OFFSET_FD = 8
OFFSET_SIZE = 16
OFFSET_BASE_ADDR = 24
OFFSET_NAME = 32
OFFSET_NAME_LEN = 64
OFFSET_TX_HEAD = OFFSET_NAME + OFFSET_NAME_LEN
OFFSET_TX_TAIL = OFFSET_TX_HEAD + 8
OFFSET_TX_OVERFLOW = OFFSET_TX_TAIL + 8
OFFSET_TX_UNDERFLOW = OFFSET_TX_OVERFLOW + 8
OFFSET_RX_HEAD = OFFSET_TX_UNDERFLOW + 8
OFFSET_RX_TAIL = OFFSET_RX_HEAD + 8
OFFSET_RX_OVERFLOW = OFFSET_RX_TAIL + 8
OFFSET_RX_UNDERFLOW = OFFSET_RX_OVERFLOW + 8
OFFSET_DATA_START = OFFSET_RX_UNDERFLOW + 8  # 96 bytes de cabecera


@dataclass
class RegionInfo:
    """Información de una región de memoria compartida."""
    name: str
    size: int
    fd: Optional[int]
    mm: Optional[mmap.mmap]
    is_master: bool
    created_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_access: float = field(default_factory=time.time)
    
    def touch(self):
        """Actualizar timestamp de último acceso."""
        self.last_access = time.time()
        self.access_count += 1


class SharedMemoryManager:
    """
    Gestor centralizado de memoria compartida ZCKB.
    
    Características:
    - Creación y apertura de regiones POSIX shm_open
    - Mapeo/desmapeo automático con mmap
    - Validación de magia y versión
    - Thread-safe con locks
    - Estadísticas de uso por región
    - Limpieza automática de regiones huérfanas
    """
    
    def __init__(self):
        self.regions: Dict[str, RegionInfo] = {}
        self.lock = threading.RLock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = False
        
    def start(self):
        """Iniciar thread de limpieza background."""
        self._running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        logging.info("SharedMemoryManager iniciado")
    
    def stop(self):
        """Detener gestor y liberar todas las regiones."""
        self._running = False
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=2.0)
        
        with self.lock:
            for name in list(self.regions.keys()):
                self._unmap_region_internal(name)
        logging.info("SharedMemoryManager detenido")
    
    def _cleanup_loop(self):
        """Thread background para limpiar regiones inactivas."""
        while self._running:
            time.sleep(60.0)  # Chequear cada minuto
            self._cleanup_stale_regions()
    
    def _cleanup_stale_regions(self):
        """Limpiar regiones sin acceso por más de 10 minutos."""
        stale_threshold = 600.0  # 10 minutos
        now = time.time()
        
        with self.lock:
            to_remove = []
            for name, info in self.regions.items():
                if not info.is_master and (now - info.last_access) > stale_threshold:
                    to_remove.append(name)
            
            for name in to_remove:
                logging.warning("Limpiando región stale: %s", name)
                self._unmap_region_internal(name)
    
    def create_region(self, name: str, size: int = ZCKB_RING_SIZE) -> bool:
        """
        Crear nueva región de memoria compartida (modo master).
        
        Args:
            name: Nombre único de la región
            size: Tamaño en bytes (default: 1MB)
        
        Returns:
            True si éxito, False si ya existe o error
        """
        shm_path = f"/zckb_{name}"
        
        with self.lock:
            if name in self.regions:
                logging.warning("Región '%s' ya existe", name)
                return False
            
            try:
                # Crear segmento shm
                fd = os.posix_shm_open(shm_path, os.O_CREAT | os.O_RDWR, 0o666)
                if fd < 0:
                    logging.error("Fallo al crear shm '%s'", shm_path)
                    return False
                
                # Establecer tamaño
                os.ftruncate(fd, size)
                
                # Mapear memoria
                mm = mmap.mmap(fd, size, mmap.MAP_SHARED, 
                              mmap.PROT_READ | mmap.PROT_WRITE)
                
                # Inicializar cabecera
                self._write_u32(mm, OFFSET_MAGIC, ZCKB_MAGIC)
                self._write_u32(mm, OFFSET_VERSION, ZCKB_VERSION)
                self._write_u64(mm, OFFSET_SIZE, size)
                self._write_u64(mm, OFFSET_TX_HEAD, 0)
                self._write_u64(mm, OFFSET_TX_TAIL, 0)
                self._write_u64(mm, OFFSET_RX_HEAD, 0)
                self._write_u64(mm, OFFSET_RX_TAIL, 0)
                
                # Registrar región
                info = RegionInfo(
                    name=name,
                    size=size,
                    fd=fd,
                    mm=mm,
                    is_master=True
                )
                self.regions[name] = info
                
                logging.info("Región ZCKB creada: %s (%d bytes)", name, size)
                return True
                
            except Exception as e:
                logging.error("Error creando región '%s': %s", name, e)
                return False
    
    def open_region(self, name: str, timeout: float = 5.0) -> bool:
        """
        Abrir región existente (modo slave).
        
        Args:
            name: Nombre de la región
            timeout: Tiempo máximo de espera si no existe
        
        Returns:
            True si éxito, False si timeout o error
        """
        shm_path = f"/zckb_{name}"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self.lock:
                if name in self.regions:
                    logging.warning("Región '%s' ya está abierta", name)
                    return True
            
            try:
                fd = os.posix_shm_open(shm_path, os.O_RDWR, 0o666)
                if fd >= 0:
                    # Obtener tamaño
                    size = os.fstat(fd).st_size
                    
                    # Mapear
                    mm = mmap.mmap(fd, size, mmap.MAP_SHARED,
                                  mmap.PROT_READ | mmap.PROT_WRITE)
                    
                    # Verificar magia
                    magic = self._read_u32(mm, OFFSET_MAGIC)
                    if magic != ZCKB_MAGIC:
                        logging.error("Magia inválida en región '%s': 0x%08X", name, magic)
                        mm.close()
                        os.close(fd)
                        return False
                    
                    # Verificar versión
                    version = self._read_u32(mm, OFFSET_VERSION)
                    if version != ZCKB_VERSION:
                        logging.error("Versión incompatible en región '%s': %d", name, version)
                        mm.close()
                        os.close(fd)
                        return False
                    
                    # Registrar
                    with self.lock:
                        info = RegionInfo(
                            name=name,
                            size=size,
                            fd=fd,
                            mm=mm,
                            is_master=False
                        )
                        self.regions[name] = info
                    
                    logging.info("Región ZCKB abierta: %s (%d bytes)", name, size)
                    return True
                    
            except FileNotFoundError:
                pass  # Intentar de nuevo después de sleep
            except Exception as e:
                logging.error("Error abriendo región '%s': %s", name, e)
                return False
            
            time.sleep(0.1)  # Esperar antes de reintentar
        
        logging.error("Timeout abriendo región '%s'", name)
        return False
    
    def get_region(self, name: str) -> Optional[RegionInfo]:
        """Obtener información de región por nombre."""
        with self.lock:
            return self.regions.get(name)
    
    def get_mmap(self, name: str) -> Optional[mmap.mmap]:
        """Obtener mmap directo para zero-copy operations."""
        with self.lock:
            info = self.regions.get(name)
            if info:
                info.touch()
                return info.mm
            return None
    
    def close_region(self, name: str, unlink: bool = False) -> bool:
        """
        Cerrar región y opcionalmente eliminar del sistema.
        
        Args:
            name: Nombre de región
            unlink: Si True, eliminar segmento shm (solo master)
        
        Returns:
            True si éxito
        """
        with self.lock:
            return self._unmap_region_internal(name, unlink)
    
    def _unmap_region_internal(self, name: str, unlink: bool = False) -> bool:
        """Implementación interna de desmapeo."""
        info = self.regions.pop(name, None)
        if not info:
            return False
        
        try:
            if info.mm:
                info.mm.close()
            if info.fd is not None:
                os.close(info.fd)
            if unlink and info.is_master:
                shm_path = f"/zckb_{name}"
                try:
                    os.posix_shm_unlink(shm_path)
                    logging.info("Región eliminada: %s", name)
                except:
                    pass
        except Exception as e:
            logging.error("Error cerrando región '%s': %s", name, e)
            return False
        
        return True
    
    def _read_u32(self, mm: mmap.mmap, offset: int) -> int:
        """Leer uint32 desde mmap."""
        mm.seek(offset)
        return struct.unpack('<I', mm.read(4))[0]
    
    def _write_u32(self, mm: mmap.mmap, offset: int, value: int):
        """Escribir uint32 en mmap."""
        mm.seek(offset)
        mm.write(struct.pack('<I', value))
    
    def _read_u64(self, mm: mmap.mmap, offset: int) -> int:
        """Leer uint64 desde mmap."""
        mm.seek(offset)
        return struct.unpack('<Q', mm.read(8))[0]
    
    def _write_u64(self, mm: mmap.mmap, offset: int, value: int):
        """Escribir uint64 en mmap."""
        mm.seek(offset)
        mm.write(struct.pack('<Q', value))
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtener estadísticas de todas las regiones."""
        with self.lock:
            stats = {
                'region_count': len(self.regions),
                'total_mapped_bytes': sum(r.size for r in self.regions.values()),
                'regions': {}
            }
            for name, info in self.regions.items():
                stats['regions'][name] = {
                    'size': info.size,
                    'is_master': info.is_master,
                    'access_count': info.access_count,
                    'age_seconds': time.time() - info.created_at,
                    'idle_seconds': time.time() - info.last_access
                }
            return stats


# Instancia global singleton
_memory_manager: Optional[SharedMemoryManager] = None

def get_memory_manager() -> SharedMemoryManager:
    """Obtener instancia singleton del gestor de memoria."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = SharedMemoryManager()
    return _memory_manager


def init_memory_manager() -> SharedMemoryManager:
    """Inicializar y arrancar el gestor de memoria."""
    global _memory_manager
    _memory_manager = SharedMemoryManager()
    _memory_manager.start()
    return _memory_manager


def shutdown_memory_manager():
    """Detener y limpiar el gestor de memoria."""
    global _memory_manager
    if _memory_manager:
        _memory_manager.stop()
        _memory_manager = None
