# Manual de Calibración Automática de pivot_offset_z

## Cinemática de 5 Ejes - Sistema de Calibración Automatizada

**Versión del Documento:** 1.0
**Fecha de Creación:** 2026-04-01
**Módulo:** `pivot_calibrate.py`
**Cinematica Asociada:** `five_axis.py`

---

## 1. Introduccion

Este manual describe el sistema de calibración automatizada para el parámetro `pivot_offset_z` implementado en el módulo `pivot_calibrate.py`. El sistema permite realizar calibraciones completas sin intervención manual, utilizando el sensor de probe integrado y algoritmos de convergencia automática.

### 1.1 Objetivos del Sistema

El sistema de calibración automatizada persigue los siguientes objetivos técnicos:

- **Eliminación de la intervención manual**: El sistema ejecuta secuencias completas de medición, cálculo y validación de forma autónoma
- **Convergencia automática**: Implementa algoritmos iterativos que refinan el valor de `pivot_offset_z` hasta alcanzar la tolerancia especificada
- **Detección inteligente de errores**: Identifica valores atípicos, tendencias anómalas y problemas mecánicos durante el proceso
- **Recuperación ante fallos**: Implementa estrategias de retry con backoff exponencial y métodos alternativos
- **Generación de reportes**: Produce informes detallados en formato JSON con métricas de calidad y trazabilidad

### 1.2 Principios Físicos

El parámetro `pivot_offset_z` representa la distancia vertical entre el punto de pivote del cabezal rotativo y la punta del nozzle. Esta compensación es necesaria porque la rotación del eje A alrededor de un punto que no coincide exactamente con el nozzle genera un desplazamiento en Z que sigue la ecuación de compensación TCP (Tool Center Point):

```
Z_tcp = Z_base + pivot_offset_z × (1 - cos(θ))
```

Donde:
- `Z_tcp` es la altura efectiva del Tool Center Point
- `Z_base` es la altura medida con el eje A en posición cero
- `pivot_offset_z` es el desplazamiento del centro de rotación respecto al nozzle
- `θ` es el ángulo de rotación del eje A

---

## 2. Arquitectura del Sistema

### 2.1 Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PivotCalibrate (Clase Principal)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌──────────────────────┐  ┌────────────────────┐  │
│  │ MeasurementQuality  │  │   OutlierDetector    │  │ ConvergenceChecker │  │
│  │    Validator        │  │                      │  │                    │  │
│  │  - CV < 2%          │  │  - Método IQR        │  │  - Tolerancia pri. │  │
│  │  - Rango < tol      │  │  - Q1 - 1.5×IQR      │  │  - Tolerancia sec. │  │
│  │  - Muestras ≥ 3     │  │  - Q3 + 1.5×IQR      │  │  - Ventana estab.  │  │
│  └─────────────────────┘  └──────────────────────┘  └────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                 CalibrationRecoveryManager                            │  │
│  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                              │  │
│  │  │ E01 │ │ E02 │ │ E03 │ │ E04 │ │ E05 │   (Códigos de Error)         │  │
│  │  │Retry│ │Retry│ │IncIt│ │Rehom│ │Wait │                              │  │
│  │  │Reset│ │SameP│ │ers  │ │All  │ │&Ret │                              │  │
│  │  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘                              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Estados del Sistema                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   IDLE ──► INITIALIZING ──► HOMING ──► PROBING_BASE ──► PROBING_ANGLE ──►   │
│     ▲                             │                │                        │
│     │                             │                ▼                        │
│     │                             │       CALCULATING ──► VALIDATING        │
│     │                             │                        │                │
│     │                             │                        ▼                │
│     │                             └──────────────────► SAVING ──► COMPLETED │
│     │                                                               │       │
│     │                            ERROR ◄────────────────────────────┘       │
│     │                                                                       │
│     └─────────────────────────────────────────────────────────── ABORTED    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Clases de Validación y Recuperación

#### MeasurementQualityValidator

Esta clase evalúa la calidad de las mediciones en tiempo real utilizando criterios estadísticos:

**Criterios de Aceptación:**
- Coeficiente de Variación (CV) < 2%
- Error estándar de la media < 0.005mm
- Rango (max - min) < tolerancia especificada
- Mínimo de 3 muestras por punto

