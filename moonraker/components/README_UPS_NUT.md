# Componente UPS NUT para Moonraker

## Descripción General

Este componente proporciona integración completa con servidores **NUT (Network UPS Tools)** para monitoreo y gestión de sistemas de alimentación ininterrumpida (UPS/SAI) en impresoras 3D que utilizan Klipper/Moonraker.

## Características Principales

### Monitoreo en Tiempo Real
- Polling periódico configurable del estado de la UPS
- Lectura de parámetros críticos:
  - Estado general (en línea, batería, carga baja)
  - Porcentaje de carga de batería
  - Tiempo de autonomía restante estimado
  - Voltaje de entrada/salida
  - Porcentaje de carga conectada
  - Temperatura de la UPS

### Notificaciones Automáticas
- Eventos push vía WebSocket a clientes conectados
- Notificaciones de:
  - `ups_on_battery` - Detección de fallo de energía
  - `ups_power_restored` - Retorno de energía eléctrica
  - `ups_low_battery` - Advertencia de batería baja (<20%)
  - `ups_critical_battery` - Nivel crítico de batería (<10%)

### API REST Completa
Endpoints disponibles:

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/printer/ups/status` | Obtiene estado actual de la UPS |
| GET | `/printer/ups/config` | Obtiene configuración del componente |
| POST | `/printer/ups/config` | Actualiza configuración |
| POST | `/printer/ups/test_connection` | Prueba conexión con servidor NUT |
| POST | `/printer/ups/shutdown` | Inicia apagado seguro |

### Integración con Klipper KARES
- Envío de eventos a Klipper para activar checkpointing
- Coordinación con el extra `kares.py` para recuperación segura
- Apagado ordenado cuando la batería es crítica

## Instalación

### Requisitos Previos

1. **Servidor NUT instalado y configurado**
   ```bash
   sudo apt-get install nut nut-server nut-client
   ```

2. **Configurar NUT** (`/etc/nut/ups.conf`):
   ```ini
   [ups]
       driver = usbhid-ups
       port = auto
       desc = "Mi UPS"
   ```

3. **Configurar usuarios NUT** (`/etc/nut/upsd.users`):
   ```ini
   [monuser]
       password = secret
       instcmds = ALL
       actions = READ
       upsmon = slave
   ```

4. **Reiniciar servicios NUT**:
   ```bash
   sudo systemctl restart nut-server
   sudo systemctl restart nut-monitor
   ```

### Instalación del Componente

1. Copiar el archivo `ups_nut.py` al directorio de componentes de Moonraker:
   ```bash
   cp ups_nut.py /opt/moonraker/moonraker/components/
   ```

2. Añadir configuración a `moonraker.conf`:
   ```ini
   [ups_nut]
   host: 127.0.0.1
   port: 3493
   username: monuser
   password: secret
   ups_name: ups
   poll_interval: 5.0
   enabled: True
   ```

3. Reiniciar Moonraker:
   ```bash
   sudo systemctl restart moonraker
   ```

## Configuración

### Parámetros Disponibles

| Parámetro | Tipo | Valor por Defecto | Descripción |
|-----------|------|-------------------|-------------|
| `host` | String | `127.0.0.1` | IP o hostname del servidor NUT |
| `port` | Integer | `3493` | Puerto TCP del servidor NUT |
| `username` | String | `monuser` | Usuario para autenticación NUT |
| `password` | String | `secret` | Contraseña del usuario NUT |
| `ups_name` | String | `ups` | Nombre de la UPS en configuración NUT |
| `poll_interval` | Float | `5.0` | Intervalo de sondeo en segundos (mín: 1.0) |
| `enabled` | Boolean | `True` | Habilitar/deshabilitar componente |

### Ejemplo de Configuración Completa

```ini
[ups_nut]
# Servidor NUT local
host: 127.0.0.1
port: 3493

# Credenciales
username: kares_monitor
password: MiPasswordSeguro123

# Nombre de la UPS (debe coincidir con ups.conf)
ups_name: my_apc_ups

# Polling cada 3 segundos para respuesta rápida
poll_interval: 3.0

