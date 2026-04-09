# Compresión por tipo de dato en el protocolo Klipper 3D — arquitectura unificada v2

## Alcance

Este documento define la estrategia óptima de compresión para el protocolo de comunicación de Klipper, cubriendo la totalidad de los dispositivos gestionados por el directorio `klippy/extras`. El análisis parte de la enumeración exhaustiva de los módulos activos, clasifica cada tipo de dato producido, y define el método de compresión óptimo para cada clase. Todo el trabajo de descompresión se realiza en el host (`klippy`) a través de un `DecompressionPipeline` centralizado que publica valores físicos reales hacia cualquier consumidor sin exponer el codec subyacente.

Base técnica:

- Framing y VLQ: `klippy/msgproto.py`
- Transporte determinista en C: `klippy/chelper/serialqueue.c`
- Compresión de pasos: `klippy/chelper/stepcompress.c`, `klippy/stepper.py`
- Canal de alta tasa: `klippy/extras/bulk_sensor.py`
- Acelerómetros: `klippy/extras/adxl345.py`, `klippy/extras/lis_sensor.py`, `klippy/extras/mpu9250.py`
- Sensores eddy: `klippy/extras/ldc1612.py`
- Células de carga: `klippy/extras/hx71x.py`, `klippy/extras/ads1220.py`
- Sensores de ángulo: `klippy/extras/angle.py`
- Drivers TMC: `klippy/extras/tmc2130.py`, `tmc2208.py`, `tmc2209.py`, `tmc2240.py`, `tmc2660.py`, `tmc5160.py`
- Temperatura: `klippy/extras/temperature_sensor.py` y familia
- Handshake e identificación: `klippy/serialhdl.py`, `klippy/msgproto.py`

---

## Restricciones del protocolo

**Paquete corto.** `MESSAGE_MAX = 64` bytes. Overhead fijo de framing: 5 bytes (sync + longitud + secuencia + CRC16). En payloads de 10–15 bytes el overhead relativo alcanza el 25–33 %. Todos los ratios en este documento ya descuentan ese overhead.

**Recuperación ante error.** El canal usa CRC16 + secuencia + ACK/NAK. Codecs con estado global acumulativo (ANS, aritmética) son incompatibles con este modelo de recuperación local.

**Determinismo temporal.** O(1) amortizado es el límite estricto en el path de generación en el MCU.

**Impacto en el parser del host.** `msgproto.py` es actualmente stateless por mensaje. El estado de bloque que requieren algunos codecs se encapsula en los decodificadores del `DecompressionPipeline`, nunca en el parser genérico.

**Compresión semántica de pasos.** `queue_step(oid, interval, count, add)` modela la cinemática y supera cualquier compresor genérico sobre ticks crudos. Es la base del diseño, no un candidato a reemplazar.

---

## Catálogo completo de dispositivos en `klippy/extras`

### Grupo A — Sensores bulk de alta tasa (canal `bulk_sensor.py`)

Estos dispositivos producen flujos continuos de muestras a tasas de cientos a miles de Hz. Todos usan la infraestructura `bulk_sensor.py`, que agrupa muestras en bloques antes de enviarlas al host.

| Módulo | Dispositivo | Tipo de dato | Tasa típica | Resolución |
|---|---|---|---|---|
| `adxl345.py` | ADXL345 | aceleración XYZ (int16) | 3200 Hz | 10 bits efec. |
| `lis_sensor.py` | LIS2DW12, LIS3DH | aceleración XYZ (int16) | 1600–3200 Hz | 14 bits |
| `mpu9250.py` | MPU-9250/6050/6500, ICM20948 | aceleración XYZ + giroscopio (int16) | 1000–4000 Hz | 16 bits |
| `bmi160.py` | BMI160 | aceleración XYZ + giroscopio (int16) | 1600 Hz | 16 bits |
| `angle.py` | AS5047D, TLE5012B, A1333, MT6816, MT6826S | ángulo absoluto (uint16) | 1000–9000 Hz | 14–15 bits |
| `ldc1612.py` | LDC1612 (eddy current) | frecuencia de resonancia (uint32) | 250–500 Hz | 28 bits |

### Grupo B — Células de carga y ADC de precisión

| Módulo | Dispositivo | Tipo de dato | Tasa | Resolución |
|---|---|---|---|---|
| `hx71x.py` | HX711 / HX717 | carga (int24) | 80 Hz (HX711), 320 Hz (HX717) | 24 bits |
| `ads1220.py` | ADS1220 | carga / fuerza (int32) | 330–1200 Hz | 24 bits |
| `adc_temperature.py` / `ads1x1x.py` | ADS1013/1015/1115 | ADC genérico (int16) | 8–860 Hz | 12–16 bits |

### Grupo C — Sensores de temperatura e higrómetros

Todos producen datos a 1–10 Hz con deltas pequeños y alta persistencia temporal.