**Métodos Principales:**

| Método | Descripción |
|--------|-------------|
| `add_measurement(z_value)` | Registra una nueva medición |
| `is_quality_acceptable()` | Retorna (bool, mensaje) con resultado de validación |
| `get_recommended_action()` | Sugiere CONTINUE, ABORT o RETRY |
| `get_statistics()` | Retorna diccionario con count, mean, std_dev, min, max, range |

#### OutlierDetector

Implementa el método IQR (Interquartile Range) para detección de valores atípicos:

**Algoritmo:**
```
Q1 = percentil 25
Q3 = percentil 75
IQR = Q3 - Q1
Límite inferior = Q1 - 1.5 × IQR
Límite superior = Q3 + 1.5 × IQR
Valores fuera de [límite inferior, límite superior] son atípicos
```

**Métodos Principales:**

| Método | Descripción |
|--------|-------------|
| `is_outlier(new_value, historical_values)` | Determina si un valor es atípico |
| `filter_outliers(values)` | Retorna lista sin valores atípicos |
| `get_filtered_median(values)` | Calcula mediana excluyendo atípicos |

#### CalibrationRecoveryManager

Gestiona la recuperación ante errores durante el proceso de calibración:

**Códigos de Error y Estrategias:**

| Código | Descripción | Max Retries | Acción | Fallback |
|--------|-------------|-------------|--------|----------|
| E01 | Error en probe | 3 | RETRY_WITH_RESET | USE_MANUAL_MODE |
| E02 | Calidad inaceptable | 2 | RETRY_SAME_POSITION | SKIP_POINT |
| E03 | Convergencia lenta | 5 | INCREASE_ITERATIONS | REDUCE_ACCURACY |
| E04 | Fallo en homing | 1 | REHOME_ALL | ABORT |
| E05 | Timeout | 10 | WAIT_AND_RETRY | CONTINUE_WITH_WARNING |

**Estrategia de Backoff Exponencial:**
```
delay = backoff_base ^ retry_count
```

#### ConvergenceChecker

Implementa criterios de convergencia dual para determinar cuándo el algoritmo ha alcanzado el óptimo:

**Criterio Primario:**
```
|new_value - value_history[0]| < primary_tolerance
```

**Criterio Secundario (Estabilidad):**
```
std_dev(history[-stability_window:]) < secondary_tolerance
```

**Ventana de Estabilidad:** Por defecto 3 iteraciones

---

## 3. Comandos G-code Disponibles

### 3.1 Comandos de Calibración Manual

#### CALIBRATE_PIVOT_OFFSET_Z

Inicia el proceso de calibración manual interactiva.

**Sintaxis:**
```
CALIBRATE_PIVOT_OFFSET_Z [ANGLES=ang1,ang2,...]
```

**Parámetros Opcionales:**
- `ANGLES`: Lista de ángulos a medir (separados por comas). Por defecto: 0, 30, 45, 60

**Flujo de Ejecución:**
1. Verifica que la cinemática sea five_axis
2. Verifica que todos los ejes estén homed
3. Solicita medición en A=0 (línea base)
4. Para cada ángulo especificado:
   - Rota a la posición angular
   - Solicita medición manual (test del papel)
   - Registra el valor Z

**Mensajes de Salida Esperados:**
```
Iniciando calibración interactiva de pivot_offset_z.
Este proceso ajustará el centro de rotación del cabezal.
Ángulos planificados: 0.00, 30.00, 45.00, 60.00

Paso 1: Estableciendo línea base en A=0.00
Posicionando cabezal para ángulo A=0.00...
Realice el 'test del papel' (use TESTZ) y luego ACCEPT.
Medición base (A=0.00) registrada: Z=0.0000

Paso 2 de 4: Moviendo a A=30.00...
```

#### PIVOT_MEASURE

Registra una medición manual de Z para el ángulo actual.

**Sintaxis:**
```
PIVOT_MEASURE
```

**Uso:**
- Primera ejecución: Registra línea base en A=0
- Ejecuciones posteriores: Registra punto adicional

#### SAVE_PIVOT_OFFSET_Z

Calcula el valor óptimo y persiste la configuración.

**Sintaxis:**
```
SAVE_PIVOT_OFFSET_Z
```

