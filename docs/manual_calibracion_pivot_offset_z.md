# Manual de Calibración del Parámetro pivot_offset_z

## Cinemática de 5 Ejes para Klipper

**Versión del documento:** 1.0
**Fecha de creación:** 2026-04-01
**Módulo:** `pivot_calibrate.py` en `klippy/extras`
**Cinemática compatible:** `five_axis` en `klippy/kinematics/five_axis.py`

---

## 1. Introducción y Fundamentos Teóricos

### 1.1 Descripción del Parámetro

El parámetro `pivot_offset_z` define la distancia vertical entre el plano de referencia de la mesa y el centro de rotación del mecanismo de inclinación (eje A) en una máquina de 5 ejes. Este valor es crítico para la compensación precisa de la Trayectoria del Centro de la Herramienta (TCP, por sus siglas en inglés).

### 1.2 Principio Físico de la Compensación

Cuando el eje A rota un ángulo θ, la posición del TCP en el eje Z se desplaza según la ecuación de compensación:

```
Z_tcp = Z_base + pivot_offset_z × (1 - cos(θ))
```

Donde:
- `Z_tcp` = Posición Z corregida del TCP
- `Z_base` = Posición Z de referencia (mesa sin rotación)
- `pivot_offset_z` = Distancia vertical al centro de rotación
- `θ` = Ángulo de rotación del eje A

Este desplazamiento ocurre porque el radio de rotación aumenta proporcionalmente a la distancia vertical al pivote. Un `pivot_offset_z` inexacto produce errores dimensionales que crecen con el seno del ángulo de rotación.

### 1.3 Impacto en la Precisión del Mecanizado

| Error en pivot_offset_z | Efecto a θ=45° | Efecto a θ=90° |
|------------------------|----------------|----------------|
| +1 mm | +0.293 mm en Z | +1.0 mm en Z |
| -1 mm | -0.293 mm en Z | -1.0 mm en Z |
| +0.1 mm | +0.029 mm en Z | +0.1 mm en Z |

---

## 2. Preparación Inicial de la Máquina

### 2.1 Requisitos de Hardware

Antes de iniciar la calibración, verifique que la máquina cumple los siguientes requisitos:

1. **Cinemática de 5 ejes configurada** con `five_axis` como tipo de kinematics
2. **Homing completado** para todos los ejes (X, Y, Z, A, B)
3. **Superficie de referencia estable** (mesa de trabajo o bloque patrón)
4. **Herramienta de medición** (calibrador digital, reloj comparador o método del papel)
5. **Acceso a la consola Klipper** mediante OctoPrint, Mainsail, Fluidd o conexión SSH

### 2.2 Verificación del Estado del Sistema

Ejecute los siguientes comandos para verificar el estado:

```
G28 ALL    ; Homing de todos los ejes
GET_POSITION    ; Muestra posición actual de todos los ejes
```

La consola debe mostrar algo similar a:

```
========= Quieting down for prepare_download#########
mcu 'mcu': timestamps: 1457709.958 1457709.968 1457709.977
stepper_a: position: 0000
stepper_b: position: 0000
stepper_x: position: 0000
stepper_y: position: 0000
stepper_z: position: 0000
```

### 2.3 Configuración del Módulo

Agregue la siguiente sección a su archivo `printer.cfg`:

```ini
[pivot_calibrate]
max_pivot_offset: 500.0
calibration_angles: 0.0, 30.0, 45.0, 60.0
```

**Parámetros configurables:**

| Parámetro | Tipo | Valor por defecto | Descripción |
|-----------|------|-------------------|--------------|
| `max_pivot_offset` | float | 500.0 mm | Límite superior válido para el offset |
| `calibration_angles` | lista | 0.0, 30.0, 45.0, 60.0 | Ángulos de calibración en grados |

---

## 3. Procedimiento de Calibración Paso a Paso

