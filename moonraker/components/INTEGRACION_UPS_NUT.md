# Integración UPS NUT: Moonraker + Klipper KARES

## Arquitectura del Sistema

El sistema de gestión de UPS está dividido en dos módulos independientes que se comunican entre sí:

```
┌─────────────────────────────────────────────────────────────────┐
│                         IMPRESORA 3D                             │
│                                                                   │
│  ┌───────────────────┐         ┌─────────────────────────────┐  │
│  │   MOONRAKER       │         │      KLIPPER                │  │
│  │   (Servidor Web)  │◄───────►│      (Firmware MCU)         │  │
│  │                   │  HTTP/  │                             │  │
│  │  [ups_nut.py]     │ WebSocket│  [kares.py]                 │  │
│  │  Componente UPS   │         │  Extra de Gestión UPS       │  │
│  │                   │         │                             │  │
│  │  - API REST       │         │  - Checkpointing            │  │
│  │  - Polling NUT    │         │  - Control térmico          │  │
│  │  - Notificaciones │         │  - Recuperación             │  │
│  │  - Eventos        │         │  - G-code commands          │  │
│  └───────────────────┘         └─────────────────────────────┘  │
│           │                                  │                   │
│           │                                  │                   │
│           ▼                                  ▼                   │
│  ┌───────────────────┐         ┌─────────────────────────────┐  │
│  │   Servidor NUT    │         │      MCUs (Steppers, etc.)  │  │
│  │   (upsd)          │         │                             │  │
│  │   Puerto 3493     │         │  - Guardar posición         │  │
│  │                   │         │  - Control heaters          │  │
│  │  - Monitor UPS    │         │  - Gestionar ventiladores   │  │
│  └───────────────────┘         └─────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │      UPS / SAI    │
                  │  (APC, Eaton, etc.)│
                  │                   │
                  │  - Batería        │
                  │  - Voltaje        │
                  │  - Carga          │
                  └───────────────────┘
```

## Flujo de Comunicación

### 1. Monitoreo Continuo (Moonraker → NUT)

```python
# moonraker/components/ups_nut.py
class UpsNut:
    async def _poll_loop(self):
        while True:
            state = await self._nut_client.get_ups_status()
            # Detectar cambios importantes
            await self._check_state_changes()
            # Notificar clientes
            await self._notify_clients()
            await asyncio.sleep(poll_interval)
```

### 2. Notificación de Eventos (Moonraker → Klipper)

Cuando Moonraker detecta un evento crítico:

```python
# Envío de evento a Klipper
event_manager.send_event("ups:initiate_shutdown", {
    "reason": "critical_battery",
    "battery_charge": 8
})
```

### 3. Respuesta de Klipper (KARES)

```python
# klippy/extras/kares.py
def _handle_ups_event(self, event_data):
    if event_data.get("reason") == "critical_battery":
        # Guardar checkpoint inmediatamente
        self._save_checkpoint(reason="ups_critical")
        # Ejecutar apagado seguro si está configurado
        if self.shutdown_on_fault:
            self._initiate_safe_shutdown()
```

## Configuración Completa

### Paso 1: Configurar NUT (Sistema Operativo)

**Archivo: `/etc/nut/ups.conf`**
```ini
[myups]
    driver = usbhid-ups
    port = auto
    desc = "APC Back-UPS Pro"
    vendorid = 051d
    productid = 0002
```

**Archivo: `/etc/nut/upsd.users`**
```ini
[kares_monitor]
    password = SecurePassword123
    instcmds = ALL
    actions = READ
    upsmon = slave

[admin]
    password = AdminPassword456
    instcmds = ALL
    actions = SET
    upsmon = master
```

**Archivo: `/etc/nut/upsmon.conf`**
```ini
MONITOR myups@localhost 1 kares_monitor SecurePassword123 slave
DEADTIME 15
POWERDOWNFLAG /etc/nut/powerdownflag
NOTIFYCMD /usr/sbin/upssched
POLLFREQ 5
POLLFREQALERT 1
```

**Reiniciar servicios:**
```bash
sudo systemctl daemon-reload
sudo systemctl restart nut-server
sudo systemctl restart nut-monitor
sudo systemctl enable nut-server
sudo systemctl enable nut-monitor
```

### Paso 2: Configurar Moonraker

**Archivo: `moonraker.conf`**
```ini
# ============================================
# COMPONENTE UPS NUT PARA MOONRAKER
# ============================================

[ups_nut]
# Conexión al servidor NUT local
host: 127.0.0.1
port: 3493

# Credenciales de autenticación
username: kares_monitor
password: SecurePassword123

# Nombre de la UPS (debe coincidir con ups.conf)
ups_name: myups

# Intervalo de sondeo (segundos)
# - Valores bajos = respuesta más rápida pero más CPU
# - Valores altos = menos precisión pero más eficiente
poll_interval: 3.0

# Habilitar componente
enabled: True

# ============================================
# NOTIFICACIONES (Opcional pero recomendado)
# ============================================

[notifier]
# Configurar notificaciones para eventos UPS
notify_ups_on_battery: True
notify_ups_low_battery: True
notify_ups_critical_battery: True
```