**Flujo:**
1. Calcula `pivot_offset_z` usando mínimos cuadrados
2. Valida que el valor esté dentro del rango físico
3. Actualiza `kin.pivot_offset_z`
4. Persiste en `printer.cfg` mediante `configfile.set()`
5. Muestra reporte de calibración

**Reporte Generado:**
```
--- REPORTE DE CALIBRACIÓN PIVOT_OFFSET_Z ---
Análisis de desviación para cinemática 5-ejes
-------------------------------------------
Pivot Offset actual:      50.0000 mm
Z Base (Referencia A=0):   0.0000 mm

Ángulo A (°) | Z Medido   | Error (dz)  | Est. Pivot
-------------------------------------------------------
       30.00 |   0.0682   |   0.0682   |     50.1396
       45.00 |   0.1464   |   0.1464   |     50.1404
       60.00 |   0.2500   |   0.2500   |     50.1443
-------------------------------------------------------
Ajuste sugerido (Delta P): +0.1403 mm
NUEVO PIVOT_OFFSET_Z CALCULADO:  50.1403 mm

===============================================
  pivot_offset_z actualizado a: 50.140300 mm
===============================================
  *** IMPORTANTE: EJECUTE SAVE_CONFIG ***
```

### 3.2 Comandos de Calibración Automática

#### AUTO_PIVOT_CALIBRATE

Inicia el proceso de calibración automática completo.

**Sintaxis:**
```
AUTO_PIVOT_CALIBRATE [ANGLES=ang1,ang2,...]
```

**Parámetros de Configuración:**
| Parámetro | Tipo | Default | Descripción |
|-----------|------|---------|-------------|
| `max_pivot_offset` | float | 1000.0 | Límite físico superior del pivot |
| `calibration_angles` | list | [0,30,45,60,90] | Ángulos para calibración |
| `calibration_tolerance` | float | 0.001 | Tolerancia de convergencia (mm) |
| `max_iterations` | int | 10 | Iteraciones máximas por ángulo |
| `probe_samples` | int | 5 | Muestras por punto de medición |
| `min_r_squared` | float | 0.95 | R² mínimo para aceptación |
| `safe_z_clearance` | float | 20.0 | Altura segura de movimiento (mm) |
| `probe_speed` | float | 5.0 | Velocidad de aproximación del probe |
| `lift_speed` | float | 10.0 | Velocidad de elevación |

**Flujo de Ejecución Completo:**

```
┌───────────────────────────────────────────────────────────────┐
│                    AUTO_PIVOT_CALIBRATE                       │
├───────────────────────────────────────────────────────────────┤
│  1. INITIALIZING                                              │
│     ├── Verifica cinemática five_axis                         │
│     ├── Verifica que XYZAB estén homed                        │
│     ├── Genera ID de calibración (PCAL-YYYYMMDD-HHMMSS)       │
│     └── Inicializa validadores y contadores                   │
│                                                               │
│  2. HOMING                                                    │
│     └── Ejecuta G28 (homing completo)                         │
│                                                               │
│  3. PROBING_BASE                                              │
│     ├── Mueve a posición segura                               │
│     ├── Posiciona en XY actual, Z alto                        │
│     ├── Ejecuta probe_samples mediciones                      │
│     ├── Valida calidad (CV < 2%, rango < tol)                 │
│     └── Establece Z_base como referencia                      │
│                                                               │
│  4. PROBING_ANGLE (para cada ángulo)                          │
│     ├── Para ángulo = 0: omitir (referencia)                  │
│     ├── Para cada ángulo restante:                            │
│     │   ├── Iterar hasta max_iterations o convergencia        │
│     │   │   ├── Mover a posición segura                       │
│     │   │   ├── Rotar eje A al ángulo objetivo                │
│     │   │   ├── Ejecutar probe_samples                        │
│     │   │   ├── Filtrar outliers                              │
│     │   │   ├── Calcular mediana filtrada                     │
│     │   │   ├── Verificar convergencia                        │
│     │   │   └── Si convergen y calidad OK: siguiente ángulo   │
│     │   └── Si no converge: logging warning                   │
│     └── Registrar measurements[]                              │
│                                                               │
│  5. CALCULATING                                               │
│     ├── Aplicar regresión por mínimos cuadrados               │
│     ├── Calcular delta_p optimal                              │
│     ├── Calcular R² para evaluar bondad del ajuste            │
│     └── Generar quality_metrics                               │
│                                                               │
│  6. VALIDATING                                                │
│     ├── Verificar rango físico (0 <= pivot <= max)            │
│     ├── Verificar R² >= min_r_squared                         │
│     └── Verificar que no se excedió max_iterations            │
│                                                               │
│  7. SAVING                                                    │
│     ├── Actualizar kin.pivot_offset_z                         │
│     ├── Llamar configfile.set('five_axis', 'pivot_offset_z')  │
│     └── Mostrar reporte final                                 │
│                                                               │
│  8. COMPLETED                                                 │
│     └── Sistema listo para SAVE_CONFIG                        │
└───────────────────────────────────────────────────────────────┘
```

