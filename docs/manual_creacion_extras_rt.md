# Manual de Creación de Extras para TUXEDO_RT

## Índice
1. [Introducción General](#1-introducción-general)
2. [Requisitos Previos](#2-requisitos-previos)
3. [Guía Paso a Paso](#3-guía-paso-a-paso)
4. [Implementación de i18n](#4-implementación-de-i18n)
5. [Integración con Core RT (APIs y Métodos)](#5-integración-con-core-rt)
6. [Testing y Validación](#6-testing-y-validación)
7. [Documentación de Referencia](#7-documentación-de-referencia)
8. [Ejemplos Completos](#8-ejemplos-completos)

---

## 1. Introducción General
Los **Extras** en TUXEDO_RT son módulos extensibles que permiten añadir funcionalidades adicionales a Klipper sin modificar el código base. A diferencia de los extras estándar de Klipper, los extras de TUXEDO_RT están diseñados para operar dentro de un entorno de **Tiempo Real Determinista**, aprovechando las capacidades de gestión de memoria pre-asignada y priorización de hilos.

### Casos de Uso Comunes:
- Control de hardware especializado (sensores personalizados, actuadores).
- Algoritmos de compensación cinemática avanzada.
- Interfaces de usuario o telemetría personalizada.
- Sistemas de seguridad y monitorización proactiva.

---

## 2. Requisitos Previos
Para desarrollar extras de alto rendimiento, se requiere:
- **Conocimientos técnicos**: Python 3.x, arquitectura de Klipper, conceptos básicos de tiempo real (latencia, jitter, planificación de hilos).
- **Versiones compatibles**: TUXEDO_RT v1.0 o superior (Klippy basado en Python 3).
- **Herramientas**: Editor de código (Trae, VS Code), acceso SSH a la máquina Linux donde corre Klipper, y herramientas de profiling como `cProfile` o `py-spy`.

---

## 3. Guía Paso a Paso

### 3.1 Estructura de Archivos
Un extra bien estructurado debe residir en `klippy/extras/` y seguir este patrón:
```text
klippy/extras/mi_extra/
├── __init__.py          # Inicialización y carga
├── core.py              # Lógica principal del extra
└── locales/             # Traducciones i18n
    ├── en.json
    └── es.json
```

### 3.2 Configuración Inicial
Todo extra debe heredar de la interfaz `ExtraInterface` definida en `rtcore.plugin_manager`.

```python
from rtcore.plugin_manager import ExtraInterface

class MiExtra(ExtraInterface):
    def __init__(self, config):
        super().__init__(config.get_printer())
        self.printer = config.get_printer()
        # Leer parámetros del archivo printer.cfg
        self.parametro = config.getfloat('mi_parametro', 1.0)
```

---

## 4. Implementación de i18n
TUXEDO_RT utiliza un sistema de internacionalización basado en archivos JSON.

### Cómo crear traducciones:
1. Crea una carpeta `locales` dentro de tu extra.
2. Define archivos JSON (ej. `es.json`):
```json
{
  "mi_extra": {
    "status": "Estado actual: {valor}",
    "error": "Fallo crítico en sensor {id}"
  }
}
```
3. Uso en el código:
```python
from rtcore.i18n import _
text = _("mi_extra.status", valor=10)
```

---

## 5. Integración con Core RT
El sistema proporciona APIs específicas para garantizar el rendimiento.

### APIs Principales:
- **Memory Manager**: `self.printer.memory_factory.acquire(tipo)` para obtener objetos pre-asignados.
- **RT Priority**: `self.printer.rt_core.set_realtime_priority(priority)` para hilos críticos.
- **Fault Tolerance**: `self.printer.fault_tolerance.register_watchdog(nombre)` para monitorizar la salud del extra.

---

## 6. Testing y Validación
1. **Validación Sintáctica**: `python3 -m py_compile mi_extra.py`.
2. **Pruebas de Latencia**: Ejecutar en entorno real y monitorizar `rt_core` logs para detectar picos de jitter.
3. **Checklist de Deployment**:
   - [ ] ¿Hereda de `ExtraInterface`?
   - [ ] ¿Tiene implementado `get_status`?
   - [ ] ¿Los logs usan el sistema `i18n`?
   - [ ] ¿Evita la creación dinámica de objetos en bucles críticos?

---

## 7. Documentación de Referencia
- **Glosario**:
  - *Jitter*: Variación en el tiempo de ejecución de una tarea periódica.
  - *Lock-free*: Algoritmos que no requieren bloqueos (mutex) para la sincronización.
- **FAQ**:
  - *¿Puedo usar librerías externas?* Sí, siempre que no introduzcan latencias impredecibles por bloqueos de E/S.

---

## 8. Ejemplos Completos

### Ejemplo 1: Monitor de Temperatura RT
Un extra que monitoriza un sensor y actúa con prioridad de tiempo real.
(Ver archivo adjunto `ejemplos/rt_temp_monitor.py`)

### Ejemplo 2: Notificador Multiidioma
Un extra que envía alertas personalizadas basadas en el idioma del sistema.
(Ver archivo adjunto `ejemplos/i18n_notifier.py`)

### Ejemplo 3: Gestor de Memoria para Telemetría
Uso avanzado de pools de objetos para registrar datos de alta frecuencia.
(Ver archivo adjunto `ejemplos/rt_telemetry_pool.py`)
