# ConnPlug RT Framework - Especificación Técnica Completa

## 📋 Resumen Ejecutivo

**ConnPlug RT Framework** es una arquitectura modular de tiempo real determinista para sistemas embebidos de alto rendimiento, diseñada para proporcionar comunicación ultra-rápida entre el host (Klipper) y múltiples MCUs mediante diversos protocolos de transporte.

### Versión: 2.0.0-ZCKB
### Fecha: 2024
### Autor: Kevin O'Connor et al.

---

## 🏗️ Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        KLIPPER HOST                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ mcu_conn.py │  │  connhdl.py │  │  rtcore/connections/    │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│         │                │                      │               │
│         └────────────────┼──────────────────────┘               │
│                          │                                      │
│              ┌───────────▼───────────┐                          │
│              │   chelper/__init__.py │                          │
│              │   (CFFI Build System) │                          │
│              └───────────┬───────────┘                          │
│                          │ FFI                                  │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    CHELPER SHARED LIBRARY                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ conn_manager.c (Backend Dispatcher)                     │   │
│  │  ├── conn_serial_backend   (UART/USB)                   │   │
│  │  ├── conn_can_backend      (CAN 2.0/CAN-FD)             │   │
│  │  ├── conn_ethertux_backend (EtherCAT/IGH)               │   │
│  │  ├── conn_rs485_backend    (RS-485 Half-Duplex)         │   │
│  │  ├── conn_spi_backend      (SPI/DSPI/QSPI)              │   │
│  │  ├── conn_debugpipe_backend (DEBUGFILE/PIPE unified)    │   │
│  │  └── zckb_shm.c            (Zero-Copy Kernel-Bypass)    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔧 Componentes del Núcleo

### 1. Zero-Copy Kernel-Bypass (ZCKB-Shm)

#### Archivos C Low-Level
- **`zckb_shm.h`**: Define estructuras atómicas y prototipos
  - `ZCKB_MAGIC` (0x5A434B42): Número mágico de validación
  - `zckb_ring_state_t`: Estado atómico lock-free con contadores de overflow/underflow
  - `zckb_region_t`: Región de memoria compartida con TX/RX rings
  - `zckb_context_t`: Contexto principal con gestión de múltiples regiones

- **`zckb_shm.c`**: Implementación optimizada
  - `shm_open()`, `mmap()`: Creación/mapeo de segmentos POSIX
  - `zckb_ring_push/pop()`: Operaciones lock-free con barreras atómicas
  - Optimizaciones: `__attribute__((hot))`, `optimize("O3")`, `inline`

#### Wrapper Python High-Level
- **`ring_buffer_zckb.py`**: 
  - Clase `ZCKBRingBuffer`: mmap directo, métodos push/pop sin copias
  - Clase `DualRingBuffer`: Comunicación full-duplex C ↔ Python
  
- **`memory_shm.py`**:
  - Clase `SharedMemoryManager`: Ciclo de vida de regiones compartidas
  - Limpieza automática de regiones huérfanas
  - Thread-safe con RLock

- **`ml_congestion_predictor.py`**:
  - `OnlineLinearRegressor`: Regresión lineal con gradient descent online
  - `CongestionPredictor`: Predicción de congestión basada en throughput, latency, queue depth

---

### 2. Backends de Conexión

#### Backend SPI (`conn_spi.c`)
```c
// Constantes definidas en conn_backend.h
#define CONN_TYPE_SPI 0x06

// VTable registrada en conn_manager.c
extern const conn_backend_ops_t conn_spi_backend;
```

**Características:**
- Detección automática: SPI (1 línea), DSPI (2 líneas), QSPI (4 líneas)
- Velocidad configurable: 1-50 MHz
- DMA support para transferencias grandes
- Hardware CRC opcional (CRC-16-CCITT)
- Auto-negociación de velocidad y modo

#### Backend DEBUGPIPE Unificado (`conn_debugpipe.c`)
```c
#define CONN_TYPE_DEBUGPIPE 0x07
#define CONN_TYPE_DEBUGFILE 0x04  // Legacy alias → DEBUGPIPE

extern const conn_backend_ops_t conn_debugpipe_backend;
```

**Unifica:**
- Escritura directa a FD (pipe)
- Salida a stdout para debug
- Cálculo de bit-time instantáneo (0.0)

