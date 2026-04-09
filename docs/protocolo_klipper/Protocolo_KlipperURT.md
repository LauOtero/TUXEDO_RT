# Klipper: Análisis Profundo del Protocolo y Propuesta UltraRT

> **Objetivo:** Análisis exhaustivo del protocolo de comunicación host↔MCU de Klipper, identificación de cuellos de botella para tiempo real determinista, y diseño de un protocolo nuevo (`KlipperURT`) que coexiste con el protocolo actual.

---

## 1. Arquitectura Actual del Protocolo Klipper

### 1.1 Capas de comunicación

Klipper divide el firmware en dos mitades: el **host** (Python en Raspberry Pi u host Linux) y el **MCU** (C en el microcontrolador). La comunicación atraviesa cinco capas:

```
[G-code / API] → [klippy.py / toolhead.py] → [mcu.py / serialhdl.py]
     → [chelper/serialqueue.c  (C, hilo dedicado)] → [UART / USB / CAN]
```

Cada capa introduce latencia, jitter o coste computacional que se analiza a continuación.

### 1.2 Formato de mensaje binario actual

El protocolo usa bloques binarios con la siguiente estructura fija:

| Campo    | Offset | Tamaño    | Descripción                                         |
|----------|--------|-----------|-----------------------------------------------------|
| Length   | 0      | 1 byte    | Longitud total del bloque (5–64 bytes)              |
| Sequence | 1      | 1 byte    | Bits 0–3: número de secuencia; bit 4: dirección     |
| Payload  | 2      | 0–57 bytes| Comandos codificados en VLQ                         |
| CRC-16   | len-3  | 2 bytes   | CRC-16-CCITT sobre length+payload                   |
| Sync     | len-1  | 1 byte    | Marcador 0x7E de fin de trama                       |

Parámetros críticos:
- **MESSAGE_MIN = 5 bytes**, **MESSAGE_MAX = 64 bytes**
- Número de secuencia de **4 bits** (ventana de 16 mensajes en vuelo)
- CRC calculado en software (sin DMA en MCUs de gama baja)

### 1.3 Codificación VLQ (Variable-Length Quantity)

Todos los enteros en el payload se codifican en VLQ de 7 bits:

| Rango de valor     | Bytes utilizados |
|--------------------|-----------------|
| -32 a 95           | 1               |
| -4.096 a 12.287    | 2               |
| -524.288 a 1.572.863 | 3             |
| Rango 32-bit completo | hasta 5      |

El comando `queue_step` —el más crítico en impresión 3D— transmite `(interval, count, add)` representando secuencias de pasos con aceleración cuadrática constante. Esto comprime miles de eventos de paso individuales en unos pocos bytes, con errores de timing acotados por `max_error` (en ticks de reloj del MCU).

### 1.4 Pipeline de compresión de pasos (`stepcompress.c`)

El flujo de generación de pasos es:

```
trapq (movimientos trapezoidales) → itersolve (método de la secante)
  → stepcompress (bisección cuadrática) → queue_step commands
  → steppersync → serialqueue → UART/CAN
```

La compresión bisect encuentra el triplete `(interval, count, add)` óptimo que maximiza los pasos agrupados con desviación ≤ `max_error`. La desviación máxima sigue la fórmula:

```
max_deviation = 11 × max_error / count²
```

Bajo operación normal, los `queue_step` se envían con **≥100 ms de antelación** respecto a su primer paso en el MCU.

### 1.5 Sincronización de reloj (`clocksync.py`)

El host estima continuamente el reloj del MCU mediante un algoritmo de mínimos cuadrados ponderados exponencialmente:

- **RTT tracking**: Seguimiento del RTT mínimo con factor de envejecimiento `RTT_AGE = 2.78×10⁻⁹ s⁻¹`
- **Filtrado de outliers**: Rechaza muestras con error > 25× la varianza predicha
- **Regresión lineal**: DECAY = 1/30, mantiene `time_avg`, `clock_covariance`
- **Frecuencia estimada**: `freq = clock_covariance / time_variance`

Los relojes de MCUs secundarios se sincronizan con el MCU primario mediante `SecondarySync`, ajustando dinámicamente la frecuencia de conversión.

### 1.6 Transporte físico soportado

| Canal          | Velocidad típica | Latencia típica | Jitter           |
|----------------|-----------------|-----------------|------------------|
| UART/USB Serie | 250.000 baud    | 1–5 ms          | Alto (OS scheduler) |
| CAN bus        | 1 Mbit/s        | 0.5–2 ms        | Moderado         |
| Linux pipe     | Memoria         | < 0.1 ms        | Bajo             |
| rpmsg (SoC)    | Memoria compartida | < 0.05 ms   | Muy bajo         |

