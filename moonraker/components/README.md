# Sistema de Gestión de UPS NUT para Klipper/Moonraker

## Descripción General

Este proyecto implementa un sistema completo de gestión de Sistemas de Alimentación Ininterrumpida (UPS/SAI) mediante el protocolo **NUT (Network UPS Tools)** para impresoras 3D que utilizan **Klipper** como firmware y **Moonraker** como servidor web.

El sistema está dividido en dos módulos independientes que trabajan de forma coordinada:

### 1. Componente Moonraker (`ups_nut.py`)

Un componente nativo de Moonraker que proporciona:

- **Monitoreo en tiempo real** del estado de la UPS mediante polling asíncrono
- **API REST completa** para consulta y configuración
- **Notificaciones push** vía WebSocket a clientes conectados
- **Gestión de eventos** de fallo de energía, batería baja, etc.
- **Integración con Klipper** para activar protocolos de seguridad

**Ubicación:** `/workspace/moonraker/components/ups_nut.py`

### 2. Extra Klipper KARES (`kares.py`)

Un extra de Klipper existente que ha sido diseñado para trabajar con gestión de UPS:

- **Checkpointing determinístico** del estado de impresión
- **Protección térmica** durante cortes de energía
- **Recuperación segura** después de fallos
- **Control de hardware** (steppers, heaters, ventiladores)
- **Comandos G-code** para gestión manual

**Ubicación:** `/workspace/klippy/extras/kares.py`

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                      SISTEMA COMPLETO                        │
│                                                              │
│  ┌────────────────────┐         ┌────────────────────────┐  │
│  │   MOONRAKER        │         │   KLIPPER              │  │
│  │                    │         │                        │  │
│  │  [ups_nut.py]      │◄───────►│  [kares.py]            │  │
│  │  - API REST        │  Event  │  - Checkpointing       │  │
│  │  - Polling NUT     │  System │  - Control Térmico     │  │
│  │  - Notificaciones  │         │  - G-code Commands     │  │
│  │  - Eventos         │         │  - Recuperación        │  │
│  └────────────────────┘         └────────────────────────┘  │
│           │                              │                   │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌────────────────────┐         ┌────────────────────────┐  │
│  │   Servidor NUT     │         │   MCUs / Hardware      │  │
│  │   (upsd:3493)      │         │                        │  │
│  │                    │         │  - Steppers            │  │
│  │  - Monitor UPS     │         │  - Heaters             │  │
│  │  - Variables       │         │  - Ventiladores        │  │
│  └────────────────────┘         └────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   UPS / SAI        │
                  │   (APC, Eaton...)  │
                  └────────────────────┘
```

## Características Principales

### Monitoreo de UPS

- Estado general (en línea, batería, carga baja)
- Porcentaje de carga de batería
- Tiempo de autonomía restante estimado
- Voltaje de entrada/salida
- Porcentaje de carga conectada
- Temperatura de la UPS

### Notificaciones Automáticas

Eventos notificados automáticamente:

| Evento | Descripción | Acción Típica |
|--------|-------------|---------------|
| `ups_on_battery` | Detección de fallo de energía | Entrar en modo ahorro |
| `ups_power_restored` | Retorno de energía eléctrica | Restaurar operación normal |
| `ups_low_battery` | Batería < 20% | Preparar apagado |
| `ups_critical_battery` | Batería < 10% | Guardar checkpoint, apagar |

### API REST (Moonraker)

Endpoints disponibles:

```
GET  /printer/ups/status          # Estado actual de UPS
GET  /printer/ups/config          # Configuración del componente
POST /printer/ups/config          # Actualizar configuración
POST /printer/ups/test_connection # Probar conexión NUT
POST /printer/ups/shutdown        # Iniciar apagado seguro
```

### Comandos G-code (Klipper)

Comandos disponibles en KARES:

```gcode
KARES_STATUS              # Mostrar estado del sistema
KARES_NUT_STATUS          # Mostrar estado detallado de UPS
KARES_SAVE_CHECKPOINT     # Guardar punto de recuperación
KARES_RESUME_CHECKPOINT   # Recuperar desde checkpoint
KARES_HEALTH              # Verificar integridad del sistema
KARES_METRICS             # Mostrar métricas Prometheus
```

## Instalación

### Requisitos Previos

1. **Klipper** instalado y configurado
2. **Moonraker** instalado y configurado
3. **Servidor NUT** instalado y configurado
4. **UPS compatible con NUT** conectada al sistema

### Paso 1: Instalar y Configurar NUT

```bash
# Instalar paquetes NUT
sudo apt-get update
sudo apt-get install nut nut-server nut-client nut-monitor