# Componente habilitado
enabled: True
```

## Uso de la API

### Obtener Estado de la UPS

```bash
curl http://localhost:7125/printer/ups/status
```

Respuesta ejemplo:
```json
{
  "enabled": true,
  "connected": true,
  "last_update": 1698420500.123,
  "poll_interval": 5.0,
  "ups": {
    "name": "ups",
    "status": "OL",
    "on_battery": false,
    "low_battery": false,
    "running": true,
    "charging": false,
    "battery": {
      "charge_pct": 100,
      "runtime_s": 2700
    },
    "input": {
      "voltage": 230.5
    },
    "output": {
      "load_pct": 15.2
    },
    "ups": {
      "temperature_c": 35.0
    }
  }
}
```

### Probar Conexión

```bash
curl -X POST http://localhost:7125/printer/ups/test_connection
```

### Iniciar Apagado Seguro

```bash
curl -X POST http://localhost:7125/printer/ups/shutdown
```

## Eventos WebSocket

Los clientes WebSocket recibirán notificaciones automáticas:

### Notificación de Actualización de Estado
```json
{
  "method": "notify_ups_update",
  "params": {
    "enabled": true,
    "connected": true,
    "ups": { ... }
  }
}
```

### Evento de Fallo de Energía
```json
{
  "event": "ups_on_battery",
  "data": {
    "message": "Power failure detected - UPS running on battery",
    "battery_charge": 95,
    "runtime_remaining": 2400
  }
}
```

### Evento de Batería Crítica
```json
{
  "event": "ups_critical_battery",
  "data": {
    "message": "Critical battery level - immediate shutdown recommended",
    "battery_charge": 8,
    "runtime_remaining": 180
  }
}
```

## Integración con Klipper KARES

Este componente trabaja en conjunto con el extra `kares.py` de Klipper para proporcionar:

1. **Checkpointing Automático**: Cuando se detecta batería crítica, KARES guarda el estado de la impresión
2. **Protección Térmica**: Mantiene la cama caliente durante cortes de energía
3. **Recuperación Segura**: Permite reanudar la impresión exactamente donde se interrumpió

### Flujo de Trabajo Típico

1. **Fallo de energía detectado** → Evento `ups_on_battery`
2. **KARES entra en modo ahorro** → Apaga hotend, mantiene cama caliente
3. **Batería crítica** → Evento `ups_critical_battery`
4. **KARES guarda checkpoint** → Guarda estado completo de impresión
5. **Apagado seguro** → Sistema se apaga ordenadamente
6. **Energía restaurada** → Usuario ejecuta `KARES_RESUME_CHECKPOINT`

## Solución de Problemas

### El componente no se inicia

**Verificar logs de Moonraker:**
```bash
journalctl -u moonraker -f | grep ups_nut
```

**Posibles causas:**
- Puerto NUT incorrecto
- Servidor NUT no está corriendo
- Credenciales inválidas

### No hay datos de la UPS

**Verificar conexión NUT manualmente:**
```bash
upsc ups@localhost
```

**Si falla:**
1. Verificar que `upsd` esté activo: `sudo systemctl status nut-server`
2. Verificar configuración en `/etc/nut/ups.conf`
3. Verificar permisos de usuario en `/etc/nut/upsd.users`

### Latencia alta en polling

**Soluciones:**
- Aumentar `poll_interval` a 10 o 15 segundos
- Usar conexión local (127.0.0.1) en lugar de red
- Verificar carga del sistema

## Seguridad

### Mejores Prácticas

1. **No usar credenciales por defecto** en producción
2. **Restringir acceso** al puerto NUT (3493) solo a localhost
3. **Usar firewall** para bloquear acceso externo al puerto NUT
4. **Rotar contraseñas** periódicamente

### Configuración de Firewall

```bash
# Permitir solo localhost
sudo ufw deny from any to any port 3493
sudo ufw allow from 127.0.0.1 to any port 3493
```

## Desarrollo

### Estructura del Componente

```
ups_nut.py
├── NUTClient          # Cliente asíncrono NUT
│   ├── connect()
│   ├── disconnect()
│   └── get_ups_status()
├── UpsNut             # Componente Moonraker
│   ├── component_init()
│   ├── _poll_loop()
│   ├── _register_api_endpoints()
│   └── component_close()
└── load_component()   # Función de registro
```

### Añadir Nuevas Funcionalidades

1. Extender `NUTClient` para nuevos comandos NUT
2. Registrar nuevos endpoints en `_register_api_endpoints()`
3. Añadir manejadores de eventos según sea necesario

## Referencias

- [Protocolo NUT](https://networkupstools.org/)
- [Documentación de Moonraker](https://moonraker.readthedocs.io/)
- [KARES para Klipper](../klippy/extras/kares.py)

## Licencia

GPL-3.0 - Ver LICENSE en el directorio raíz del proyecto.

## Autor

TUXEDO_RT Project - Sistema de impresión 3D de tiempo real