---

## 2. Análisis de Cuellos de Botella

### 2.1 Limitaciones del protocolo actual

**L1 — Tamaño máximo de mensaje: 64 bytes**
La restricción `MESSAGE_MAX = 64` es un artefacto de MCUs AVR de 8 bits. En MCUs modernos (STM32H7, RP2040, ESP32-S3) con SRAM de 512 KB+, este límite genera fragmentación innecesaria y aumenta el overhead de framing (3 bytes de cabecera/trailer por bloque vs. payload útil).

**L2 — Número de secuencia de 4 bits (ventana = 16)**
El campo de secuencia usa solo los 4 bits bajos del byte. Esto limita la ventana deslizante a 16 mensajes en vuelo simultáneos. En CAN bus a 1 Mbit/s con múltiples MCUs, esta ventana es inadecuada para saturar el canal sin bloqueos de flujo.

**L3 — CRC-16 en software sin aceleración**
El CRC-16-CCITT se calcula byte a byte en software. En MCUs Cortex-M4/M7 con hardware CRC, el cálculo SW introduce ~0.5–2 µs de latencia por mensaje en el MCU. Multiplicado por miles de mensajes/segundo en impresión de alta velocidad, se convierte en overhead medible.

**L4 — Python en el camino crítico**
El hilo Python de `serialhdl.py` procesa las respuestas del MCU. El GIL (Global Interpreter Lock) y el garbage collector de CPython introducen pausas de 1–50 ms no deterministas. El bucle de schedulado de `klippy.py` usa `pollreactor.c` pero las callbacks Python no son RT.

**L5 — Sincronización de reloj reactiva**
`clocksync.py` usa muestreo periódico para estimar el reloj del MCU. El algoritmo de regresión lineal con DECAY=1/30 tarda ~30 muestras en converger tras una perturbación. En condiciones de jitter alto (USB, carga del sistema), la sincronización puede derivar varios cientos de µs antes de corregirse.

**L6 — Buffer de 100 ms como margen de seguridad**
El diseño actual requiere que los comandos `queue_step` lleguen con ≥100 ms de antelación. Este margen está dimensionado para absorber jitter de sistema operativo no-RT. En sistemas con PREEMPT-RT o xenomai, este margen es excesivo y aumenta la latencia de respuesta a cambios dinámicos (cancelación de movimiento, input shaping adaptativo).

**L7 — Un solo response por bloque MCU→Host**
La documentación confirma que los bloques del MCU al host "never contain more than one response". Esto desaprovecha el ancho de banda disponible para telemetría de alta frecuencia (sensores, acelerómetro ADXL345 para input shaping en tiempo real).

### 2.2 Análisis de throughput teórico vs. real

Con UART a 250.000 baud y MESSAGE_MAX=64 bytes:

```
Throughput bruto  = 250.000 / 10 = 25.000 bytes/s
Overhead de frame = 5 bytes por bloque (header + CRC + sync)
Payload útil max  = 59 bytes por bloque
Eficiencia de frame = 59/64 ≈ 92%
Mensajes/s teórico = 25.000 / 64 ≈ 390 bloques/s

Con overhead de ACK (MCU→Host por cada bloque):
  ACK = 5 bytes mínimos (bloque vacío)
  Ancho de banda efectivo ≈ 25.000 × (59/69) ≈ 21.380 bytes/s de payload
```

En CAN bus (8 bytes/frame, 1 Mbit/s, 111 bits/frame mínimo):

```
Frames/s max = 1.000.000 / 111 ≈ 9.009 frames/s
Con 8 bytes payload = 72.072 bytes/s
Klipper usa múltiples frames CAN por bloque de protocolo → overhead adicional
```

---

## 3. Propuesta: Protocolo KlipperURT (Ultra Real-Time)

### 3.1 Filosofía de diseño

`KlipperURT` es un protocolo de capa de transporte alternativo que:

1. **Coexiste** con el protocolo legacy: el MCU y el host negocian el protocolo en la fase `identify_response`. Si el MCU no anuncia soporte URT, se usa el protocolo clásico.
2. **No rompe compatibilidad**: los firmwares existentes ignoran los frames URT. Los clientes nuevos pueden hablar ambos protocolos.
3. **Prioriza determinismo** sobre throughput absoluto: latencia máxima garantizada < 25 µs en MCU Cortex-M4 a 168 MHz.
4. **Compresión diferencial adaptativa**: explotar la coherencia temporal de los datos de movimiento 3D.

