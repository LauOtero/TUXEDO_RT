# Manual Técnico: Reactor de Tiempo Real (RT) - TUXEDO_RT

Este documento proporciona una guía exhaustiva sobre el funcionamiento, configuración y uso del componente `reactor.py` en el ecosistema TUXEDO_RT. El Reactor es el motor central de despacho de eventos, diseñado para ofrecer una precisión de tiempo real superior en sistemas de impresión 3D de alto rendimiento.

---

## **Índice**
1. [Introducción y Arquitectura](#1-introducción-y-arquitectura)
2. [Configuración y Requisitos](#2-configuración-y-requisitos)
3. [Clases Principales](#3-clases-principales)
4. [Métodos del Reactor](#4-métodos-del-reactor)
5. [Gestión de Greenlets y Pausas](#5-gestión-de-greenlets-y-pausas)
6. [Integración con C-Helper (RT Core)](#6-integración-con-c-helper-rt-core)
7. [Ejemplos Prácticos](#7-ejemplos-prácticos)
8. [Uso en Klipper Extras](#8-uso-en-klipper-extras)
9. [Manejo de Errores](#9-manejo-de-errores)
10. [FAQ y Solución de Problemas](#10-faq-y-solución-de-problemas)

---

## **1. Introducción y Arquitectura**

El Reactor en TUXEDO_RT es una evolución del reactor estándar de Klipper, optimizado para entornos con parches de tiempo real (**PREEMPT_RT**). Su función principal es gestionar:
- **Timers:** Tareas programadas con precisión de microsegundos.
- **E/S de Archivos:** Lectura y escritura no bloqueante en descriptores de archivos (sockets, dispositivos seriales).
- **Concurrencia Cooperativa:** Uso de `greenlets` para multitarea ligera.

### **Diagrama de Flujo del Bucle de Despacho**
```text
[Inicio] --> [Check Timers] --> [Calcula Timeout] --> [Select/Poll/EPoll]
    ^               |                 |                      |
    |               v                 v                      v
    +------- [Ejecuta Callbacks] <-- [Eventos de Red/Disco/MCU]
```

---

## **2. Configuración y Requisitos**

### **Requisitos del Sistema**
- **Sistema Operativo:** Linux con parches `PREEMPT_RT` (Recomendado para máxima precisión).
- **Librerías:** `python3-greenlet`, `libc-dev`.
- **Módulos TUXEDO_RT:** Requiere el módulo `chelper` compilado para acceso al reloj monotónico de hardware.

### **Inicialización Automática**
El Reactor selecciona automáticamente el mejor mecanismo de notificación disponible en el sistema:
1. **EPollReactor:** Máximo rendimiento (Linux moderno).
2. **PollReactor:** Estándar de alta eficiencia.
3. **SelectReactor:** Fallback de compatibilidad.

---

## **3. Clases Principales**

### **`ReactorTimer`**
Representa un evento programado en el futuro.
- **Atributos:**
  - `callback`: Función a ejecutar.
  - `waketime`: Tiempo (monotónico) de activación.

### **`ReactorCompletion`**
Utilizado para sincronizar eventos asíncronos. Permite que un `greenlet` espere hasta que una tarea externa se complete.
- **Método `wait(waketime)`**: Pausa el hilo actual hasta que se llame a `complete()`.

### **`ReactorMutex`**
Implementación de exclusión mutua cooperativa para evitar condiciones de carrera entre greenlets.

---

## **4. Métodos del Reactor**

### **`register_timer(callback, waketime)`**
Registra una función para ser ejecutada en un momento específico.
- **Parámetros:**
  - `callback(eventtime)`: Función que recibe el tiempo actual. Debe retornar el próximo `waketime` o `NEVER`.
  - `waketime`: Tiempo absoluto basado en `reactor.monotonic()`.

### **`register_fd(fd, read_callback, write_callback=None)`**
Monitorea un descriptor de archivo para eventos de lectura o escritura.
- **Caso de uso:** Lectura de datos desde una MCU por USB o CAN.

### **`monotonic()`**
Retorna el tiempo actual del sistema con resolución RT.
- **Nota:** En TUXEDO_RT, este método llama directamente a la función de C `get_monotonic` para evitar el overhead de Python.

---

## **5. Gestión de Greenlets y Pausas**

El Reactor utiliza `greenlets` para permitir que el código parezca síncrono mientras es asíncrono por debajo.

### **Método `pause(waketime)`**
Pausa el `greenlet` actual hasta el tiempo indicado.
- **Si el reactor está corriendo:** Cede el control al bucle de despacho.
- **Si el reactor está detenido:** Realiza un `time.sleep()` real (útil durante la inicialización).

---

## **6. Integración con C-Helper (RT Core)**

TUXEDO_RT introduce una conexión directa entre Python y C para minimizar la latencia:
- **Reloj Unificado:** El reactor usa el mismo reloj que el planificador de pasos (step compressor) en C.
- **Async Callbacks:** El método `register_async_callback` permite que una interrupción de hardware en C despierte al reactor de Python de forma inmediata mediante un `pipe` interno.

---

## **7. Ejemplos Prácticos**

### **Ejemplo 1: Timer de precisión**
```python
def mi_tarea(eventtime):
    print(f"Ejecutado en: {eventtime}")
    # Ejecutar de nuevo en 1 segundo
    return eventtime + 1.0

# Registro inicial
reactor.register_timer(mi_tarea, reactor.monotonic() + 1.0)
```

### **Ejemplo 2: Uso de Completion (Sincronización)**
```python
def proceso_largo():
    completion = reactor.completion()
    
    # Supongamos que recibimos una respuesta asíncrona de la MCU
    def al_recibir_datos(resultado):
        completion.complete(resultado)
        
    # Esperar hasta 2 segundos por la respuesta
    resultado = completion.wait(reactor.monotonic() + 2.0)
    return resultado
```

---

## **8. Uso en Klipper Extras**

Los "extras" de Klipper son módulos que extienden la funcionalidad de la impresora. El Reactor es fundamental para que estos módulos realicen tareas periódicas o manejen eventos sin bloquear el hilo principal.

### **Obtención del Reactor**
Dentro de un extra, se accede al reactor a través del objeto `printer`:
```python
class MiExtra:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
```

### **Funcionalidades para Extras**

1.  **Timers Recurrentes**: Ideales para telemetría o chequeos de estado.
    - `register_timer(callback, waketime)`: Inicia el timer.
    - `update_timer(timer, waketime)`: Cambia la próxima ejecución.
    - `unregister_timer(timer)`: Detiene el timer.

2.  **Callbacks Asíncronos**: Para ejecutar código en el hilo del reactor desde manejadores de eventos o hilos externos.
    - `register_async_callback(callback)`: Encola el callback para ejecución inmediata en el reactor.

3.  **Protección de Tiempo Crítico**:
    - `with self.reactor.assert_no_pause():`: Garantiza que no haya suspensiones de greenlets en bloques de código sensibles a la latencia.

### **Ejemplo: Sensor de Filamento Simple**
```python
class FilamentSensor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.check_timer = self.reactor.register_timer(self._check_sensor)
        
    def handle_ready(self):
        # Iniciar chequeo inmediatamente al estar listos
        self.reactor.update_timer(self.check_timer, self.reactor.NOW)

    def _check_sensor(self, eventtime):
        # Leer estado del pin (simulado)
        if self._read_pin():
            logging.info("Filamento detectado")
        
        # Volver a chequear en 500ms
        return eventtime + 0.500

    def _read_pin(self):
        # Lógica de lectura
        return True
```

---

## **9. Manejo de Errores**

### **`ReactorError`**
Se lanza cuando ocurre una violación de las reglas del reactor, como intentar pausar cuando las pausas están prohibidas explícitamente (`assert_no_pause`).

### **Excepciones Comunes**
- **"Internal error - reactor pause disabled"**: Se intentó llamar a `pause()` dentro de un bloque protegido por `assert_no_pause()`.
- **"ValueError: list.remove(x): x not in list"**: Ocurre si se intenta desregistrar un timer que ya ha sido eliminado o nunca existió.

---

## **10. FAQ y Solución de Problemas**

**P: ¿Por qué mi timer llega tarde?**
**R:** Python es un lenguaje con recolector de basura (GC). Si el GC se activa, puede retrasar el reactor. TUXEDO_RT intenta mitigar esto con `_check_gc`, pero para máxima precisión, asegúrate de no crear demasiados objetos temporales en bucles críticos.

**P: ¿Puedo usar hilos (`threading`) con el Reactor?**
**R:** Sí, pero no directamente. Si un hilo externo quiere interactuar con el reactor, **debe** usar `register_async_callback`. Nunca llame a métodos normales del reactor desde otro hilo.

**P: ¿Cómo sé qué reactor estoy usando?**
**R:** Puedes verificarlo en los logs de inicio. TUXEDO_RT imprimirá si está utilizando `EPoll`, `Poll` o `Select`.

---
*Documentación generada para TUXEDO_RT - Nivel Ingeniero Senior.*