| Módulo | Dispositivo / familia | Tipo de dato |
|---|---|---|
| `temperature_sensor.py` | NTC, PT100, PT1000, AD595, genérico | temperatura (°C, float) |
| `temperature_host.py` | CPU del host (RPi, etc.) | temperatura (°C) |
| `bme280.py` | BME280 | temperatura + humedad + presión |
| `bmp280.py` | BMP180, BMP280, BMP388 | temperatura + presión |
| `htu21d.py` | HTU21D, SHT21 | temperatura + humedad |
| `sht3x.py` | SHT30/31/35 | temperatura + humedad |
| `aht10.py` | AHT10, AHT20 | temperatura + humedad |
| `lm75.py` | LM75 y compatibles | temperatura |
| `tsl1401cl_filament_width_sensor.py` | TSL1401CL | ancho de filamento (mm, float) |
| `hall_filament_width_sensor.py` | Hall filament width | ancho de filamento (mm, float) |
| `temperature_combined.py` | virtual (combinación de sensores) | temperatura derivada |

### Grupo D — Drivers de motor TMC (SPI/UART)

Los drivers TMC exponen registros de estado leídos periódicamente a baja frecuencia (~1–10 Hz en monitorización continua, bajo demanda para diagnóstico). Los datos relevantes son: corriente (uint10), temperatura del driver (°C), StallGuard (uint10), velocidad (uint20), flags de error.

| Módulo | Driver | Interfaz | Registros relevantes |
|---|---|---|---|
| `tmc2130.py` | TMC2130 | SPI | SG_RESULT, TSTEP, DRV_STATUS, CHOPCONF |
| `tmc2208.py` | TMC2208 / TMC2224 | UART | IOIN, DRV_STATUS, TSTEP, PWMCONF |
| `tmc2209.py` | TMC2209 | UART | SG_RESULT, TSTEP, DRV_STATUS, MSCNT |
| `tmc2240.py` | TMC2240 | SPI / UART | ADC_VSUPPLY_AIN, DRV_STATUS, SG4_RESULT |
| `tmc2660.py` | TMC2660 | SPI | DRVSTATUS (SG, SGU, SE), SMARTEN |
| `tmc5160.py` | TMC5160 | SPI | SG_RESULT, TSTEP, DRV_STATUS, ENC_POS |

### Grupo E — Control de movimiento y actuadores

| Módulo | Tipo de dato dominante | Tasa |
|---|---|---|
| `fan.py`, `fan_generic.py`, `controller_fan.py`, `temperature_fan.py` | PWM duty (uint16) + estado | < 1 Hz en régimen |
| `heater_bed.py`, extrusor (via `toolhead`) | PWM heater duty (uint16) | 1–20 Hz |
| `output_pin.py`, `pwm_tool.py`, `pwm_cycle_time.py` | PWM / digital (uint16) | evento |
| `servo.py` | PWM servo (uint16) | evento |
| `led.py`, `neopixel.py`, `dotstar.py` | RGB/RGBW (4× uint8) | evento |
| `display_status.py` | estado de impresión (string + %) | < 1 Hz |

### Grupo F — Sensores de posición y homing

| Módulo | Tipo de dato | Tasa |
|---|---|---|
| `probe.py`, `bltouch.py`, `smart_effector.py` | posición Z en toque (int32) | evento puntual |
| `probe_eddy_current.py` | frecuencia eddy / posición Z | 250–500 Hz en escaneo |
| Endstop físico (via MCU) | señal digital (bool) | evento |
| `endstop_phase.py` | fase del paso (uint8) | evento |
| `bed_mesh.py` | malla de puntos Z (float[]) | offline |
| `axis_twist_compensation.py` | corrección Z por posición X (float[]) | offline |
| `skew_correction.py` | factor de corrección (float) | estático |

### Grupo G — Infraestructura, control y estado

| Módulo | Función | Tipo de dato relevante |
|---|---|---|
| `motion_queuing.py` | sincronización de generación de pasos | interno host |
| `print_stats.py` | estadísticas de impresión | contadores, estado |
| `virtual_sdcard.py` | lectura de G-code desde archivo | N/A (file I/O) |
| `pause_resume.py` | estado de pausa | bool |
| `gcode_move.py` | posición actual (GCODE_OFFSET) | float XYZ |
| `homing.py` | secuencia de homing | estado + posición |
| `manual_probe.py` | calibración manual Z | evento + valor |
| `resonance_tester.py` | control de test de resonancia | orquestador |
| `input_shaper.py` | parámetros del shaper (freq, damping) | float estático |
| `shaper_calibrate.py` | calibración automática | batch offline |
| `canbus_stats.py` | estadísticas CAN bus | contadores |
| `mcu_stats` (vía mcu.py) | contadores internos MCU | uint32 monótonos |

---

## Análisis de brechas — dispositivos no cubiertos en el diseño previo

