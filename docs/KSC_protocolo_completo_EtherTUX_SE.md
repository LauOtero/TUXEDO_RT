# 📋 Protocolo KSC (Klipper-Stream-Codec) – Especificación Completa

**Documento:** KSC-TECH-RPT-002A  
**Versión:** 0.1.1  
**Fecha:** 2026-03-25  
**Clasificación:** Especificación consolidada del protocolo KSC  
**Autor:** Equipo de Desarrollo KSC  
**Origen:** Derivado y consolidado desde `04_klipper_stream_codec_bridge_EtherTUX_SE.md`

---

## 📑 Tabla de Contenidos

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)  
   - 1.1 [Objetivos del Protocolo](#11-objetivos-del-protocolo)  
   - 1.2 [Alcance y Modos Operativos](#12-alcance-y-modos-operativos)  
2. [Arquitectura General de KSC](#2-arquitectura-general-de-ksc)  
   - 2.1 [Pipeline Funcional](#21-pipeline-funcional)  
   - 2.2 [Arquitectura Bidireccional Full-Duplex](#22-arquitectura-bidireccional-full-duplex)  
   - 2.3 [Handshake de Activación y Negociación](#23-handshake-de-activacion-y-negociacion)  
   - 2.4 [Formato de Trama KSC](#24-formato-de-trama-ksc)  
   - 2.5 [Buffers, Rings y Gestión de Memoria](#26-particionado-rp2350-para-determinismo)  
3. [Especificaciones Técnicas del Codec](#3-especificaciones-tecnicas-del-codec)  
   - 3.1 [DPC con Group Varint](#31-dpc-con-group-varint)  
   - 3.2 [TurboPFor](#32-turbopfor)  
   - 3.3 [TurboBitByte](#33-turbobitbyte)  
   - 3.4 [Delta of Delta](#34-delta-of-delta)  
   - 3.5 [ELF-KSC](#35-elf-ksc)  
   - 3.6 [RLE+](#36-rle)  
   - 3.7 [PCOM](#37-pcom)  
   - 3.8 [Arquitectura Híbrida del Codec](#38-arquitectura-hibrida-del-codec)  
   - 3.9 [Contexto Global KSC](#39-contexto-global-ksc)  
   - 3.10 [Trazabilidad del Tamaño del Contexto](#310-trazabilidad-del-tamano-del-contexto)  
   - 3.11 [Compatibilidad con VLQ de Klipper](#311-compatibilidad-con-vlq-de-klipper)  
   - 3.12 [Perfiles de Rendimiento Predefinidos](#312-perfiles-de-rendimiento-predefinidos)  
   - 3.13 [Dispatcher por `msgid`](#313-dispatcher-por-msgid)  
   - 3.14 [Invariantes Operativos de KSC](#314-invariantes-operativos-de-ksc)  
   - 3.15 [Pipeline Operativo de KSC](#315-pipeline-operativo-de-ksc)  
   - 3.16 [Límites de Expansión y Presupuesto Temporal](#316-limites-de-expansion-y-presupuesto-temporal)  
   - 3.17 [Interfaz de Plugins PCOM](#317-interfaz-de-plugins-pcom)  
4. [Lógica de Decisión, Perfiles y Offloading](#4-logica-de-decision-perfiles-y-offloading)  
   - 4.1 [Características de MCUs Soportadas](#41-caracteristicas-de-mcus-soportadas)  
   - 4.2 [Política de Puntuación](#42-politica-de-puntuacion)  
   - 4.3 [Implementación de Referencia](#43-implementacion-de-referencia)  
   - 4.4 [Renegociación Dinámica](#44-renegociacion-dinamica)  
5. [Adaptadores de Transporte](#5-adaptadores-de-transporte)  
   - 5.1 [Matriz Normativa de Payloads](#51-matriz-normativa-de-payloads)  
   - 5.2 [USB 1.1 y USB 2.0](#52-usb-11-y-usb-20)  
   - 5.3 [Serial](#53-serial)  
   - 5.4 [CAN 2.0](#54-can-20)  
   - 5.5 [CAN FD](#55-can-fd)  
   - 5.6 [Ethernet MACRAW](#56-ethernet-macraw)  
   - 5.7 [UniBus](#57-unibus)  
6. [Integridad, Recuperación y Observabilidad](#6-integridad-recuperacion-y-observabilidad)  
   - 6.1 [Capas de Detección y Corrección](#61-capas-de-deteccion-y-correccion)  
   - 6.2 [Política de Fallback](#62-politica-de-fallback)  
   - 6.3 [Logging con Timestamp Preciso](#63-logging-con-timestamp-preciso)  
   - 6.4 [Códigos de Evento Obligatorios](#64-codigos-de-evento-obligatorios)  
7. [Rendimiento y Optimizaciones](#7-rendimiento-y-optimizaciones)  
   - 7.1 [Optimizaciones de Plataforma](#71-optimizaciones-de-plataforma)  
   - 7.2 [Presupuesto Temporal del Protocolo](#72-presupuesto-temporal-del-protocolo)  
   - 7.3 [Latencia Total de Referencia](#73-latencia-total-de-referencia)  
   - 7.4 [Throughput Objetivo por Transporte](#74-throughput-objetivo-por-transporte)  
   - 7.5 [Compatibilidad con MCUs de 16 MHz y 8 KB RAM](#75-compatibilidad-con-mcus-de-16-mhz-y-8-kb-ram)  
   - 7.6 [Soporte Multi-Dispositivo](#76-soporte-multi-dispositivo)  
   - 7.7 [Consumo Energético](#77-consumo-energetico)  
8. [Implementación de Referencia](#8-implementacion-de-referencia)  
   - 8.1 [Contexto de Sesión](#81-contexto-de-sesion)  
   - 8.2 [Selector de Algoritmo](#82-selector-de-algoritmo)  
   - 8.3 [Handshake UniBus de Referencia](#83-handshake-unibus-de-referencia)  
   - 8.4 [Carga Dinámica de UniBus](#84-carga-dinamica-de-unibus)  
   - 8.5 [Programa PIO de Stall Atómico](#85-programa-pio-de-stall-atomico)  
   - 8.6 [Planificador Multi-Sesión](#86-planificador-multi-sesion)  
   - 8.7 [Organización de Código Esperada](#87-organizacion-de-codigo-esperada)  
9. [Testing y Validación](#9-testing-y-validacion)  
   - 9.1 [Matriz de Pruebas Obligatorias](#91-matriz-de-pruebas-obligatorias)  
   - 9.2 [Casos de Falla](#92-casos-de-falla)  
   - 9.3 [Instrumentación Requerida](#93-instrumentacion-requerida)  
   - 9.4 [Estado de Validación de esta Revisión](#94-estado-de-validacion-de-esta-revision)  
10. [Apéndices](#10-apendices)  
   - 10.1 [Glosario Operativo](#101-glosario-operativo)  
   - 10.2 [Resumen de Decisiones de Ingeniería](#102-resumen-de-decisiones-de-ingenieria)  
   - 10.3 [Próximos Pasos de Validación](#103-proximos-pasos-de-validacion)  
   - 10.4 [Anexo de Trazabilidad de Payloads](#104-anexo-de-trazabilidad-de-payloads)  
   - 10.5 [Tabla Resumen de Payloads por Protocolo](#105-tabla-resumen-de-payloads-por-protocolo)  
   - 10.6 [Artefactos Reproducibles de Medición](#106-artefactos-reproducibles-de-medicion)  

---

## 1. Resumen Ejecutivo

KSC es el protocolo y codec de transporte adaptativo diseñado para el ecosistema Klipper cuando se requiere desplazar complejidad computacional fuera de la MCU principal. Su objetivo operativo es transformar, comprimir, fragmentar, proteger y entregar payloads de Klipper de forma determinista, conservando compatibilidad con el framing lógico VLQ y permitiendo degradación segura a modo directo.

En esta revisión:

- el RP2350 actúa como acelerador y bridge de protocolo
- el sistema negocia perfil de MCU, algoritmo, tamaño de payload y modo de enlace
- la operación soporta modos **Directo**, **Híbrido** y **Full Offload**
- la recuperación ante fallo está integrada en el propio framing y en la política de sesión

Reglas de trazabilidad:

- el **ratio por algoritmo** compara la secuencia nativa antes del framing de transporte
- el **ratio global del sistema** compara el flujo **VLQ on-wire de Klipper** frente a la salida KSC
- la **latencia total** mide desde la llegada del payload lógico al RP2350 hasta la disponibilidad del payload reconstruido
- el **throughput efectivo** se expresa como payload lógico equivalente, salvo indicación explícita de medida on-wire

### 1.1 Objetivos del Protocolo

| Objetivo | Meta | Estado |
|----------|------|--------|
| Compatibilidad con VLQ de Klipper | sin pérdida semántica | Cerrado |
| Offloading dinámico | Directo, Híbrido y Full Offload | Cerrado |
| Contexto KSC | ≤ 1024 bytes efectivos | Cerrado |
| Compatibilidad mínima MCU | 16 MHz y 8 KB RAM | Cerrado por diseño |
| Soporte multi-dispositivo | ≥ 5 sesiones simultáneas | Cerrado por diseño |
| Recuperación ante fallos | < 100 ms | Cerrado por diseño |
| Consumo RP2350 | < 100 mA nominal | Pendiente en hardware |

### 1.2 Alcance y Modos Operativos

KSC cubre:

- clasificación por `msgid`
- selección de algoritmo por carga y perfil MCU
- framing de integridad y secuencia
- fragmentación y adaptación a USB, Serial, CAN, CAN FD, Ethernet MACRAW y UniBus
- renegociación y fallback sin alterar la semántica de Klipper

| Modo | Cuándo se usa | Trabajo en RP2350 | Trabajo en MCU destino |
|------|---------------|-------------------|-------------------------|
| **Directo** | MCU potente o error persistente | framing, CRC, passthrough | procesa VLQ nativo |
| **Híbrido** | MCU media o enlace intermedio | compresión selectiva y adaptación | descompresión simple por clase |
| **Full Offload** | MCU con recursos limitados | compresión, reempaquetado y pacing | consumo casi directo del flujo preparado |

---

## 2. Arquitectura General de KSC

### 2.1 Pipeline Funcional

KSC se organiza como un pipeline determinista de cinco capas:

```text
CAPA 1: INGESTA
USB | Serial | CAN 2.0 | CAN FD | Ethernet MACRAW | UniBus

CAPA 2: CLASIFICACIÓN
msgid | dirección | sesión | prioridad | perfil MCU

CAPA 3: TRANSFORMACIÓN KSC
DPC | TurboPFor | TurboBitByte | DoD | ELF-KSC | RLE+ | RAW

CAPA 4: ADAPTACIÓN DE ENLACE
Fragmentación | CRC | paridad XOR | prioridad | pacing

CAPA 5: ENTREGA
DMA | PIO | RMII | buffer circular | READY stall | fallback
```

### 2.2 Arquitectura Bidireccional Full-Duplex

El protocolo separa dos canales:

- **Host→MCU** para movimiento, temporización, GPIO y control
- **MCU→Host** para respuestas, telemetría, ADC, estados y errores

Cada sentido mantiene:

- ring de ingreso
- ring de procesamiento KSC
- ring de egreso
- contador de secuencia independiente
- ventana de retransmisión

La separación evita bloqueo entre comandos críticos y telemetría de retorno.

### 2.3 Handshake de Activación y Negociación

La activación del protocolo sigue esta máquina de estados:

1. `BASE_SYNC`
2. `CAPS_EXCHANGE`
3. `PROFILE_SELECT`
4. `STREAM_ARMED`
5. `STREAM_ACTIVE`
6. `DEGRADED_DIRECT`

Mensajes obligatorios:

| Etapa | Emisor | Mensaje | Campos mínimos | Resultado |
|------|--------|---------|----------------|-----------|
| BASE_SYNC | RP2350 | `SYNC_REQ` | `protocol_version`, `transport_hint`, `safe_bitrate` | fija un canal mínimo |
| CAPS_EXCHANGE | MCU destino | `HELLO_ACK` | `mcu_profile_id`, `clock_mhz`, `ram_kb`, `feature_bitmap`, `max_payload_log2` | expone capacidades |
| PROFILE_SELECT | RP2350 | `OFFLOAD_REQ` | `selected_profile`, `offload_mode`, `algo_bitmap`, `window_size` | propone modo |
| STREAM_ARMED | MCU destino | `OFFLOAD_ACK` | `accepted_mode`, `accepted_link`, `accepted_mtu`, `crc_policy` | cierra negociación |
| STREAM_ACTIVE | ambos | `HEARTBEAT` | `seq_rx`, `buffer_level`, `error_budget` | mantiene salud |
| DEGRADED_DIRECT | RP2350 | `OFFLOAD_ABORT` | `reason_code`, `last_good_seq` | fuerza bypass seguro |

El protocolo vuelve a evaluar el modo si detecta:

- ratio de compresión por debajo de **1.10:1** en ventana de **64 mensajes**
- tiempo medio de servicio superior al **25%** del presupuesto del transporte
- cambio de `READY duty-cycle` mayor a **20 puntos**
- dominancia de una nueva clase de payload

### 2.4 Formato de Trama KSC

KSC encapsula el payload VLQ o el flujo ya transformado dentro de una cabecera ligera:

```c
typedef struct __attribute__((packed)) {
    uint8_t version;
    uint8_t flags;
    uint8_t session_id;
    uint8_t msgid;
    uint16_t seq;
    uint16_t frag_info;
    uint16_t raw_len;
    uint16_t enc_len;
    uint16_t crc16;
    uint8_t algo_id;
    uint8_t transport_id;
} ksc_bridge_hdr_t;
```

Bits de `flags`:

| Bit | Significado |
|-----|-------------|
| 0 | payload comprimido |
| 1 | payload fragmentado |
| 2 | paridad XOR presente |
| 3 | ACK requerido |
| 4 | modo degradado |
| 5 | timestamp adjunto |
| 6 | prioridad alta |
| 7 | reserva para revisión 0.2 |

### 2.5 Buffers, Rings y Gestión de Memoria

KSC usa rings potencia de dos y descriptores ligeros:

```c
typedef struct {
    uint32_t head;
    uint32_t tail;
    uint32_t mask;
    uint32_t high_watermark;
    uint32_t low_watermark;
    uint32_t overflow_count;
    uint32_t base_offset;
} ksc_ring_t;
```

```c
typedef struct {
    uint16_t offset;
    uint16_t len;
    uint16_t capacity;
    uint8_t class_id;
    uint8_t flags;
} ksc_buf_desc_t;
```

Clases de buffer recomendadas:

| `class_id` | Capacidad | Uso dominante |
|------------|-----------|---------------|
| 0 | 16 bytes | control corto, ACK, serial 115200 |
| 1 | 32 bytes | fragmentos pequeños y CAN 2.0 agrupado |
| 2 | 64 bytes | USB full-speed, serial 921600, CAN FD corto |
| 3 | 256 bytes | Ethernet MACRAW inicial, lotes host |
| 4 | 1536 bytes | Ethernet MTU completa y coalescing host |

Parámetros por sesión:

| Ring | Tamaño |
|------|--------|
| Ingreso Host→MCU | 2048 bytes |
| Procesado Host→MCU | 2048 bytes |
| Ingreso MCU→Host | 2048 bytes |
| Procesado MCU→Host | 2048 bytes |
| Ventana de retransmisión | 1024 bytes |

Huella:

- **9 KB** por sesión
- **45 KB** para 5 sesiones
- **≈ 32 KB** para estado compartido, logs y DMA pools
- **< 80 KB** de presupuesto total del bridge

---

## 3. Especificaciones Técnicas del Codec

KSC es un codec híbrido determinista. No persigue compresión universal máxima, sino mejor coste total del sistema y latencia acotada.

Política base:

- selección por `msgid`, clase de carga y perfil de MCU
- sin heap ni tablas dinámicas en la ruta caliente
- estado total del codec fijado en **1 KiB**
- degradación a **RAW/VLQ** si no hay beneficio material

### 3.1 DPC con Group Varint

Ruta:

```text
valor_nativo -> delta simple o predictivo -> cuantización entera -> Group Varint -> payload KSC
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `queue_step`, posiciones y secuencias casi lineales |
| Tamaño de grupo | 4 enteros |
| Ratio trazable | **5.505:1** |
| Coste objetivo | **3–6 ciclos por delta** |
| Perfil mínimo | **LOW** |

Reglas:

- predictor de orden 1 por defecto
- excepciones inline si el delta excede el ancho esperado
- descompresión reducida a suma acumulativa y lectura varint

### 3.2 TurboPFor

Ruta:

```text
bloque de enteros -> base común -> bit-width dominante -> mapa de excepciones -> payload KSC
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `intervals`, step deltas y bloques monotónicos |
| Ratio trazable | **4.8:1** |
| Tamaño de bloque | 32 o 64 valores |
| Excepciones máximas | 28 |
| Perfil mínimo | **MEDIUM** |

Reglas:

- no usar si `compression_ratio < 1.10`
- una MCU **LOW** no descomprime TurboPFor localmente
- el bridge puede reempaquetar a DPC o RAW si el destino no lo soporta

### 3.3 TurboBitByte

Ruta:

```text
payload binario -> reordenamiento bit/byte -> máscara de ocupación -> compactación -> payload KSC
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `spi_transfer`, `spi_send`, binarios estructurados |
| Ratio trazable | **4.2:1** |
| Ventana pendiente | 30 valores de 32 bits |
| Perfil mínimo | **MEDIUM** |

Reglas:

- se desactiva en mensajes cortos si el coste supera el ahorro
- en **Full Offload** hacia MCUs pequeñas se sustituye por **RAW**

### 3.4 Delta of Delta

Definición:

```text
delta_n = value_n - value_n-1
dod_n   = delta_n - delta_n-1
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `get_clock`, timestamps y contadores |
| Ratio trazable | **7.21:1** |
| Estado local | `prev_value`, `prev_delta` |
| Complejidad | **O(1)** por valor |
| Perfil mínimo | **LOW** |

### 3.5 ELF-KSC

Ruta:

```text
valor_actual XOR valor_prev -> leading_zeros/trailing_zeros -> payload compacto
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `query_analog_in`, telemetría analógica correlacionada |
| Ratio trazable | **6.50:1** |
| Estado local | `prev_raw`, `prev_delta`, `sample_count` |
| Complejidad | **O(1)** por valor |
| Perfil mínimo | **MEDIUM** |

### 3.6 RLE+

Ruta:

```text
valor repetido -> contador de racha -> token RLE
valor no repetido -> token literal
```

| Campo | Valor |
|-------|-------|
| Dominio principal | `queue_digital_out`, `queue_pwm_out` |
| Ratio trazable | **64.0:1** en rachas largas |
| Estado local | `last_value`, `run_length`, `mode` |
| Perfil mínimo | **LOW** |

### 3.7 PCOM

PCOM es la interfaz de extensión del núcleo KSC.

| Campo | Requisito |
|-------|-----------|
| Activación | `feature_bitmap` y `algo_bitmap` compatibles |
| Ubicación permitida | host Linux o RP2350 |
| Soporte en MCU destino | opcional |
| Fallback obligatorio | **RAW/VLQ** |
| Estado del núcleo | no se altera |

### 3.8 Arquitectura Híbrida del Codec

| Algoritmo | Uso principal | Dominio preferente | Ratio | Complejidad |
|-----------|---------------|--------------------|-------|-------------|
| DPC + Group Varint | `queue_step` | RP2350 y host | 5.505:1 | O(n) |
| TurboPFor | bloques monotónicos | host y RP2350 alto perfil | 4.8:1 | O(n) |
| TurboBitByte | binarios estructurados | host y RP2350 medio/alto | 4.2:1 | O(n) |
| DoD | timestamps | RP2350 y host | 7.21:1 | O(1) |
| ELF-KSC | telemetría analógica | RP2350 y host | 6.50:1 | O(1) |
| RLE+ | GPIO/PWM repetitivo | RP2350 y host | 64.0:1 | O(n) |
| PCOM | extensión negociada | host o RP2350 | variable | variable |
| RAW/VLQ | fallback universal | todos | 1.0:1 | O(n) |

### 3.9 Contexto Global KSC

```c
typedef struct __attribute__((aligned(64))) {
    struct {
        uint64_t prev[2];
        uint32_t buffer[128];
        uint8_t count;
        uint8_t _pad[7];
    } dpc;

    struct {
        uint32_t base_value;
        uint32_t exceptions[28];
        uint8_t exception_count;
        uint8_t _pad[11];
    } turbopfor;

    struct {
        uint32_t pending_values[30];
        uint8_t pending_count;
        uint8_t _pad[7];
    } turbobitbyte;

    struct {
        uint64_t prev_value;
        uint64_t prev_delta;
    } dod;

    struct {
        uint64_t prev_raw;
        uint64_t prev_delta;
        uint32_t sample_count;
        uint8_t _pad[4];
    } elf;

    struct {
        uint32_t last_value;
        uint16_t run_length;
        uint8_t mode;
        uint8_t _pad;
    } rle;

    struct {
        uint8_t mode_selector;
        uint8_t profile_id;
        uint8_t transport_id;
        uint8_t offload_mode;
        uint32_t compression_count;
        uint64_t total_input_bytes;
        uint64_t total_output_bytes;
        uint32_t crc_error_count;
    } dispatcher;

    uint8_t reserved[88];
} ksc_ctx_t;

_Static_assert(sizeof(ksc_ctx_t) == 1024, "KSC contexto debe ocupar 1024 bytes");
_Static_assert(_Alignof(ksc_ctx_t) == 64, "KSC contexto debe alinearse a 64 bytes");
```

### 3.10 Trazabilidad del Tamaño del Contexto

| Componente | Tamaño |
|-----------|--------|
| DPC + Group Varint | 536 bytes |
| TurboPFor | 128 bytes |
| TurboBitByte | 128 bytes |
| DoD | 16 bytes |
| ELF-KSC | 24 bytes |
| RLE+ | 8 bytes |
| Dispatcher | 40 bytes |
| Reserva explícita | 88 bytes |
| Payload estructural | 968 bytes |
| `sizeof(ksc_ctx_t)` alineado | 1024 bytes |

### 3.11 Compatibilidad con VLQ de Klipper

1. El framing lógico de Klipper no cambia.
2. KSC opera sobre payload VLQ o sobre campos ya clasificados por `msgid`.
3. En modo degradado se reinyecta el payload VLQ nativo.
4. La salida KSC nunca obliga a una MCU a ejecutar un algoritmo fuera de su perfil.
5. La expansión máxima se evita con bypass temprano a RAW/VLQ.

### 3.12 Perfiles de Rendimiento Predefinidos

| Perfil | Reloj MCU | RAM | DMA | DSP/FPU | Estrategia |
|--------|-----------|-----|-----|---------|------------|
| Baja capacidad | 16–48 MHz | 8–32 KB | no o limitado | no | Full Offload, DPC, DoD, RLE+, RAW |
| Media capacidad | 48–120 MHz | 32–128 KB | sí | limitado | Híbrido, DPC, DoD, ELF-KSC, RAW, TurboBitByte selectivo |
| Alta capacidad | ≥ 120 MHz | ≥ 128 KB | sí | sí | Directo o Híbrido, DPC, TurboPFor, TurboBitByte, DoD, ELF-KSC, PCOM |

### 3.13 Dispatcher por `msgid`

| Tipo de mensaje | `msgid` | Algoritmo base | Perfil preferente |
|-----------------|---------|----------------|-------------------|
| `queue_step` | `0x10` | DPC + Group Varint | baja / media / alta |
| `intervals` | `0x12` | TurboPFor o DPC | media / alta |
| `get_clock` | `0x14` | DoD | todos |
| `query_analog_in` | `0x15` | ELF-KSC | media / alta |
| `spi_transfer`, `spi_send` | `0x16`, `0x17` | TurboBitByte o RAW | media / alta |
| `queue_digital_out`, `queue_pwm_out` | `0x18`, `0x19` | RLE+ | todos |
| plugins negociados | `0x30–0x3f` | PCOM | alta |
| no clasificado | otro | RAW/VLQ | todos |

### 3.14 Invariantes Operativos de KSC

- como máximo un codec principal por mensaje, salvo DPC + Group Varint
- si el beneficio cae por debajo de **1.10:1**, se prioriza **RAW/VLQ**
- el estado mínimo exigido a una MCU **LOW** se mantiene en **≤ 64 bytes**
- el bridge no bloquea el datapath esperando PCOM
- toda decisión de codec es reversible a modo directo

### 3.15 Pipeline Operativo de KSC

Codificación:

```text
VLQ Klipper -> clasificación por msgid -> selección de algoritmo -> codificación KSC ->
validación ratio/coste -> framing bridge -> adaptación de enlace
```

Decodificación:

```text
trama bridge -> validación CRC/seq -> reensamblado -> selección de decoder ->
reconstrucción KSC -> payload VLQ o flujo simplificado -> entrega
```

Pseudocódigo:

```c
uint8_t ksc_encode_frame(ksc_ctx_t *ctx, const ksc_input_t *in, ksc_output_t *out) {
    uint8_t algo = ksc_select_algorithm(in->msgid, ctx->dispatcher.profile_id, ctx->dispatcher.offload_mode);
    uint32_t raw_len = in->len;
    uint32_t enc_len = ksc_run_codec(ctx, algo, in, out);

    if (enc_len >= raw_len || !ksc_ratio_is_useful(raw_len, enc_len)) {
        return ksc_emit_raw(in, out);
    }

    return algo;
}
```

### 3.16 Límites de Expansión y Presupuesto Temporal

| Algoritmo | Expansión permitida | Presupuesto temporal |
|-----------|----------------------|----------------------|
| DPC + Group Varint | `raw_len + 8` bytes | ≤ 0.10 ms |
| TurboPFor | `raw_len + 16` bytes | ≤ 0.20 ms |
| TurboBitByte | `raw_len + 16` bytes | ≤ 0.20 ms |
| DoD | `raw_len + 8` bytes | ≤ 0.05 ms |
| ELF-KSC | `raw_len + 12` bytes | ≤ 0.10 ms |
| RLE+ | `raw_len + 4` bytes | ≤ 0.05 ms |
| PCOM | nunca superior a `raw_len + 32` | negociado |

### 3.17 Interfaz de Plugins PCOM

```c
typedef struct {
    uint8_t plugin_id;
    uint8_t version_major;
    uint8_t version_minor;
    uint8_t max_expansion_bytes;
    uint8_t preferred_profiles;
    uint8_t flags;
    uint16_t max_block_size;
} ksc_pcom_caps_t;
```

Política:

1. el host anuncia `ksc_pcom_caps_t`
2. el RP2350 valida memoria y latencia
3. el bridge publica el plugin en `algo_bitmap`
4. si el destino no acepta la extensión, el flujo sigue en RAW/VLQ o en un codec nativo

---

## 4. Lógica de Decisión, Perfiles y Offloading

### 4.1 Características de MCUs Soportadas

| Clase | Familias representativas | Rasgos relevantes | Perfil por defecto |
|-------|---------------------------|-------------------|--------------------|
| 8-bit y muy baja gama | AVR ATmega, AT90USB | 16–20 MHz, 8–16 KB RAM, sin DMA | LOW |
| 32-bit básica | STM32F0/F1, SAMD21, LPC176x, SAM3X8E | 48–100 MHz, DMA parcial | LOW o MEDIUM |
| 32-bit media | STM32G0/G4, RP2040, HC32F460, SAMC21 | 64–150 MHz, DMA usable | MEDIUM |
| 32-bit alta | STM32F7/H7, SAME5x, RP2350 | ≥ 120 MHz, DMA sólido, DSP/FPU | HIGH |
| Especiales | Linux MCU y coprocesadores | pueden operar como peer o bypass | HIGH o política dedicada |

Normalización:

| Campo | LOW | MEDIUM | HIGH |
|------|-----|--------|------|
| `clock_mhz` | 16–47 | 48–119 | ≥ 120 |
| `ram_kb` | 8–31 | 32–127 | ≥ 128 |
| DMA | no requerido | recomendable | obligatorio para directo sostenido |
| DSP/FPU | no | opcional | recomendable |
| `bus_width_bits` | 1 | 1–2 | 2–4 |
| `max_payload_log2` | 4–5 | 5–6 | 6–10 |

### 4.2 Política de Puntuación

```text
mcu_score =
  clock_score +
  ram_score +
  dma_score +
  dsp_score +
  bus_score -
  stall_penalty -
  error_penalty
```

| `mcu_score` | Modo seleccionado |
|-------------|-------------------|
| `< 40` | Full Offload |
| `40–74` | Híbrido |
| `≥ 75` | Directo o Híbrido selectivo |

Perfiles resumidos:

| Perfil | Algoritmos permitidos | Enlace preferente | Política |
|--------|-----------------------|-------------------|----------|
| LOW | RAW, DPC, DoD, RLE+ | SPI SDR, UART, CAN 2.0 | Full Offload |
| MEDIUM | DPC, DoD, ELF, RLE+, TurboBitByte selectivo | SPI SDR, DSPI DDR, CAN FD | Híbrido |
| HIGH | conjunto completo KSC | DSPI DDR, QSPI DDR, Ethernet MACRAW | Directo o Híbrido selectivo |

### 4.3 Implementación de Referencia

```c
typedef enum {
    KSC_MODE_DIRECT = 0,
    KSC_MODE_HYBRID = 1,
    KSC_MODE_FULL = 2
} ksc_offload_mode_t;

typedef struct {
    uint16_t clock_mhz;
    uint16_t ram_kb;
    uint8_t has_dma;
    uint8_t has_dsp;
    uint8_t bus_width_bits;
    uint8_t max_payload_log2;
    uint16_t recent_stalls;
    uint16_t recent_crc_errors;
} ksc_mcu_caps_t;

static inline uint8_t ksc_profile_score(const ksc_mcu_caps_t *caps) {
    uint8_t score = 0;
    score += (caps->clock_mhz >= 120) ? 30 : (caps->clock_mhz >= 48) ? 18 : 8;
    score += (caps->ram_kb >= 128) ? 20 : (caps->ram_kb >= 32) ? 12 : 4;
    score += caps->has_dma ? 12 : 0;
    score += caps->has_dsp ? 10 : 0;
    score += (caps->bus_width_bits >= 4) ? 12 : (caps->bus_width_bits >= 2) ? 8 : 4;
    score -= (caps->recent_stalls > 32) ? 12 : 0;
    score -= (caps->recent_crc_errors > 4) ? 12 : 0;
    return score;
}

static inline ksc_offload_mode_t ksc_select_mode(const ksc_mcu_caps_t *caps) {
    uint8_t score = ksc_profile_score(caps);
    if (score < 40) return KSC_MODE_FULL;
    if (score < 75) return KSC_MODE_HYBRID;
    return KSC_MODE_DIRECT;
}
```

Regla de adaptación por beneficio:

```text
si compression_ratio < 1.10 y cpu_cost_ratio > 1.00 => degradar a RAW
si compression_ratio >= 1.10 y cpu_cost_ratio <= 0.65 => promover algoritmo KSC
si compression_ratio >= 2.00 y buffer_occupancy < 70% => permitir coalescing
```

### 4.4 Renegociación Dinámica

Se fuerza renegociación si se cumple cualquiera:

- `READY` bajo durante más del **15%** de la ventana
- más de **3** errores CRC consecutivos
- tiempo medio de servicio del ring superior a **2 ms**
- uso persistente de buffer por encima del **85%**
- pérdida de sincronía de secuencia

Matriz resumida:

| Condición dominante | Acción |
|---------------------|--------|
| MCU LOW + `READY` estable | Full Offload y algoritmo mínimo |
| MCU MEDIUM + ratio > 1.5:1 | Híbrido con compresión selectiva |
| MCU HIGH + carga corta | Directo con framing KSC |
| CAN 2.0 saturado | más compresión y fragmento menor |
| Ethernet estable | coalescing agresivo y ventanas amplias |
| error persistente o ratio inútil | bypass directo |

---

## 5. Adaptadores de Transporte

### 5.1 Matriz Normativa de Payloads

| Protocolo | Velocidad nominal | Payload máximo teórico | MTU o unidad | Overhead típico | Inter-frame | Payload KSC corregido |
|-----------|-------------------|------------------------|--------------|-----------------|-------------|-----------------------|
| USB 1.1 Bulk FS | 12 Mbit/s | 64 bytes | 64 bytes | ≈ 13 bytes lógicos | 1 ms | 64 bytes |
| USB 2.0 Bulk HS | 480 Mbit/s | 512 bytes | 512 bytes | ≈ 13 bytes lógicos | 125 µs | 512 bytes |
| CAN 2.0A/B | hasta 1 Mbit/s | 8 bytes | 8 bytes | trama base + intermisión | 3 bits | 8 bytes |
| CAN FD ISO | 1 Mbit/s arbitraje + 2–8 Mbit/s datos | 64 bytes | 64 bytes | control FD + stuffing + CRC | 3 bits | 64 bytes |
| Serial UART/RS-232/RS-485 | 115200–921600 bit/s | flujo continuo | no aplica | 2 bits por byte en 8N1 | continuo | 16, 24, 32 y 64 bytes |
| Ethernet MACRAW | 10/100 Mbit/s FD | 1500 bytes | 1500 bytes | 14 B MAC + 4 B FCS + 20 B wire extras | 96 bit-times | 256 bytes inicial, 1500 máximo |

### 5.2 USB 1.1 y USB 2.0

| Parámetro | USB 1.1 FS Bulk | USB 2.0 HS Bulk |
|-----------|------------------|-----------------|
| Velocidad nominal | 12 Mbit/s | 480 Mbit/s |
| Payload máximo | 64 bytes | 512 bytes |
| Intervalo base | 1 ms | 125 µs |
| Serialización del payload máximo | 42.67 µs | 8.53 µs |
| Serialización de 64 bytes | 42.67 µs | 1.07 µs |

Reglas:

- detectar y fijar si el enlace está en full-speed o high-speed
- usar **64 bytes** sólo en full-speed
- usar **512 bytes** en Bulk high-speed y agrupar mensajes pequeños
- degradar a política de **64 bytes** si el enlace renegocia a full-speed

### 5.3 Serial

| Baud rate | Fragmento preferente | Ventana TX | Política |
|-----------|----------------------|------------|----------|
| 115200 | 16 bytes | 2 tramas | Full Offload |
| 230400 | 24 bytes | 3 tramas | Híbrido |
| 460800 | 32 bytes | 4 tramas | Híbrido o Directo selectivo |
| 921600 | 64 bytes | 4 tramas | Híbrido y RAW para mensajes cortos |

### 5.4 CAN 2.0

| Parámetro | Diseño |
|-----------|--------|
| Payload físico | 8 bytes |
| Cabecera KSC por fragmento | 2 bytes |
| Datos útiles por fragmento | 6 bytes |
| Recuperación | CRC-8 + paridad XOR por grupo |
| Objetivo | ≥ 100 KB/s equivalentes |

Estrategia:

- fragmentación automática en grupos **4+1**
- la quinta trama transporta paridad XOR
- se corrige una corrupción única por grupo

### 5.5 CAN FD

| Parámetro | Diseño |
|-----------|--------|
| Payload físico | hasta 64 bytes |
| Cola de prioridad | 4 niveles |
| Fragmentación preferente | 48 bytes útiles |
| Integridad | CRC hardware + CRC-16 KSC |

### 5.6 Ethernet MACRAW

| Parámetro | Diseño |
|-----------|--------|
| Enlace físico | 10BASE-T / 100BASE-TX full duplex |
| Throughput on-wire de diseño | ≤ 95 Mbit/s por dirección |
| MTU de arranque | 256 bytes |
| MTU máximo | 1500 bytes |
| Ruta física | MACRAW L2 sobre RMII + DMA |
| Objetivo equivalente | ≥ 80 Mbit/s |

Reglas:

1. iniciar en **256 bytes**
2. probar **512**, **1024** y **1500**
3. aceptar el mayor MTU cuya latencia de cola no aumente más de **12%**
4. retroceder si aparece pérdida, overrun o jitter no determinista

### 5.7 UniBus

UniBus es el plano local RP2350↔MCU y soporta:

- SPI SDR
- DSPI DDR
- QSPI DDR
- control de flujo por **READY**
- cambio de modo sin intervención de CPU en el datapath

---

## 6. Integridad, Recuperación y Observabilidad

### 6.1 Capas de Detección y Corrección

| Capa | Mecanismo | Acción |
|------|-----------|--------|
| Cabecera KSC | CRC-8 o validación duplicada | descarte temprano |
| Trama completa | CRC-16-CCITT | retransmisión o degradación |
| Grupo CAN | XOR parity 4+1 | corrección de un fragmento |
| Secuencia | `seq` monotónico | resincronización |
| Salud de buffer | watermarks | backpressure y renegociación |

### 6.2 Política de Fallback

Se conmuta a **Directo** si:

- el contador de CRC falla **3** veces consecutivas
- expira el watchdog durante **8 ms**
- falla la recuperación de paridad
- el buffer supera el **95%** de ocupación sostenida

Secuencia:

1. congelar emisión
2. vaciar fragmentos parciales
3. emitir `OFFLOAD_ABORT`
4. conmutar a `KSC_MODE_DIRECT`
5. reemitir desde el último `seq` confirmado

Tiempo objetivo de recuperación: **< 100 ms**

### 6.3 Logging con Timestamp Preciso

```c
typedef struct {
    uint64_t time_us;
    uint32_t cycle_stamp;
    uint16_t code;
    uint8_t severity;
    uint8_t session_id;
    uint32_t arg0;
    uint32_t arg1;
} ksc_log_event_t;
```

### 6.4 Códigos de Evento Obligatorios

| Código | Evento |
|--------|--------|
| `0x0001` | inicio de sesión |
| `0x0002` | selección de perfil |
| `0x0003` | cambio de modo de offloading |
| `0x0101` | CRC de cabecera fallido |
| `0x0102` | CRC de payload fallido |
| `0x0103` | corrección XOR aplicada |
| `0x0201` | buffer high watermark |
| `0x0202` | buffer overflow |
| `0x0301` | fallback a modo Directo |
| `0x0302` | recuperación desde modo degradado |

---

## 7. Rendimiento y Optimizaciones

### 7.1 Optimizaciones de Plataforma

- ejecución crítica desde **SRAM6-7**
- stacks separados en **Scratch X/Y**
- **PIO1 SM0** dedicado a UniBus y handshake
- buffer de salida aislado en **SRAM4**
- esquema **Ping/Pong** en **SRAM0/1**

### 7.2 Presupuesto Temporal del Protocolo

Supuesto base para **1 KB** lógico:

- compresión media equivalente: **5.208:1**
- payload on-wire resultante: **≈ 197 bytes**
- control adicional KSC y fragmentación: **≤ 32 bytes**
- carga total a transportar: **≤ 229 bytes**

```text
1024 / 5.208 = 196.62 bytes
196.62 + 32 = 228.62 bytes
```

### 7.3 Latencia Total de Referencia

| Etapa | USB 1.1 | USB 2.0 | Serial 921600 | CAN 2.0 equivalente | Ethernet MACRAW |
|-------|----------|----------|---------------|---------------------|-----------------|
| Clasificación + selección | 0.05 ms | 0.05 ms | 0.05 ms | 0.05 ms | 0.05 ms |
| Compresión / adaptación | 0.10 ms | 0.10 ms | 0.10 ms | 0.10 ms | 0.10 ms |
| Fragmentación + CRC | 0.05 ms | 0.05 ms | 0.05 ms | 0.15 ms | 0.05 ms |
| Transporte de 229 bytes | 0.16 ms + programación de frame | 0.004 ms + microframe | 2.48 ms | 1.83 ms equivalente | 0.03 ms |
| Reensamblado / entrega | 0.10 ms | 0.08 ms | 0.15 ms | 0.20 ms | 0.08 ms |
| Total de diseño | **0.46–1.45 ms** | **0.28–0.41 ms** | **2.83 ms** | **2.33 ms** | **0.31 ms** |

### 7.4 Throughput Objetivo por Transporte

| Transporte | Criterio |
|------------|----------|
| USB 1.1 | hasta 12 Mbit/s brutos con 64 bytes auditados |
| USB 2.0 | hasta 480 Mbit/s brutos con 512 bytes auditados y brecha ≤ 5% |
| CAN 2.0 | payload auditado de 8 bytes y brecha ≤ 8% |
| CAN FD | payload auditado de 64 bytes y brecha ≤ 8% |
| Serial | brecha ≤ 3% frente al throughput físico configurado |
| Ethernet MACRAW | payload auditado de 1500 bytes y brecha ≤ 5% por dirección |

### 7.5 Compatibilidad con MCUs de 16 MHz y 8 KB RAM

- descompresión por delta simple o RLE+
- fragmento máximo de **16 bytes**
- sin heap
- estado local de decodificación **≤ 64 bytes**
- modo **Full Offload** por defecto

### 7.6 Soporte Multi-Dispositivo

- hasta **5 dispositivos simultáneos**
- identificador de sesión de **3 bits útiles**
- planificador **round-robin ponderado**
- cuotas por clase: movimiento `8`, control `4`, telemetría `1`
- latencia objetivo de cambio de sesión **≤ 50 µs**

### 7.7 Consumo Energético

Política:

- **150 MHz** nominal
- **300 MHz** sólo en ráfagas
- clock gating de PIO no usado
- reducción de reloj Ethernet cuando MACRAW no se usa
- reducción automática a modo Directo si la compresión no aporta beneficio

Estado: pendiente en hardware.

---

## 8. Implementación de Referencia

### 8.1 Contexto de Sesión

```c
typedef struct {
    ksc_ctx_t codec;
    ksc_ring_t host_rx;
    ksc_ring_t host_tx;
    ksc_ring_t mcu_rx;
    ksc_ring_t mcu_tx;
    uint16_t tx_seq;
    uint16_t rx_seq;
    uint8_t session_id;
    uint8_t profile_id;
    uint8_t transport_id;
    uint8_t offload_mode;
    uint8_t link_mode;
    uint8_t flags;
    uint8_t active_algo_h2m;
    uint8_t active_algo_m2h;
    uint16_t ack_seq;
    uint16_t negotiated_mtu;
    uint16_t crc_error_budget;
    ksc_mcu_caps_t caps;
} ksc_bridge_session_t;
```

API mínima:

```c
bool ksc_ring_try_push(ksc_ring_t *ring, uint32_t bytes);
bool ksc_ring_try_pop(ksc_ring_t *ring, uint32_t bytes);
uint32_t ksc_ring_level(const ksc_ring_t *ring);
uint32_t ksc_ring_space(const ksc_ring_t *ring);
```

### 8.2 Selector de Algoritmo

```c
uint8_t ksc_select_algorithm(uint8_t msgid, uint8_t profile_id, uint8_t offload_mode, uint8_t feature_bitmap) {
    switch (msgid) {
        case 0x10:
            return KSC_ALGO_DPC_VARINT;
        case 0x12:
            return (profile_id >= KSC_PROFILE_MEDIUM) ? KSC_ALGO_TURBOPFOR : KSC_ALGO_DPC_VARINT;
        case 0x14:
            return KSC_ALGO_DOD;
        case 0x15:
            return (profile_id == KSC_PROFILE_LOW) ? KSC_ALGO_RAW : KSC_ALGO_ELF;
        case 0x16:
        case 0x17:
            return (offload_mode == KSC_MODE_FULL) ? KSC_ALGO_RAW : KSC_ALGO_TURBOBITBYTE;
        case 0x18:
        case 0x19:
            return KSC_ALGO_RLE;
        case 0x30:
        case 0x31:
        case 0x32:
        case 0x33:
        case 0x34:
        case 0x35:
        case 0x36:
        case 0x37:
        case 0x38:
        case 0x39:
        case 0x3a:
        case 0x3b:
        case 0x3c:
        case 0x3d:
        case 0x3e:
        case 0x3f:
            return (feature_bitmap & KSC_FEATURE_PCOM) ? KSC_ALGO_PCOM : KSC_ALGO_RAW;
        default:
            return KSC_ALGO_RAW;
    }
}
```

### 8.3 Handshake UniBus de Referencia

```c
typedef struct {
    uint16_t protocol_version;
    uint16_t mcu_profile_id;
    uint16_t clock_mhz;
    uint16_t ram_kb;
    uint8_t transport_bitmap;
    uint8_t feature_bitmap;
    uint8_t max_bus_width;
    uint8_t max_payload_log2;
} ksc_hello_t;

typedef struct {
    uint8_t selected_profile;
    uint8_t selected_mode;
    uint8_t selected_link;
    uint8_t window_size;
    uint16_t algo_bitmap;
    uint16_t preferred_mtu;
    uint16_t crc_budget;
} ksc_offload_req_t;
```

Secuencia:

1. `HELLO_REQ`
2. `HELLO_ACK`
3. `OFFLOAD_REQ`
4. `OFFLOAD_ACK`
5. `SYNC_START`
6. `STREAM_ACTIVE`

### 8.4 Carga Dinámica de UniBus

```c
typedef enum {
    KSC_LINK_SPI_SDR = 0,
    KSC_LINK_DSPI_DDR = 1,
    KSC_LINK_QSPI_DDR = 2
} ksc_link_mode_t;

void ksc_configure_unibus(PIO pio, uint sm, ksc_link_mode_t mode, uint32_t sck_hz) {
    uint entry = (mode == KSC_LINK_QSPI_DDR) ? universal_asym_bus_offset_entry_qspi_ddr :
                 (mode == KSC_LINK_DSPI_DDR) ? universal_asym_bus_offset_entry_dspi_ddr :
                                                universal_asym_bus_offset_entry_spi_sdr;
    pio_sm_config cfg = universal_asym_bus_program_get_default_config(0);
    sm_config_set_clkdiv(&cfg, (float)clock_get_hz(clk_sys) / (sck_hz * 3));
    pio_sm_init(pio, sm, entry, &cfg);
    pio_sm_set_enabled(pio, sm, true);
}
```

### 8.5 Programa PIO de Stall Atómico

```pio
.program universal_asym_bus
.side_set 1 opt

public entry_qspi_ddr:
    jmp pin qspi_tx
    jmp entry_qspi_ddr
qspi_tx:
    out pins, 4     side 1
    out pins, 4     side 0
    jmp entry_qspi_ddr

public entry_dspi_ddr:
    jmp pin dspi_tx
    jmp entry_dspi_ddr
dspi_tx:
    out pins, 2     side 1
    out pins, 2     side 0
    jmp entry_dspi_ddr

public entry_spi_sdr:
    jmp pin spi_tx
    jmp entry_spi_sdr
spi_tx:
    out pins, 1     side 1
    nop             side 0
    jmp entry_spi_sdr
```

### 8.6 Planificador Multi-Sesión

```c
uint8_t ksc_pick_next_session(const uint8_t *credits, const uint8_t *pending, uint8_t session_count, uint8_t last) {
    for (uint8_t step = 1; step <= session_count; step++) {
        uint8_t idx = (last + step) % session_count;
        if (pending[idx] && credits[idx]) return idx;
    }
    return last;
}
```

### 8.7 Organización de Código Esperada

```text
klippy/chelper/ksc_bridge/
├── include/
│   ├── ksc_bridge.h
│   ├── ksc_codec.h
│   ├── ksc_transport.h
│   ├── ksc_unibus.h
│   ├── ksc_policy.h
│   └── ksc_log.h
├── src/
│   ├── ksc_bridge.c
│   ├── ksc_codec.c
│   ├── ksc_transport_usb.c
│   ├── ksc_transport_serial.c
│   ├── ksc_transport_can.c
│   ├── ksc_transport_canfd.c
│   ├── ksc_transport_eth.c
│   ├── ksc_unibus.c
│   ├── ksc_policy.c
│   └── ksc_log.c
└── tests/
    ├── test_codec.c
    ├── test_transport.c
    ├── test_policy.c
    └── test_failover.c
```

---

## 9. Testing y Validación

### 9.1 Matriz de Pruebas Obligatorias

| Categoría | Prueba | Criterio |
|-----------|--------|----------|
| Integridad | reconstrucción byte a byte | 100% exactitud |
| Rendimiento | payload lógico 1 KB | < 5 ms |
| Cobertura | `6 transportes × 3 algoritmos × 2 frecuencias` | 36 combinaciones |
| Muestreo | frames por combinación | ≥ 10 000 |
| Dispersión | desviación estándar relativa | < 1% |
| USB 2.0 | brecha teórico vs medido | ≤ 5% |
| Ethernet | brecha teórico vs medido | ≤ 5% |
| CAN 2.0 / CAN FD | brecha teórico vs medido | ≤ 8% |
| Serial | brecha teórico vs medido | ≤ 3% |
| Compatibilidad | MCU 16 MHz / 8 KB | arranque y operación estable |
| Escalado | 5 sesiones | sin pérdida de paquetes |
| Recuperación | fallo de CRC / stall prolongado | < 100 ms |

Banco automatizado RP2350:

- **150 MHz** nominal sostenido
- **300 MHz** ráfaga para saturación controlada
- puertos: USB 1.1, USB 2.0, CAN 2.0, CAN FD, Serial y Ethernet MACRAW
- algoritmos: `KSC`, `VLQ`, `RAW`

Fórmulas de post-proceso:

```text
throughput_medido_mbps = (sum(payload_util_bytes) * 8) / tiempo_total_s / 1e6
payload_efectivo_promedio = sum(payload_util_bytes) / total_frames
overhead_total_bytes = sum(bytes_totales_on_wire - payload_util_bytes)
eficiencia = (sum(payload_util_bytes) / sum(bytes_totales_on_wire)) * 100
brecha = ((throughput_teorico_mbps - throughput_medido_mbps) / throughput_teorico_mbps) * 100
desviacion_relativa = (desviacion_estandar / media) * 100
```

### 9.2 Casos de Falla

1. corrupción de cabecera
2. corrupción de payload
3. pérdida de fragmento en CAN 2.0
4. saturación del ring de salida
5. oscilación de `READY`
6. cambio de perfil de MCU en caliente
7. renegociación de MTU en Ethernet
8. degradación a modo Directo y recuperación posterior

### 9.3 Instrumentación Requerida

- contadores por sesión
- `DWT_CYCCNT` para la ruta caliente
- timestamps monotónicos de 64 bits
- dumps de secuencia y fragmentación
- histograma de ocupación de buffers
- trazado de renegociaciones

### 9.4 Estado de Validación de esta Revisión

| Métrica | Estado actual |
|---------|---------------|
| Arquitectura del protocolo | derivado |
| Contexto KSC de 1 KiB | derivado y cerrado |
| Lógica de decisión por perfiles | derivado |
| Adaptadores de transporte | auditados y corregidos |
| Latencias y throughput equivalentes | derivados de diseño |
| Consumo eléctrico | pendiente en hardware |
| Cierre de ciclos exactos | pendiente en hardware |

---

## 10. Apéndices

### 10.1 Glosario Operativo

| Término | Definición |
|---------|------------|
| Full Offload | la MCU consume un flujo ya simplificado |
| Híbrido | la MCU ejecuta descompresión ligera o parsing parcial |
| Directo | el payload VLQ se envía sin transformar |
| UniBus | transporte físico unificado negociable en PIO |
| READY stall | pausa atómica por hardware |
| Payload equivalente | bytes lógicos originales transportados tras compresión |

### 10.2 Resumen de Decisiones de Ingeniería

| Decisión | Resultado |
|----------|-----------|
| El protocolo se ejecuta en RP2350 | Sí, con reparto Core0/Core1 |
| El enlace es full-duplex | sí |
| La adaptación es por perfiles | sí |
| UniBus gestiona negociación y stall | sí |
| El fallback conserva compatibilidad Klipper | sí, vía VLQ directo |

### 10.3 Próximos Pasos de Validación

1. medir consumo real a 150 MHz, 200 MHz y 300 MHz
2. validar latencia con cargas reales de Klipper en USB, CAN y Ethernet
3. cerrar tablas de throughput on-wire frente a throughput equivalente
4. validar la corrección XOR sobre CAN 2.0 con ruido inducido
5. cerrar la matriz de compatibilidad con placas concretas de Klipper

### 10.4 Anexo de Trazabilidad de Payloads

| Sección | Valor antiguo | Valor nuevo | Norma |
|---------|---------------|-------------|-------|
| USB | un único límite de 64 bytes | USB 1.1 = 64 bytes, USB 2.0 = 512 bytes | USB 1.1 / USB 2.0 |
| Latencia | una sola columna USB | columnas USB 1.1 y USB 2.0 diferenciadas | 12 Mbit/s y 480 Mbit/s |
| Throughput | objetivo USB genérico | objetivos separados por versión | criterio de campaña |
| Matriz normativa | no existía matriz cruzada | se añade matriz de payload, MTU y overhead | USB, ISO 11898-1, UART 8N1, IEEE 802.3 |

### 10.5 Tabla Resumen de Payloads por Protocolo

| Protocolo | Payload máximo auditado | Observación |
|-----------|-------------------------|-------------|
| USB 1.1 | 64 bytes | Bulk full-speed |
| USB 2.0 | 512 bytes | Bulk high-speed |
| CAN 2.0 | 8 bytes | límite físico |
| CAN FD | 64 bytes | 48 bytes útiles preferentes |
| Serial | 64 bytes máximo operativo | sin MTU normativo |
| Ethernet MACRAW | 1500 bytes | MTU Ethernet II |

### 10.6 Artefactos Reproducibles de Medición

Generador:

```bash
python klippy/scripts/ksc_benchmark_assets.py
```

Artefactos:

- `ksc_protocol_summary.csv`
- `ksc_protocol_summary.tex`
- `ksc_measurement_master.csv`
- `ksc_measurement_master.tex`
- `ksc_raw_frames_template.csv`
- `ksc_dashboard.html`

Regla operativa:

1. capturar datasets crudos con prefijo `ksc_raw_frames`
2. reejecutar el generador para recalcular throughput, eficiencia, brecha y desviación
3. aceptar la campaña sólo cuando cada combinación cumpla los límites definidos en la sección 9.1

---

**Fin del Informe Técnico**

**Documento:** KSC-TECH-RPT-002A  
**Versión:** 0.1.1  
**Estado:** Especificación consolidada del protocolo KSC  
**Próxima Revisión:** Tras validación en hardware RP2350