# Configurar UPS en /etc/nut/ups.conf
# Configurar usuarios en /etc/nut/upsd.users
# Configurar monitor en /etc/nut/upsmon.conf

# Reiniciar servicios
sudo systemctl restart nut-server
sudo systemctl restart nut-monitor
```

### Paso 2: Instalar Componente Moonraker

```bash
# Copiar componente a directorio de Moonraker
cp /workspace/moonraker/components/ups_nut.py \
   /opt/moonraker/moonraker/components/

# Añadir configuración a moonraker.conf
# (ver sección de configuración más abajo)

# Reiniciar Moonraker
sudo systemctl restart moonraker
```

### Paso 3: Configurar Klipper KARES

El extra `kares.py` ya está incluido en Klipper. Solo necesita configuración:

```ini
# En printer.cfg
[kares]
backend: nut
nut_host: 127.0.0.1
nut_port: 3493
nut_username: kares_monitor
nut_password: tu_contraseña
nut_ups_name: ups
poll_interval: 0.05
shutdown_on_fault: True
bed_keepalive_on_ups: True
bed_min_temp_on_ups: 60.0
```

## Configuración Detallada

### Moonraker (`moonraker.conf`)

```ini
[ups_nut]
# Servidor NUT
host: 127.0.0.1
port: 3493

# Credenciales
username: kares_monitor
password: SecurePassword123

# Nombre de UPS
ups_name: myups

# Intervalo de polling (segundos)
poll_interval: 3.0

# Habilitar componente
enabled: True
```

### Klipper (`printer.cfg`)

```ini
[kares]
# Backend
backend: nut

# Conexión NUT (debe coincidir con Moonraker)
nut_host: 127.0.0.1
nut_port: 3493
nut_ups_name: myups
nut_username: kares_monitor
nut_password: SecurePassword123
nut_timeout: 0.20

# Polling interno (segundos, puede ser más rápido que Moonraker)
poll_interval: 0.05

# Seguridad
shutdown_on_fault: True
checkpoint_path: ~/printer_data/config/kares_checkpoint.json
enable_wal: True

# Protección térmica
bed_keepalive_on_ups: True
bed_min_temp_on_ups: 60.0

# Opcional: Métricas
enable_metrics: True
enable_rt: True
rt_priority: 75
```

## Uso

### Verificar Estado

Desde terminal:
```bash
curl http://localhost:7125/printer/ups/status
```

Desde G-code:
```gcode
KARES_STATUS
KARES_NUT_STATUS
```

### Prueba de Conexión

```bash
curl -X POST http://localhost:7125/printer/ups/test_connection
```

### Apagado Manual

```bash
curl -X POST http://localhost:7125/printer/ups/shutdown
```

O desde G-code:
```gcode
M112 ; Parada de emergencia (KARES guarda checkpoint automáticamente)
```

## Escenarios de Uso

### Corte de Energía Breve (< 5 min)

1. UPS entra en modo batería → Moonraker detecta `on_battery`
2. KARES entra en modo ahorro (apaga hotend, mantiene cama)
3. Energía restaurada → KARES restaura temperaturas
4. Usuario reanuda impresión normalmente

### Corte de Energía Prolongado (> 10 min)

1. Batería baja (< 20%) → Moonraker notifica `ups_low_battery`
2. Batería crítica (< 10%) → Moonraker notifica `ups_critical_battery`
3. KARES ejecuta `KARES_SAVE_CHECKPOINT`
4. Sistema se apaga ordenadamente
5. Usuario retorna → Ejecuta `KARES_RESUME_CHECKPOINT`
6. Impresión continúa exactamente donde falló

### Error de Filamento o Colisión

```gcode
; Macro de pausa con checkpoint
[gcode_macro PAUSE_WITH_CHECKPOINT]
gcode:
    KARES_SAVE_CHECKPOINT REASON=filament_change PAUSE=0
    PAUSE
    RESPOND MSG="Impresión pausada. Checkpoint guardado."