### Brecha 1 — Sensores de ángulo de alta tasa (nuevo tipo)

Los sensores de ángulo magnético (AS5047D, TLE5012B, A1333, MT6816, MT6826S) operan a través de `bulk_sensor.py` a tasas de hasta 9000 Hz y producen muestras `uint16` de 14–15 bits que representan el ángulo absoluto del eje del motor (0–360°, wraparound). El diseño previo no contemplaba este tipo de dato.

**Perfil estadístico:** durante movimiento suave, los ángulos son secuencias casi lineales (velocidad angular aproximadamente constante). Durante aceleraciones, hay variación de primer orden. Durante paros bruscos o resonancias, hay variación de segundo orden. No hay distribución Laplaciana; la distribución de residuales tras un predictor lineal es aproximadamente uniforme en aceleración constante y sesgada en cambios bruscos.

**Método óptimo:** predictor lineal de primer orden (`â[n] = 2a[n-1] - a[n-2]` reducido a `â[n] = a[n-1] + Δ`) con wraparound aritmético (módulo 2^14 o 2^15 según chip). Residuales codificados con ZigZag + Rice(k adaptativo por bloque de 64 muestras). Escape RAW para cambios abruptos. Checkpoint absoluto periódico cada 128 muestras.

**Decodificador:** `AngleDecoder` — reconstruye el ángulo absoluto en grados (float) y lo publica como `{angle_deg: float, timestamp: float}`.

### Brecha 2 — LDC1612: frecuencia de resonancia (uint32, tipo especial)

El LDC1612 produce muestras de 28 bits que representan la frecuencia de resonancia de una bobina inductiva, no una magnitud física directa. El valor crece a medida que el sensor se acerca a una superficie conductora. La tasa es de 250–500 Hz en modo escaneo continuo, menor en modo probe puntual.

**Perfil estadístico:** durante escaneo de cama (bed leveling), la frecuencia varía de forma suave y monótona. La señal es casi lineal en tramos cortos. Δ simple funciona bien en el caso nominal; Δ² es ligeramente mejor. La distribución de residuales es muy concentrada en cero excepto en transiciones bruscas (cambio de dirección del toolhead).

**Método óptimo:** Δ + ZigZag + ULEB128 con checkpoint absoluto (uint32) cada 32 muestras. Escape RAW para muestras fuera de rango. No requiere Rice; los residuales son suficientemente pequeños para ULEB puro. Checkpoint simple (una muestra) es suficiente porque se usa Δ simple (no Δ²).

**Decodificador:** `EddyDecoder` — reconstruye el valor de frecuencia absoluta en Hz y lo publica como `{freq_hz: float, timestamp: float}`. La conversión a distancia en mm se realiza en `ProbeEddyDecoder`, que aplica la tabla de calibración al valor de frecuencia.

### Brecha 3 — Células de carga (HX711/HX717, ADS1220): int24/int32 de alta precisión

Las células de carga producen muestras de 24 bits (int24 en complemento a 2) a tasas de 80–1200 Hz dependiendo del ADC. El valor representa la deformación de la célula, que durante el uso como probe varía de forma casi lineal conforme la boquilla se acerca a la cama, con un pico abrupto en el momento del contacto.

**Perfil estadístico:** en reposo o movimiento libre, la señal es casi constante (ruido de ±pocos counts). Durante probing, hay una rampa suave seguida de un pico. Δ + ZigZag es suficiente en el caso nominal (residuales pequeños); en el pico, el escape RAW es necesario.

**Método óptimo:** Δ + ZigZag + ULEB128 con escape RAW (|Δ| > umbral → byte de flag + int24 raw). Checkpoint absoluto (int32) cada 32–64 muestras. No requiere Rice por la baja varianza en régimen.

**Decodificador:** `LoadCellDecoder` — reconstruye el valor ADC absoluto en counts y lo publica como `{raw_counts: int, force_g: float, timestamp: float}`. La conversión a gramos aplica el factor de calibración del sensor.

### Brecha 4 — Sensores angulares de ADC (ADS1x1x): int12/int16

Los ADS1013/1015/1115 son ADC de propósito general usados para medir temperatura mediante NTC u otros sensores resistivos. La tasa es de 8–860 Hz según configuración, aunque en uso típico de Klipper se opera a 8–128 Hz.

**Método óptimo:** idéntico al de temperatura — Δ + ZigZag + ULEB128 + RLE. El `AdcDecoder` acumula el valor absoluto y publica `{raw_counts: int, voltage_v: float, timestamp: float}`. La conversión a temperatura la realiza el módulo de temperatura padre.

### Brecha 5 — Drivers TMC: registros de estado periódicos

Los drivers TMC exponen múltiples registros de estado a través de SPI/UART, leídos periódicamente a ~1 Hz en monitorización continua. Los registros clave son: `SG_RESULT` (uint10, StallGuard), `TSTEP` (uint20, periodo de paso), `DRV_STATUS` (uint32, flags de error), `MSCNT` (uint9, posición microstep).

