# Manual Técnico de Configuración CAN Bus en TUXEDO_RT / Klipper

## 1. Introducción al Protocolo CAN Bus

El protocolo **CAN (Controller Area Network)** es un estándar de comunicación robusto y diferencial, diseñado originalmente para la industria automotriz y ahora ampliamente adoptado en la impresión 3D de alto rendimiento. En el contexto de Klipper y TUXEDO_RT, el CAN Bus permite conectar múltiples microcontroladores (como placas de cabezal de herramientas o expansiones) utilizando solo cuatro cables: alimentación (V+), tierra (GND), y el par diferencial de datos (CAN_H y CAN_L).

### Ventajas principales:
- **Reducción de cableado:** Elimina la necesidad de cadenas portacables gruesas y pesadas hacia el cabezal.
- **Inmunidad al ruido:** La señal diferencial es altamente resistente a las interferencias electromagnéticas de motores y calentadores.
- **Topología de red:** Permite añadir múltiples nodos esclavos de forma sencilla.

---

## 2. Requisitos Previos

### Hardware:
- **Interfaz Host CAN:** Un adaptador USB a CAN (como U2C de BTT o CANable) o una placa base con puerto CAN nativo (ej. Octopus).
- **Resistencias de terminación:** Es obligatorio tener una resistencia de **120 ohmios** en cada extremo físico del bus (usualmente un puente/jumper en la placa base y otro en el último nodo esclavo).
- **Cableado:** Se recomienda el uso de cable trenzado para CAN_H y CAN_L.

### Software:
- Klipper instalado y actualizado.
- Firmware de la MCU compilado con soporte para CAN Bus.
- Utilidad `can-utils` instalada en el host Linux para diagnóstico.

---

## 3. Configuración en `printer.cfg`

La configuración de un nodo CAN se realiza mediante la sección `[mcu <nombre>]`. TUXEDO_RT extiende las capacidades estándar de Klipper permitiendo parámetros avanzados de autonegociación.

### Parámetros de Configuración:

| Parámetro | Tipo | Descripción |
| :--- | :--- | :--- |
| `canbus_uuid` | Obligatorio | Identificador único de 12 caracteres hexadecimales de la placa. |
| `canbus_interface` | Opcional | Nombre de la interfaz en el host (por defecto `can0`). |
| `canbus_mode` | Opcional | Modo de operación: `classic`, `fd_brs`, `xl` (TUXEDO_RT). |
| `autoneg_retries` | Opcional | Número de intentos de descubrimiento de velocidad (por defecto 3). |

### Ejemplo de Estructura:

```ini
[mcu toolhead]
canbus_uuid: 1a2b3c4d5e6f
canbus_interface: can0
# Opcional: Modo avanzado TUXEDO_RT
canbus_mode: fd_brs
```

---

## 4. Ejemplos por Placa Controladora

### BTT Octopus (v1.1 / Pro)
La Octopus suele actuar como puente USB-a-CAN.
```ini
[mcu]
canbus_uuid: b0b1b2b3b4b5
canbus_interface: can0
```

### BigTreeTech SKR 3 / SKR 3 EZ
```ini
[mcu]
canbus_uuid: c0c1c2c3c4c5
```

### Duet 3 Mini 5+ (Modo CAN)
```ini
[mcu duet]
canbus_uuid: d0d1d2d3d4d5
```

---

## 5. Verificación y Diagnóstico

### Comandos de Validación (Host Linux):

1. **Listar interfaces activas:**
   ```bash
   ip -s link show can0
   ```
2. **Detectar UUIDs disponibles:**
   ```bash
   ~/klippy-env/bin/python ~/klipper/scripts/canbus_query.py can0
   ```
3. **Monitorear tráfico en tiempo real:**
   ```bash
   candump can0
   ```

### Errores Comunes y Soluciones:

- **"Internal error during connect: mcu 'toolhead': Unable to open CAN port"**
  - *Causa:* La interfaz `can0` no está levantada en el sistema operativo.
  - *Solución:* Ejecutar `sudo ip link set can0 up type can bitrate 1000000`.
- **"MCU 'toolhead' shutdown: ADC out of range"**
  - *Causa:* Ruido eléctrico en el bus CAN afectando la integridad de los datos o mala terminación.
  - *Solución:* Verificar la resistencia de 120 ohmios y que el cableado de datos esté alejado de cables de potencia.

---

## 6. Diagramas y Especificaciones Eléctricas

### Cableado Estándar:
- **Rojo:** DC 24V
- **Negro:** GND
- **Amarillo/Blanco:** CAN High (CAN_H)
- **Verde/Azul:** CAN Low (CAN_L)

**Nota:** Los cables CAN_H y CAN_L **deben** estar trenzados entre sí (aproximadamente 20-30 vueltas por metro) para maximizar la inmunidad al ruido.

---

## 7. Glosario Técnico

- **Bitrate:** Velocidad de transmisión de datos (típicamente 250k, 500k o 1M en impresoras 3D).
- **ID de Arbitraje:** Prioridad del mensaje en el bus.
- **Nodo:** Cualquier dispositivo conectado al bus (Host, Toolhead, Placa Base).
- **UUID:** Identificador único universal asignado a cada MCU durante la compilación.
- **XL / FD:** Estándares extendidos de CAN que permiten mayor velocidad y carga útil de datos (Soportados en TUXEDO_RT).

---

## 8. Advertencias de Seguridad

> [!CAUTION]
> **Riesgo Eléctrico:** Nunca conecte o desconecte cables del bus CAN mientras la impresora esté encendida. Los picos de voltaje pueden destruir los transceptores CAN.
> **Polaridad:** Verifique doblemente la polaridad de V+ y GND. Invertirlos dañará permanentemente todos los nodos de la red.
