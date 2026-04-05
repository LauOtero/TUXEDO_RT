# Registro de Cambios TUXEDO_RT

## 2026-03-31

### Tiempo real determinista

- `klippy/rtcore/rt_core.py` añade afinidad de CPU, perfiles por plataforma, buffers residentes y telemetría circular en memoria compartida.
- `klippy/mcu.py` expone estadísticas de latencia de `trdispatch` para análisis y benchmarking desde Python.
- `klippy/chelper/trdispatch.c` sustituye sincronización basada en mutex por spinlocks ligeros y agrega acumuladores de latencia y disparos.

### Optimización numérica

- `klippy/chelper/kin_shaper.c` incorpora una ruta SIMD condicional para pares de pulsos dentro del mismo movimiento.
- `klippy/chelper/compiler.h` centraliza primitivas de spinlock, relajación de CPU y prefetched portable para Linux y Windows.
- `klippy/chelper/__init__.py` eleva el perfil de compilación a `-O3` y activa banderas opcionales de afinación nativa cuando el compilador las soporta.

### Validación

- `tests/test_rtcore.py` verifica perfiles de plataforma, anillo de latencia y memoria compartida.
- `tests/benchmark_rtcore.py` añade una carga sintética orientada al objetivo de 500 000 eventos por segundo e informa `p99`, jitter y anomalías.
- `klippy/rtcore/latency_analyzer.py` incorpora análisis textual en tiempo real sobre el buffer compartido de telemetría.