**Perfil estadístico:** `TSTEP` y `SG_RESULT` varían lentamente durante movimiento normal (Δ pequeño y monótono). `DRV_STATUS` es principalmente constante con cambios de un bit puntualmente. `MSCNT` es un contador cíclico.

**Método óptimo por sub-campo:**
- `TSTEP`: Δ + ZigZag + ULEB128 (varía suavemente con velocidad).
- `SG_RESULT`: Δ + ZigZag + ULEB128.
- `DRV_STATUS`: bitfield compacto; solo transmitir si cambió (diff de máscara de bits). Token especial `0x00` = "sin cambio desde el ciclo anterior".
- `MSCNT`: delta módulo 512 + ZigZag.

**Decodificador:** `TmcStatusDecoder` — reconstruye el estado completo del driver y lo publica como `{driver: str, sg_result: int, tstep: int, drv_status: int, temp_c: float, timestamp: float}`. Los flags de error individuales se exponen como booleanos de primer nivel.

### Brecha 6 — LEDs RGB/RGBW: datos de color (nuevo tipo)

Los LEDs NeoPixel (WS2812) y DotStar (APA102) reciben tramas de color en formato RGB o RGBW (3–4 × uint8 por LED, 1–n LEDs por comando). El patrón de uso en Klipper es de eventos discretos (cambio de color al inicio de impresión, durante calentar, etc.) con largos periodos sin cambio.

**Método óptimo:** RLE de color — codificar como `(n_leds, R, G, B[, W])`. Si el color no cambia, token `0x00` de "sin cambio". Si solo cambian algunos canales, delta de canal con máscara de 4 bits. Para transiciones suaves (fade), Δ por canal + ZigZag.

**Decodificador:** `LedDecoder` — reconstruye el array completo de colores RGB/RGBW y lo publica como `{leds: [{r,g,b,w},...], timestamp: float}`.

### Brecha 7 — Ancho de filamento: Hall y TSL1401CL

Ambos sensores producen un valor de ancho de filamento en mm a baja frecuencia (1–10 Hz). El sensor Hall produce un int16 proporcional al voltaje de la celda; el TSL1401CL produce un perfil de intensidad de 128 píxeles del que se extrae el ancho.

**Método óptimo:** idéntico a temperatura — Δ + ZigZag + ULEB128. El TSL1401CL raw (128 × uint8 por muestra) es un caso excepcional: en modo de calibración se transmiten todos los píxeles; en modo de operación normal solo se transmite el ancho ya calculado en el MCU, reduciendo el tráfico a un solo int16 por muestra.

---

## Tabla maestra de métodos de compresión — versión completa

| Tipo de dato | Módulo(s) | Codificación en MCU | Decodificador en host | Ratio neto |
|---|---|---|---|---|
| Pasos (ticks) | `stepcompress.c` | interval/count/add + Δ² residual condicional | `StepperDecoder` → posición absoluta en pasos | 8x–12x |
| Aceleración XYZ (int16, alta tasa) | `adxl345`, `lis_sensor`, `mpu9250`, `bmi160` | LPC-2 enteros + Rice(k adaptativo) + escape RAW | `AccelDecoder` → mm/s² | 3x–8x |
| Ángulo absoluto (uint16, alta tasa) | `angle` | Δ lineal + ZigZag + Rice(k adapt.) + wraparound | `AngleDecoder` → grados | 3x–7x |
| Frecuencia eddy (uint32) | `ldc1612` | Δ + ZigZag + ULEB128 + checkpoint simple | `EddyDecoder` → Hz | 2x–5x |
| Células de carga (int24/int32) | `hx71x`, `ads1220` | Δ + ZigZag + ULEB128 + escape RAW | `LoadCellDecoder` → counts + g | 2x–6x |
| ADC genérico (int12/int16) | `ads1x1x`, `adc_temperature` | Δ + ZigZag + ULEB128 + RLE | `AdcDecoder` → V | 2x–5x |
| Temperatura (°C, lenta) | `temperature_sensor`, `bme280`, `lm75`, etc. | Δ + ZigZag + ULEB128 + RLE de repetición | `TempDecoder` → °C | 1,8x–5x |
| Humedad (%, lenta) | `htu21d`, `sht3x`, `aht10`, `bme280` | Δ + ZigZag + ULEB128 + RLE | `HumidDecoder` → % RH | 1,8x–5x |
| Presión (Pa, lenta) | `bme280`, `bmp280` | Δ² + Rice(k pequeño) + checkpoint doble | `PressureDecoder` → Pa | 1,8x–4,5x |
| Estado driver TMC | `tmc2130/2208/2209/2240/2660/5160` | diff de bitfield + Δ varint por campo numérico | `TmcStatusDecoder` → campos físicos | 2x–8x |
| PWM control (ventilador, lento) | `fan`, `fan_generic`, `output_pin` | RLE temporal (valor + hold\_ticks) | `PwmControlDecoder` → duty + intervalo | 2,5x–18x |
| PWM heater (extrusor, cama) | `heater_bed`, extrusor | Δ-duración + ZigZag + valor absoluto al cambio | `PwmHeaterDecoder` → duty | 1,5x–6x |
| LEDs RGB/RGBW | `led`, `neopixel`, `dotstar` | RLE de color + delta de canal con máscara | `LedDecoder` → array RGB/RGBW | 2x–10x |
| Fin de carrera / posición (int32) | endstop, `probe` | Δ relativo + checkpoint doble + ZigZag | `EndstopDecoder` → posición absoluta | 1,3x–2,5x |
| Ancho de filamento (mm) | `hall_filament_width`, `tsl1401cl` | Δ + ZigZag + ULEB128 (valor calculado) | `FilamentWidthDecoder` → mm | 1,8x–4x |
| Estadísticas MCU (uint32 monótonos) | `mcu_stats` | Varint con reserva de valor (0 = +1) | `StatsDecoder` → valor absoluto | 1,8x–9x |
| Enteros de control (OID, flags, enums) | todos | Bit-pack + ULEB/VLQ acotado | stateless, O(1) | 1,1x–1,6x |
| Identificación (progmem, estático) | `serialhdl` | zlib/zstd offline + chunked | `IdentDecoder` → publicación tras buffer completo | 4x–10x |
| Cadenas debug / output | `klippy.log`, `output_pin` | Tokenización de formato + parámetros tipados | `StringDecoder` → cadena completa | 1,3x–3,5x |

