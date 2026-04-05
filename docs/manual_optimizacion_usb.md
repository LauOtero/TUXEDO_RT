# Manual Técnico: Optimización y Configuración de USB Dinámico en Klipper (TUXEDO_RT)

## 1. Introducción
La comunicación USB tradicional en Klipper utiliza un tamaño de bloque estático y conservador (generalmente optimizado para la latencia en hardware más antiguo o en conexiones serie emuladas). Con la introducción de **TUXEDO_RT**, hemos rediseñado la capa de transporte para detectar automáticamente la versión y velocidad del puerto USB conectado a la placa controladora (MCU).

Esta detección permite ajustar **dinámicamente** el tamaño del payload (carga útil) de las transferencias masivas (Bulk Transfers) para maximizar el ancho de banda y reducir la sobrecarga (overhead) del procesador, adaptándose perfectamente a:
- **USB 1.x (Full Speed, 12 Mbps):** Payload de 64 bytes.
- **USB 2.0 (High Speed, 480 Mbps):** Payload de 512 bytes.
- **USB 3.x (SuperSpeed, 5+ Gbps):** Payload de 1024 bytes.

## 2. Requisitos Previos
- **Hardware:** 
  - Placa controladora (MCU) conectada vía USB nativo al host (Raspberry Pi, PC, etc.).
  - Cable USB de datos adecuado (con buen blindaje para evitar interferencias EMI).
  - Para aprovechar USB 3.x, tanto el host como la MCU (si existe alguna compatible) deben soportarlo.
- **Software:** 
  - Sistema operativo Linux (requerido para la lectura de la topología USB mediante `sysfs` en `/sys/class/tty/`).
  - Klipper con la extensión TUXEDO_RT instalada.

## 3. Configuración en `printer.cfg`

### Parámetros de Configuración [mcu]

A continuación se detallan los parámetros disponibles para la sección `[mcu]` cuando se utiliza la interfaz USB/UART en TUXEDO_RT:

| Parámetro | Tipo | Requerido | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| **serial** | `str` | **SÍ** | N/A | Ruta del dispositivo serial (ej. `/dev/serial/by-id/...`). |
| **baud** | `int` | No | `250000` | Velocidad en baudios. Ignorado en conexiones USB nativas. |
| **rts** | `bool` | No | `True` | Activa/Desactiva la señal Request To Send (RTS) en el puerto. |
| **usb_optimize** | `bool` | No | `True` | **TUXEDO_RT:** Si se establece en `False`, desactiva la detección de velocidad USB y utiliza el perfil de payload original de Klipper. |

**Ejemplo de configuración:**
```ini
[mcu]
serial: /dev/serial/by-id/usb-Klipper_stm32f446xx_12345678-if00
baud: 250000
rts: True
# Para forzar el comportamiento original de Klipper (sin optimización dinámica):
# usb_optimize: False
```

---

## 4. Perfiles de Optimización (Automáticos)

Aunque la optimización es automática, es importante conocer los valores de `max_pending_blocks` que TUXEDO_RT aplica internamente según la velocidad detectada del bus USB. Estos valores controlan cuántos bloques de mensajes se pueden encolar antes de realizar una transferencia masiva (Bulk Transfer).

| Velocidad Detectada | Versión USB | `max_pending_blocks` | Payload (Bytes) |
| :--- | :--- | :--- | :--- |
| **1.5 / 12 Mbps** | USB 1.x (Full Speed) | `1` | **64 bytes** |
| **480 Mbps** | USB 2.0 (High Speed) | `8` | **512 bytes** |
| **5000 / 10000 Mbps** | USB 3.x (SuperSpeed) | `16` | **1024 bytes** |
| **unknown** (UART) | N/A | `12` | **768 bytes** (Fallback) |

---

## 5. Verificación y Diagnóstico

Para confirmar que la optimización se está aplicando correctamente, revise el archivo de registro `klippy.log` después de iniciar el sistema.

### Mensajes de Éxito Esperados
Debería ver una línea indicando la velocidad detectada y el ajuste del payload:

**Para USB 2.0 (Más común en placas modernas como Octopus, Spider, etc.):**
```text
mcu 'mcu': Detected USB High-Speed (2.0) - Optimizing payload to 512 bytes
```

**Para USB 1.x (Placas antiguas o conexiones a través de hubs lentos):**
```text
mcu 'mcu': Detected USB Full-Speed (1.x) - Optimizing payload to 64 bytes
```

### Comportamiento por Defecto (Fallback)
Si el sistema operativo no expone la información a través de `sysfs` (por ejemplo, en un entorno de pruebas no Linux o si es un puerto serie virtual UART directo), el sistema usará el tamaño seguro por defecto (equivalente a `max_blocks = 12`) y registrará lo siguiente:
```text
mcu 'mcu': USB speed not detected or not applicable (speed=unknown), using default payload
```

## 5. Solución de Problemas Comunes

| Síntoma / Error | Causa Probable | Solución |
|-----------------|----------------|----------|
| **La MCU se desconecta aleatoriamente** | Cable USB de mala calidad o demasiado largo para USB 2.0 High Speed. | Cambie a un cable USB apantallado, más corto o con núcleo de ferrita. |
| **Aparece "USB speed not detected" en USB nativo** | El sistema operativo tiene restricciones de permisos en `/sys/` o se está ejecutando en un contenedor Docker sin acceso a host. | Verifique que el usuario `pi` o `klipper` tenga permisos de lectura, o pase `/sys` como volumen en Docker. |
| **Latencia alta en impresiones de alta velocidad** | Posible saturación del bus USB debido a otros dispositivos (webcams, discos duros). | Conecte la MCU a un puerto USB dedicado (directo a la placa base, sin hubs compartidos). |

## 6. Arquitectura Interna (Para Desarrolladores)
- **Capa Python:** `klippy/rtcore/transport/transport_uart.py` -> Contiene `detect_usb_speed()` y `setup_serialqueue()`.
- **Capa C:** `klippy/chelper/serialqueue.c` -> La variable estática se ha sustituido por `sq->max_pending_blocks`, configurable a través de la API FFI `serialqueue_set_usb_profile()`.
- **Tests:** Las pruebas de integración se encuentran en `tests/test_usb_payload.py`, asegurando que el motor de inferencia de velocidad asigne correctamente los perfiles.