### 3.1 Diagrama de Flujo del Proceso

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INICIO DE CALIBRACIÓN                        │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. HOMING COMPLETO (G28 ALL)                                        │
│    ¿Todos los ejes referenciados?                                   │
└─────────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │ NO                    │ SÍ
                    ▼                       ▼
         ┌──────────────┐      ┌─────────────────────────────────┐
         │ Error:       │      │ 2. VERIFICAR CINEMÁTICA         │
         │ Ejecutar     │      │ ¿Kinematics es five_axis?       │
         │ G28 ALL      │      └─────────────────────────────────┘
         └──────────────┘                     │
                                  ┌───────────┴───────────┐
                                  │ NO                    │ SÍ
                                  ▼                       ▼
                       ┌──────────────┐    ┌─────────────────────────────────┐
                       │ Error:       │    │ 3. INICIAR CALIBRACIÓN          │
                       │ Cinemática   │    │ CALIBRATE_PIVOT_OFFSET_Z        │
                       │ incompatible │    │ [ANGLES=0,30,45,60]             │
                       └──────────────┘    └─────────────────────────────────┘
                                                      │
                                                      ▼
                              ┌─────────────────────────────────────────┐
                              │ 4. ESTABLECER LÍNEA BASE (A=0°)         │
                              │    Colocar papel/pie de rey y ACCEPT    │
                              └─────────────────────────────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────────────┐
                              │ 5. ROTACIÓN AUTOMÁTICA A SIGUIENTE      │
                              │    ÁNGULO Y MEDICIÓN                    │
                              │    Repetir para cada ángulo             │
                              └─────────────────────────────────────────┘
                                              │
                              ┌───────────────┴───────────────┐
                              │ ¿Todos los ángulos            │
                              │ medidos?                      │
                              └───────────────────────────────┘
                                        │ NO             │ SÍ
                                        ▼                ▼
                              ┌──────────────┐  ┌──────────────────────┐
                              │ Continuar    │  │ 6. GUARDAR RESULTADO │
                              │ mediciones   │  │ SAVE_PIVOT_OFFSET_Z  │
                              └──────────────┘  └──────────────────────┘
                                                          │
                                                          ▼
                                          ┌───────────────────────────┐
                                          │ 7. PERSISTIR CONFIG.      │
                                          │ SAVE_CONFIG               │
                                          └───────────────────────────┘
                                                          │
                                                          ▼
                                          ┌───────────────────────────┐
                                          │ FIN - Máquina recalibrada │
                                          └───────────────────────────┘
```

### 3.2 Ejecución del Comando de Calibración Guiada

Una vez verificado el estado de la máquina, ejecute:

```
CALIBRATE_PIVOT_OFFSET_Z
```

Opcionalmente, puede especificar ángulos personalizados:

```
CALIBRATE_PIVOT_OFFSET_Z ANGLES=0,20,40,60,80
```

### 3.3 Secuencia de Mensajes de Consola Esperados

**Fase 1 - Inicialización:**
```
Iniciando calibración interactiva de pivot_offset_z.
Este proceso ajustará el centro de rotación del cabezal.
Ángulos planificados: 0.00, 30.00, 45.00, 60.00
Paso 1: Estableciendo línea base en A=0.00
```

**Fase 2 - Línea Base:**
```
Posicionando cabezal para ángulo A=0.00...
Medición base (A=0.00) registrada: Z=10.2500
Esta es la referencia. Las siguientes mediciones determinarán la desviación.
```

**Fase 3 - Mediciones Subsecuentes:**
```
Paso 2 de 4: Moviendo a A=30.00...
Realice el 'test del papel' (use TESTZ) y luego ACCEPT.
Medición registrada para A=30.00: Z=11.5200 (Desviación: 1.2700 mm)

Paso 3 de 4: Moviendo a A=45.00...
Realice el 'test del papel' (use TESTZ) y luego ACCEPT.
Medición registrada para A=45.00: Z=13.0500 (Desviación: 2.8000 mm)
```

**Fase 4 - Finalización:**
```
¡Todas las mediciones completadas!
Proceda a ejecutar SAVE_PIVOT_OFFSET_Z para aplicar los cambios.
```

---

## 4. Interpretación de Resultados

### 4.1 Estructura del Reporte de Calibración

Después de ejecutar `SAVE_PIVOT_OFFSET_Z`, se genera un reporte detallado:

```
--- REPORTE DE CALIBRACIÓN PIVOT_OFFSET_Z ---
Análisis de desviación para cinemática 5-ejes
-------------------------------------------
Pivot Offset actual:     150.0000 mm
Z Base (Referencia A=0):  10.2500 mm

Ángulo A (°) | Z Medido     | Error (dz)   | Est. Pivot
-------------------------------------------------------
       30.00 |      11.5200 |       1.2700 |     159.4782
       45.00 |      13.0500 |       2.8000 |     159.5534
       60.00 |      15.1000 |       4.8500 |     159.7000
