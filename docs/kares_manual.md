# Manual de Usuario: KARES (Klipper Advanced Resume & Energy Safety System)

## 1. Introducción

**KARES** es un sistema avanzado de recuperación de impresiones 3D diseñado para operar sobre el framework `extra_manager.py` de Klipper. Su objetivo principal es garantizar la integridad de las impresiones ante fallos eléctricos, errores de hardware o interrupciones forzadas, permitiendo reanudar la impresión en el punto exacto donde se detuvo.

### Características Principales
- **Checkpointing Determinístico**: Guarda el estado exacto de la máquina (posiciones, temperaturas, estado G-code) con validación CRC32 por hardware.
- **Gestión Inteligente de Energía (UPS)**: Integración nativa con SAI/UPS mediante protocolo NUT.
- **Protección Térmica Crítica**: Mantiene la cama caliente en materiales técnicos (ABS, ASA, PC) durante cortes de luz para evitar el despegue de la pieza.
- **Soporte Multi-Eje**: Compatible con cinemáticas complejas (CoreXY, Delta, SCARA, IDEX, robots de 5+ ejes).
- **Tiempo Real**: Utiliza la infraestructura de `extra_manager.py` para tareas críticas de baja latencia.

---

## 2. Instalación y Configuración

### 2.1 Requisitos Previos
- Klipper instalado con soporte para `extra_manager.py`.
- Python 3.7+.
- Librerías opcionales: `pynut` o `nut-client` (si se usa backend NUT), `prometheus_client` (para métricas).
- Hardware opcional: UPS compatible con NUT, sensor de temperatura de cabina, ventilador controlable para cabina.

### 2.2 Configuración en `printer.cfg`

Añada la siguiente sección a su archivo `printer.cfg`. Todos los parámetros son opcionales excepto `[kares]`.

```ini
[kares]
# --- Configuración General ---
# Backend de gestión de energía. Opciones: 'nut', 'mock' (pruebas), 'gpio' (futuro)
backend: nut

# Intervalo de sondeo del UPS en segundos (recomendado: 0.05 - 0.1)
poll_interval: 0.05

# Si True, apaga la impresora de forma segura si la batería del UPS está crítica
shutdown_on_fault: True

# Ruta donde se guardan los checkpoints y el WAL (Write-Ahead Log)
checkpoint_path: ~/printer_data/config/kares_checkpoint.json

# Habilitar Write-Ahead Log para mayor seguridad ante caídas bruscas
enable_wal: True

# Habilitar corrección de errores ECC en el checkpoint (requiere más CPU)
enable_ecc: False

# Habilitar exportación de métricas para Prometheus/Grafana
enable_metrics: True

# --- Configuración UPS (NUT) ---
# Dirección IP del servidor NUT (usualmente localhost si corre en la misma Pi)
nut_host: 127.0.0.1

# Puerto del servidor NUT (default: 3493)
nut_port: 3493

# Nombre del UPS configurado en ups.conf
nut_ups_name: ups

# Credenciales de usuario NUT (crear en upsd.users)
nut_username: kares
nut_password: secret_password

# --- Gestión Térmica y Cabina ---
# Nombre del objeto temperature_fan que controla la calefacción de la cabina
# Ejemplo: temperature_fan chamber_heater
chamber_heater_fan: 

# Nombre del sensor de temperatura de la cabina
# Ejemplo: temperature_sensor chamber_temp
chamber_temp_sensor: 

# Temperatura objetivo de la cabina en °C
chamber_target_temp: 45.0

# --- Protección de Cama Caliente (CRÍTICO) ---
# Si True, mantiene la cama caliente incluso si el UPS entra en modo batería.
# VITAL para materiales como ABS, ASA, Policarbonato o Nylon.
bed_keepalive_on_ups: True

# Temperatura mínima a la que se mantendrá la cama en modo batería (°C).
# Se apagarán extruders y cabina para ahorrar energía, pero la cama no bajará de esto.
bed_min_temp_on_ups: 60.0

# --- Soporte Multi-Eje ---
# Prefijos para identificar steppers adicionales (ej. ejes rotativos A, B, C o IDEX)
# Por defecto detecta 'stepper_', 'extruder'. Añada prefijos personalizados aquí.
extra_stepper_prefixes: stepper_, extruder, axis_
```

---

## 3. Referencia Detallada de Parámetros

### 3.1 Parámetros de Conexión y Backend

| Parámetro | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `backend` | String | `nut` | Motor de gestión de energía. `nut`: Protocolo Network UPS Tools. `mock`: Simula un UPS para pruebas sin hardware. |
| `nut_host` | String | `127.0.0.1` | IP del servidor `upsd`. |
| `nut_port` | Integer | `3493` | Puerto TCP del servidor `upsd`. |
| `nut_ups_name` | String | `ups` | Identificador del UPS definido en la configuración del servidor NUT. |
| `nut_username` | String | `monuser` | Usuario para autenticación en el servidor NUT. |
| `nut_password` | String | `secret` | Contraseña del usuario NUT. |
| `poll_interval` | Float | `0.05` | Frecuencia de lectura del estado del UPS. Valores < 0.01 pueden afectar rendimiento. |