#### Backend ZCKB-SHM (`CONN_TYPE_ZCKB_SHM = 0x08`)
- No usa backend tradicional (retorna NULL en `conn_backend_get_ops()`)
- Path especial zero-copy vía mmap directo
- Bypass completo del kernel para latencia mínima

---

## 📊 Tabla de Tipos de Conexión

| Tipo | Constante | Valor | Backend C | Descripción |
|------|-----------|-------|-----------|-------------|
| SERIAL | `CONN_TYPE_SERIAL` | 0x01 | `conn_serial_backend` | UART/USB tradicional |
| CAN | `CONN_TYPE_CAN` | 0x02 | `conn_can_backend` | CAN 2.0 / CAN-FD |
| ETHERTUX | `CONN_TYPE_ETHERTUX` | 0x03 | `conn_ethertux_backend` | EtherCAT (IGH) |
| DEBUGFILE | `CONN_TYPE_DEBUGFILE` | 0x04 | `conn_debugpipe_backend` | Legacy (alias) |
| RS485 | `CONN_TYPE_RS485` | 0x05 | `conn_rs485_backend` | RS-485 Half-Duplex |
| SPI | `CONN_TYPE_SPI` | 0x06 | `conn_spi_backend` | SPI/DSPI/QSPI High-Speed |
| DEBUGPIPE | `CONN_TYPE_DEBUGPIPE` | 0x07 | `conn_debugpipe_backend` | PIPE unificado |
| ZCKB_SHM | `CONN_TYPE_ZCKB_SHM` | 0x08 | NULL (zero-copy) | Zero-Copy Kernel-Bypass |

---

## 🔌 Sistema de Auto-Descubrimiento

### Factory Pattern (`rtcore/connections/factory.py`)

```python
class ConnectionFactory:
    def _load_connections(self):
        """Carga dinámica de todos los conn_*.py"""
        for filename in os.listdir(dirname):
            if filename.startswith("conn_") and filename.endswith(".py"):
                module = importlib.import_module(f".{filename[:-3]}", __package__)
                # Registrar clases que hereden de RTConnection
```

**Drivers disponibles:**
- `conn_serial.py`: UART/USB con auto-negociación de baudios
- `conn_can.py`: CAN con autonegociación FD/BRS/XL
- `conn_spi.py`: SPI con detección de capacidades
- `conn_pipe.py`: Pipes named para testing
- `conn_file.py`: Archivos para replay/debug

---

## ⚡ Optimizaciones de Tiempo Real

### Compilador (chelper/__init__.py)
```python
BASE_GCC_FLAGS = [
    "-O3", "-shared", "-fPIC", "-pthread",
    "-fno-semantic-interposition",  # Inlining cross-TU
    "-falign-functions=64",         # Alineación cache line
    "-Wl,-z,now",                   # No lazy binding
    "-Wl,-z,relro"                  # GOT read-only
]
```

### Perfilado por Arquitectura
- **x86_64**: AVX-512 + VPCLMULQDQ para CRC 512-bit
- **ARM64**: CRC32 + PMULL + EOR3 (SHA3)
- **RISC-V**: Zba/Zbb/Zbc (bit manipulation + CRC32 hw)

---

## 🔒 Seguridad y Robustez

### Validaciones
1. **Magic Number**: Verificación `ZCKB_MAGIC` en mapeo
2. **Version Check**: Compatibilidad `ZCKB_VERSION`
3. **Atomic Fences**: `memory_order_seq_cst` para consistencia
4. **Thread Safety**: Mutexes + RLock en Python

### Manejo de Errores
- Overflow/Underflow counters en ring buffers
- Cleanup automático de regiones huérfanas
- Timeout en apertura de regiones compartidas

---

## 📈 Métricas de Rendimiento Esperadas

| Métrica | Tradicional | ZCKB-Shm | Mejora |
|---------|-------------|----------|--------|
| Latencia ida-vuelta | ~50 µs | ~5 µs | 10x |
| Jitter (std dev) | ~10 µs | ~1 µs | 10x |
| Throughput máximo | ~1 MB/s | ~10 MB/s | 10x |
| CPU overhead | ~5% | ~0.5% | 10x |

*Ver `ZCKB_PERFORMANCE_REPORT.md` para benchmarks detallados.*

---

## 🛠️ Guía de Integración

### Añadir Nuevo Backend C