**Salida de Consola Esperada:**
```
============================================================
INICIANDO CALIBRACIÓN AUTOMÁTICA DE PIVOT_OFFSET_Z
============================================================
ID: PCAL-20260401-143052
Ángulos: 0.0, 30.0, 45.0, 60.0, 90.0
Tolerancia: 0.001000 mm
Muestras por punto: 5
============================================================

Estado auto: idle -> initializing
Ejecutando homing completo...
Estado auto: initializing -> homing
Estado auto: homing -> probing_base
Midiendo Z base (A=0)...
  Muestra 1: Z=0.000000
  Muestra 2: Z=-0.000200
  Muestra 3: Z=0.000100
  Muestra 4: Z=-0.000100
  Muestra 5: Z=0.000050
Z base establecida: -0.000030 (σ=0.000122, n=5)

Estado auto: probing_base -> probing_angle
Probando ángulo A=30.0 (intento 1/10)
  Muestra 1: Z=0.068100
  Muestra 2: Z=0.068200
  Muestra 3: Z=0.068150
  Resultado: Z=0.068150 (σ=0.000041)
  Convergencia: Coleccionando datos de convergencia

[... repeticiones para cada ángulo ...]

Estado auto: probing_angle -> calculating
Calculando pivot_offset_z óptimo...

  pivot_offset_z actual: 50.000000 mm
  Ajuste calculado: +0.140350 mm
  NUEVO pivot_offset_z: 50.140350 mm
  R²: 0.9987 (confianza: 99.9%)

Estado auto: calculating -> validating
Estado auto: validating -> saving
============================================================
CALIBRACIÓN AUTOMÁTICA COMPLETADA
============================================================
ID: PCAL-20260401-143052
pivot_offset_z: 50.140350 mm
R²: 0.9987 (confianza: 99.9%)
El comando SAVE_CONFIG actualizará printer.cfg
Estado auto: saving -> completed
```

#### AUTO_PIVOT_STATUS

Muestra el estado actual del sistema de calibración.

**Sintaxis:**
```
AUTO_PIVOT_STATUS
```

**Salida:**
```
=== ESTADO DE CALIBRACIÓN AUTOMÁTICA ===
Estado actual: completed
ID de calibración: PCAL-20260401-143052

Z base (A=0): -0.000030 mm
Mediciones realizadas: 4
Ángulos probados: 30.0, 45.0, 60.0, 90.0

Pivot óptimo calculado: 50.140350 mm

Métricas de calidad:
  R²: 0.9987
  Confianza: 99.9%

Convergencia:
  Iteraciones: 23
  Historia: [0.0682, 0.0681, 0.0681, 0.0682, 0.0681]

==========================================
```

#### AUTO_PIVOT_ABORT

Aborta la calibración en curso.

**Sintaxis:**
```
AUTO_PIVOT_ABORT
```

**Uso:** Detiene cualquier calibración en ejecución. Los datos yacollected remain preserved for potential resume.

#### AUTO_PIVOT_RESUME

Reanuda una calibración previamente abortada.

**Sintaxis:**
```
AUTO_PIVOT_RESUME
```

**Requisito:** Solo funciona si `AUTO_PIVOT_ABORT` fue ejecutado previamente.

#### AUTO_PIVOT_VALIDATE

Valida la calibración actual contra los criterios de aceptación.

**Sintaxis:**
```
AUTO_PIVOT_VALIDATE
```