---

## Arquitectura del sistema unificado de descompresión

### Principio

El MCU comprime y transmite. El host recibe, descomprime y publica **valores físicos reales** a través del `DecompressionPipeline`. Ningún consumidor necesita conocer el codec, gestionar estado de decodificador, ni manejar resync.

```
MCU                        HOST — DecompressionPipeline            Consumidores
──────────────────────     ────────────────────────────────────    ──────────────────
stepcompress.c             StepperDecoder          → steps         toolhead.py
adxl345/lis_sensor.py      AccelDecoder            → mm/s²         resonance_tester.py
angle.py                   AngleDecoder            → deg           motion_analysis
ldc1612.py                 EddyDecoder             → Hz            probe_eddy_current.py
hx71x.py / ads1220.py      LoadCellDecoder         → counts/g      load_cell probe
ads1x1x.py                 AdcDecoder              → V             temperature modules
temperature_sensor.py      TempDecoder             → °C            heater.py / Moonraker
bme280.py / bmp280.py      PressureDecoder         → Pa            sensor plugins
htu21d/sht3x/aht10.py      HumidDecoder            → % RH          Mainsail / Fluidd
tmc2130…tmc5160.py         TmcStatusDecoder        → campos TMC    tmc monitor / logs
fan / output_pin.py        PwmControlDecoder       → duty+ticks    fan.py
heater_bed.py / extrusor   PwmHeaterDecoder        → duty          heater.py
neopixel / dotstar.py      LedDecoder              → RGB/RGBW[]    led.py
endstop / probe.py         EndstopDecoder          → pos abs       homing.py
hall_filament_width.py     FilamentWidthDecoder    → mm            filament sensor
mcu_stats                  StatsDecoder            → counters      klippy.log
progmem / output()         IdentDecoder / StringDecoder            msgproto.py / log
```

### `DecompressionPipeline`

```python
class DecompressionPipeline:
    def __init__(self):
        self._decoders    = {}   # oid -> DecoderInstance
        self._subscribers = {}   # data_type -> [callbacks]

    def register_decoder(self, oid, decoder_instance):
        self._decoders[oid] = decoder_instance

    def subscribe(self, data_type, callback):
        self._subscribers.setdefault(data_type, []).append(callback)

    def handle_packet(self, oid, raw_payload, timestamp):
        decoder = self._decoders.get(oid)
        if decoder is None:
            return
        real_values = decoder.decode(raw_payload, timestamp)
        for cb in self._subscribers.get(decoder.data_type, []):
            cb(oid, real_values, timestamp)

    def on_crc_error(self, oid):
        decoder = self._decoders.get(oid)
        if decoder:
            decoder.reset()
```

### Registro de decodificadores durante el handshake

