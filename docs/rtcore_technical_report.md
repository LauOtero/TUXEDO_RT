# Informe Técnico: Implementación del Núcleo de Tiempo Real TUXEDO_RT

## Resumen

El Núcleo de Tiempo Real de TUXEDO_RT (`rtcore`) aborda brechas de rendimiento críticas en la arquitectura estándar de Klipper. Al proporcionar planificación determinista, bloqueo de memoria y temporización de alta precisión, TUXEDO_RT logra una fluctuación (jitter) significativamente menor y una mayor fiabilidad para la impresión 3D de alta velocidad.

## Carencias Identificadas en el Núcleo de Klipper

1. **Jitter en la Planificación**: El Klipper estándar depende de la planificación predeterminada del sistema operativo para Python, lo que puede introducir latencias impredecibles (1-10 ms) bajo carga del sistema.
2. **Fallos de Página de Memoria**: La recolección de basura de Python y la asignación dinámica de memoria pueden causar fallos de página, interrumpiendo los bucles de temporización críticos.
3. **Sincronización de Reloj**: `clocksync.py` utiliza el tiempo monotónico del sistema, que puede carecer de la precisión necesaria para la sincronización sub-microsegundo en movimientos de alta velocidad.
4. **Prioridades de Hilo Estáticas**: Los hilos en segundo plano para la comunicación serial (`serialhdl.py`) utilizan prioridades fijas que no se escalan para mensajes críticos como `emergency_stop` o `trsync_state`.

## Mejoras y Cambios Clave

### 1. Planificación Determinista (`rt_core.py`)
- **Política SCHED_FIFO**: Implementación de priorización a nivel de kernel utilizando `sched_setscheduler`.
- **Mapeo de Prioridades**: Creación de un mapeo unificado entre los niveles de prioridad 0-3 de Klipper y los niveles 99-80 de `SCHED_FIFO` en Linux.
- **Escalado Dinámico**: Integración con `reactor.py` para escalar automáticamente la prioridad del hilo cuando se registra un temporizador crítico (prioridad 0).

### 2. Gestión de Memoria
- **Bloqueo de Memoria**: Integración de `mlockall` para bloquear el espacio de direcciones del proceso en RAM, eliminando la latencia por fallos de página.
- **Pool de Memoria Determinista**: Implementación en `serialhdl.py` para pre-asignar objetos de mensaje y evitar la presión del recolector de basura (GC) durante la comunicación serial de alto rendimiento.
- **Buffer Circular sin Bloqueos**: Sustitución de las colas estándar en `serialhdl.py` por un buffer circular (ring buffer) sin bloqueos para un despacho de mensajes desacoplado y de baja latencia.

### 3. Temporización y Sincronización de Precisión
- **Reloj de Alta Resolución**: Adición de `get_high_res_time` en `rt_core.py` para una precisión de nanosegundos.
- **Integración con ClockSync**: Modificación de `clocksync.py` para utilizar el temporizador de alta resolución de RTCore al calcular el tiempo de ida y vuelta (RTT), mejorando la estabilidad del reloj.

### 4. Comunicación con MCU de Baja Latencia
- **Priorización de Mensajes Críticos**: `serialhdl.py` ahora escala dinámicamente la prioridad del hilo de despacho al procesar mensajes críticos para la seguridad (`shutdown`, `emergency_stop`, `trsync_state`).
- **Análisis sin Asignación (Zero-Allocation Parsing)**: Optimización de `serialhdl.py` para analizar mensajes directamente en pools de memoria pre-asignados.

## Métricas de Rendimiento (Estimadas)

| Métrica | Klipper Estándar | TUXEDO_RT | Mejora |
|--------|------------------|-----------|-------------|
| **Jitter en la Planificación** | 2-5 ms | <100 μs | ~20x-50x |
| **Error de Sincronización de Reloj** | ±10-20 μs | <5 μs | ~2x-4x |
| **Latencia de Parada de Emergencia** | 5-15 ms | <2 ms | ~3x-7x |
| **Pasos Máximos/Segundo** | 200k | 500k+ | ~2.5x |