-------------------------------------------------------
Ajuste sugerido (Delta P):    +9.5842 mm
NUEVO PIVOT_OFFSET_Z CALCULADO: 159.584215 mm
```

### 4.2 Significado de Cada Columna

| Columna | Descripción | Interpretación |
|---------|-------------|----------------|
| `Ángulo A (°)` | Posición angular del eje A | Referencia del plano de medición |
| `Z Medido` | Altura registrada en ese ángulo | Valor crudo del sensor o test |
| `Error (dz)` | Diferencia respecto a Z base | Error de posición TCP |
| `Est. Pivot` | Estimación individual del offset | Valor calculado con este punto |

### 4.3 Criterios de Aceptación

Para considerar una calibración exitosa:

1. **Consistencia entre puntos**: Las estimaciones individuales del pivot offset no deben diferir en más de 0.5 mm
2. **Monotonicidad del error**: El error debe aumentar con el ángulo de rotación
3. **Rango válido**: El valor calculado debe estar entre 0 y `max_pivot_offset`

---

## 5. Persistencia de la Configuración

### 5.1 Comando SAVE_PIVOT_OFFSET_Z

Este comando:
1. Calcula el valor óptimo mediante regresión por mínimos cuadrados
2. Actualiza el valor en memoria de la cinemática activa
3. Prepara la configuración para ser guardada

**Mensaje de confirmación:**
```
pivot_offset_z: 159.584215
El comando SAVE_CONFIG actualizará el archivo printer.cfg
y reiniciará la máquina con los nuevos parámetros.
```

### 5.2 Comando SAVE_CONFIG

Ejecute para persistir definitivamente:

```
SAVE_CONFIG
```

Este comando:
1. Escribe el nuevo valor en `printer.cfg`
2. Crea una copia de seguridad con marca temporal
3. Solicita reinicio del firmware

### 5.3 Verificación Post-Guardado

Después del reinicio, verifique la persistencia:

```
GET_CONFIG
```

O inspeccione directamente `printer.cfg`:
```ini
[five_axis]
pivot_offset_z: 159.584215
```

---

## 6. Método de Calibración Manual (Avanzado)

Para usuarios que prefieren control total del proceso:

### 6.1 Comando PIVOT_MEASURE

Registra mediciones manuales sin guía automática:

```
; Paso 1: Establecer línea base
G28 A0     ; Rotar A a 0 grados
; Posicionar herramienta en contacto
PIVOT_MEASURE

; Paso 2: Rotar a ángulo deseado
G0 A45     ; Mover eje A a 45°
; Medir nueva altura
PIVOT_MEASURE

; Repetir para más puntos...

; Paso 3: Calcular y guardar
SAVE_PIVOT_OFFSET_Z
SAVE_CONFIG
```

### 6.2 Mensajes del Método Manual

```
Línea base registrada (A=0): Z=10.2500
Ahora rote el eje A y use PIVOT_MEASURE nuevamente.

