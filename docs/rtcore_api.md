# Núcleo de Tiempo Real (RTCore) para TUXEDO_RT

El módulo `rtcore` proporciona servicios críticos de tiempo real para la variante TUXEDO_RT de Klipper. Garantiza un comportamiento determinista, comunicación de baja latencia y sincronización de alta precisión.

## Documentación del API

### Clase RealTimeCore

La interfaz principal para los servicios de tiempo real.

#### `__init__()`
Inicializa el núcleo de tiempo real, configura el registro de eventos (logging) compacto e intenta bloquear la memoria del proceso (`mlockall`) para evitar fallos de página.

#### `set_realtime_priority(priority: int = 99)`
Establece la política de planificación del hilo que realiza la llamada a `SCHED_FIFO`.
- **priority**: Nivel de urgencia (1-99). 99 es la máxima prioridad.
- **Nota**: Requiere privilegios de root o la capacidad `CAP_SYS_NICE` en Linux.

#### `map_klipper_priority(klipper_prio: int) -> int`
Mapea las prioridades internas de los temporizadores de Klipper (0-3) a niveles de `SCHED_FIFO`.
- 0 (Crítica) -> 99
- 1 (Alta) -> 90
- 2 (Normal) -> 85
- 3 (Baja) -> 80

#### `get_high_res_time() -> float`
Devuelve el tiempo monotónico de alta resolución utilizando `time.perf_counter()`.

#### `preallocate_buffer(size: int)`
Preasigna y toca un buffer residente para minimizar fallos de página bajo carga máxima.
- **size**: Tamaño del bloque en bytes.
- **Comportamiento**: En Linux conserva el beneficio de `mlockall`; en Windows intenta fijar las páginas con `VirtualLock`.

#### `set_cpu_affinity(cpus: List[int]) -> bool`
Fija afinidad de CPU para reducir migraciones entre núcleos y mejorar la consistencia temporal.
- **cpus**: Lista de núcleos lógicos permitidos.
- **Retorno**: `True` si la afinidad se aplica, `False` si la plataforma no lo permite.

#### `create_shared_latency_buffer(name="klipper_rt_latency", capacity=4096)`
Crea un buffer circular en memoria compartida para telemetría de latencia en tiempo real.
- **name**: Nombre global del segmento compartido.
- **capacity**: Número de muestras retenidas.

#### `record_latency(latency_us: float)`
Inserta una muestra de latencia en microsegundos en el anillo local y, si existe, en el buffer compartido.

#### `get_latency_stats() -> dict`
Devuelve estadísticas agregadas de latencia.
- **Campos**: `count`, `min_us`, `max_us`, `avg_us`, `jitter_us`.

#### `configure_platform_profile(profile, cpu_affinity=None, buffer_bytes=None, latency_capacity=4096) -> dict`
Aplica un perfil determinista de plataforma con afinidad, reserva de memoria y buffer circular.
- **profile**: `sbc`, `windows` o `kvm`.
- **Retorno**: Diccionario con la política de planificación, presupuesto de latencia y el estado de la afinidad aplicada.

#### `register_thread(name, target, priority=50, is_critical=False, cpu_affinity=None, prealloc_bytes=0)`
Registra un hilo para ser gestionado por el núcleo de tiempo real.
- **name**: Identificador único del hilo.
- **target**: Función a ejecutar.
- **priority**: Prioridad `SCHED_FIFO`.
- **is_critical**: Si es True, aplica automáticamente la prioridad de tiempo real al iniciar el hilo.
- **cpu_affinity**: Máscara lógica recomendada para anclar el hilo a un conjunto de núcleos.
- **prealloc_bytes**: Reserva de memoria previa al inicio del trabajo crítico.

### Clase MultiPriorityQueue

Un sistema de colas seguro para hilos (thread-safe) con 4 niveles de prioridad distintos (Crítica, Alta, Normal, Baja).

#### `enqueue(item, priority=PRIORITY_NORMAL)`
Añade un elemento al nivel de cola especificado.

#### `dequeue() -> (item, priority)`
Recupera el elemento de mayor prioridad disponible. Se bloquea hasta que haya un elemento disponible.

### Clase LatencyAnalyzer

Procesa muestras locales o compartidas para detectar jitter y violaciones de presupuesto temporal.

#### `analyze_samples(samples) -> dict`
Calcula `min_us`, `max_us`, `avg_us`, `median_us`, `p99_us`, `jitter_us`, anomalías y estado de presupuesto.

#### `read_shared_buffer(name, capacity) -> List[float]`
Lee el buffer circular de latencia publicado por `RealTimeCore`.

#### `render_text_report(samples) -> str`
Genera una vista textual compacta adecuada para herramientas de consola y supervisión continua.

## Ejemplo de Uso

```python
from rtcore.latency_analyzer import LatencyAnalyzer
from rtcore.rt_core import RealTimeCore

rt = RealTimeCore()

# Mapear una prioridad de Klipper al nivel de tiempo real
rt_prio = rt.map_klipper_priority(0)

# Establecer el hilo actual en modo tiempo real
rt.set_realtime_priority(rt_prio)

# Aplicar un perfil específico de plataforma
perfil = rt.configure_platform_profile("sbc", cpu_affinity=[1, 2, 3])

# Crear telemetría circular en memoria compartida
segmento = rt.create_shared_latency_buffer(capacity=8192)
rt.record_latency(14.2)
analizador = LatencyAnalyzer(latency_budget_us=50.0)
reporte = analizador.render_text_report(
    analizador.read_shared_buffer(segmento, 8192))

# Obtener tiempo preciso
ahora = rt.get_high_res_time()
```

## Detalles de Implementación

- **Bloqueo de Memoria**: Utiliza `mlockall(MCL_CURRENT | MCL_FUTURE)` para eliminar la latencia por fallos de página.
- **Buffers Preasignados**: Mantiene regiones residentes para evitar asignaciones en el camino crítico.
- **Afinidad de CPU**: Reduce migraciones entre núcleos tanto en SBC como en entornos virtualizados KVM.
- **Telemetría Compartida**: Publica métricas de latencia con coste constante para analizadores externos.
- **Análisis en Tiempo Real**: El analizador calcula percentiles, jitter y anomalías sin depender de herramientas externas.
- **Planificación Determinista**: Aprovecha `SCHED_FIFO` de Linux para una planificación preventiva de prioridad fija.
- **Reloj de Alta Resolución**: Proporciona precisión a nivel de nanosegundos para la sincronización del reloj.
- **Priorización Dinámica**: `serialhdl.py` escala dinámicamente la prioridad del hilo al manejar mensajes críticos como `trsync_state` o `emergency_stop`.
- **Integración con Reactor**: `reactor.py` escala automáticamente la prioridad del bucle principal cuando se registran temporizadores críticos.