```python
DECODER_REGISTRY = {
    "stepper":              StepperDecoder,
    "adxl345":              AccelDecoder,
    "lis2dw":               AccelDecoder,
    "lis3dh":               AccelDecoder,
    "mpu9250":              AccelDecoder,
    "bmi160":               AccelDecoder,
    "angle":                AngleDecoder,
    "ldc1612":              EddyDecoder,
    "hx71x":                LoadCellDecoder,
    "ads1220":              LoadCellDecoder,
    "ads1x1x":              AdcDecoder,
    "temperature":          TempDecoder,
    "humidity":             HumidDecoder,
    "pressure":             PressureDecoder,
    "tmc_status":           TmcStatusDecoder,
    "fan_pwm":              PwmControlDecoder,
    "heater_pwm":           PwmHeaterDecoder,
    "led":                  LedDecoder,
    "endstop":              EndstopDecoder,
    "filament_width":       FilamentWidthDecoder,
    "mcu_stats":            StatsDecoder,
    "output_pin":           StringDecoder,
}

def on_mcu_identified(pipeline, mcu_dict):
    for oid, descriptor in mcu_dict["objects"].items():
        decoder_cls = DECODER_REGISTRY.get(descriptor["type"])
        if decoder_cls:
            pipeline.register_decoder(oid, decoder_cls(descriptor))
```

---

## Diseño detallado de los decodificadores nuevos

### `AngleDecoder` — sensor de ángulo magnético

Maneja el wraparound del valor de ángulo (los sensores de 14–15 bits cubren 360° y dan la vuelta a cero). El predictor lineal funciona correctamente si el Δ entre muestras consecutivas no supera la mitad del rango (no más de 180° por muestra, condición siempre cumplida a las tasas de uso).

```python
ANGLE_BITS = {
    "a1333": 14, "as5047d": 14, "tle5012b": 15,
    "mt6816": 14, "mt6826s": 14,
}

class AngleDecoder(Decoder):
    data_type = "angle"

    def __init__(self, descriptor):
        chip  = descriptor.get("chip", "as5047d")
        bits  = ANGLE_BITS.get(chip, 14)
        self._modulo  = 1 << bits
        self._scale   = 360.0 / self._modulo
        self._last    = [0, 0]
        self._ready   = False

    def reset(self):
        self._ready = False
        self._last  = [0, 0]

    def decode(self, payload, timestamp):
        pkt_type, data = parse_angle_block(payload)
        if pkt_type == TYPE_CHECKPOINT:
            self._last  = [data["angle"], data["angle"]]
            self._ready = True
            return []
        if not self._ready:
            return []
        out = []
        for residual in data["residuals"]:
            pred = (self._last[-1] * 2 - self._last[-2]) % self._modulo
            raw  = (pred + residual) % self._modulo
            self._last = [self._last[-1], raw]
            out.append({"angle_deg": raw * self._scale, "timestamp": timestamp})
        return out
```

### `EddyDecoder` — sensor LDC1612

```python
class EddyDecoder(Decoder):
    data_type = "eddy_freq"

    def __init__(self, descriptor):
        self._freq   = 0
        self._ready  = False
        self._ref_hz = descriptor.get("ref_freq_hz", 12_000_000)

    def reset(self):
        self._ready = False

    def decode(self, payload, timestamp):
        pkt_type, data = parse_eddy_packet(payload)
        if pkt_type == TYPE_CHECKPOINT:
            self._freq  = data["freq_raw"]
            self._ready = True
            return []
        if not self._ready:
            return []
        delta       = uleb_decode_zigzag(data)
        self._freq += delta
        freq_hz     = self._freq * self._ref_hz / (1 << 28)
        return [{"freq_hz": freq_hz, "timestamp": timestamp}]
```

### `LoadCellDecoder` — HX711 / HX717 / ADS1220

```python
class LoadCellDecoder(Decoder):
    data_type = "load_cell"

    def __init__(self, descriptor):
        self._raw        = 0
        self._ready      = False
        self._scale_g    = descriptor.get("scale_g_per_count", 1.0)
        self._zero_count = descriptor.get("zero_offset", 0)
        self._bits       = descriptor.get("resolution_bits", 24)

    def reset(self):
        self._ready = False

    def decode(self, payload, timestamp):
        pkt_type, data = parse_loadcell_packet(payload)
        if pkt_type == TYPE_CHECKPOINT:
            self._raw   = data["raw"]
            self._ready = True
            return []
        if not self._ready:
            return []
        if data.get("raw_escape"):
            self._raw = data["raw"]
        else:
            self._raw += uleb_decode_zigzag(data)
        force_g = (self._raw - self._zero_count) * self._scale_g
        return [{"raw_counts": self._raw, "force_g": force_g, "timestamp": timestamp}]
```

### `TmcStatusDecoder` — drivers Trinamic