Punto registrado: A=45.00 Z=13.0500 (Error: 2.8000)
Puede añadir más puntos o finalizar con SAVE_PIVOT_OFFSET_Z.
```

---

## 7. Diagnóstico de Problemas

### 7.1 Tabla de Errores Comunes

| Código de Error | Causa Probable | Solución |
|-----------------|----------------|----------|
| `La cinemática five_axis no está activa` | Kinematics incorrecto | Verificar `printer.cfg` y verificar que se usa la cinemática correcta |
| `Todos los ejes deben estar homed` | Homing incompleto | Ejecutar `G28 ALL` antes de calibrar |
| `La primera medición debe ser con A=0` | Ángulo inicial incorrecto | Rotar eje A a 0° antes de `PIVOT_MEASURE` |
| `Ya hay una calibración en curso` | Proceso previo sin finalizar | Ejecutar `SAVE_PIVOT_OFFSET_Z` o reiniciar Klipper |
| `No hay datos suficientes` | Faltan mediciones | Ejecutar `CALIBRATE_PIVOT_OFFSET_Z` primero |

### 7.2 Validación de Rangos

Si el reporte muestra:

```
¡ATENCIÓN!: El valor calculado parece incoherente.
Rango esperado: 0 - 500 mm
Causas posibles: Error en la medición base o movimiento XY.
```

**Acción correctiva:**
1. Verificar que la mesa no se movió durante la calibración
2. Confirmar que el instrumento de medición está correctamente calibrado
3. Repetir la secuencia completa de mediciones
4. Si el problema persiste, verificar la geometría mecánica de la máquina

### 7.3 Interpretación de Patrones de Error

| Patrón Observado | Síntoma | Causa Raíz |
|------------------|---------|------------|
| Error constante | Todos los dz ≈ valor fijo | Offset Z base incorrecto |
| Error creciente linealmente | dz proporc. a ángulo | pivot_offset_z incorrecto |
| Error asimétrico | Diferencia A+ vs A- | Excentricidad del pivote |
| Discontinuidad | Salto en un ángulo | Pivote con holgura o juego |

---

## 8. Validación de la Precisión

### 8.1 Método de Verificación

Después de una calibración exitosa, valide mediante:

1. **Movimiento a ángulo conocido:**
   ```
   G0 A0 Z0
   G0 A45
   ; Medir posición Z real
   ```

2. **Comparar** con el valor teórico:
   ```
   Z_esperado = Z_base + pivot_offset × (1 - cos(45°))
   ```

3. **Calcular error** = Z_esperado - Z_medido

### 8.2 Criterio de Aceptación

El error de posición Z no debe exceder ±0.05 mm para operaciones de mecanizado de precisión.

---

## 9. Recomendaciones de Mantenimiento

### 9.1 Frecuencia de Recalibración

| Condición de Uso | Frecuencia Recomendada |
|-------------------|------------------------|
| Uso regular (diario) | Cada 2-4 semanas |
| Cambio de herramienta | Después de cada cambio |
| Transporte de máquina | Después de cualquier transporte |
| Cambio térmico significativo | Después de温差 > 10°C |
| Golpe o vibración | Inmediatamente después del evento |

### 9.2 Factores que Afectan la Estabilidad

1. **Expansión térmica**: Diferenciales de temperatura modifican las dimensiones mecánicas
2. **Desgaste de componentes**: Rodamientos y guías se degradan con el uso
3. **Rigidez del sistema**: Pivotes con holgura requieren recalibración frecuente
4. **Calidad de mediciones**: Instrumentos descalibrados producen errores sistemáticos

### 9.3 Registro de Calibraciones

Mantenga un historial documentado:

| Fecha | Valor Anterior | Valor Nuevo | Operador | Observaciones |
|-------|----------------|-------------|----------|---------------|
| 2026-04-01 | 150.000 mm | 159.584 mm | Sistema | Calibración inicial |
| 2026-04-15 | 159.584 mm | 159.620 mm | Sistema | Verificación trimestral |

---

## 10. Referencia Rápida de Comandos

### 10.1 Comandos Principales

| Comando | Descripción | Ejemplo |
|---------|-------------|---------|
| `CALIBRATE_PIVOT_OFFSET_Z` | Inicia calibración guiada | `CALIBRATE_PIVOT_OFFSET_Z ANGLES=0,45,90` |
| `PIVOT_MEASURE` | Registra medición manual | `PIVOT_MEASURE` |
| `SAVE_PIVOT_OFFSET_Z` | Calcula y prepara guardado | `SAVE_PIVOT_OFFSET_Z` |
| `SAVE_CONFIG` | Persiste cambios en printer.cfg | `SAVE_CONFIG` |

### 10.2 Comandos de Verificación

| Comando | Descripción |
|---------|-------------|
| `GET_POSITION` | Muestra posición actual de todos los ejes |
| `GET_CONFIG` | Muestra configuración actual |
| `G28 A0` | Home eje A a ángulo cero |

---

## 11. Anexo: Algoritmo de Optimización

### 11.1 Método de Mínimos Cuadrados

El algoritmo calcula el `pivot_offset_z` óptimo minimizando el error cuadrático:

```
Para cada punto de medición (θ_i, Z_i):

    x_i = 1 - cos(θ_i)
    dz_i = Z_i - Z_base
    error_i = dz_i - pivot_offset × x_i

Suma minimizada: Σ(error_i²)
```

La solución analítica es:

```
pivot_offset_óptimo = Σ(dz_i × x_i) / Σ(x_i²)
```

### 11.2 Validación Matemática

El algoritmo asume:
1. Mesa de referencia plana y perpendicular al eje Z
2. Pivote de rotación sin excentricidad
3. Mediciones sin errores sistemáticos
4.温漂 térmico despreciable durante la calibración

---

**Documento elaborado para el sistema de cinemática de 5 ejes TUXEDO_RT**
**Compatible con Klipper firmware - Módulo pivot_calibrate.py v1.0**