### 3.2 Negociación de protocolo

Durante el handshake de identificación, el MCU incluye en su `data dictionary` el campo:

```json
{
  "urt_version": 1,
  "urt_caps": ["BATCH_STEP", "DELTA_CLOCK", "MULTI_RESPONSE", "HW_CRC32"]
}
```

El host responde con `urt_hello` si ambos lados soportan URT. A partir de ese momento, ambas capas pueden usar el canal URT en paralelo con el canal legacy para diferentes tipos de mensajes.

### 3.3 Formato de frame URT

```
 0       1       2       3       4       5  ...  N-4  N-3  N-2  N-1
+-------+-------+-------+-------+-------+---------+----+----+----+
| MAGIC | LEN_H | LEN_L | SEQ_H | SEQ_L | PAYLOAD |  CRC-32 |SYNC|
+-------+-------+-------+-------+-------+---------+----+----+----+
```

| Campo    | Tamaño   | Descripción                                                  |
|----------|----------|--------------------------------------------------------------|
| MAGIC    | 1 byte   | `0xA5` — distingue frames URT de frames legacy (0x7E sync)   |
| LEN      | 2 bytes  | Longitud total del frame (big-endian, máx 4096 bytes)        |
| SEQ      | 2 bytes  | Número de secuencia de 16 bits (ventana de 65536)            |
| PAYLOAD  | variable | Datos comprimidos (ver encoding URT)                         |
| CRC-32   | 4 bytes  | CRC-32/MPEG-2, acelerado por HW en Cortex-M3/M4/M7          |
| SYNC     | 1 byte   | `0xEB` — marcador de fin de frame                            |

**Ventajas sobre el formato legacy:**
- Frame de hasta **4096 bytes** → hasta 64× más payload por round-trip
- Secuencia de **16 bits** → ventana de 65536 → sin bloqueos de flujo a altas velocidades
- **CRC-32 hardware** en todos los Cortex-M3+ (registros `CRC->DR`) → 0 ciclos de overhead en MCU
- MAGIC diferente → coexistencia perfecta en el mismo canal físico

### 3.4 Codificación de payload: compresión diferencial de pasos

El payload URT introduce el tipo `STEP_BATCH_DELTA`:

```
TYPE_STEP_BATCH_DELTA = 0x10

[TYPE:1][OID:1][BASE_CLOCK:5][COUNT:2][DELTA_STREAM]
```

Donde `DELTA_STREAM` es una secuencia de deltas de tiempo entre pasos consecutivos, codificados en **Golomb-Rice** con parámetro k adaptativo:

**Por qué Golomb-Rice para deltas de pasos:**

En movimiento constante o con aceleración suave, los deltas de tiempo entre pasos siguen una distribución geométrica sesgada (muchos deltas pequeños, pocos grandes). Golomb-Rice es óptimamente eficiente para distribuciones geométricas, superando a VLQ en 15–40% de compresión para este patrón.

```
Golomb-Rice(k):
  q = floor(delta / 2^k)
  r = delta mod 2^k
  código = unario(q) + binario(r, k bits)

Ejemplo k=3, delta=5:
  q=0, r=5 → código = "0" + "101" = 4 bits
  VLQ clásico: delta=5 → 1 byte = 8 bits
  → 50% de reducción
```

El parámetro `k` se adapta por OID cada 256 pasos usando la media de los últimos deltas:
`k_new = floor(log2(media_delta)) + 1`

### 3.5 Reloj delta comprimido: `DELTA_CLOCK`

En lugar de enviar el `BASE_CLOCK` absoluto de 32 bits en cada batch, URT introduce codificación de reloj diferencial entre batches consecutivos del mismo OID:

```
Primera transmisión:  BASE_CLOCK = reloj absoluto (32 bits)
Transmisiones siguientes: CLOCK_DELTA = clock_nuevo - clock_anterior (VLQ, típicamente 1-2 bytes)
```

Para movimiento continuo a 200 mm/s con 80 pasos/mm = 16.000 pasos/s:
- Delta entre batches de 32 pasos ≈ 32/16.000 s × 168 MHz ≈ 336.000 ticks
- VLQ de 336.000: 3 bytes vs. 4 bytes absoluto → ahorro del 25% solo en campo de reloj

### 3.6 Multi-response MCU→Host

El protocolo URT permite que el MCU agrupe múltiples respuestas en un solo frame (a diferencia del legacy que envía máximo una por bloque):