**Criterios de Validación:**
- Rango físico: 0 <= pivot_offset_z <= max_pivot_offset
- Calidad del ajuste: R² >= min_r_squared (por defecto 0.95)

#### AUTO_PIVOT_REPORT

Genera un reporte completo en formato JSON.

**Sintaxis:**
```
AUTO_PIVOT_REPORT
```

**Ejemplo de Salida JSON:**
```json
{
  "calibration_id": "PCAL-20260401-143052",
  "timestamp": "2026-04-01T14:30:52.123456",
  "status": "completed",
  "parameters": {
    "initial_pivot": 50.0,
    "optimal_pivot": 50.14035,
    "adjustment": 0.14035,
    "units": "mm"
  },
  "quality_metrics": {
    "r_squared": 0.9987,
    "delta_pivot": 0.14035,
    "confidence": 99.87,
    "iterations": 23,
    "data_points": 4
  },
  "measurements": [
    {"angle": 30.0, "z": 0.06815, "samples": 5, "std_dev": 0.000041},
    {"angle": 45.0, "z": 0.14645, "samples": 5, "std_dev": 0.000038},
    {"angle": 60.0, "z": 0.25002, "samples": 5, "std_dev": 0.000052},
    {"angle": 90.0, "z": 0.50018, "samples": 5, "std_dev": 0.000061}
  ],
  "convergence": {
    "iterations": 23,
    "current_value": 0.06815,
    "history": [0.0682, 0.0681, 0.0681, 0.0682],
    "primary_tolerance": 0.001,
    "secondary_tolerance": 0.005
  },
  "validation": {
    "range_check": "PASS",
    "quality_check": "PASS",
    "convergence_check": "PASS"
  }
}
```

---

## 4. Algoritmo de Convergencia

### 4.1 Regresión por Mínimos Cuadrados

El sistema utiliza regresión por mínimos cuadrados para determinar el valor óptimo de `pivot_offset_z`:

**Modelo Matemático:**
```
ΔZ = pivot_offset_z × (1 - cos(θ))
```

Para cada medición i:
```
x_i = 1 - cos(θ_i)
y_i = Z_measured_i - Z_base_i
```

El estimador de mínimos cuadrados:
```
pivot_offset_z = Σ(x_i × y_i) / Σ(x_i²)
```

### 4.2 Cálculo del Coeficiente R²

El coeficiente de determinación R² evalúa la calidad del ajuste:

```
SS_res = Σ(y_i - ŷ_i)²     (Suma de cuadrados residual)
SS_tot = Σ(y_i - ȳ)²       (Suma de cuadrados total)

R² = 1 - (SS_res / SS_tot)
```

Donde:
- `R² = 1`: Ajuste perfecto
- `R² > 0.95`: Excelente bondad del ajuste
- `R² < 0.90`: Indica problemas en las mediciones o modelo inadecuado

### 4.3 Detección de Convergencia

El algoritmo de convergencia dual utiliza dos criterios:

**Criterio de Convergencia Primaria:**
```
|value_new - value_history[0]| < primary_tolerance
```

**Criterio de Estabilidad (Convergencia Secundaria):**
```
std_dev(history[-window:]) < secondary_tolerance
```

Donde `secondary_tolerance = 5 × primary_tolerance` por defecto.

**Condición de Terminación:**
```
primary_tolerance_satisfied AND stability_satisfied
```

---

## 4. Integración con TUXEDO_RT y Tiempo Real

El sistema de calibración `pivot_calibrate.py` ha sido diseñado específicamente para aprovechar las capacidades del núcleo **TUXEDO_RT**, garantizando precisión sub-microsegundo y ejecución determinista.

### 4.1 Arquitectura de Hilos RT

A diferencia de los extras estándar de Klipper que se ejecutan en el reactor principal (compartiendo tiempo con la gestión de G-code y comunicación), la calibración automática de TUXEDO_RT utiliza un hilo dedicado gestionado por el `RTCore`:

1. **Aislamiento de Ejecución**: El proceso de calibración automática (`AUTO_PIVOT_CALIBRATE`) se registra en el `RTCore` como un hilo crítico.
2. **Priorización SCHED_FIFO**: Se asigna una prioridad de tiempo real (por defecto 85) utilizando la política de planificación FIFO de Linux. Esto asegura que los cálculos de convergencia y las mediciones de probe no sean interrumpidos por tareas de menor prioridad del sistema operativo.
3. **Determinismo**: Al heredar de `ExtraInterface`, el módulo se integra nativamente con el gestor de plugins de TUXEDO_RT, permitiendo el bloqueo de memoria (`mlockall`) para evitar latencias por fallos de página durante la calibración.

