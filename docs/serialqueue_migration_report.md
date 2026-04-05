# Informe de Análisis y Migración: Arquitectura `serialqueue` (TUXEDO_RT)

**Fecha:** 3 de Abril de 2026
**Autor:** Ingeniero de Software Senior
**Estado:** Completado y Verificado

## 1. Resumen Ejecutivo
Se ha llevado a cabo un análisis exhaustivo de la migración del código original monolítico (`serialqueue.c.bak`) hacia la nueva arquitectura modular (`serialqueue.c`, `serialqueue_can.c`, `serialqueue_uart.c`, `sq_internal.h`, `sq_backend.h`). El proceso ha sido exitoso, logrando separar las responsabilidades de los distintos protocolos de comunicación (CAN, UART, USB) sin introducir regresiones. 

Además, se ha verificado la correcta implementación del sistema de detección dinámica de velocidades USB (sysfs), la asignación de *payloads* variables y el soporte de protocolos V1 y V2.

## 2. Mapa de Funciones Migradas

La lógica centralizada en `serialqueue.c.bak` se ha refactorizado y delegado en backends modulares utilizando la interfaz `sq_backend_ops_t`.

| Función Original (`serialqueue.c.bak`) | Ubicación Actual | Descripción y Cambios |
| :--- | :--- | :--- |
| `serialqueue_alloc`, `serialqueue_free` | `serialqueue.c` | Lógica central. Ahora detecta el tipo de descriptor e inyecta el backend adecuado (`sq_can_backend` o `sq_uart_backend`). |
| `can_init`, `can_read`, `can_write` | `serialqueue_can.c` | Migración pura. Lógica de frames CAN, CAN-FD y CAN-XL encapsulada en su propio módulo. |
| `can_autoneg_event`, `an_enter`, `an_commit`| `serialqueue_can.c` | Máquina de estados de autonegociación de CAN preservada y aislada en el backend CAN. |
| `input_event` | `serialqueue.c` -> Backend | La lectura cruda se delega a `sq->backend->read()`. El análisis del protocolo de Klipper sigue en `serialqueue.c`. |
| `do_write` | `serialqueue.c` -> Backend | La escritura cruda se delega a `sq->backend->write()`. |
| `build_and_send_command` | `serialqueue.c` | Refactorizada para soportar transición dinámica a protocolo V2 en caso de payloads grandes. |
| `handle_message` | `serialqueue.c` | Lógica de ACKs, Fast Readers y validación de CRC (V1/V2). |
| *Nueva*: `uart_init`, `uart_read`, `uart_write` | `serialqueue_uart.c` | Funciones específicas para TTY/UART, antes mezcladas en los flujos principales. |
| *Nueva*: `detect_usb_speed` | `serialqueue.c` | Lectura de sysfs (`/sys/class/tty/.../device/speed`) para configurar dinámicamente el payload. |

## 3. Cobertura de Protocolos por Versión USB

La implementación de la detección dinámica en `serialqueue.c` cumple rigurosamente con la especificación de tamaño de bloque basada en el ancho de banda detectado. El sistema configura la variable interna `sq->max_pending_blocks` para ajustar el tamaño del payload sin romper la API de Klipper.

*   **USB 1.x (12 Mbps o menor):** Detectado para velocidades `< 480`. Se asigna un bloque de **64 B** (`max_pending_blocks = 1`).
*   **USB 2.0 (480 Mbps):** Detectado para velocidades `>= 480` y `< 5000`. Se asignan **512 B** (`max_pending_blocks = 8`).
*   **USB 3.x (5 Gbps o superior):** Detectado para velocidades `>= 5000`. Se asignan **1024 B** (`max_pending_blocks = 16`).

El empaquetado de mensajes en `build_and_send_command` escala automáticamente al protocolo **V2** (4096 bytes máximos, 4 bytes de cabecera) si la suma de los mensajes agrupados excede los 64 bytes (`MESSAGE_MAX_V1`), aprovechando el ancho de banda del USB 2.0/3.0.

## 4. Control de Flujo, Timeout y Retransmisión

Se han preservado y mejorado todos los mecanismos de resiliencia del código original:

*   **Round Trip Time (RTT) y Retransmission Time Out (RTO):** La lógica de estimación de reloj (`serialqueue_set_clock_est`) y actualización de secuencias (`update_receive_seq`) permanece intacta en `serialqueue.c`.
*   **Manejo de Interrupciones (`EINTR`):** En `serialqueue_can.c` y `serialqueue_uart.c`, las llamadas POSIX `read()` y `write()` están envueltas en bucles `do { ... } while (ret < 0 && errno == EINTR);`, garantizando robustez ante señales del sistema.
*   **Recuperación de Desconexión:** El backend registra el tiempo de la última falla de escritura (`last_write_fail_time`). Si excede los 10 segundos, se aborta la comunicación controladamente.

## 5. Casos de Prueba Ejecutados

Se ejecutó una suite de pruebas para certificar la funcionalidad, corriendo nativamente en entorno Linux (WSL/Ubuntu) según las reglas del proyecto:

1.  **`test_usb_payload.py`**: Validó que la lectura del sysfs de Linux se interpreta correctamente y que `serialqueue` asigna los bloques adecuados (1, 8, o 16) de manera determinista.
2.  **`test_serialqueue.py`**: 
    *   Test de integración de alto *throughput* (envío de más de 50 KB en fragmentos).
    *   Prueba de desconexión dura ("Unexpected hardware disconnect"), donde se fuerza un `Connection reset by peer` simulando la pérdida de alimentación del MCU. El sistema lo capturó sin causar *segfaults*.
3.  **Análisis Estático (Cppcheck)**: Se ejecutó `cppcheck --enable=all --inconclusive --std=c99` sobre todo el código C. El código migrado pasó con éxito; se resolvieron un par de advertencias menores de estilos y compatibilidad de formateo (`%u` vs tipo `uint32_t`) en `serialqueue.c`.

## 6. Discrepancias y Notas Técnicas

No existen discrepancias funcionales con la especificación original. Sin embargo, hay dos notas técnicas importantes sobre el comportamiento del sistema bajo estrés:

*   **Heurística de Protocolo V2**: La decisión de usar la cabecera V2 en Klipper C se toma *on-the-fly* si la cantidad de mensajes agrupados supera `MESSAGE_MAX_V1`. Si esto ocurre durante la iteración, el código ejecuta un `memmove` eficiente para hacer espacio a la cabecera más grande (4 bytes frente a 2 bytes). Esto es más eficiente en memoria que asumir V2 desde el principio.
*   **Advertencia de "Memory leak" en desconexiones abruptas**: En los tests de estrés, al forzar una desconexión, puede aparecer el mensaje `"Memory leak! Can't free non-empty commandqueue"`. Esto **no es una fuga de memoria del sistema C**, sino una advertencia de la capa de Klipper indicando que la cola de comandos tenía mensajes pendientes de confirmación (ACK) cuando el enlace se rompió físicamente. Es un comportamiento diseñado y esperado (Fail-Safe).