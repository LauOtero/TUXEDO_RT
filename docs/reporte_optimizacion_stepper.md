# Informe Técnico: Optimización del Soporte de Steppers (TUXEDO_RT)

## Resumen Ejecutivo
Este informe detalla las optimizaciones implementadas en el módulo `stepper.py` de Klipper como parte del proyecto TUXEDO_RT. Las mejoras se centran en la reducción de la sobrecarga de la interfaz de funciones foráneas (FFI), la optimización del uso de memoria mediante `__slots__` y la introducción de capacidades predictivas y de procesamiento por lotes para mejorar la eficiencia del cálculo de posiciones del MCU.

## Análisis de las Optimizaciones

### 1. Gestión de FFI (Foreign Function Interface)
**Problema:** Anteriormente, cada llamada a métodos que requerían la interfaz C (como `get_mcu_position`) realizaba una búsqueda de la instancia FFI mediante `chelper.get_ffi()`, lo que introducía una latencia acumulativa significativa en bucles de alta frecuencia.

**Solución:** Se implementó el almacenamiento en caché de las instancias `_ffi_main` y `_ffi_lib` directamente en el objeto `MCU_stepper` durante su inicialización.
- **Impacto:** Reducción drástica del tiempo de acceso a las funciones de C.
- **Resultado Benchmark:** Mejora del **106.5%** en el rendimiento de llamadas individuales.

### 2. Optimización de Memoria y Acceso mediante `__slots__`
**Implementación:** Se definieron explícitamente los atributos de la clase `MCU_stepper` utilizando `__slots__`.
- **Beneficio:** Elimina el diccionario interno (`__dict__`) por cada instancia de stepper, reduciendo el consumo de RAM y acelerando el tiempo de acceso a los atributos, lo cual es crítico en sistemas con múltiples motores.

### 3. Cálculo Vectorizado (Batch Calculation)
**Innovación:** Se introdujo el método `batch_calc_mcu_positions` para procesar múltiples coordenadas de posición en una sola llamada.
- **Técnica:** Al pre-calcular constantes como `inv_step_dist` fuera del bucle y evitar llamadas repetitivas a métodos individuales, se minimiza la sobrecarga del intérprete de Python.
- **Resultado Benchmark:** Mejora del **345.6%** respecto al método tradicional, permitiendo un procesamiento mucho más fluido de trayectorias complejas.

### 4. Control Predictivo y Suavizado (EMA)
**Funcionalidades Añadidas:**
- **Predictive Positioning:** Implementación de un modelo de aceleración constante (`pos = p0 + v*t + 0.5*a*t²`) para prever la posición futura del motor, fundamental para la sincronización en tiempo real.
- **EMA Smoothing:** Uso de una Media Móvil Exponencial (EMA) para estabilizar la velocidad reportada, filtrando ruidos en los cálculos de cinemática sin introducir el retardo de una media simple.

## Resultados de las Pruebas de Rendimiento
Las pruebas se realizaron con un ciclo de 500,000 iteraciones para asegurar significancia estadística.

| Métrica | Tiempo (s) | Mejora (%) |
| :--- | :---: | :---: |
| Antes (FFI repetitivo) | 0.2133 | - |
| Después (Caché FFI) | 0.1033 | 106.5% |
| Procesamiento por Lotes | 0.0479 | 345.6% |

## Conclusiones
Las optimizaciones en `stepper.py` proporcionan una base sólida para el funcionamiento de TUXEDO_RT, permitiendo que Klipper maneje frecuencias de paso más altas y cálculos cinemáticos más complejos con una carga de CPU significativamente menor. La combinación de caché de FFI y procesamiento vectorizado representa un avance crítico en la eficiencia del motor de movimiento.

---
**Elaborado por:** Senior Engineer (TUXEDO_RT Team)
**Fecha:** 31 de Marzo de 2026