### 4.2 Sincronización y Seguridad

- **RT-Safe Locking**: El acceso a los estados de calibración y métricas de calidad está protegido por un `threading.Lock()` para garantizar la consistencia de datos entre el hilo de calibración RT y las consultas de estado desde la API de Moonraker/Fluidd.
- **Señales de Aborto**: El comando `AUTO_PIVOT_ABORT` utiliza un `threading.Event()` para detener de forma segura e inmediata la ejecución del hilo RT, garantizando que el cabezal se detenga y se retraiga a una posición segura.

### 4.3 Internacionalización (i18n)

El sistema soporta múltiples idiomas de forma nativa a través del núcleo `i18n`. Todos los mensajes de consola, estados de error y reportes se traducen automáticamente según la configuración del sistema, facilitando su uso en entornos globales sin sacrificar el rendimiento técnico.

---

## 5. Solución de Problemas y Diagnóstico

### 5.1 Códigos de Error

| Código | Causa | Respuesta Automática |
|--------|-------|---------------------|
| E01 | Fallo en lectura del probe | Retry con reset (3 intentos) |
| E02 | Calidad de medición inaceptable | Retry en misma posición (2 intentos) |
| E03 | Convergencia lenta | Incrementar iteraciones (5 intentos) |
| E04 | Fallo en homing | Re-homing completo (1 intento) |
| E05 | Timeout | Esperar y reintentar (10 intentos) |

### 5.2 Valores Atípicos

El detector IQR elimina valores que se desvían significativamente del patrón:

**Ejemplo de Filtrado:**
```
Mediciones originales: [0.0681, 0.0682, 0.1500, 0.0681, 0.0680]
Q1 = 0.0681
Q3 = 0.0682
IQR = 0.0001
Límite inferior = 0.0681 - 1.5 × 0.0001 = 0.06795
Límite superior = 0.0682 + 1.5 × 0.0001 = 0.06835

Valor filtrado: [0.0681, 0.0682, 0.0681, 0.0680]
Mediana filtrada: 0.0681
```

### 5.3 Recuperación ante Fallos

```
┌─────────────────────────────────────────────────────────────┐
│                  Flujo de Recuperación                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Error Detectado ──► Registro en error_history             │
│                              │                              │
│                              ▼                              │
│                    ¿retry_count < max?                      │
│                     │              │                        │
│                    SÍ              NO                       │
│                     │              │                        │
│                     ▼              ▼                        │
│          Calcular backoff    Ejecutar fallback              │
│          delay = 2^retry      (método alternativo)          │
│                     │              │                        │
│                     └──────┬───────┘                        │
│                            ▼                                │
│                    ¿fallback == ABORT?                      │
│                     │              │                        │
│                    SÍ              NO                       │
│                     │              │                        │
│                     ▼              ▼                        │
│              Terminar con       Continuar con               │
│              error fatal        resultado parcial           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Configuración en printer.cfg

### 6.1 Sección [five_axis]

```ini
[five_axis]
pivot_offset_z: 50.000000
TCP_compensation: True
homing_order: xyzab
```

### 6.2 Sección [pivot_calibrate]

```ini
[pivot_calibrate]
max_pivot_offset: 1000.0
calibration_angles: 0, 30, 45, 60, 90
calibration_tolerance: 0.001
max_iterations: 10
probe_samples: 5
min_r_squared: 0.95
safe_z_clearance: 20.0
probe_speed: 5.0
lift_speed: 10.0
```

### 6.3 Parámetros Detallados

| Parámetro | Tipo | Default | Rango | Descripción |
|-----------|------|---------|-------|-------------|
| `max_pivot_offset` | float | 1000.0 | > 0 | Límite físico superior del pivot |
| `calibration_angles` | list | [0,30,45,60,90] | - | Ángulos para calibración |
| `calibration_tolerance` | float | 0.001 | > 0 | Tolerancia de convergencia (mm) |
| `max_iterations` | int | 10 | >= 1 | Iteraciones máximas por ángulo |
| `probe_samples` | int | 5 | >= 3 | Muestras por punto de medición |
| `min_r_squared` | float | 0.95 | [0,1] | R² mínimo para aceptación |
| `safe_z_clearance` | float | 20.0 | > 0 | Altura segura de movimiento (mm) |
| `probe_speed` | float | 5.0 | > 0 | Velocidad de aproximación del probe |
| `lift_speed` | float | 10.0 | > 0 | Velocidad de elevación |

---

## 7. Procedimiento Completo de Calibración Automática

### 7.1 Precondiciones

1. **Sistema Mecánico:**
   - Cabezal de 5 ejes instalado y nivelado
   - Sensor de probe configurado y funcional
   - Cama de impresión estable y nivelada

2. **Configuración Software:**
   - `[five_axis]` kinematics cargado
   - `[probe]` sensor configurado
   - `[pivot_calibrate]` sección definida en printer.cfg

3. **Condiciones Ambientales:**
   - Máquina en temperatura de operación
   - Sin vibraciones externas significativas
   - Entorno libre de corrientes de aire

### 7.2 Secuencia de Comandos

```bash
# 1. Verificar estado inicial
AUTO_PIVOT_STATUS

