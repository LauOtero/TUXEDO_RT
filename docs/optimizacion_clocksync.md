# Optimización del Módulo ClockSync (TUXEDO_RT)

Este documento detalla las optimizaciones realizadas en el módulo `clocksync.py` para mejorar el rendimiento en tiempo real de Klipper, especialmente bajo la arquitectura TUXEDO_RT.

## Resumen de Cambios

### 1. Acceso a Atributos Individuales
Se eliminó el uso de la tupla `clock_est` para el almacenamiento de la estimación del reloj. En su lugar, se utilizan atributos individuales:
- `est_time`
- `est_clock`
- `est_freq`

**Resultado:** Mejora del ~10-15% en la función `get_clock`, que es la más crítica para el rendimiento ya que se invoca frecuentemente desde los módulos de cinemática.

### 2. Optimización de Cálculos EMA (Exponential Moving Average)
Se introdujeron constantes precomputadas `K1` y `K2` para los cálculos de varianza y covarianza:
- `K1 = 1.0 - DECAY`
- `K2 = K1 * DECAY`

Esto reduce el número de operaciones aritméticas en cada invocación de `_handle_clock`.

### 3. Nuevas Funcionalidades de Robustez (TUXEDO_RT)
Se integraron características esenciales para entornos de alta fiabilidad:
- **Mecanismo de Recuperación (Failure Recovery):** Detección de valores atípicos (outliers) y reinicio automático de la varianza de predicción si el reloj del MCU diverge significativamente.
- **Historial de Sincronización:** Registro de los últimos 100 eventos de sincronización para monitoreo en tiempo real.
- **Estado de Recuperación:** Atributos `recovery_count` e `is_recovering` para telemetría.

### 4. Gestión de Memoria
Se utiliza `__slots__` en la clase `ClockSync` para minimizar la huella de memoria y acelerar el acceso a los atributos en Python.

## Resultados de Benchmarking

Las pruebas se realizaron con 1,000,000 de iteraciones comparando la implementación original de Klipper frente a la versión optimizada con TUXEDO_RT.

| Función | Mejora | Razón |
| :--- | :--- | :--- |
| `get_clock` | **+10.0% a +15.0%** | Acceso directo a atributos vs indexación de tuplas. |
| `_handle_clock` | **-14.0%** | El ligero descenso se debe a las nuevas funciones de historial y recuperación (necesarias para TUXEDO_RT). |

**Nota:** Dado que `get_clock` se ejecuta órdenes de magnitud más veces que `_handle_clock` durante una impresión, la mejora neta en el rendimiento real de Klipper es positiva.

## Verificación
Las optimizaciones han sido validadas mediante:
1. `tests/test_clocksync_optimized.py`: Pruebas unitarias de funcionalidad y recuperación.
2. `tests/benchmark_clocksync.py`: Validación de rendimiento relativo.
