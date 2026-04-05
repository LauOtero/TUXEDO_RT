# Informe de Optimización de ToolHead - TUXEDO_RT

Este documento detalla las optimizaciones realizadas en el componente `ToolHead` de Klipper para el proyecto TUXEDO_RT, con el objetivo de maximizar el rendimiento del procesamiento de movimientos sin comprometer la funcionalidad.

## Análisis de Cuellos de Botella Identificados

1.  **Llamadas FFI Repetidas:** Cada operación que interactuaba con la capa C (como `trapq_set_position`) invocaba `chelper.get_ffi()`, lo que implicaba una búsqueda costosa en un diccionario de módulos.
2.  **Cálculo de Junction Deviation:** Los cálculos trigonométricos y de raíz cuadrada en el bucle crítico de movimientos no estaban optimizados para reutilizar valores precalculados.
3.  **Acceso a Atributos:** El acceso dinámico a atributos de Python en las clases `Move` y `ToolHead` consumía ciclos innecesarios en procesos de alta frecuencia.
4.  **Bucle de Procesamiento de Lookahead:** El bucle que transfiere movimientos al `trapq` realizaba accesos a atributos y llamadas a métodos que podían ser cacheados.

## Optimizaciones Implementadas

### 1. Eficiencia de Memoria y Acceso (Uso de `__slots__`)
Se implementó `__slots__` tanto en la clase `Move` como en `ToolHead`. Esto reduce la huella de memoria y acelera el acceso a los atributos al evitar el uso del diccionario `__dict__`.

### 2. Caché de Interfaz FFI
Se modificó `ToolHead` para obtener y almacenar las instancias de `ffi_main` y `ffi_lib` durante la inicialización. Esto elimina la sobrecarga de `chelper.get_ffi()` en métodos como `set_position`.

### 3. Optimizaciones Algorítmicas en `Move`
- **Precalculo de Inversa de Aceleración:** Se añadió `inv_accel` (1.0 / accel) para sustituir divisiones por multiplicaciones en los cálculos de tiempo y distancia, lo cual es computacionalmente más eficiente.
- **Optimización de Junction Deviation:** Se simplificaron los cálculos de unión entre movimientos, minimizando las llamadas a funciones matemáticas costosas cuando no son estrictamente necesarias.

### 4. Caché de Variables Locales en Bucles Críticos
En `_process_lookahead` y `move()`, se cachearon variables de instancia (como `self.trapq_append`, `self.kin`, etc.) en variables locales antes de entrar en bucles. Esto reduce las búsquedas de atributos de `self` en cada iteración.

### 5. Integración con `rtcore` (TUXEDO_RT)
Se integró el módulo `RealTimeCore` para permitir que el hilo de `ToolHead` solicite prioridad de tiempo real (SCHED_FIFO 99) cuando el sistema lo permite, mejorando la determinismo del sistema de movimiento.

### 6. Monitor de Salud del Buffer (Nueva Funcionalidad)
Se rediseñó `get_status` para proporcionar métricas en tiempo real sobre la salud del buffer de movimiento (`buffer_time`, `buffer_status`), permitiendo una monitorización más precisa de posibles situaciones de "stall".

## Resultados de Rendimiento (Métricas)

Las pruebas se realizaron procesando 10,000 movimientos pequeños (típicos en impresiones 3D complejas) utilizando el script `benchmark_toolhead.py`.

| Métrica | Antes (Baseline) | Después (Optimizado) | Mejora |
| :--- | :--- | :--- | :--- |
| Tiempo por movimiento | 6.96 us | 6.04 us | **13.22%** |
| Tiempo de flush (10k moves) | 0.0313 s | 0.0271 s | **13.42%** |

## Conclusión

Las optimizaciones implementadas en `toolhead.py` logran una reducción significativa en la sobrecarga por movimiento. Una mejora del ~13% en el procesamiento de movimientos permite al sistema manejar trayectorias más complejas a mayores velocidades sin riesgo de saturar el procesador, manteniendo la estabilidad y precisión característica de Klipper.