# 2. Asegurar que todos los ejes están homed
G28

# 3. Ejecutar calibración automática
AUTO_PIVOT_CALIBRATE ANGLES=0,30,45,60,90

# 4. Verificar resultados
AUTO_PIVOT_STATUS

# 5. Validar calidad del ajuste
AUTO_PIVOT_VALIDATE

# 6. Generar reporte si es necesario
AUTO_PIVOT_REPORT

# 7. Persistir configuración
SAVE_CONFIG

# 8. Verificar que se aplicó correctamente
GET_POSITION
```

### 7.3 Postcondiciones

Después de una calibración exitosa:

1. **Verificar `pivot_offset_z` en printer.cfg:**
   ```bash
   # El archivo debe contener:
   [five_axis]
   pivot_offset_z: 50.140350
   ```

2. **Ejecutar FIRMWARE_RESTART:**
   ```bash
   FIRMWARE_RESTART
   ```

3. **Verificar precisión:**
   - Imprimir pieza de prueba en diferentes ángulos
   - Medir desviaciones dimensionales
   - Comparar con especificaciones

---

## 8. Mantenimiento y Recalibración

### 8.1 Frecuencia de Calibración

| Situación | Frecuencia Recomendada |
|-----------|----------------------|
| Operación normal | Cada 100 horas de uso |
| Cambio de cabezal | Inmediatamente después |
| Cambio de nozzle | Verificar con pieza de prueba |
| Temperatura ambiente > 10°C diferente | Verificar antes de impresion |
| Después de mantenimiento mecánico | Siempre |

### 8.2 Indicadores de Necesidad de Recalibración

- Desviaciones dimensionales > 0.1mm en piezas de prueba
- Inconsistencia en mediciones de altura al rotar eje A
- Mensajes de error durante AUTO_PIVOT_CALIBRATE
- R² < 0.90 en validación posterior

### 8.3 Registro de Calibraciones

Se recomienda mantener un registro de calibraciones:


| Fecha       | ID Calibración | Pivot Anterior | Pivot Nuevo | R²    | Operador |
|-------------|----------------|----------------|-------------|-------|----------|
| 2026-04-01  | PCAL-20260401  | 50.000000      | 50.140350   | 0.998 | Sistema  |
| 2026-03-15  | PCAL-20260315  | 50.100000      | 50.000000   | 0.992 | Sistema  |

---

## 9. Solución de Problemas

### 9.1 Errores Comunes

| Error | Causa Probable | Solución |
|-------|---------------|----------|
| "Kinematics no es five_axis" | Configuración incorrecta | Verificar que five_axis.py está cargado |
| "Ejes no homed" | Secuencia de homing incompleta | Ejecutar G28 antes de calibrar |
| "Probe no disponible" | Sensor no configurado | Verificar sección [probe] en printer.cfg |
| "Calidad inaceptable" | Vibración o inestabilidad | Verificar mecânica y entorno |
| "R² bajo" | Mediciones inconsistentes | Repetir calibración en mejores condiciones |
| "Pivot fuera de rango" | Error en medición base | Verificar posición y movimiento XY |

### 9.2 Diagnóstico de Fallos

**Flujo de Diagnóstico:**
```
Síntoma ──► AUTO_PIVOT_STATUS ──► AUTO_PIVOT_REPORT ──► Análisis de métricas
                                              │
                                              ▼
                                   ¿quality_metrics.r_squared OK?
                                      │                    │
                                     SÍ                   NO
                                      │                    │
                                      ▼                    ▼
                              Calibración válida    Revisar measurements[]
                                                             │
                                                             ▼
                                                  ¿Outliers detectados?
                                                     │              │
                                                    SÍ             NO
                                                     │              │
                                                     ▼              ▼
                                             Filtrar datos    Verificar mecánica
                                             y recalcular     y entorno
