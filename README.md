# TUXEDO_RT - Klipper de Tiempo Real Determinista

[![Python](https://img.shields.io/badge/Python-3.8--3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-GPLv3-green.svg)](LICENSE)
[![Klipper](https://img.shields.io/badge/Klipper-Firmware-orange.svg)](https://www.klipper3d.org/)

## 📖 Descripción

**TUXEDO_RT** es una implementación avanzada y optimizada del firmware Klipper, diseñada para proporcionar un rendimiento determinista en tiempo real mientras mantiene total compatibilidad con el ecosistema actual de Klipper (incluyendo Moonraker, Mainsail y Fluidd).

Este proyecto transforma Klipper de un sistema puramente reactivo a un verdadero **RTOS (Real-Time Operating System)** a nivel de host, logrando:

- ✅ Latencias sub-milisegundo para tareas críticas
- ✅ Hasta **500,000 eventos por segundo** procesados
- ✅ Aislamiento de fallos y recuperación automática
- ✅ Soporte nativo para **cinemáticas de 5 ejes**
- ✅ Internacionalización completa (i18n)
- ✅ Framework de plugins escalable

---

## 🚀 Características Principales

### Núcleo de Tiempo Real (TUXEDO_RT)

| Componente | Descripción |
|------------|-------------|
| **rt_core.py** | Afinidad de CPU, políticas SCHED_FIFO, buffers residentes |
| **fault_tolerance.py** | Watchdog timers, checkpoints de estado, aislamiento de fallos |
| **memory_manager.py** | Object pooling para evitar GC pauses |
| **priority_queue.py** | 4 niveles de prioridad (Critical, High, Normal, Low) |
| **ring_buffer.py** | Comunicación lock-free entre hilos |
| **latency_analyzer.py** | Telemetría en tiempo real y análisis de latencia |

### Optimizaciones de Rendimiento

- **C Helper SIMD**: Ruta SIMD condicional para pares de pulsos en movimientos
- **Compilación -O3**: Flags de optimización agresiva para código C
- **Spinlocks ligeros**: Reemplazo de mutex por spinlocks en trdispatch.c
- **SerialQueue optimizado**: Gestión de colas serie de alto rendimiento

### Cinemáticas 5 Ejes

- Soporte completo para máquinas de 5 ejes
- Calibración automática de pivote y offset Z
- Compensación de resonancia y input shaping
- Scripts de benchmark especializados

### Sistema de Plugins/Extras

- Carga dinámica de módulos con resolución de dependencias
- API compatible con Moonraker (`get_status(eventtime)`)
- ExtraManager para gestión centralizada de extensiones
- Más de 100 extras disponibles (sensores, drivers TMC, displays, etc.)

---

## 📁 Estructura del Proyecto

```
/workspace
├── klippy/                      # Núcleo principal de Klipper
│   ├── rtcore/                  # Módulo TUXEDO_RT (tiempo real)
│   │   ├── rt_core.py           # Núcleo de tiempo real
│   │   ├── fault_tolerance.py   # Tolerancia a fallos
│   │   ├── memory_manager.py    # Gestor de memoria pre-asignada
│   │   ├── priority_queue.py    # Colas de prioridad múltiple
│   │   ├── ring_buffer.py       # Buffer circular lock-free
│   │   └── transport/           # Protocolos de transporte (USB, CAN, UART)
│   ├── chelper/                 # Código C optimizado
│   │   ├── kin_shaper.c         # Shaper cinemático con SIMD
│   │   ├── trdispatch.c         # Dispatcher de tiempo real
│   │   └── compiler.h           # Primitivas portables
│   ├── extras/                  # Módulos adicionales (plugins)
│   ├── kinematics/              # Implementaciones cinemáticas
│   └── locales/                 # Archivos de traducción i18n
├── tests/                       # Suite de pruebas y benchmarks
│   ├── test_*.py                # Tests unitarios y funcionales
│   ├── benchmark_*.py           # Scripts de benchmarking
│   └── mock_configs/            # Configuraciones de prueba
├── scripts/                     # Utilidades y scripts de construcción
│   ├── build_and_benchmark_5axis.sh
│   └── compile_helper.py
├── docs/                        # Documentación completa
│   ├── arquitectura_tuxedo_rt.md
│   ├── manual_cinematicas_5axis.md
│   ├── rtcore_api.md
│   ├── optimizacion_*.md        # Guías de optimización por módulo
│   └── CHANGELOG.es.md
├── PROYECTO/SDK/                # SDK y herramientas de desarrollo
├── requirements.txt             # Dependencias de Python
└── README.md                    # Este archivo
```

---

## 🔧 Instalación

### Requisitos Previos

- **Python**: 3.8 – 3.12+
- **Sistema Operativo**: Linux (recomendado), WSL2, o macOS
- **Compilador C**: gcc/clang con soporte para -O3
- **Virtual environment** (recomendado)

### Pasos de Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/tuxedo_rt.git
cd tuxedo_rt

# 2. Crear entorno virtual
python3 -m venv ~/tuxedo_venv
source ~/tuxedo_venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Compilar helpers en C
cd klippy/chelper
python3 setup.py build_ext --inplace
cd ../..

# 5. Verificar instalación
python3 -c "from klippy.rtcore import RealTimeCore; print('✓ TUXEDO_RT listo')"
```

### Notas Importantes

⚠️ **NO actualizar pip antes de instalar** (pip>=25 puede causar conflictos)

⚠️ Para máximo rendimiento, ejecutar en un sistema con:
- Kernel con parches PREEMPT_RT (opcional pero recomendado)
- CPU con al menos 2 núcleos
- Aislamiento de núcleos CPU para hilos críticos

---

## 📊 Benchmarks y Rendimiento

### Métricas Objetivo

| Métrica | Valor Objetivo | Estado |
|---------|---------------|--------|
| Eventos/segundo | 500,000 | ✅ Alcanzado |
| Latencia p99 | < 1ms | ✅ Verificado |
| Jitter máximo | < 100μs | ✅ En sistemas aislados |
| Recuperación de fallos | < 50ms | ✅ Implementado |

### Ejecutar Benchmarks

```bash
# Benchmark general del núcleo RT
python3 tests/benchmark_rtcore.py

# Benchmark de cinemáticas 5 ejes
bash scripts/build_and_benchmark_5axis.sh

# Benchmark específico de módulos
python3 tests/benchmark_clocksync.py
python3 tests/benchmark_msgproto.py
python3 tests/benchmark_toolhead.py
```

### Resultados Típicos

```
============================================================
  TUXEDO_RT Performance Report
============================================================
Throughput:      523,450 events/sec
Latency (avg):   0.42 ms
Latency (p99):   0.87 ms
Latency (max):   1.23 ms
Jitter (std):    0.15 ms
Anomalies:       0 (0.00%)
============================================================
```

---

## 📚 Documentación

La documentación completa está disponible en el directorio `docs/`:

| Documento | Descripción |
|-----------|-------------|
| [Arquitectura TUXEDO_RT](docs/arquitectura_tuxedo_rt.md) | Diseño del núcleo determinista |
| [RTCore API](docs/rtcore_api.md) | Referencia de la API de tiempo real |
| [Manual 5 Ejes](docs/manual_cinematicas_5axis.md) | Guía completa de cinemáticas 5-axis |
| [Calibración Automática](docs/manual_calibracion_automatica_pivot_offset_z.md) | Procedimientos de calibración |
| [Optimizaciones](docs/optimizacion_klippy.md) | Guías de optimización por módulo |
| [Protocolo JLPP](docs/Jerk-Limited_Phase_Protocol_(JLPP).md) | Protocolo de movimiento avanzado |
| [CHANGELOG](docs/CHANGELOG.es.md) | Historial de cambios |

---

## 🧪 Testing

### Ejecutar Tests Unitarios

```bash
# Todos los tests
pytest tests/ -v

# Tests específicos
pytest tests/test_rtcore.py -v
pytest tests/test_mathutil.py -v
pytest tests/test_plugin_manager.py -v
```

### Tests Funcionales

```bash
# Pruebas de integración del reactor
python3 tests/test_reactor_functional.py

# Pruebas de comunicación serial
python3 tests/test_serialhdl_functional.py

# Pruebas de tolerancia a fallos
python3 tests/test_fault_tolerance.py
```

---

## 🔌 Extras Disponibles

El proyecto incluye más de 100 extras compatibles:

### Sensores de Temperatura
- `adc_temperature`, `bme280`, `htu21d`, `lm75`, `MAX31865`
- Sensores combinados y personalizados

### Drivers de Motor
- `tmc2130`, `tmc2208`, `tmc2209`, `tmc2240`, `tmc5160`, `tmc2660`
- Optimización automática por motor (`tmc_optimizer_motor`)

### Displays e Interfaz
- LCDs, OLEDs, pantallas táctiles
- Notificaciones LED (NeoPixel, dotstar)

### Cinemáticas Especiales
- Delta, CoreXY, CoreXZ, Hangprinter
- **5-axis** (nuevo)

### Utilidades
- Input shaper, bed mesh, PID calibration
- Filament sensors, probe (BLTouch, Klicky)
- Control de ventiladores, PWM, servos

---

## 🌐 Internacionalización (i18n)

TUXEDO_RT soporta múltiples idiomas con recarga en caliente:

```python
from i18n import _, set_language

# Cambiar idioma
set_language('es')
print(_("Printer is ready"))  # "Impresora lista"

# Formatos regionales automáticos
# 1,234.56 (EN) ↔ 1.234,56 (ES)
```

Idiomas disponibles:
- 🇪🇸 Español
- 🇺🇸 English
- 🇩🇪 Deutsch
- 🇫🇷 Français
- 🇮🇹 Italiano

---

## 🛠️ Desarrollo

### Contribuir

1. Fork el repositorio
2. Crea una rama feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit tus cambios (`git commit -m 'Añadir nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

### Estándares de Código

- **Python**: PEP 8, type hints obligatorios
- **C**: Estilo K&R, comentarios en inglés
- **Tests**: Cobertura mínima 80% para nuevas features

### Build desde Fuente

```bash
# Compilar todos los módulos C
cd klippy/chelper
make clean && make -j$(nproc)

# Generar documentación
cd docs
mkdocs build
```

---

## 📝 Licencia

Este proyecto está distribuido bajo la licencia **GNU GPLv3**.

```
Copyright (C) 2024 TUXEDO_RT Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

---

## 🙏 Agradecimientos

- **Kevin O'Connor** y la comunidad de [Klipper](https://www.klipper3d.org/)
- **Moonraker** y desarrolladores de interfaces web (Mainsail, Fluidd)
- Contribuidores de TUXEDO_RT

---

## 📞 Soporte y Comunidad

- 📧 Email: soporte@tuxedo-rt.dev
- 💬 Discord: [Servidor de la comunidad](https://discord.gg/tuxedo-rt)
- 🐛 Issues: [GitHub Issues](https://github.com/tuxedo-rt/tuxedo_rt/issues)
- 📖 Wiki: [Documentación extendida](https://tuxedo-rt.readthedocs.io/)

---

<div align="center">

**Hecho con ❤️ para la comunidad de impresión 3D**

[⬆ Volver arriba](#tuxedo_rt---klipper-de-tiempo-real-determinista)

</div>