### Paso 3: Configurar Klipper (KARES)

**Archivo: `printer.cfg`**
```ini
# ============================================
# SISTEMA KARES - GESTIÓN DE UPS Y RECUPERACIÓN
# ============================================

[kares]
# Backend de gestión de energía
# Opciones: 'nut' (recomendado), 'gpio', 'mock' (pruebas)
backend: nut

# Configuración de conexión NUT
# Debe coincidir con la configuración de Moonraker
nut_host: 127.0.0.1
nut_port: 3493
nut_ups_name: myups
nut_username: kares_monitor
nut_password: SecurePassword123
nut_timeout: 0.20

# Intervalo de sondeo interno de KARES (segundos)
# Puede ser diferente al de Moonraker para redundancia
poll_interval: 0.05

# Apagado automático en fallo crítico
shutdown_on_fault: True

# Ruta del archivo de checkpoint
checkpoint_path: ~/printer_data/config/kares_checkpoint.json

# Write-Ahead Log para mayor seguridad
enable_wal: True

# Métricas Prometheus (opcional)
enable_metrics: True

# ============================================
# PROTECCIÓN TÉRMICA (CRÍTICO para materiales técnicos)
# ============================================

# Mantener cama caliente durante corte de energía
# VITAL para ABS, ASA, PC, Nylon
bed_keepalive_on_ups: True

# Temperatura mínima de cama en modo batería (°C)
bed_min_temp_on_ups: 60.0

# Control de calefacción de cabina (opcional)
# chamber_heater_fan: temperature_fan chamber_heater
# chamber_temp_sensor: temperature_sensor chamber_temp
# chamber_target_temp: 45.0

# ============================================
# CONFIGURACIÓN AVANZADA
# ============================================

# Prioridad de tiempo real (50-99, mayor = más prioridad)
enable_rt: True
rt_priority: 75
rt_critical: False

# Prefijos para steppers adicionales (ej. ejes rotativos A, B, C)
extra_stepper_prefixes: stepper_, extruder, axis_
```

## Comandos G-code Disponibles

Una vez configurado KARES, los siguientes comandos están disponibles:

### `KARES_STATUS`
Muestra el estado actual del sistema UPS y KARES.

```gcode
KARES_STATUS
```

Salida esperada:
```
KARES Status:
  Backend: nut
  Fault Active: False
  Fault Count: 0
  Last Fault Reason: 0x00
  Checkpoint: ~/printer_data/config/kares_checkpoint.json
  WAL: Enabled | ECC: Disabled | Metrics: Enabled
```

### `KARES_NUT_STATUS`
Muestra información detallada de la UPS.

```gcode
KARES_NUT_STATUS
```

Salida esperada:
```
KARES NUT Status:
  Status: OL (On Line)
  Battery: 100.0% (45 min)
  Load: 12.5%
  Temperature: 35.0C
  Input Voltage: 230.5V
```

### `KARES_SAVE_CHECKPOINT`
Guarda manualmente un punto de recuperación.

```gcode
; Guardar checkpoint y pausar
KARES_SAVE_CHECKPOINT REASON=manual_pause PAUSE=1

; Guardar checkpoint sin pausar (útil en macros)
KARES_SAVE_CHECKPOINT REASON=layer_complete PAUSE=0
```

### `KARES_RESUME_CHECKPOINT`
Recupera desde el último checkpoint guardado.

```gcode
; Reanudar con estrategia predeterminada
KARES_RESUME_CHECKPOINT

; Reanudar con estrategia específica
KARES_RESUME_CHECKPOINT STRATEGY=replay_inflight SKIP_WARMUP=0
```

### `KARES_HEALTH`
Verifica la integridad del sistema.

```gcode
KARES_HEALTH
```

## Escenarios de Uso

### Escenario 1: Corte de Energía Breve

1. **Evento**: Se corta la luz, UPS entra en modo batería
2. **Moonraker**: Detecta `on_battery=true`, envía notificación
3. **KARES**: 
   - Entra en modo ahorro energético
   - Apaga hotend para reducir consumo
   - Mantiene cama caliente a 60°C mínimo
   - Notifica al usuario
4. **Energía restaurada** (< 5 minutos):
   - KARES detecta retorno de energía
   - Restaura temperaturas originales
   - Notifica "Energía restaurada, puede continuar"
5. **Usuario**: Reanuda impresión normalmente

### Escenario 2: Corte de Energía Prolongado

1. **Evento**: Corte de energía prolongado (> 10 minutos)
2. **Batería baja** (< 20%):
   - Moonraker envía evento `ups_low_battery`
   - KARES prepara para apagado
3. **Batería crítica** (< 10%):
   - Moonraker envía evento `ups_critical_battery`
   - KARES ejecuta `KARES_SAVE_CHECKPOINT`
   - Guarda estado completo de impresión
   - Apaga impresora ordenadamente