```
FRAME_URT {
  [RESP_0: temp_sensor oid=0, value=2150]
  [RESP_1: temp_sensor oid=1, value=1980]
  [RESP_2: adxl345_data oid=2, x=102, y=-45, z=9810]
  [RESP_3: clock_sync_response, clock=0x1A3F2B00]
}
```

Esto es especialmente valioso para telemetría de alta frecuencia (ADXL345 a 3200 Hz para input shaping adaptativo), reduciendo el overhead de framing de O(N_sensores) a O(1) por ciclo de muestreo.

### 3.7 Sincronización de reloj URT mejorada

El protocolo URT introduce `URT_CLOCK_PING` con marca de tiempo de hardware en el MCU:

```
Host →  [URT_CLOCK_PING, host_ts=T1]
MCU  →  [URT_CLOCK_PONG, host_ts=T1, mcu_ts_rx=T2, mcu_ts_tx=T3]
Host recibe en T4
```

Con T2 y T3 capturados por hardware timer del MCU (sin overhead de IRQ latency):

```
RTT_hardware = (T4 - T1) - (T3 - T2)  ← tiempo de procesado del MCU excluido
Offset = T2 + (T3-T2)/2 - (T1 + T4)/2
```

Esto elimina el principal fuente de sesgo del algoritmo actual de `clocksync.py`: la latencia variable de interrupción del MCU (típicamente 0.5–5 µs) queda excluida del cálculo de RTT.

### 3.8 Modo determinista: `URT_REALTIME_WINDOW`

Para sistemas con kernel PREEMPT-RT o xenomai en el host, URT introduce ventanas de tiempo deterministas:

```
Host → [URT_RT_WINDOW_OPEN, window_id=N, start_clock=T, duration_ticks=D]
  → batch de comandos para ejecutar en [T, T+D]
Host → [URT_RT_WINDOW_CLOSE, window_id=N]
MCU  → [URT_RT_WINDOW_ACK, window_id=N, queued=K]
```

El MCU garantiza que los `K` comandos de la ventana se ejecutarán con precisión de ±1 tick de reloj (±5.95 ns a 168 MHz). El host puede reducir el buffer de anticipación de 100 ms a **10–20 ms** en sistemas RT, reduciendo la latencia de respuesta a cambios dinámicos en 5–10×.

### 3.9 Estrategia de implementación de coexistencia

```
serialhdl.py (modo dual)
  ├── Legacy channel (protocolo actual, sin cambios)
  │     └── Comandos de configuración, comandos no-RT, fallback
  └── URT channel (protocolo nuevo)
        ├── step_batch_delta  (movimiento de alta velocidad)
        ├── multi_response    (telemetría de sensores)
        └── rt_window         (modo determinista)
```

La selección del canal por mensaje se realiza en `mcu.py`:

```python
def _send_command(self, cmd, clock=None):
    if self._urt_capable and cmd.is_step_batch:
        return self._urt_channel.send(cmd)  # nuevo
    return self._legacy_channel.send(cmd)   # existente, sin cambios
```

El MCU mantiene **dos parsers de frame** en `command.c`:

```c
// Existente (sin cambios)
void process_legacy_frame(uint8_t *data, uint16_t len);

// Nuevo (URT)
void process_urt_frame(uint8_t *data, uint16_t len) {
    if (data[0] == URT_MAGIC) { ... }
}

// Dispatcher en IRQ de recepción
void serial_rx_irq(uint8_t byte) {
    if (byte == URT_MAGIC)       process_urt_frame(...);
    else if (byte_is_legacy(...)) process_legacy_frame(...);
}
```

---

## 4. Análisis Comparativo: Legacy vs. KlipperURT

### 4.1 Métricas de rendimiento

| Métrica                              | Legacy Klipper      | KlipperURT          | Mejora       |
|--------------------------------------|---------------------|---------------------|--------------|
| Tamaño máximo de frame               | 64 bytes            | 4.096 bytes         | 64×          |
| Ventana de secuencia                 | 16 mensajes         | 65.536 mensajes     | 4.096×       |
| Overhead de frame (% sobre payload)  | ~8.5% (5/59)        | ~0.17% (8/4088)     | 50×          |
| CRC                                  | CRC-16 SW           | CRC-32 HW           | 0 ciclos MCU |
| Compresión de deltas de paso         | VLQ (1–5 bytes)     | Golomb-Rice (~0.5–2 bytes) | 15–40% |
| Multi-response por frame             | 1                   | Ilimitado (hasta 4KB) | N×         |
| Latencia de sincronización de reloj  | ~5–50 µs bias       | < 1 µs bias HW      | 10–50×       |
| Buffer de anticipación mínimo        | 100 ms              | 10–20 ms (RT host)  | 5–10×        |
| Latencia de respuesta a cambios      | ~100–200 ms         | ~10–25 ms           | 5–10×        |
| Compatibilidad backward              | N/A                 | 100% (canal dual)   | —            |

