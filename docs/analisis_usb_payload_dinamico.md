# 📋 INFORME DE ANÁLISIS: Optimización Dinámica del Payload USB
## Klipper 3D - Análisis de Protocolo USB y Detección Dinámica de Velocidad

**Documento:** KLIPPER-USB-DYN-PAYLOAD-2026-03
**Clasificación:** Ingeniería de Software - Optimización de Transporte
**Fecha:** 31 de Marzo de 2026

---

## 🎯 1. RESUMEN EJECUTIVO

Este documento presenta un análisis profundo sobre la adaptación dinámica del payload de comunicaciones en Klipper basada en la versión del protocolo USB subyacente. El objetivo principal es maximizar el rendimiento del enlace de datos entre el Host y el MCU ajustando el tamaño del bloque de transmisión para que coincida de forma óptima con los límites de transferencia Bulk de USB 1.x, 2.0 y 3.x.

## 🔬 2. ANÁLISIS DEL PROTOCOLO USB EN EL CONTEXTO DE KLIPPER

El estándar USB utiliza diferentes tamaños máximos para las transferencias de tipo "Bulk", que son las empleadas típicamente por los conversores Serie-USB (como CDC ACM, FTDI, CH340, etc.):

- **USB 1.x (Full Speed, 12 Mbps):** Tamaño máximo de paquete Bulk = **64 bytes**.
- **USB 2.0 (High Speed, 480 Mbps):** Tamaño máximo de paquete Bulk = **512 bytes**.
- **USB 3.x (SuperSpeed, 5+ Gbps):** Tamaño máximo de paquete Bulk = **1024 bytes**.

### 2.1. Arquitectura Actual de Klipper

Klipper utiliza un tamaño de bloque máximo (`MESSAGE_MAX`) de 64 bytes. La capa de transporte en C (`serialqueue.c`) agrupa estos mensajes en bloques pendientes (`MAX_PENDING_BLOCKS = 12`), escribiendo hasta 768 bytes en el descriptor de archivo (FD) por cada ciclo del reactor.

**Problema Actual:**
Al escribir ráfagas estáticas de 768 bytes, el sistema operativo fragmenta los datos según las capacidades del endpoint USB:
- En USB 1.x: Se generan múltiples interrupciones (overhead alto).
- En USB 2.0: 768 bytes se dividen en un paquete de 512 y otro de 256, lo cual es ineficiente.
- En USB 3.x: No se aprovecha el paquete máximo de 1024 bytes, perdiendo potencial de throughput.

## 🛠️ 3. ESTRATEGIA DE OPTIMIZACIÓN PROPUESTA

Para adaptar la comunicación y maximizar el rendimiento sin modificar la estructura fundamental de framing del firmware (lo que rompería la compatibilidad hacia atrás), proponemos un ajuste dinámico de los "Pending Blocks" a nivel de Host.

### 3.1. Detección Dinámica en Python

Implementaremos un mecanismo en Python para inspeccionar el árbol `sysfs` de Linux (`/sys/class/tty/...`) y detectar la velocidad real de negociación del dispositivo USB (`speed`):

- `1.5` o `12` Mbps → Full Speed (USB 1.x)
- `480` Mbps → High Speed (USB 2.0)
- `5000` o `10000` Mbps → SuperSpeed (USB 3.x)

### 3.2. Adaptación Dinámica en `serialqueue.c`

1. **Reemplazo del límite estático:**
   Reemplazaremos la constante `#define MAX_PENDING_BLOCKS 12` por una variable a nivel de instancia `sq->max_pending_blocks`.

2. **Ajuste por perfil USB:**
   - **USB 1.x (64 bytes):** `max_pending_blocks = 1`. Optimiza la latencia garantizando que cada mensaje del host quepa en exactamente un paquete Bulk.
   - **USB 2.0 (512 bytes):** `max_pending_blocks = 8` (8 * 64 = 512 bytes). Alineación perfecta con el tamaño de Bulk USB 2.0, requiriendo un solo ACK a nivel USB por ráfaga completa de Klipper.
   - **USB 3.x (1024 bytes):** `max_pending_blocks = 16` (16 * 64 = 1024 bytes). Alineación perfecta con SuperSpeed.
   - **Fallback (UART puro/CAN):** Mantiene el comportamiento tradicional de 12 bloques.

3. **Nueva API FFI:**
   Crearemos `serialqueue_set_usb_profile(sq, max_pending_blocks)` para que Python comunique al backend en C el perfil detectado.

## 🚀 4. IMPACTO ESPERADO

- **Reducción del Jitter:** Al alinear las ráfagas de escritura con el MTU del USB, el kernel de Linux puede despachar los URBs (USB Request Blocks) sin fragmentación adicional, lo que reduce el jitter introducido por el scheduling del USB host controller.
- **Mejora de Throughput:** USB 2.0/3.0 verán un uso óptimo del ancho de banda, permitiendo saturar el enlace de manera más eficiente en impresoras de altísima velocidad o con gran densidad de datos (ej. sensores de resonancia alta frecuencia).

## 📝 5. PLAN DE IMPLEMENTACIÓN

1. **[C Helper]** Modificar `serialqueue.c` y `serialqueue.h` para soportar `max_pending_blocks` dinámico y redimensionar el buffer estático de transmisión al máximo teórico (16 * 64 = 1024).
2. **[C Helper]** Exponer la función `serialqueue_set_usb_profile` en `__init__.py`.
3. **[Python]** Implementar la detección sysfs en `rtcore/transport/utils.py` o `serialhdl.py`.
4. **[Python]** Enlazar la detección de la ruta USB con el setup de `serialqueue`.