; Reanudar
[gcode_macro RESUME_FROM_CHECKPOINT]
gcode:
    KARES_RESUME_CHECKPOINT
    RESUME
```

## Documentación Adicional

- **[README_UPS_NUT.md](./README_UPS_NUT.md)** - Documentación detallada del componente Moonraker
- **[INTEGRACION_UPS_NUT.md](./INTEGRACION_UPS_NUT.md)** - Guía completa de integración y configuración
- **[kares_manual.md](../../docs/kares_manual.md)** - Manual completo de KARES para Klipper

## Solución de Problemas

### Verificar Conexión NUT

```bash
# ¿Está corriendo el servidor NUT?
sudo systemctl status nut-server

# ¿Puede conectar manualmente?
upsc myups@localhost

# Ver logs de Moonraker
journalctl -u moonraker -f | grep ups

# Ver logs de Klipper
tail -f ~/printer_data/logs/klippy.log | grep KARES
```

### Problemas Comunes

| Síntoma | Causa Probable | Solución |
|---------|----------------|----------|
| No hay datos de UPS | Servidor NUT caído | `sudo systemctl restart nut-server` |
| Error de autenticación | Credenciales incorrectas | Verificar `/etc/nut/upsd.users` |
| Latencia alta | Poll interval muy largo | Reducir `poll_interval` |
| Checkpoint corrupto | Escritura interrumpida | Activar `enable_wal: True` |

## Seguridad

### Mejores Prácticas

1. **No usar credenciales por defecto** en producción
2. **Restringir acceso** al puerto NUT (3493) solo a localhost
3. **Usar firewall** para bloquear acceso externo
4. **Rotar contraseñas** periódicamente

### Configuración de Firewall

```bash
# Permitir solo localhost al puerto NUT
sudo ufw deny from any to any port 3493
sudo ufw allow from 127.0.0.1 to any port 3493
```

## Desarrollo

### Estructura de Archivos

```
/workspace/
├── moonraker/
│   └── components/
│       ├── ups_nut.py              # Componente Moonraker
│       ├── ups_nut.example.conf    # Ejemplo de configuración
│       ├── README_UPS_NUT.md       # Documentación del componente
│       └── INTEGRACION_UPS_NUT.md  # Guía de integración
│
└── klippy/
    └── extras/
        └── kares.py                # Extra Klipper (ya existe)
```

### Añadir Nuevas Funcionalidades

1. **Extender NUTClient** para nuevos comandos NUT
2. **Registrar nuevos endpoints** en `_register_api_endpoints()`
3. **Añadir manejadores de eventos** según sea necesario
4. **Actualizar documentación** en consecuencia

## Referencias

- [Protocolo NUT](https://networkupstools.org/)
- [Documentación de Moonraker](https://moonraker.readthedocs.io/)
- [Documentación de Klipper](https://www.klipper3d.org/)
- [Configuración de UPS para Linux](https://github.com/networkupstools/nut/wiki)

## Licencia

GPL-3.0 - Ver LICENSE en el directorio raíz del proyecto.

## Autor

**TUXEDO_RT Project** - Sistema de impresión 3D de tiempo real

Desarrollado como parte del ecosistema TUXEDO_RT para proporcionar gestión robusta de energía y recuperación de impresiones en entornos de producción.

## Contribuciones

Las contribuciones son bienvenidas. Por favor:

1. Fork el repositorio
2. Crear rama para feature (`git checkout -b feature/AmazingFeature`)
3. Commit cambios (`git commit -m 'Add AmazingFeature'`)
4. Push a rama (`git push origin feature/AmazingFeature`)
5. Abrir Pull Request

## Soporte

Para problemas específicos:

1. Revisar logs de ambos componentes (Moonraker y Klipper)
2. Verificar conectividad NUT manualmente
3. Probar con `backend: mock` para aislar problemas
4. Reportar issues en el repositorio del proyecto
5. Consultar documentación adicional en los archivos Markdown incluidos