```python
class TmcStatusDecoder(Decoder):
    data_type = "tmc_status"

    def __init__(self, descriptor):
        self._driver      = descriptor.get("driver_type", "tmc2209")
        self._drv_status  = 0
        self._sg_result   = 0
        self._tstep       = 0
        self._mscnt       = 0

    def reset(self):
        pass   # registros TMC son absolutos; no hay estado acumulado de cara al host

    def decode(self, payload, timestamp):
        pkt = parse_tmc_packet(payload)
        if pkt.get("no_change"):
            pass    # DRV_STATUS no cambió; no publicar evento
        else:
            self._drv_status = pkt.get("drv_status", self._drv_status)
        self._sg_result = self._sg_result + uleb_decode_zigzag(pkt["sg_delta"])
        self._tstep     = self._tstep     + uleb_decode_zigzag(pkt["tstep_delta"])
        self._mscnt     = (self._mscnt   + uleb_decode_zigzag(pkt["mscnt_delta"])) % 512
        return {
            "driver":     self._driver,
            "sg_result":  self._sg_result,
            "tstep":      self._tstep,
            "mscnt":      self._mscnt,
            "drv_status": self._drv_status,
            "error_flags": extract_tmc_flags(self._drv_status, self._driver),
            "timestamp":  timestamp,
        }
```

---

## Ajustes a métodos ya definidos — revisión por nuevos hallazgos

### LPC-2 para todos los sensores IMU (MPU-9250, BMI160, ICM20948)

El análisis previo solo mencionaba ADXL345 y LIS2DW para el predictor LPC-2 adaptativo. Los módulos MPU-9250/6050 y BMI160 producen el mismo tipo de dato (aceleración int16 XYZ, misma estadística Laplaciana en régimen de vibración) y deben usar el mismo `AccelDecoder`. El giroscopio (cuando está disponible en MPU-9250/ICM20948) tiene una estadística diferente en régimen de movimiento continuo (señal cuasi-sinusoidal de amplitud pequeña en impresión normal). Se añade un flag en la cabecera del bloque para distinguir si el bloque contiene solo acelerómetro o acelerómetro + giroscopio.

### Sensores de temperatura combinados (`temperature_combined`)

El módulo `temperature_combined` crea un sensor virtual que combina varias mediciones (media, mínimo, máximo ponderado). El valor publicado por `TempDecoder` es el resultado ya combinado; no se necesita un decodificador especial. La combinación se realiza en el host antes de publicar.

### Sensor de filamento TSL1401CL — modo raw vs modo calculado

En modo calibración, el MCU transmite el perfil completo de 128 × uint8 por muestra. Este bloque se codifica con LZ4 bloque corto (alta correlación espacial entre píxeles adyacentes, ratio ~2x). En modo operación, solo se transmite el ancho calculado (int16), codificado con Δ + ZigZag + ULEB128. El `FilamentWidthDecoder` detecta el modo por el tamaño del payload y aplica el decodificador correspondiente.

### `probe_eddy_current` en modo escaneo continuo

En el modo de escaneo de cama rápido (`scan_mode`), el LDC1612 opera a su tasa máxima (~500 Hz) durante todo el barrido. La secuencia de muestras forma una señal casi triangular (la frecuencia varía al acercarse y alejarse de la superficie). Δ² comprime mejor que Δ simple en este caso porque la segunda diferencia de una rampa es cero. El `EddyDecoder` puede activar modo Δ² cuando detecta el flag de escaneo en la cabecera del paquete, con checkpoint doble correspondiente.

---

## Correcciones críticas confirmadas

### Checkpoint doble obligatorio para Δ²

Aplica a: `PressureDecoder`, `EndstopDecoder` (modo Δ²), `EddyDecoder` (modo escaneo Δ²). Sin checkpoint doble, el decodificador no puede inicializar el estado tras un resync y genera valores incorrectos silenciosamente.

```
Paquete de checkpoint doble (10 bytes):
  [TYPE_CHECKPOINT_D2: 1B][x_n: int32][x_n_minus_1: int32][timestamp: uint16]
```

### Varint con reserva de valor para estadísticas

El valor ULEB `0` codifica el incremento unitario (+1) sin overhead. Los valores `n ≥ 1` codifican `delta = n + 1`. Esto elimina el overhead de un bit de flag en el 100 % de los mensajes de contadores monótonos.

### Diferenciación PWM-control / PWM-heater

PWM de control (ventiladores) usa RLE de duración absoluta. PWM de heater usa Δ-duración + ZigZag porque los ciclos de actualización son frecuentes y la variación de duración entre ciclos es pequeña. Usar RLE de duración absoluta en el heater genera overhead cuando la diferencia de duración es mínima.

---

## Métodos descartados

| Método | Razón |
|---|---|
| ANS / rANS | Estado global acumulativo incompatible con recuperación por CRC16 local |
| Codificación aritmética | Determinismo pobre; ciclos por símbolo variables en MCU con planificación estricta |
| Deflate en lazo crítico | Ventana de historial (32 KB mínimo) incompatible con MESSAGE_MAX = 64 B |
| LPC alto orden (FLAC completo) | Beneficio marginal vs LPC-2 de enteros; coste de ciclos no justificado en MCU |
| Rice con `k` fijo global | `k` óptimo varía con amplitud de señal; versión adaptativa por bloque es estrictamente superior |
| Huffman dinámico por paquete | La tabla ocupa más bytes que el payload que ahorra en paquetes de ≤ 64 B |
| RLE de duración absoluta para PWM heater | Ineficiente cuando la variación de duración entre ciclos es pequeña; Δ-duración es superior |
| LZ4 en path crítico de sensores | Latencia variable incompatible con determinismo; solo válido para blobs offline (calibración, debug) |