### 3.2 Parámetros de Persistencia y Checkpoint

| Parámetro | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `checkpoint_path` | Path | `~/kares_checkpoint.json` | Ruta absoluta o relativa al archivo JSON donde se guarda el estado. Debe estar en una partición persistente. |
| `enable_wal` | Boolean | `True` | Activa el *Write-Ahead Log*. Escribe operaciones en un log secuencial antes de aplicarlas al checkpoint principal. Previene corrupción si la energía se corta *durante* la escritura del checkpoint. |
| `enable_ecc` | Boolean | `False` | Activa códigos de corrección de errores en los datos del checkpoint. Útil en tarjetas SD propensas a corrupción bit-flip. Consume más CPU. |
| `enable_metrics` | Boolean | `True` | Expone métricas internas (tiempos de guardado, estado de batería, temperatura) en formato Prometheus en el puerto web de Klipper. |

### 3.3 Parámetros Térmicos y de Seguridad (UPS)

| Parámetro | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `bed_keepalive_on_ups` | Boolean | `True` | **Función Crítica**. Cuando el UPS indica "On Battery", el sistema entra en modo ahorro. Si esto es `True`, la cama caliente se mantiene activa ignorando comandos de apagado globales, priorizando la adhesión de la pieza. |
| `bed_min_temp_on_ups` | Float | `60.0` | Límite inferior de temperatura para la cama en modo batería. El sistema modulará la potencia del heater de la cama para mantenerla cerca de este valor, apagando otros consumibles (ventiladores, hotend) si la carga del UPS es alta. |
| `chamber_heater_fan` | String | `None` | Objeto `temperature_fan` que controla el calentamiento activo de la cabina. Si se define, KARES gestionará su encendido/apagado durante la reanudación. |
| `chamber_temp_sensor` | String | `None` | Sensor `temperature_sensor` para leer la temperatura real de la cabina. |
| `chamber_target_temp` | Float | `45.0` | Temperatura objetivo para la cabina. Usado durante la fase de pre-calentamiento antes de reanudar. |

### 3.4 Parámetros de Cinemática (Multi-Eje)

| Parámetro | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `extra_stepper_prefixes` | Lista | `stepper_, extruder` | Cadena separada por comas con prefijos de nombre para buscar objetos stepper. KARES escanea todos los objetos del printer que empiecen con estos prefijos para guardar sus posiciones MCU y coordenadas. Útil para ejes rotativos (A, B, C) o sistemas IDEX (stepper_x1, stepper_y1). |

---

## 4. Comandos G-code

KARES registra los siguientes comandos en el intérprete de G-code de Klipper.

### 4.1 `KARES_SAVE_CHECKPOINT`
Fuerza el guardado manual de un checkpoint en el estado actual.

**Sintaxis:**
```gcode
KARES_SAVE_CHECKPOINT [REASON=<texto>] [PAUSE=<0|1>]
```

**Parámetros:**
- `REASON`: (Opcional) Cadena de texto describiendo el motivo (ej: "cambio_filamento", "error_capa", "manual"). Se guarda en el log del checkpoint.
- `PAUSE`: (Opcional, default 1) Si es `1`, pausa la impresión automáticamente después de guardar. Si es `0`, solo guarda y continúa (útil para puntos de control automáticos cada N capas).

**Ejemplo:**
```gcode
; Guardar punto de control antes de cambiar filamento y pausar
KARES_SAVE_CHECKPOINT REASON=cambio_filamento PAUSE=1
```

### 4.2 `KARES_RESUME_CHECKPOINT`
Inicia la secuencia de recuperación desde el último checkpoint válido.

**Sintaxis:**
```gcode
KARES_RESUME_CHECKPOINT [STRATEGY=<modo>] [SKIP_WARMUP=<0|1>]
```

**Parámetros:**
- `STRATEGY`: (Opcional) Define cómo manejar el comando interrumpido.
    - `replay_inflight`: Re-ejecuta el comando G-code que estaba en proceso cuando ocurrió el fallo. (Default, más seguro).
    - `continue_after_inflight`: Asume que el comando en vuelo se completó parcialmente y salta al siguiente.
    - `host_stream_or_macro`: Delega la decisión a una macro externa o al host.
- `SKIP_WARMUP`: (Opcional, default 0) Si es `1`, omite la espera de estabilización térmica. **Peligroso**: Solo usar si se sabe que las temperaturas ya son estables.