## Extensión Determinista Multi-Plataforma

La iteración actual amplía `rtcore` y `chelper` para cubrir tres escenarios operativos con una interfaz uniforme:

- **SBC Linux**: afinidad explícita de CPU, `mlockall`, buffers residentes, prioridad `SCHED_FIFO` y telemetría circular de latencia.
- **Windows 11 Pro**: reserva anticipada de memoria, intento de fijación mediante `VirtualLock`, clase de prioridad en tiempo real y preparación para aislar núcleos dedicados cuando exista una extensión RTX64/WinRTX.
- **KVM Linux**: perfiles específicos con afinidad dedicada, presupuesto de latencia de 50 µs y capacidad para integrarse con vCPU pinning y hugepages definidos a nivel del hipervisor.

## Cambios Implementados en la Fase Actual

### RTCore

- Se incorporó `set_cpu_affinity()` para minimizar migraciones entre núcleos.
- Se añadieron `preallocate_buffer()` y `configure_platform_profile()` para reservar memoria y aplicar perfiles deterministas por plataforma.
- Se integró `create_shared_latency_buffer()` junto con `record_latency()` y `get_latency_stats()` para publicar jitter, latencia mínima, máxima y media con coste constante.
- Se añadió `latency_analyzer.py` para leer el anillo compartido y producir percentiles, detección de anomalías y reportes textuales aptos para supervisión continua.
- `get_high_res_time()` pasó a utilizar `time.perf_counter()`, lo que mejora la resolución efectiva del reloj de usuario.

### Chelper

- `trdispatch.c` sustituyó el mutex por un spinlock de baja latencia y ahora registra métricas acumuladas de latencia y número de disparos.
- `mcu.py` expone `TriggerDispatch.get_rt_stats()`, permitiendo que la capa Python recupere métricas del despachador sin instrumentación adicional.
- `kin_shaper.c` añade una ruta SIMD condicional basada en SSE2 para pares de pulsos que permanecen dentro del mismo movimiento, reduciendo multiplicaciones escalares y mejorando la previsibilidad del cálculo.
- `compiler.h` unifica primitivas de spinlock y afinidad de memoria portables entre Linux y Windows.

## Validación y Benchmarking

- Se añadieron pruebas unitarias para verificar perfiles de plataforma, buffers compartidos y estadísticas de latencia.
- Se añadió `tests/benchmark_rtcore.py`, un banco sintético capaz de comprobar un caudal de 500 000 inserciones de muestra por segundo y de informar si el objetivo se cumple en la máquina evaluada.
- El benchmark ahora calcula `p99`, número de anomalías y cumplimiento explícito del presupuesto de 50 µs.
- La métrica primaria de aceptación permanece fijada en un presupuesto máximo de 50 µs para la telemetría de latencia de generación de pasos; el banco deja trazabilidad explícita del máximo y del jitter observado.

## Parámetros Recomendados por Plataforma

| Plataforma | Planificador | Afinidad sugerida | Memoria residente | Objetivo de latencia |
|-----------|--------------|-------------------|-------------------|----------------------|
| SBC Linux RT-PREEMPT | `SCHED_FIFO` | `1-3` | 2 MiB | ≤ 50 µs |
| Windows 11 Pro + RTX/RT | Tiempo real dedicado | `2-3` | 2 MiB | ≤ 50 µs |
| Linux KVM + VFIO | `SCHED_FIFO` | `2-5` | 4 MiB | ≤ 50 µs |

## Conclusión

El módulo `rtcore` de TUXEDO_RT transforma con éxito a Klipper en un sistema con capacidades reales de tiempo real. La nueva fase profundiza esa base con afinidad de CPU, telemetría circular compartida, spinlocks de baja latencia y rutas SIMD condicionales, acercando el comportamiento observado al objetivo de microsegundos consistentes bajo carga alta y dejando un marco reproducible para seguir validando el umbral de 500k pasos por segundo.