1. Crear `klippy/chelper/conn_nuevo.c`:
```c
#include "conn_manager.h"
#include "conn_backend.h"

static int nuevo_init(struct conn_manager *cm) { ... }
static int nuevo_read(struct conn_manager *cm, double eventtime) { ... }
static int nuevo_write(struct conn_manager *cm, const void *buf, int len) { ... }

const conn_backend_ops_t conn_nuevo_backend = {
    .init = nuevo_init,
    .read = nuevo_read,
    .write = nuevo_write,
    .calc_bittime = nuevo_calc_bittime,
    // ... resto de VTable
};
```

2. Definir constante en `conn_backend.h`:
```c
#define CONN_TYPE_NUEVO 0x09
extern const conn_backend_ops_t conn_nuevo_backend;
```

3. Registrar en `conn_manager.c`:
```c
case CONN_TYPE_NUEVO: return &conn_nuevo_backend;
```

4. Actualizar `chelper/__init__.py`:
```python
SOURCE_FILES = [..., 'conn_nuevo.c']
defs_conn_manager += "#define CONN_TYPE_NUEVO 0x09\n"
```

5. Crear wrapper Python en `rtcore/connections/conn_nuevo.py`

---

## 📁 Estructura de Directorios

```
klippy/
├── chelper/
│   ├── __init__.py          # Build system CFFI
│   ├── conn_backend.h       # Constantes y VTable
│   ├── conn_manager.c       # Dispatcher central
│   ├── conn_serial.c
│   ├── conn_can.c
│   ├── conn_ethertux.c
│   ├── conn_rs485.c
│   ├── conn_spi.c           # SPI High-Speed
│   ├── conn_debugpipe.c     # DEBUGFILE/PIPE unificado
│   ├── zckb_shm.h           # Zero-Copy header
│   └── zckb_shm.c           # Zero-Copy implementation
│
├── rtcore/
│   ├── connections/
│   │   ├── factory.py       # Auto-descubrimiento
│   │   ├── base.py          # Clase base RTConnection
│   │   ├── conn_serial.py
│   │   ├── conn_can.py
│   │   ├── conn_spi.py      # Driver Python SPI
│   │   └── ...
│   ├── ring_buffer_zckb.py  # Ring buffer zero-copy
│   ├── memory_shm.py        # Gestor memoria compartida
│   └── ml_congestion_predictor.py  # ML predictor
│
├── mcu_conn.py              # Integración conn_type
├── connhdl.py               # Handler constants
│
docs/
├── CONNPLUG_RT_FRAMEWORK_SPEC.md      # Este documento
├── ZCKB_PERFORMANCE_REPORT.md         # Benchmarks
├── ANALISIS_COMPLETO_CONEXIONES.md    # Análisis forense original
└── GUIA_CREACION_CONEXIONES.md        # Tutorial desarrolladores
```

---

## ✅ Checklist de Implementación

### Núcleo Zero-Copy
- [x] `zckb_shm.h` con estructuras atómicas
- [x] `zckb_shm.c` con funciones lock-free
- [x] `ring_buffer_zckb.py` con mmap directo
- [x] `memory_shm.py` con gestión de ciclo de vida
- [x] `ml_congestion_predictor.py` con regresión online

### Backends de Conexión
- [x] `conn_spi.c` completo con DMA y VTable
- [x] `conn_debugpipe.c` unificado DEBUGFILE/PIPE
- [x] `conn_spi.py` con auto-negociación
- [x] `factory.py` con auto-descubrimiento

### Integración y Build
- [x] `__init__.py` incluye todos los fuentes
- [x] `conn_backend.h` con constantes numéricas
- [x] `conn_manager.c` switch completo
- [x] Constantes exportadas vía FFI

### Documentación
- [x] Especificación técnica completa
- [x] Reporte de benchmarks
- [x] Análisis forense original
- [x] Guía para desarrolladores

---

## 🚀 Comandos de Build

```bash
# Recompilar chelper
cd /workspace/klippy/chelper
python3 __init__.py

# Ejecutar tests
cd /workspace/tests
python3 test_rtcore.py
python3 benchmark_rtcore.py
```

---

## 📞 Soporte y Contribución

Para reportar bugs o solicitar features:
1. Abrir issue en GitHub con etiqueta `connplug-rt`
2. Incluir logs de `dmesg` y `journalctl -u klipper`
3. Adjuntar configuración `printer.cfg` relevante

---

**Fin del documento**