**Secuencia de Recuperación Automática:**
1.  **Validación**: Verifica homing (X, Y, Z) y estado de la impresora.
2.  **Seguridad Z**: Eleva el cabezal a 5mm sobre la última posición Z conocida para evitar colisiones.
3.  **Restauración Térmica**: Enciende hotend, cama y cabina a los valores guardados.
4.  **Estabilización**: Espera hasta que todos los sensores estén a ±5°C del objetivo.
5.  **Posicionamiento XY**: Mueve X e Y a la posición exacta del fallo.
6.  **Restauración de Estado**: Aplica factores M220 (velocidad), M221 (flujo) y modo de coordenadas (G90/G91).
7.  **Descenso Z**: Baja al punto exacto de extrusión.
8.  **Ejecución**: Reanuda el flujo de G-code según la estrategia.

**Ejemplo:**
```gcode
; Reanudar impresión tras corte de luz
KARES_RESUME_CHECKPOINT STRATEGY=replay_inflight
```

### 4.3 `KARES_STATUS`
Muestra el estado actual del sistema KARES y del UPS.

**Salida típica:**
```text
KARES Status:
  State: IDLE
  Last Checkpoint: 2023-10-27 10:30:45 (Layer 142)
  UPS: ONLINE (Battery: 100%, Load: 15%)
  Chamber Temp: 44.5/45.0 °C
  Bed Keepalive: ACTIVE
```

### 4.4 `KARES_NUT_STATUS`
Muestra información detallada del UPS obtenida vía NUT.

**Salida típica:**
```text
UPS Name: ups
Status: OL (On Line)
Battery Charge: 100%
Runtime Left: 45 min
Input Voltage: 230.5 V
Load Percent: 12%
```

### 4.5 `KARES_HEALTH`
Realiza un diagnóstico de integridad del sistema.
- Verifica existencia y validez del archivo de checkpoint (CRC32).
- Comprueba conexión con el backend (NUT).
- Verifica permisos de escritura en `checkpoint_path`.

### 4.6 `KARES_METRICS`
Imprime las métricas actuales en formato legible (útil si no hay Grafana configurado).

---

## 5. Flujos de Trabajo (Workflows)

### Escenario A: Corte de Energía con UPS (Modo Seguridad)
Este es el caso de uso principal para materiales técnicos.

1.  **Evento**: Se corta la luz eléctrica. El UPS pasa a batería (`ON_BATTERY`).
2.  **Acción KARES**:
    - Detecta el cambio de estado vía NUT.
    - Si `bed_keepalive_on_ups = True`:
        - Envía comando `M140 S{bed_min_temp_on_ups}` para asegurar que la cama no baje de 60°C.
        - Apaga el hotend (`M104 S0`) y el ventilador de cabina para reducir consumo.
        - Notifica al usuario: "Modo Ahorro Activado: Cama caliente mantenida".
3.  **Espera**: El sistema monitorea la batería.
    - Si la luz vuelve (`ON_LINE`): Restaura automáticamente hotend y cabina a sus valores anteriores y notifica "Energía Restaurada. Puede reanudar.".
    - Si la batería es crítica (<15%): Ejecuta `KARES_SAVE_CHECKPOINT` inmediatamente y apaga la impresora ordenadamente (`SHUTDOWN`).
4.  **Reanudación**: Cuando el usuario vuelva, ejecuta `KARES_RESUME_CHECKPOINT`. El sistema calentará todo de nuevo y continuará exactamente donde se quedó.

### Escenario B: Error de Filamento o Colisión (Pausa Manual)
1.  **Evento**: El usuario detecta un problema (ej. spaghetti, falta de filamento) y pulsa "Pause" en la pantalla o usa `M600`.
2.  **Acción**: Antes de detener los motores, el usuario (o una macro automática) ejecuta:
    ```gcode
    KARES_SAVE_CHECKPOINT REASON=filament_runout
    ```
3.  **Corrección**: El usuario soluciona el problema (cambia bobina, limpia boquilla).
4.  **Reanudación**:
    ```gcode
    KARES_RESUME_CHECKPOINT
    ```
    El sistema reposiciona el cabezal y reanuda sin perder la capa actual.

### Escenario C: Impresión Larga con Mantenimiento Programado
Para impresiones de semanas en granjas de impresión:
1.  Configurar `enable_wal: True` para máxima seguridad contra corrupción de SD.
2.  En el slicer, insertar al final de cada capa (o cada 10 capas):
    ```gcode
    ; Custom G-code at layer change
    KARES_SAVE_CHECKPOINT REASON=layer_complete PAUSE=0
    ```
    Esto crea puntos de restauración frecuentes sin pausar la impresión. Si la máquina falla días después, se puede recuperar perdiendo como máximo 10 capas de progreso.

---

## 6. Solución de Problemas (Troubleshooting)