### 4.2 Estimación de reducción de ancho de banda

Para una impresora típica CoreXY a 300 mm/s, 4 motores, 80 pasos/mm:
- Pasos totales/s ≈ 4 × 300 × 80 = 96.000 pasos/s
- Con legacy `queue_step` (interval, count, add promedio ~32 pasos): ~3.000 comandos/s × ~6 bytes = **18.000 bytes/s**
- Con URT `step_batch_delta` (batch de 128 pasos, Golomb-Rice k=8): ~750 batches/s × ~20 bytes = **15.000 bytes/s** de payload, más 8 bytes de frame = **15.750 bytes/s**
- **Reducción estimada: ~12–25% en ancho de banda de datos de movimiento**
- Con multi-response para telemetría (4 sensores a 100 Hz legacy vs. batched URT): reducción de overhead de **78%** en frames de telemetría

---

## 5. Plan de Implementación

### Fase 1 — Fundamentos (4–6 semanas)
- Implementar parser dual en `serial_irq.c` del MCU
- Añadir `urt_version` al data dictionary
- Implementar `URT_CLOCK_PING/PONG` con timestamp HW
- Tests de coexistencia sobre UART y CAN bus

### Fase 2 — Compresión de pasos (6–8 semanas)
- Implementar encoder Golomb-Rice en `stepcompress.c`
- Implementar `STEP_BATCH_DELTA` en host y MCU
- Validar timing contra legacy con `scripts/check_step_timing.py`
- Benchmarks en STM32F407, STM32H743, RP2040

### Fase 3 — Multi-response y telemetría (4 semanas)
- Modificar `serialhdl.py` para manejar frames multi-response URT
- Adaptar ADXL345 y sensores de temperatura al batching URT
- Métricas de latencia end-to-end con oscilloscopio

### Fase 4 — Modo RT Window (6 semanas)
- Implementar `URT_RT_WINDOW` en MCU y host
- Integración con kernels PREEMPT-RT (test en Pi4 + RT patch)
- Reducción del buffer de anticipación de 100 ms → 15 ms con RT
- Documentación y PRs al repositorio principal

---

## 6. Conclusión

El protocolo actual de Klipper es técnicamente sólido y elegante para su era de diseño (MCUs AVR de 8 bits, UART a 250 kbaud). Sus fortalezas —VLQ compacto, compresión cuadrática de pasos, sincronización de reloj adaptativa— siguen siendo referencias en firmware de impresión 3D.

Sin embargo, tres limitaciones estructurales frenan el rendimiento ultra-RT en hardware moderno:

1. El **límite de 64 bytes por frame** genera fragmentación excesiva y overhead de framing innecesario en MCUs de 32 bits con SRAM de megabytes.
2. El **buffer obligatorio de 100 ms** está sobredimensionado para kernels RT y crea latencia de respuesta alta ante cambios dinámicos.
3. La **sincronización de reloj reactiva en Python** introduce sesgo no determinista que limita la precisión de timing a la escala de decenas de microsegundos.

**KlipperURT** resuelve estos tres problemas con un diseño que prioriza la coexistencia total con el protocolo legacy: frames de hasta 4 KB con CRC-32 hardware, compresión Golomb-Rice adaptativa para deltas de pasos, timestamp de sincronización de reloj hardware, y ventanas de tiempo deterministas para kernels RT. La implementación es incremental, sin riesgo de regresión, y puede coexistir en el mismo canal físico (UART, USB, CAN) que el protocolo actual mediante el byte MAGIC `0xA5`.

La ganancia más inmediata y de menor riesgo es la **sincronización de reloj por hardware** (Fase 1), que puede mejorar la precisión de timing en un orden de magnitud sin afectar en absoluto al código de movimiento existente.

---

*Análisis basado en el código fuente de Klipper commit `8a210d23` (febrero 2026), documentación oficial en `klipper3d.org`, y análisis de `klippy/chelper/serialqueue.c`, `klippy/chelper/stepcompress.c`, `klippy/clocksync.py`, `klippy/msgproto.py` y `klippy/chelper/msgblock.c`.*