# Arquitectura TUXEDO_RT: Núcleo Determinista en Tiempo Real

## Introducción
Este documento detalla el diseño y la implementación del nuevo núcleo de Klipper denominado **TUXEDO_RT**, enfocado en proporcionar un alto rendimiento determinista en tiempo real mientras mantiene total compatibilidad con la arquitectura y ecosistema actual (incluyendo la comunicación con Moonraker).

## 1. Núcleo Base de Tiempo Real y Concurrencia (`rt_core.py`)
El núcleo implementa una arquitectura multihilo que aprovecha las políticas de planificación del sistema operativo Linux (`SCHED_FIFO`) para priorizar hilos críticos.
- **Aislamiento de Recursos:** Se utiliza la política FIFO para asegurar que los procesos de control de motores y gestión térmica nunca sean interrumpidos por tareas de usuario.
- **Lock-Free Communication:** Implementado en `ring_buffer.py`, utilizando punteros circulares atómicos que evitan bloqueos (`mutexes`) en rutas de ejecución críticas.

## 2. Gestión de Memoria Pre-asignada (`memory_manager.py`)
Para evitar los picos de latencia impredecibles causados por el recolector de basura (Garbage Collector) de Python, TUXEDO_RT implementa **Object Pooling**.
- Los objetos críticos (eventos de movimiento, paquetes de telemetría) se pre-asignan al inicio.
- Se reutilizan dinámicamente en un pool en O(1), logrando un comportamiento determinista equivalente a un GC de tiempo real.

## 3. Sistema de Colas de Prioridad Múltiple (`priority_queue.py`)
Un sistema de enrutamiento de eventos basado en 4 niveles de prioridad:
1. **Critical:** Emergencias, paradas de seguridad, watchdog.
2. **High:** Planificación de movimiento cinemático y control de pasos.
3. **Normal:** Control térmico y telemetría estándar.
4. **Low:** Tareas de fondo y peticiones de usuario.
Garantiza que el procesamiento de bajo nivel nunca sufra inanición (starvation).

## 4. Framework de Plugins/Extras Escalable (`plugin_manager.py`)
Se ha rediseñado la forma en que los extras se cargan y comunican:
- **Descubrimiento Dinámico:** Carga módulos basándose en interfaces (`ExtraInterface`).
- **Resolución de Dependencias:** Validación automática de dependencias para asegurar un orden de inicialización seguro.
- **Compatibilidad con Moonraker:** Todos los extras deben implementar `get_status(eventtime)`, manteniendo el contrato de API para que las interfaces web como Mainsail o Fluidd sigan funcionando sin cambios.

## 5. Internacionalización y Localización (i18n) (`i18n.py`)
Soporte nativo para traducción de la interfaz y los logs sin penalización de rendimiento.
- **Recarga en Caliente:** Permite cambiar el idioma o actualizar traducciones sin reiniciar el núcleo.
- **Formatos Regionales:** Manejo automático de formatos de números (separadores decimales y de miles) según la configuración regional detectada.

## 6. Tolerancia a Fallos y Telemetría (`fault_tolerance.py`)
Una de las piedras angulares de TUXEDO_RT es su robustez ante fallos en módulos individuales:
- **Watchdog Timers:** Monitorizan el latido (heartbeat) de cada módulo. Si un módulo (ej. un termistor) deja de responder, se aísla de inmediato.
- **Checkpoints de Estado:** Guardan el último estado válido conocido, permitiendo una recuperación rápida y segura en caso de un cuelgue temporal.
- **Métricas de Rendimiento:** Trazado determinista de eventos para análisis predictivo sin impacto en el rendimiento en producción.

## Conclusión
La arquitectura TUXEDO_RT transforma Klipper de un sistema puramente reaccionario a un verdadero RTOS (Real-Time Operating System) a nivel de host, aislando fallos, garantizando latencias sub-milisegundo para tareas críticas y proveyendo un entorno moderno y escalable para el desarrollo de nuevos plugins.