```

### 9.3 Recuperación Manual

Si la calibración automática falla:

1. **Usar modo manual:**
   ```bash
   CALIBRATE_PIVOT_OFFSET_Z ANGLES=0,30,45,60
   ```

2. **Medir manualmente:**
   ```bash
   # En A=0
   G0 Z=0
   PIVOT_MEASURE
   
   # En cada ángulo
   G0 A=30
   # Ajustar Z hasta papel rozando
   PIVOT_MEASURE
   
   # Repetir para cada ángulo
   ```

3. **Guardar resultado:**
   ```bash
   SAVE_PIVOT_OFFSET_Z
   ```

---

## 10. Especificaciones Técnicas

### 10.1 Requisitos del Sistema

- **Hardware:** Sistema de 5 ejes con eje A rotativo
- **Firmware:** Klipper con soporte five_axis kinematics
- **Sensor:** Probe compatible con modo endstop继电器
- **Memoria:** Mínimo 50KB libre para operación

### 10.2 Límites Operacionales

| Parámetro | Valor Mínimo | Valor Típico | Valor Máximo |
|-----------|--------------|--------------|--------------|
| `pivot_offset_z` | 0.0 mm | 30-100 mm | 1000.0 mm |
| Ángulo de rotación | -180° | 0-90° | +180° |
| Tolerancia | 0.0001 mm | 0.001 mm | 0.1 mm |
| Muestras | 3 | 5 | 20 |

### 10.3 Precisión Esperada

| Fuente de Error | Magnitud Típica |
|----------------|------------------|
| Resolución del probe | ±0.005 mm |
| Repetibilidad | ±0.010 mm |
| Error de modelo TCP | ±0.020 mm |
| Error total esperado | ±0.050 mm |

---

## 11. Referencia Rápida de Comandos

| Comando | Descripción |
|---------|------------|
| CALIBRATE_PIVOT_OFFSET_Z | Inicia calibración manual/interactiva |
| PIVOT_MEASURE| Registra medición manual en ángulo actual |
| SAVE_PIVOT_OFFSET_Z | Calcula y guarda pivot_offset_z óptimo |
| AUTO_PIVOT_CALIBRATE | Inicia calibración completamente automática |
| AUTO_PIVOT_STATUS | Muestra estado actual del sistema |
| AUTO_PIVOT_ABORT | Aborta calibración en curso |
| AUTO_PIVOT_RESUME | Reanuda calibración abortada |
| AUTO_PIVOT_VALIDATE | Valida calibración actual |
| AUTO_PIVOT_REPORT | Genera reporte JSON de la última calibración |

---

## 12. Glosario

| Término | Definición |
|---------|------------|
| **TCP (Tool Center Point)** | Punto de referencia del outil, tipicamente el nozzle |
| **pivot_offset_z** | Desplazamiento vertical del centro de rotación respecto al TCP |
| **Covarianza** | Medida de cómo dos variables cambian juntas |
| **IQR (Interquartile Range)** | Rango intercuartílico, diferencia entre Q3 y Q1 |
| **R² (Coeficiente de determinación)** | Métrica de bondad del ajuste en regresión |
| **Backoff exponencial** | Estrategia de retry con demoras crecientes |
| **Convergencia** | Condición donde iteraciones sucesivas producen valores cada vez más cercanos |

---

*Documento generado automáticamente para el sistema de calibración de cinemática de 5 ejes TUXEDO_RT*