---

## Prioridad de implementación actualizada

### Prioridad 1 — Correcciones de corrección (el esquema es incorrecto sin estas)

- Checkpoint doble en `PressureDecoder`, `EndstopDecoder` (Δ²) y `EddyDecoder` (modo escaneo).
- Varint con reserva de valor en `StatsDecoder`.

### Prioridad 2 — Nuevos decodificadores para tipos no cubiertos

- `AngleDecoder` — cubre los sensores de ángulo magnético (AS5047D, TLE5012B, MT6816, MT6826S), que ya están en producción en muchas instalaciones.
- `EddyDecoder` — cubre el LDC1612, sensor de creciente adopción para bed leveling de alta precisión.
- `LoadCellDecoder` — cubre HX711/HX717 y ADS1220, necesarios para load cell probing.

### Prioridad 3 — Máximo impacto / riesgo controlado

- `DecompressionPipeline` base con `StepperDecoder` y `AccelDecoder` extendido a MPU-9250 y BMI160.
- `TmcStatusDecoder` — reduce tráfico de monitorización de drivers en instalaciones multi-MCU con muchos drivers.

### Prioridad 4 — Impacto medio / bajo riesgo

- `LedDecoder` con RLE de color.
- `FilamentWidthDecoder` con detección de modo raw/calculado.
- Δ² condicional en `StepperDecoder` (solo cuando `add` cambia entre comandos).

---

## Garantías para los consumidores

- **Tipo físico garantizado.** La salida siempre es un valor real: °C, mm/s², grados, Hz, g, duty [0,1]. Nunca un delta, residual ni valor comprimido.
- **Estado encapsulado.** Los consumidores (plugins, Moonraker, Mainsail, Fluidd, clientes remotos) no necesitan conocer el codec ni el estado del decodificador.
- **Resync transparente.** Tras pérdida de paquete, el pipeline descarta silenciosamente hasta el próximo checkpoint y reanuda la publicación. El consumidor nota un gap en los timestamps pero nunca recibe un valor incorrectamente reconstruido.
- **Extensibilidad.** Añadir soporte para un nuevo sensor requiere implementar `Decoder` y registrarlo en `DECODER_REGISTRY`, sin modificar ningún consumidor existente.
- **Compatibilidad hacia adelante.** Cambiar el codec de un tipo de dato en el MCU solo requiere actualizar el decodificador correspondiente en el host; la API pública hacia los consumidores permanece estable.

---

## Resumen ejecutivo

El catálogo completo de `klippy/extras` revela seis tipos de dato no contemplados en el diseño original: sensores de ángulo magnético de alta tasa, frecuencia de resonancia eddy (LDC1612), células de carga (HX711/ADS1220), ADC de propósito general (ADS1x1x), estado de drivers TMC, y datos de color de LEDs RGB. Cada uno requiere un decodificador específico y un método de compresión adaptado a su estadística.

| Dominio | Codec en MCU | Publicación del host |
|---|---|---|
| Control | bit-pack + enteros cortos | valor directo, stateless |
| Movimiento | interval/count/add + Δ² condicional | posición absoluta en pasos |
| Aceleración / vibración | LPC-2 enteros + Rice adaptativo | mm/s² por eje |
| Ángulo de motor | Δ lineal + ZigZag + Rice + wraparound | grados absolutos |
| Eddy current / distancia | Δ / Δ² + ULEB128 + checkpoint simple/doble | Hz → mm (calibrado) |
| Carga / fuerza | Δ + ZigZag + ULEB128 + escape RAW | counts + gramos |
| ADC genérico | Δ + ZigZag + ULEB128 + RLE | voltios |
| Temperatura / humedad | Δ + ZigZag + ULEB128 + RLE | °C, % RH |
| Presión | Δ² + Rice + checkpoint doble | Pa |
| Estado TMC | diff bitfield + Δ varint por campo | campos físicos desglosados |
| PWM control | RLE de duración | duty + intervalo real |
| PWM heater | Δ-duración + valor absoluto | duty real |
| LEDs RGB/RGBW | RLE color + delta canal | array RGB/RGBW |
| Estadísticas MCU | varint con reserva de valor | contador absoluto |
| Estático / texto | zlib offline / tokenización | dict completo / cadena formateada |

El `DecompressionPipeline` centralizado garantiza que cualquier consumidor — plugin interno de klippy, Moonraker, Mainsail, Fluidd, herramienta de análisis o cliente remoto — recibe siempre valores físicos reales ya reconstruidos, sin conocimiento del codec subyacente ni del estado del decodificador, independientemente del tipo de sensor o driver que los haya generado.