4. **Usuario retorna** (horas/días después):
   - Enciende impresora
   - Ejecuta `KARES_RESUME_CHECKPOINT`
   - Sistema calienta y reposiciona
   - Reanuda impresión exactamente donde falló

### Escenario 3: Error de Filamento

1. **Usuario detecta problema**: Filamento agotado o atasco
2. **Ejecuta macro de pausa**:
   ```gcode
   M600 ; Cambio de filamento
   KARES_SAVE_CHECKPOINT REASON=filament_change PAUSE=1
   ```
3. **Soluciona problema**: Cambia bobina, limpia boquilla
4. **Reanuda**:
   ```gcode
   KARES_RESUME_CHECKPOINT
   ```

## Macros Recomendadas

### Macro de Pausa Inteligente

**Archivo: `macros.cfg`**
```ini
[gcode_macro PAUSE_WITH_CHECKPOINT]
description: Pausa impresión y guarda checkpoint
gcode:
    ; Guardar checkpoint antes de pausar
    KARES_SAVE_CHECKPOINT REASON=user_pause PAUSE=0
    
    ; Pausa estándar de Klipper
    PAUSE
    
    ; Notificar al usuario
    RESPOND MSG="Impresión pausada. Checkpoint guardado."

[gcode_macro RESUME_FROM_CHECKPOINT]
description: Recupera desde checkpoint y reanuda
gcode:
    ; Verificar estado de UPS primero
    KARES_STATUS
    
    ; Reanudar desde checkpoint
    KARES_RESUME_CHECKPOINT
    
    ; Reanudar impresión
    RESUME
    
    ; Confirmar
    RESPOND MSG="Impresión reanudada desde checkpoint."

[gcode_macro EMERGENCY_STOP]
description: Detención de emergencia con checkpoint
gcode:
    ; Guardar checkpoint inmediatamente
    KARES_SAVE_CHECKPOINT REASON=emergency_stop PAUSE=0
    
    ; Parada de emergencia
    M112
    
    ; Apagar calentadores
    M104 S0
    M140 S0
    
    ; Notificar
    RESPOND MSG="PARADA DE EMERGENCIA. Checkpoint guardado para recuperación."
```

## Monitoreo y Diagnóstico

### Verificar Estado desde Terminal

```bash
# Estado de la UPS vía NUT
upsc myups@localhost

# Logs de Moonraker (eventos UPS)
journalctl -u moonraker -f | grep ups

# Logs de Klipper (KARES)
tail -f ~/printer_data/logs/klippy.log | grep KARES
```

### Dashboard de Monitoreo (Opcional)

Con métricas Prometheus habilitadas:

```ini
# En moonraker.conf
[prometheus_exporter]
enabled: True
port: 9100
```

Acceder a `http://<ip-impressora>:9100/metrics` para ver:
- `kares_checkpoints_total` - Total de checkpoints guardados
- `kares_battery_charge_percent` - Carga actual de batería
- `kares_input_voltage_volts` - Voltaje de entrada
- `kares_on_battery` - Estado de batería (0/1)

## Solución de Problemas

### Problema: KARES no detecta la UPS

**Verificar:**
```bash
# 1. ¿Está corriendo el servidor NUT?
sudo systemctl status nut-server

# 2. ¿Puede conectar manualmente?
upsc myups@localhost

# 3. ¿Coinciden las credenciales?
grep -A 3 "\[kares_monitor\]" /etc/nut/upsd.users
```

**Solución:**
- Reiniciar servicios NUT
- Verificar configuración en ambos lados (Moonraker y Klipper)
- Usar `backend: mock` temporalmente para pruebas

### Problema: Latencia alta en detección

**Síntoma**: La UPS entra en batería pero KARES tarda >10s en detectar

**Soluciones:**
1. Reducir `poll_interval` en Moonraker a 1-2 segundos
2. Aumentar prioridad RT en KARES:
   ```ini
   [kares]
   rt_priority: 85
   ```
3. Verificar carga de CPU del sistema

### Problema: Checkpoint corrupto después de apagado

**Causa probable**: Escritura interrumpida durante corte de energía

**Soluciones:**
1. Activar WAL (ya activado por defecto):
   ```ini
   [kares]
   enable_wal: True
   ```
2. Usar almacenamiento más confiable (SSD en lugar de SD)
3. Activar ECC si está disponible:
   ```ini
   enable_ecc: True
   ```

## Referencias

- [Documentación NUT](https://networkupstools.org/documentation.html)
- [Moonraker Components](https://moonraker.readthedocs.io/en/latest/components/)
- [KARES Manual Completo](../../docs/kares_manual.md)
- [Configuración de UPS para Linux](https://github.com/networkupstools/nut/wiki/UPS-Configuration-Examples)

## Soporte

Para problemas específicos de integración:
1. Revisar logs de ambos componentes
2. Verificar conectividad NUT manual
3. Probar con `backend: mock` para aislar el problema
4. Reportar issues en el repositorio del proyecto