### Error: "Checkpoint CRC Mismatch"
- **Causa**: El archivo de checkpoint está corrupto (corte de energía durante escritura, tarjeta SD dañada).
- **Solución**:
    1. KARES intentará automáticamente recuperar desde el WAL (`kares_checkpoint.wal`).
    2. Si falla, elimine manualmente `kares_checkpoint.json` y `*.wal`. Deberá reiniciar la impresión desde el inicio o usar la función "Start from Layer" del slicer si tiene el G-code original.
    3. Considere activar `enable_ecc: True` si esto ocurre frecuentemente.

### Error: "UPS Not Found" o "Connection Refused"
- **Causa**: El servicio NUT no está corriendo o la configuración de red es incorrecta.
- **Solución**:
    1. Verifique que `upsd` esté activo: `sudo systemctl status nut-server`.
    2. Compruebe conectividad: `upsc ups@localhost` desde la terminal de la Raspberry Pi.
    3. Revise `nut_host` y `nut_port` en `printer.cfg`.
    4. Use `backend: mock` temporalmente para verificar que el resto de KARES funciona sin el UPS físico.

### La cama se enfría durante un corte de luz
- **Causa**: `bed_keepalive_on_ups` está en `False` o la potencia del UPS es insuficiente.
- **Solución**:
    1. Asegúrese de que `bed_keepalive_on_ups: True` en `[kares]`.
    2. Verifique que el UPS tenga suficiente capacidad (VA/Watts) para soportar la cama caliente. Las camas de 24V consumen mucho. Si el UPS se sobrecarga, KARES podría verse forzado a apagar todo para proteger el hardware.

### La impresora no reanuda en la posición exacta (Z-hop incorrecto)
- **Causa**: Obstrucción mecánica o configuración de `safe_z_height` insuficiente.
- **Solución**:
    - KARES usa un Z-hop dinámico (mínimo 5mm sobre la pieza). Si hay protuberancias grandes en la impresión, aumente el margen de seguridad modificando la lógica interna o asegúrese de que la zona de fallo esté libre de obstáculos antes de reanudar.

---

## 7. Arquitectura Técnica (Para Desarrolladores)

KARES está diseñado siguiendo estrictamente el patrón de extras de Klipper extendido por `extra_manager.py`.

### Dependencias y Ciclo de Vida
- **Herencia**: La clase `KARES` hereda de `ExtraInterface`. No instancia su propio reactor ni bucles de evento; utiliza `self.reactor`, `self.toolhead`, `self.gcode` provistos por el manager.
- **Hilos**:
    - **Hilo Principal (Reactor)**: Maneja comandos G-code, callbacks de eventos y temporizadores de alta prioridad.
    - **Hilo NUT (Background)**: Un hilo dedicado (`threading.Thread`) ejecuta el polling de la UPS para no bloquear el bucle de tiempo real de Klipper. Los datos se sincronizan mediante locks atómicos.
- **CRC32 Hardware**: Utiliza `ultracrc32_compute` de `chelper` (biblioteca C de Klipper) para validar los bloques de datos del checkpoint. Esto permite checksums de megabytes de datos en microsegundos, aprovechando instrucciones SSE4.2 (x86) o CRC32 (ARM/RISC-V).

### Estructura del Checkpoint (JSON)
El archivo generado contiene:
```json
{
  "version": 2,
  "timestamp": 1698420500.123,
  "gcode_state": {
    "absolute_coords": true,
    "speed_factor": 1.0,
    "extrude_factor": 1.0
  },
  "axes": {
    "stepper_x": {"mcu_pos": 15000, "coord": 150.0},
    "stepper_y": {"mcu_pos": 15000, "coord": 150.0},
    "stepper_z": {"mcu_pos": 5000, "coord": 20.0},
    "extruder": {"mcu_pos": 500, "coord": 12.5}
  },
  "temperatures": {
    "extruder": 240.0,
    "heater_bed": 100.0,
    "chamber_fan": 45.0
  },
  "ups_context": {
    "on_battery": false,
    "battery_charge": 100,
    "runtime_left": 3600
  },
  "crc32": "a1b2c3d4"
}
```

### Integración con Prometeo
Si `enable_metrics: True`, KARES expone:
- `kares_checkpoint_age_seconds`: Tiempo desde el último checkpoint.
- `kares_ups_battery_percent`: Carga actual de la batería.
- `kares_ups_load_percent`: Carga de salida del UPS.
- `kares_recovery_duration_seconds`: Tiempo tomado en la última reanudación.
- `kares_errors_total`: Contador de errores críticos.

---

## 8. Licencia y Créditos
KARES es software libre desarrollado para la comunidad Klipper. Utiliza tecnologías de código abierto incluyendo NUT y el núcleo de Klipper.

*Documentación generada para la versión 2.0 de KARES.*
