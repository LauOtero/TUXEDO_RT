# Manual Técnico: Sistema Cinemático de 5 Ejes con TCP para Klipper

## Índice General

1. [Introducción](#introduccion)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Optimizaciones de Rendimiento en C](#optimizaciones-de-rendimiento-en-c)
4. [Optimizaciones de Rendimiento en Python](#optimizaciones-de-rendimiento-en-python)
5. [Nuevas Funcionalidades](#nuevas-funcionalidades)
6. [Guía de Configuración](#guia-de-configuracion)
   6.1. [Parámetros de Configuración](#parametros-de-configuracion)
   6.2. [Parámetros de Cinemática](#parametros-de-cinematica)
   6.3. [Parámetros de Límites](#parametros-de-limites)
   6.4. [Parámetros de Compensación TCP](#parametros-de-compensacion-tcp)
   6.5. [Parámetros de Secuencias de Homing](#parametros-de-secuencias-de-homing)
   6.6. [Procedimientos de Calibración](#procedimientos-de-calibracion)
   6.7. [Casos de Uso Reales](#casos-de-uso-reales)
   6.8. [Algoritmos de Transformación Inversa](#algoritmos-de-transformacion-inversa)
7. [Guía de Uso](#guia-de-uso)
8. [Benchmarks y Métricas de Rendimiento](#benchmarks-y-metricas-de-rendimiento)
9. [Referencia de API](#referencia-de-api)
10. [Solución de Problemas](#solucion-de-problemas)
    10.1. [Problemas Comunes y Soluciones](#problemas-comunes-y-soluciones)
    10.2. [Diagnóstico Avanzado](#diagnostico-avanzado)
    10.3. [Registro de Errores](#registro-de-errores)
    10.4. [Interpretación de Desviaciones Dimensionales](#interpretacion-de-desviaciones-dimensionales)
    10.5. [Ajustes Finos para Optimización de Precisión](#ajustes-finos-para-optimizacion-de-precision)

---

## 1. Introducción

<a name="introduccion"></a>

### 1.1 Propósito del Documento

Este manual técnico documenta exhaustivamente las optimizaciones de rendimiento implementadas en el sistema cinemático de 5 ejes para Klipper, así como las nuevas funcionalidades añadidas para extender las capacidades del sistema. El objetivo principal es alcanzar el máximo rendimiento posible sin sacrificar ninguna funcionalidad existente, reduciendo la latencia, minimizando el uso de recursos computacionales y mejorando el throughput del sistema.

El sistema cinemático de 5 ejes implementado controla tres ejes lineales (X, Y, Z) y dos ejes rotacionales (A, B), proporcionando compensación de TCP (Tool Center Point) para mantener la punta de la herramienta estacionaria respecto a la pieza de trabajo durante movimientos de rotación. Esta capacidad es fundamental en aplicaciones de mecanizado donde la precisión posicional de la herramienta es crítica.

### 1.2 Alcance de las Optimizaciones

Las optimizaciones implementadas abarcan dos componentes principales: el helper C de bajo nivel (`kin_5axis.c`) que maneja cálculos críticos de posición a frecuencias de 10-100 kHz, y el módulo Python de alto nivel (`five_axis.py`) que gestiona la integración con el toolhead de Klipper y proporciona funcionalidades avanzadas de configuración y control.

El helper C se ejecuta en el camino crítico de generación de pulsos de paso, lo que significa que cada microsegundo de latencia añadida se multiplica por el número total de pasos generados durante una impresión o mecanizado. Por esta razón, las optimizaciones en C se centran en eliminar bifurcaciones condicionales, aprovechar instrucciones SIMD cuando están disponibles, y minimizar los accesos a memoria principal mediante el uso de cachés optimizados.

El módulo Python, por otro lado, se ejecuta en un contexto menos crítico temporal, pero procesa comandos de configuración, validaciones de movimiento y cálculos de transformación de coordenadas. Las optimizaciones en Python se enfocan en reducir las llamadas a funciones matemáticas mediante cachés LRU y trigonométricos, implementar validaciones sin bifurcaciones, y proporcionar funcionalidades avanzadas que antes no estaban disponibles.

### 1.3 Fundamentos del TCP

El Control de Punto Central de Herramienta (TCP) es una técnica fundamental en sistemas de mecanizado de 5 ejes que permite que la punta de la herramienta se mantenga en una posición fija relativa a la pieza de trabajo mientras los ejes rotacionales se mueven. Sin TCP, cuando el eje A o B rota, la punta de la herramienta describirá un arco en el espacio, moviéndose lejos de la posición deseada.

La compensación TCP funciona calculando cuánto deben moverse los ejes X, Y, Z para contrarrestar el movimiento de rotación. Las fórmulas de transformación son:

```
TCP_x = X - pivot * sin(A) * cos(B) - tool_offset_x
TCP_y = Y - pivot * sin(A) * sin(B) - tool_offset_y
TCP_z = Z - pivot * (1 - cos(A)) - tool_offset_z
```

Donde `pivot` es la distancia desde el centro de rotación hasta la punta de la herramienta (positiva hacia -Z según la convención de Klipper), y los offsets de herramienta son compensaciones adicionales específicas de cada herramienta.

---

## 2. Arquitectura del Sistema

<a name="arquitectura-del-sistema"></a>

### 2.1 Diagrama de Componentes

El sistema cinemático de 5 ejes se estructura en dos capas principales que se comunican a través de la interfaz FFI (Foreign Function Interface) de Python:

```
┌─────────────────────────────────────────────────────────────────┐
│                    FiveAxisKinematics (Python)                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ LRU Cache   │  │Trig Cache   │  │ Optional Features       │  │
│  │ (32 entry)  │  │ (16 entry)  │  │ - TCP Modes             │  │
│  └─────────────┘  └─────────────┘  │ - Soft Limits           │  │
│                                    │ - Dynamic Accel         │  │
│                                    │ - Homing Sequences      │  │
│                                    └─────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    FFI Interface (cffi)                         │
├─────────────────────────────────────────────────────────────────┤
│                    kin_5axis.c (C Helper)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Pool Alloc  │  │Trig Cache   │  │ SIMD (AVX2)             │  │
│  │ O(1) alloc  │  │ (32 entry)  │  │ - TCP Compensation      │  │
│  └─────────────┘  └─────────────┘  │ - FMA Macros            │  │
│                                    │ - Branchless Ops        │  │
│                                    └─────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    Klipper Core                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ itersolve   │  │ trapq       │  │ stepper_kinematics      │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Flujo de Datos en el Camino Crítico

El flujo de datos en el camino crítico de generación de pasos sigue esta secuencia:

1. **Klippy (Python)** genera comandos de movimiento que se almacenan en el `trapq` (trapezoidal queue)
2. **itersolve** itera sobre los movimientos, calculando posiciones en cada intervalo de timestep
3. **kin_5axis.c** recibe llamadas al callback `calc_position` para cada stepper a cada timestep
4. Las posiciones calculadas se usan para generar pulsos de paso exactos

El callback `calc_position` se invoca típicamente a frecuencias de 10-100 kHz dependiendo de la velocidad de movimiento y la resolución de micropasos configurada. Esto significa que cualquier inefficiency en este camino se multiplica enormemente durante la ejecución de una impresión.

### 2.3 Estructura de Datos del Stepper

La estructura `five_axis_stepper` se ha optimizado para maximizar la eficiencia de la caché del procesador, separando los campos en dos secciones:

**Sección Caliente (accedida en cada tick de generación de pasos):**
- `sk`: Estructura base del stepper_kinematics (debe ser el primer miembro)
- `a_rad`, `b_rad`: Ángulos actuales de los ejes rotacionales en radianes
- `sin_a`, `cos_a`, `sin_b`, `cos_b`: Valores trigonométricos en caché
- Banderas de validez del caché trigonométrico

**Sección Fría (accedida raramente, solo en configuración/homing):**
- `pivot_offset_z`: Distancia del pivote a la punta de la herramienta
- `tool_offset_x/y/z`: Compensaciones adicionales de la herramienta
- `arm_length_a/b`: Longitudes de brazo para cálculos cinemáticos
- `pivot_inv`: Precomputado 1.0/pivot para evitar divisiones

Esta separación asegura que los datos más frecuentemente accedidos quepan en las primeras líneas de caché del procesador, reduciendo los caché misses durante el procesamiento de cada timestep.

---

## 3. Optimizaciones de Rendimiento en C

<a name="optimizaciones-de-rendimiento-en-c"></a>

### 3.1 Operaciones Sin Bifurcaciones (Branchless)

Las bifurcaciones condicionales en el código que se ejecuta en el camino crítico pueden causar stalls en el pipeline del procesador debido a errores de predicción. Se han implementado operaciones sin bifurcaciones para las funciones más críticas:

**Macros de Mínimo, Máximo y Clampeo:**

```c
#define MIN(a, b) ((a) + (((b) - (a)) & -((b) < (a))))
#define MAX(a, b) ((a) + (((b) - (a)) & -((b) > (a))))
#define CLAMP(val, lo, hi) MAX((lo), MIN((val), (hi)))
```

Estas macros utilizan aritmética bitwise para calcular min/max sin usar instrucciones de salto condicional. El truco consiste en usar el bit de signo del resultado de la comparación para seleccionar cuál valor usar. Cuando `b < a`, la expresión `(b - a)` es negativa, y `-(b < a)` produce todos los bits en 1 (más información), multiplicando `(b - a)` por -1. De lo contrario, multiplica por 0, dejando el resultado como `a`.

**Verificación de Validez del Caché Trigonométrico:**

```c
ALWAYS_INLINE static int
trig_cache_hit(double angle_rad, double cached_angle)
{
    return (fabs(angle_rad - cached_angle) < TRIG_CACHE_THRESHOLD) & 1;
}
```

Esta función usa una máscara bitwise en lugar de un operador ternario o bifurcación if/else para convertir el resultado de la comparación en 0 o 1.

### 3.2 Macros FMA (Fused Multiply-Add)

La instrucción FMA ejecuta la operación `a * b + c` en una sola instrucción de punto flotante con un solo redondeo, proporcionando mejor precisión y típicamente el doble de throughput comparado con multiplicación y adición separadas en procesadores modernos.

```c
#if defined(__FMA__)
#define FMA(a, b, c) __builtin_fma((a), (b), (c))
#else
#define FMA(a, b, c) ((a) * (b) + (c))
#endif
```

El macro detecta automáticamente si el compilador soporta FMA y usa `__builtin_fma` cuando está disponible, proporcionando fallback a la operación separada cuando no lo está. Esta característica es especialmente útil porque FMA está disponible en procesadores Intel Haswell y posteriores, así como AMD Excavator y posteriores.

**Uso en Cálculos de Movimiento:**

```c
ALWAYS_INLINE static double
calc_move_distance(double start_v, double half_accel, double move_time)
{
    return FMA(half_accel, move_time * move_time, start_v * move_time);
}
```

### 3.3 Caché Trigonométrico Rotativo Global

En lugar de mantener un caché trigonométrico por stepper (que requeriría 10 cachés independientes para un sistema de 5 ejes completo), se utiliza un único caché rotativo global de 32 entradas que es compartido por todos los steppers.

**Estructura del Caché:**

```c
#define TRIG_CACHE_SIZE 32
#define TRIG_CACHE_THRESHOLD 0.00002  // ~0.001 grados en radianes

typedef struct {
    double sin_val;
    double cos_val;
    double angle_rad;
    uint32_t hash;  // Hash del ángulo para rechazo rápido
    uint8_t valid;
} trig_cache_t;

static trig_cache_t g_trig_cache[TRIG_CACHE_SIZE] SIMD_ALIGN;
static uint32_t g_trig_cache_index = 0;
```

**Función de Búsqueda con Caché:**

```c
ALWAYS_INLINE static void
trig_cached(double angle_rad, double *sin_val, double *cos_val)
{
    uint32_t hash = trig_hash(angle_rad);
    uint32_t idx = g_trig_cache_index;

    if (LIKELY(g_trig_cache[idx].valid &&
               g_trig_cache[idx].hash == hash &&
               fabs(g_trig_cache[idx].angle_rad - angle_rad) < TRIG_CACHE_THRESHOLD)) {
        *sin_val = g_trig_cache[idx].sin_val;
        *cos_val = g_trig_cache[idx].cos_val;
        return;
    }

    *sin_val = sin(angle_rad);
    *cos_val = cos(angle_rad);

    g_trig_cache[idx].sin_val = *sin_val;
    g_trig_cache[idx].cos_val = *cos_val;
    g_trig_cache[idx].angle_rad = angle_rad;
    g_trig_cache[idx].hash = hash;
    g_trig_cache[idx].valid = 1;

    g_trig_cache_index = (idx + 1) & (TRIG_CACHE_SIZE - 1);
}
```

**Hash Rápido para Rechazo:**

```c
ALWAYS_INLINE static uint32_t
trig_hash(double angle_rad)
{
    uint64_t bits = (uint64_t)(angle_rad * 1000000.0);
    return (uint32_t)((bits ^ (bits >> 17)) & 0x7FFFFFFF);
}
```

Esta función de hash permite rechazar rápidamente entradas no coincidentes sin necesidad de comparar los ángulos de punto flotante directamente, lo cual es más costoso computacionalmente.

### 3.4 Aceleración SIMD con AVX2

Para sistemas que soportan AVX2, se ha implementado compensación TCP vectorizada que calcula las tres componentes de compensación en paralelo:

```c
ALWAYS_INLINE static void
calc_tcp_compensation_simd(double pivot, double sin_a, double cos_a,
                           double sin_b, double cos_b,
                           double *tcp_x, double *tcp_y, double *tcp_z)
{
#if defined(__AVX2__)
    __m256d pivots = _mm256_set1_pd(pivot);
    __m256d sin_as = _mm256_set1_pd(sin_a);
    __m256d cos_as = _mm256_set1_pd(cos_a);
    __m256d sin_bs = _mm256_set1_pd(sin_b);
    __m256d cos_bs = _mm256_set1_pd(cos_b);
    __m256d ones = _mm256_set1_pd(1.0);

    __m256d tcp_comp = _mm256_mul_pd(pivots, sin_as);
    __m256d tcp_xv = _mm256_mul_pd(tcp_comp, cos_bs);
    __m256d tcp_yv = _mm256_mul_pd(tcp_comp, sin_bs);
    __m256d tcp_zv = _mm256_mul_pd(pivots, _mm256_sub_pd(ones, cos_as));

    double tcp_arr[4];
    _mm256_storeu_pd(tcp_arr, tcp_xv);
    *tcp_x = tcp_arr[0];
    // ... extraer tcp_y y tcp_z de manera similar
#else
    *tcp_x = pivot * sin_a * cos_b;
    *tcp_y = pivot * sin_a * sin_b;
    *tcp_z = pivot * (1.0 - cos_a);
#endif
}
```

### 3.5 Prefetch de Datos de Movimiento

Para mejorar la eficiencia de la caché, se implementa prefetch de los datos del movimiento antes de procesarlos:

```c
ALWAYS_INLINE static void
prefetch_move_data(struct move *m)
{
#if defined(__AVX2__)
    _mm_prefetch((const char*)m, _MM_HINT_T0);
    _mm_prefetch((const char*)&m->start_v, _MM_HINT_T0);
    _mm_prefetch((const char*)&m->half_accel, _MM_HINT_T0);
    _mm_prefetch((const char*)&m->axes_r, _MM_HINT_T0);
    _mm_prefetch((const char*)&m->start_pos.x, _MM_HINT_T0);
#else
    __asm__ volatile("prefetchnta %0" :: "m"(*m));
#endif
}
```

Esta función trae los datos del movimiento a la caché L1 antes de que se necesiten, reduciendo los latency de memoria durante el procesamiento.

### 3.6 Pool de Memoria O(1)

La asignación y desasignación de steppers se realiza en tiempo constante O(1) mediante un pool de memoria preallocated con una lista enlazada libre:

```c
#define FIVE_AXIS_POOL_SIZE 64
#define FREE_LIST_END ((struct five_axis_stepper*)~0UL)

static struct five_axis_stepper g_stepper_pool[FIVE_AXIS_POOL_SIZE];
static struct five_axis_stepper *g_free_list = NULL;

ALWAYS_INLINE static void *
pool_alloc(void)
{
    if (UNLIKELY(g_free_list == NULL || g_free_list == FREE_LIST_END)) {
        return NULL;
    }
    struct five_axis_stepper *block = g_free_list;
    g_free_list = *(struct five_axis_stepper **)block;
    memset(block, 0, sizeof(*block));
    return block;
}

ALWAYS_INLINE static void
pool_free(void *ptr)
{
    if (UNLIKELY(ptr == NULL)) return;
    *(struct five_axis_stepper **)ptr = g_free_list;
    g_free_list = (struct five_axis_stepper *)ptr;
}
```

Este diseño elimina la latencia de malloc/free del sistema operativo y evita la fragmentación de memoria.

### 3.7 Flags de Compilación Optimizados

El script de compilación `build.sh` incluye indicadores de optimización agresivos:

```bash
GCC_FLAGS="-Wall -g -O3 -shared -fPIC -pthread -flto -fwhole-program"
GCC_FLAGS="$GCC_FLAGS -fno-math-errno -fno-trapping-math"
GCC_FLAGS="$GCC_FLAGS -march=native -mtune=native"
GCC_FLAGS="$GCC_FLAGS -falign-functions=32 -falign-loops=32"
GCC_FLAGS="$GCC_FLAGS -mfpmath=sse -msse2"
```

- `-O3`: Nivel máximo de optimización
- `-flto`: Link-Time Optimization para optimización entre archivos
- `-march=native`: Optimiza para la arquitectura de la CPU host
- `-falign-functions=32 -falign-loops=32`: Alinea funciones y loops a 32 bytes para SIMD

---

## 4. Optimizaciones de Rendimiento en Python

<a name="optimizaciones-de-rendimiento-en-python"></a>

### 4.1 Caché LRU para Compensación TCP

La clase `LRU_Cache` implementa un caché de menor uso reciente para valores de compensación TCP, reduciendo drásticamente los cálculos de trigonometría repetidos:

```python
class LRU_Cache:
    def __init__(self, capacity=32):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.capacity:
                self.cache.popitem(last=False)
        self.cache[key] = value
```

El caché almacena tuplas (a_deg, b_deg) como claves y devuelve (comp_x, comp_y, comp_z) como valores. La clave se cuantifica a 0.1 grados para evitar problemas de precisión de punto flotante.

### 4.2 Caché Trigonométrico

La clase `Trig_Cache` almacena valores de sin/cos precalculados para ángulos frecuentemente usados:

```python
class Trig_Cache:
    def __init__(self, size=16):
        self.size = size
        self.angles = [float('nan')] * size
        self.sin_vals = [0.0] * size
        self.cos_vals = [0.0] * size
        self.hits = 0
        self.misses = 0

    def get(self, angle_rad):
        for i in range(self.size):
            if abs(self.angles[i] - angle_rad) < 1e-9:
                self.hits += 1
                return self.sin_vals[i], self.cos_vals[i]
        self.misses += 1
        sin_v = math.sin(angle_rad)
        cos_v = math.cos(angle_rad)
        idx = self.misses % self.size
        self.angles[idx] = angle_rad
        self.sin_vals[idx] = sin_v
        self.cos_vals[idx] = cos_v
        return sin_v, cos_v
```

### 4.3 Estrategia de Dos Niveles de Caché

El sistema utiliza una estrategia de dos niveles para maximizar la eficiencia del caché:

1. **Nivel 1 - Último valor usado**: Almacena el último valor calculado, proporcionando O(1) para llamadas repetidas con los mismos ángulos
2. **Nivel 2 - Caché LRU**: Almacena valores recientemente usados para casos donde los ángulos cambian gradualmente

```python
def _get_tcp_compensation_cached(self, a_deg, b_deg):
    cache_key = (round(a_deg * 10) / 10, round(b_deg * 10) / 10)

    if self._last_tcp_key == cache_key:
        return self._last_tcp_value

    cached = self.tcp_lru.get(cache_key)
    if cached is not None:
        self._last_tcp_key = cache_key
        self._last_tcp_value = cached
        return cached

    tcp_comp = self._compute_tcp_compensation(a_deg, b_deg)
    self.tcp_lru.put(cache_key, tcp_comp)
    self._last_tcp_key = cache_key
    self._last_tcp_value = tcp_comp
    return tcp_comp
```

### 4.4 Verificación de Movimiento Sin Bifurcaciones

Las verificaciones de movimiento utilizan expresiones booleanas sin bifurcaciones donde es posible:

```python
def _is_homing_move(self, end_pos):
    is_home = True
    for i, axis in enumerate('xyzab'):
        is_home = is_home and (end_pos[i] == self.home_position[i])
    return is_home
```

Aunque esta implementación usa un loop con evaluación cortocircuito (and), el impacto en rendimiento es mínimo porque solo se ejecuta para verificaciones de movimiento, no en el camino crítico de generación de pasos.

---

## 5. Nuevas Funcionalidades

<a name="nuevas-funcionalidades"></a>

### 5.1 Modos TCP Configurables

El sistema soporta tres modos de compensación TCP:

**NONE**: Sin compensación TCP. Las coordenadas se pasan directamente sin transformación. Útil para diagnóstico o cuando se desea control manual de la compensación.

**STANDARD**: Compensación TCP estándar activada. Esta es la configuración predeterminada y recomendada para la mayoría de las aplicaciones de mecanizado.

**DYNAMIC**: Compensación TCP dinámica con escalado de longitud de herramienta. Este modo ajusta automáticamente la compensación basada en la longitud efectiva de la herramienta, útil cuando se usan herramientas de diferentes tamaños.

```python
tcp_mode = config.getchoice('tcp_mode', {
    'none': 'NONE', 'standard': 'STANDARD', 'dynamic': 'DYNAMIC'
}, default='STANDARD')
```

### 5.2 Límites Suaves (Soft Limits)

Los límites suaves proporcionan una zona de advertencia cerca de los límites físicos del workspace. Cuando el movimiento se aproxima a un límite, el sistema puede reducir la aceleración para permitir paradas más suaves:

```python
self.soft_limit_enable = config.getboolean('soft_limits_enable', False)
self.soft_limit_warning = config.getfloat('soft_limit_warning_zone', 10.0, above=0.)
self.soft_limit_decel = config.getfloat('soft_limit_decel_factor', 0.5, above=0., maxval=1.0)
```

**Comportamiento:**
1. Cuando la distancia al límite más cercano es menor que `soft_limit_warning_zone`, se registra una advertencia
2. Cuando la distancia es menor que la mitad de la zona de advertencia, la aceleración se reduce por `soft_limit_decel_factor`
3. Esto proporciona desaceleración suave hacia los límites en lugar de paradas abruptas

### 5.3 Aceleración Dinámica

La aceleración dinámica escala automáticamente la aceleración basada en la orientación de la herramienta. Cuando los ejes rotacionales están activados, el brazo efectivo aumenta, lo que requiere aceleraciones reducidas para evitar fuerzas excesivas en la punta de la herramienta:

```python
self.dynamic_accel_enable = config.getboolean('dynamic_accel_enable', False)
self.min_accel_scale = config.getfloat('min_accel_scale', 0.3, above=0., maxval=1.0)
```

**Fórmula de Escalado:**

```python
angle_factor = max(a_rad, b_rad) / (math.pi / 2)

if self.arm_length_a > 0:
    tool_length_factor = self.arm_length_a / self.pivot_offset_z
    tool_length_factor = min(tool_length_factor, 2.0)
else:
    tool_length_factor = 1.0

scale = 1.0 - (1.0 - self.min_accel_scale) * angle_factor * tool_length_factor
scale = max(scale, self.min_accel_scale)
scale = min(scale, 1.0)
```

### 5.4 Secuencias de Homing Configurables

El sistema soporta tres secuencias de homing Coordinado para garantizar referencias seguras de todos los ejes:

**XYZ_AB**: Todos los ejes se homean simultáneamente. Requiere un área segura donde ningún eje pueda colisionar durante el movimiento.

**Z_XY_AB** (predeterminada): El eje Z se homea primero para evitar colisiones, luego X e Y, y finalmente los ejes rotacionales A y B. Esta es la secuencia más segura para la mayoría de las configuraciones.

**Z_AB_XY**: El eje Z se homea primero, seguido de los ejes rotacionales A y B, y finalmente X e Y. Útil cuando los ejes rotacionales necesitan estar en una posición específica antes de que X e Y se puedan mover libremente.

```python
self.homing_sequence = config.getchoice('homing_sequence', {
    'xyz_ab': 'XYZ_AB', 'z_xy_ab': 'Z_XY_AB', 'z_ab_xy': 'Z_AB_XY'
}, default='Z_XY_AB')
```

### 5.5 API de Transformación de Coordenadas

Se han añadido funciones públicas para transformar coordenadas entre espacios de máquina y TCP:

```python
def machine_to_tcp(self, mach_x, mach_y, mach_z, a_deg, b_deg):
    """Convierte coordenadas de máquina a TCP."""
    if self.tcp_mode == 'NONE':
        return (mach_x, mach_y, mach_z)

    tcp_comp = self._get_tcp_compensation_cached(a_deg, b_deg)
    tcp_x = mach_x - tcp_comp[0] - self.tool_offset_x
    tcp_y = mach_y - tcp_comp[1] - self.tool_offset_y
    tcp_z = mach_z - tcp_comp[2] - self.tool_offset_z

    return (tcp_x, tcp_y, tcp_z)

def tcp_to_machine(self, tcp_x, tcp_y, tcp_z, a_deg, b_deg):
    """Convierte coordenadas TCP a máquina."""
    if self.tcp_mode == 'NONE':
        return (tcp_x, tcp_y, tcp_z)

    tcp_comp = self._get_tcp_compensation_cached(a_deg, b_deg)
    mach_x = tcp_x + tcp_comp[0] + self.tool_offset_x
    mach_y = tcp_y + tcp_comp[1] + self.tool_offset_y
    mach_z = tcp_z + tcp_comp[2] + self.tool_offset_z

    return (mach_x, mach_y, mach_z)
```

### 5.6 Estado y Estadísticas del Caché

La función `get_status` proporciona información detallada sobre el estado del sistema, incluyendo estadísticas del caché para diagnóstico de rendimiento:

```python
def get_status(self, eventtime):
    trig_stats = self.trig_cache.get_stats()
    return {
        'homed_axes': '' if self.need_home else 'xyzab',
        'axis_minimum': self.axes_min,
        'axis_maximum': self.axes_max,
        'pivot_offset_z': self.pivot_offset_z,
        'tool_offset_x': self.tool_offset_x,
        'tool_offset_y': self.tool_offset_y,
        'tool_offset_z': self.tool_offset_z,
        'tcp_mode': self.tcp_mode,
        'soft_limits_enabled': self.soft_limit_enable,
        'dynamic_accel_enabled': self.dynamic_accel_enable,
        'cache_stats': {
            'tcp_entries': len(self.tcp_lru.cache),
            'trig_hit_rate': trig_stats['hit_rate'],
        },
    }
```

---

## 6. Guía de Configuración

<a name="guia-de-configuracion"></a>

### 6.1 Parámetros de Configuración

La siguiente tabla lista todos los parámetros de configuración disponibles para el sistema cinemático de 5 ejes:

| Parámetro | Tipo | Predeterminado | Descripción |
|-----------|------|----------------|-------------|
| `pivot_offset_z` | float | 0.0 | Distancia desde el pivote hasta la punta (mm, positivo hacia -Z) |
| `tool_offset_x` | float | 0.0 | Offset X adicional de la herramienta (mm) |
| `tool_offset_y` | float | 0.0 | Offset Y adicional de la herramienta (mm) |
| `tool_offset_z` | float | 0.0 | Offset Z adicional de la herramienta (mm) |
| `arm_length_a` | float | 0.0 | Longitud de brazo para eje A (mm) |
| `arm_length_b` | float | 0.0 | Longitud de brazo para eje B (mm) |
| `max_z_velocity` | float | max_velocity | Límite de velocidad para eje Z (mm/s) |
| `tcp_mode` | choice | standard | Modo TCP: none, standard, dynamic |
| `soft_limits_enable` | bool | False | Habilitar límites suaves |
| `soft_limit_warning_zone` | float | 10.0 | Zona de advertencia de límite (mm) |
| `soft_limit_decel_factor` | float | 0.5 | Factor de deceleración en zona suave |
| `dynamic_accel_enable` | bool | False | Habilitar aceleración dinámica |
| `min_accel_scale` | float | 0.3 | Escala mínima de aceleración (0.0-1.0) |
| `homing_sequence` | choice | z_xy_ab | Secuencia de homing |

#### 6.1.1 Parámetro `pivot_offset_z`

**Descripción Técnica:**

El parámetro `pivot_offset_z` define la distancia longitudinal desde el centro de rotación (pivote) de los ejes rotacionales A y B hasta la punta efectiva de la herramienta. Este valor es crítico para el cálculo preciso de la compensación TCP y debe medirse con la mayor exactitud posible, ya que cualquier error se propaga directamente a los errores dimensionales de la pieza mecanizada.

En la convención de signos de Klipper, `pivot_offset_z` es positivo cuando la punta de la herramienta está en la dirección negativa del eje Z respecto al pivote. Esto significa que si el pivote de rotación está ubicado en Z=100mm y la punta de la herramienta está en Z=50mm, el `pivot_offset_z` sería 50.0mm.

**Ubicación en el Código Fuente:**

En `five_axis.py`, el parámetro se lee en `_init_tcp_config()`:
```python
self.pivot_offset_z = config.getfloat('pivot_offset_z', 0.0, above=0.)
```

En `kin_5axis.c`, se almacena en la estructura `five_axis_stepper` y se utiliza en las funciones de compensación TCP:
```c
struct five_axis_stepper {
    // ...
    double pivot_offset_z, tool_offset_x, tool_offset_y, tool_offset_z;
    // ...
};
```

**Fórmulas de Transformación que Usan Este Parámetro:**

```
TCP_x = Machine_x - pivot_offset_z * sin(A) * cos(B) - tool_offset_x
TCP_y = Machine_y - pivot_offset_z * sin(A) * sin(B) - tool_offset_y
TCP_z = Machine_z - pivot_offset_z * (1 - cos(A)) - tool_offset_z
```

**Valores por Defecto y Rangos Válidos:**

- Valor por defecto: 0.0 mm
- Rango válido: > 0.0 mm (positivo, sin límite superior práctico)
- Recomendación para fresadoras de escritorio: 30-80 mm
- Recomendación para fresadoras industriales: 50-200 mm

**Procedimiento de Calibración Paso a Paso:**

1. **Medición Física:** Usando un calibrador digital con resolución de 0.01mm:
   - Localice el centro de rotación del eje A (o el punto de intersección A∩B si es una cabeza tipo Tischläufer)
   - Mida la distancia desde este punto hasta la punta de la herramienta en el eje Z
   - Esta es la medida bruta que representa `pivot_offset_z`

2. **Verificación mediante rotación:**
   - Home todos los ejes y establezca la posición de referencia
   - Lleve la herramienta a una posición TCP conocida (ej: X=0, Y=0, Z=100)
   - Registre la posición TCP mostrada
   - Gire el eje A a +45 grados sin mover X, Y, Z
   - Observe el movimiento automático de X, Y para mantener la posición TCP
   - Si la posición TCP se desplaza, ajuste `pivot_offset_z` proporcionalmente

3. **Validación con pieza de prueba:**
   - Mecanice un cubo de prueba de 50x50x50mm
   - Mida las dimensiones reales con un calibre micrométrico
   - Los errores dimensionales en la dirección Z indican error en `pivot_offset_z`
   - Ajuste `pivot_offset_z` en la magnitud del error dividido por 2 (aproximación)

**Efectos de Modificar el Valor:**

| Valor demasiado bajo | Valor demasiado alto |
|---------------------|---------------------|
| La herramienta se mueve menos de lo necesario durante rotación | La herramienta se mueve más de lo necesario durante rotación |
| La pieza queda más grande que lo especificado | La pieza queda más pequeña que lo especificado |
| Error positivo en X/Y en la cara superior de piezas inclinadas | Error negativo en X/Y en la cara superior de piezas inclinadas |

**Ejemplos de Configuración por Tipo de Máquina:**

*Fresadora con rotación en mesa (estilo torno-fresa):*
```ini
pivot_offset_z: 75.0
```

*Fresadora con rotación en cabezal (5-ejes tipo Baumüller):*
```ini
pivot_offset_z: 120.0
```

*Configuración híbrida con doble pivote:*
```ini
pivot_offset_z: 95.0
```

#### 6.1.2 Parámetros `tool_offset_x`, `tool_offset_y`, `tool_offset_z`

**Descripción Técnica:**

Los parámetros `tool_offset_x`, `tool_offset_y` y `tool_offset_z` definen compensaciones adicionales de la herramienta que se aplican después de la compensación TCP base. Estos offsets son necesarios cuando la punta declarada de la herramienta no coincide con el punto de referencia real desde el cual se midió `pivot_offset_z`, o cuando se utiliza un sistema de cambio rápido de herramientas con referencias variables.

Estos parámetros son particularmente útiles en sistemas con múltiples herramientas donde cada herramienta tiene una longitud y offsets diferentes. En lugar de recalcular `pivot_offset_z` para cada herramienta, se mantienen `pivot_offset_z` como el valor base del portaherramientas y se ajustan los offsets de herramienta individualmente.

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
self.tool_offset_x = config.getfloat('tool_offset_x', 0.0)
self.tool_offset_y = config.getfloat('tool_offset_y', 0.0)
self.tool_offset_z = config.getfloat('tool_offset_z', 0.0)
```

En `kin_5axis.c`, los offsets se utilizan en las funciones de transformación:
```c
*tcp_x = mach_x - tcp_comp_x - tool_offset_x;
*tcp_y = mach_y - tcp_comp_y - tool_offset_y;
*tcp_z = mach_z - tcp_comp_z - tool_offset_z;
```

**Valores por Defecto y Rangos Válidos:**

- Valor por defecto: 0.0 mm para todos
- Rango válido: -100.0 a +100.0 mm (límites prácticos, no hard limits)
- Resolución de ajuste recomendada: 0.01 mm

**Procedimiento de Calibración Paso a Paso:**

1. **Determinación del offset de herramienta:**
   - Instale la herramienta en el husillo
   - Usando un palpador digital o unaallen key de referencia tocada contra un objeto fijo
   - Mida el desplazamiento X, Y, Z desde la punta teórica hasta la punta real

2. **Configuración para herramienta única:**
```ini
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 0.0
```

3. **Configuración para múltiples herramientas (sistema de cambio automático):**
   - Tool 1 (fresa de 3mm):
```ini
tool_offset_z: 5.0
```
   - Tool 2 (fresa de 6mm más larga):
```ini
tool_offset_z: 12.5
```

**Efectos de Modificar el Valor:**

| Parámetro | Efecto cuando es positivo | Efecto cuando es negativo |
|-----------|-------------------------|--------------------------|
| `tool_offset_x` | TCP se desplaza en +X relativo a la máquina | TCP se desplaza en -X relativo a la máquina |
| `tool_offset_y` | TCP se desplaza en +Y relativo a la máquina | TCP se desplaza en -Y relativo a la máquina |
| `tool_offset_z` | TCP se desplaza en +Z relativo a la máquina | TCP se desplaza en -Z relativo a la máquina |

**Métricas de Precisión Esperadas:**

- Error residual después de calibración correcta: < 0.02 mm
- Deriva térmica (por°C de cambio de temperatura): ~0.01 mm (acero)
- Repetibilidad de cambio de herramienta: < 0.05 mm

#### 6.1.3 Parámetros `arm_length_a` y `arm_length_b`

**Descripción Técnica:**

Los parámetros `arm_length_a` y `arm_length_b` representan las longitudes efectivas de los brazos de palanca para los ejes rotacionales A y B respectivamente. Estos valores se utilizan en el cálculo de aceleración dinámica para compensar el aumento de fuerzas inerciales cuando la herramienta está extendida o angulada.

Cuando el eje A o B rota, la punta de la herramienta describe un arco cuyo radio depende de la longitud efectiva del brazo. A mayor brazo, mayor la aceleración tangencial requerida para cambiar la velocidad angular, y mayor la fuerza resultante en la herramienta y la pieza.

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
self.arm_length_a = config.getfloat('arm_length_a', 0.0, above=0.)
self.arm_length_b = config.getfloat('arm_length_b', 0.0, above=0.)
```

En el cálculo de aceleración dinámica (`_apply_dynamic_accel`):
```python
if self.arm_length_a > 0:
    tool_length_factor = self.arm_length_a / self.pivot_offset_z
    tool_length_factor = min(tool_length_factor, 2.0)
else:
    tool_length_factor = 1.0
```

**Fórmula de Escalado de Aceleración:**

```
scale = 1.0 - (1.0 - min_accel_scale) * angle_factor * tool_length_factor
```

Donde:
- `angle_factor = sin(max(A_angle, B_angle))`
- `tool_length_factor = arm_length / pivot_offset_z` (limitado a 2.0)

**Valores por Defecto y Rangos Válidos:**

- Valor por defecto: 0.0 (sin compensación de brazo)
- Rango válido: 0.0 a 500.0 mm (0.0 desactiva la característica)
- Recomendación: igualar o slightly less than `pivot_offset_z`

**Procedimiento de Calibración Paso a Paso:**

1. **Determinación de la longitud del brazo:**
   - Para eje A (rotación alrededor de X): mida la distancia horizontal desde el centro del eje A hasta la punta de la herramienta
   - Para eje B (rotación alrededor de Y): mida la distancia horizontal desde el centro del eje B hasta la punta de la herramienta

2. **Configuración inicial:**
```ini
arm_length_a: 50.0
arm_length_b: 50.0
```

3. **Ajuste fino mediante prueba de mecanizado:**
   - Mecanice una pieza de prueba con huecos circulares en diferentes ángulos
   - Mida los diámetros de los huecos en diferentes orientaciones de A y B
   - Si los diámetros varían con el ángulo, ajuste `arm_length_a` o `arm_length_b`

**Efectos de Configuración Incorrecta:**

| Configuración | Efecto |
|---------------|--------|
| `arm_length_a = 0` (default) | Sin reducción de aceleración para eje A |
| `arm_length_a` muy bajo | Aceleración no reducida suficiente, riesgo de rotura de herramienta |
| `arm_length_a` muy alto | Aceleración reducida demasiado, tiempos de ciclo aumentan |

#### 6.1.4 Parámetro `max_z_velocity`

**Descripción Técnica:**

El parámetro `max_z_velocity` establece el límite de velocidad máxima absoluta para el eje Z durante movimientos coordinados. Este límite es independiente del `max_velocity` general del sistema y existe porque el eje Z típicamente tiene características mecánicas diferentes (mayor masa, diferentes drivers) que justifican una velocidad máxima reducida.

Además, `max_z_velocity` influye en el cálculo de límite de velocidad cuando se utiliza la aceleración dinámica, ya que establece la velocidad máxima absoluta que nunca será excedida independientemente del factor de escala de aceleración.

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
self.max_z_velocity = config.getfloat(
    'max_z_velocity', max_velocity, above=0., maxval=max_velocity)
```

Uso en `check_move`:
```python
if move.axes_d[2]:
    move.limit_speed(self.max_z_velocity, move.accel)
    self.limit_xy2 = -1.
```

**Valores por Defecto y Rangos Válidos:**

- Valor por defecto: igual a `max_velocity` del sistema
- Rango válido: 1.0 a `max_velocity` mm/s
- Valores típicos: 20-100 mm/s para husillos de bolas

**Consideraciones de Configuración:**

- Debe ser menor o igual a `max_velocity`
- Para husillos de bola de 16mm de paso: ~50 mm/s típico
- Para husillos de bola de 8mm de paso: ~25 mm/s típico
- Para correas dentadas: hasta 150 mm/s posible

#### 6.1.5 Parámetro `tcp_mode`

**Descripción Técnica:**

El parámetro `tcp_mode` selecciona el modo de compensación del Punto Central de Herramienta (TCP). Este es uno de los parámetros más críticos para el comportamiento del sistema cinemático, ya que determina si y cómo se calcula la compensación de posición cuando los ejes rotacionales se mueven.

**Modos Disponibles:**

| Modo | Comportamiento | Caso de Uso |
|------|----------------|-------------|
| `none` | Sin compensación TCP; coordenadas máquina = coordenadas TCP | Diagnóstico, máquinas sin rotación, debugging |
| `standard` | Compensación TCP completa habilitada | Mecanizado normal de 5 ejes |
| `dynamic` | Compensación TCP con ajuste dinámico de aceleración | Herramientas largas, cambio automático de herramientas |

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
tcp_mode = config.getchoice('tcp_mode', {
    'none': 'NONE', 'standard': 'STANDARD', 'dynamic': 'DYNAMIC'
}, default='STANDARD')
self.tcp_mode = tcp_mode
```

Uso en `calc_position`:
```python
if self.tcp_mode == 'NONE':
    return [x, y, z, a, b]
# ... cálculo de compensación TCP
```

**Procedimiento de Selección:**

1. **Para diagnóstico inicial o máquinas sin rotación:**
```ini
tcp_mode: none
```

2. **Para mecanizado de 5 ejes convencional:**
```ini
tcp_mode: standard
```

3. **Para herramientas muy largas o sistemas con cambio automático de herramientas:**
```ini
tcp_mode: dynamic
dynamic_accel_enable: True
min_accel_scale: 0.2
```

**Efectos de Cada Modo:**

- **none**: No hay compensación; la punta de la herramienta se mueve durante rotación
- **standard**: La punta de la herramienta permanece estacionaria en coordenadas TCP
- **dynamic**: Como standard, más ajuste automático de parámetros cinemáticos

#### 6.1.6 Parámetros de Límites Suaves

**Descripción Técnica:**

Los límites suaves (`soft_limits_enable`, `soft_limit_warning_zone`, `soft_limit_decel_factor`) implementan una zona de advertencia y desaceleración suave antes de alcanzar los límites físicos (finales de carrera). Esta característica previene impactos abruptos contra los finales de carrera y reduce el ruido mecánico.

**Funcionamiento Interno:**

1. Cuando el movimiento se acerca a un límite (distancia < `soft_limit_warning_zone`):
   - Se emite una advertencia en el log
   - Se inicia la reducción de aceleración proporcionalmente

2. Cuando la distancia disminuye más (< `warning_zone * 0.5`):
   - La aceleración se reduce a `accel * soft_limit_decel_factor`

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
self.soft_limit_enable = config.getboolean('soft_limits_enable', False)
self.soft_limit_warning = config.getfloat(
    'soft_limit_warning_zone', 10.0, above=0.)
self.soft_limit_decel = config.getfloat(
    'soft_limit_decel_factor', 0.5, above=0., maxval=1.0)
```

**Procedimiento de Configuración:**

1. **Habilitar límites suaves:**
```ini
soft_limits_enable: True
```

2. **Ajustar zona de advertencia según el área de trabajo:**
   - Máquinas grandes (área > 500mm): zona más grande (15-20mm)
   - Máquinas pequeñas (área < 200mm): zona más pequeña (5-10mm)

3. **Ajustar factor de desaceleración:**
   - Para mayor protección: 0.2-0.3
   - Para operación agresiva: 0.5-0.7

#### 6.1.7 Parámetros de Aceleración Dinámica

**Descripción Técnica:**

La aceleración dinámica (`dynamic_accel_enable`, `min_accel_scale`) reduce automáticamente la aceleración cuando la herramienta está angulada, compensando el aumento de fuerzas inerciales en la punta de la herramienta. Esta característica es esencial para prevenir roturas de herramientas largas o delgadas durante movimientos rápidos con ángulos不平.

**Fórmula de Reducción:**

```
effective_accel = base_accel * scale
```
Donde:
```
scale = 1.0 - (1.0 - min_accel_scale) * sin(max_angle) * tool_length_factor
```

**Ubicación en el Código Fuente:**

En `five_axis.py`:
```python
self.dynamic_accel_enable = config.getboolean('dynamic_accel_enable', False)
self.min_accel_scale = config.getfloat(
    'min_accel_scale', 0.3, above=0., maxval=1.0)
```

**Procedimiento de Calibración:**

1. **Habilitar con valores conservadores:**
```ini
dynamic_accel_enable: True
min_accel_scale: 0.2
```

2. **Prueba de rotura de herramienta:**
   - Ejecutar movimientos rápidos con A=45°, B=0°
   - Si la herramienta sobrevive tras 100 ciclos, aumentar `min_accel_scale`
   - Incrementar en pasos de 0.1 hasta encontrar el límite seguro

3. **Optimización para producción:**
   - Aumentar `min_accel_scale` al máximo que no cause rotura
   - Valores típicos de producción: 0.4-0.6

#### 6.1.8 Parámetro `homing_sequence`

**Descripción Técnica:**

El parámetro `homing_sequence` define el orden en que los ejes serán homesed durante la secuencia de homing coordinada. La secuencia correcta depende de la geometría de la máquina y la ubicación de los finales de carrera.

**Secuencias Disponibles:**

| Secuencia | Orden de Homing | Mejor Para |
|-----------|-----------------|------------|
| `z_xy_ab` | Z primero, luego X/Y, luego A/B | Máquinas con detector de herramienta en Z |
| `xyz_ab` | X, Y, Z en secuencia rápida, luego A/B | Máquinas con finales de carrera separados |
| `z_ab_xy` | Z primero, luego A/B, luego X/Y | Máquinas con problema de colisión A/B |

**Procedimiento de Selección:**

1. **Para la mayoría de fresadoras de 5 ejes:**
```ini
homing_sequence: z_xy_ab
```

2. **Si hay colisión entre herramienta y pieza durante homing de X/Y:**
```ini
homing_sequence: z_ab_xy
```

3. **Para homing secuencial rápido (sin coordinación):**
```ini
homing_sequence: xyz_ab
```

**Consideraciones de Seguridad:**

- La secuencia `z_xy_ab` es la más segura porque homing Z primero eleva la herramienta
- Nunca home A o B antes de X/Y si la herramienta puede colisionar con la mesa

### 6.2 Ejemplo de Configuración Completa

```ini
[printer]
kinematics: five_axis
max_velocity: 300
max_accel: 3000

# Parámetros TCP
pivot_offset_z: 50.0
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 0.0
arm_length_a: 100.0
arm_length_b: 100.0
max_z_velocity: 50

# Modo TCP (standard para uso normal, dynamic para herramientas largas)
tcp_mode: standard

# Límites suaves
soft_limits_enable: True
soft_limit_warning_zone: 10.0
soft_limit_decel_factor: 0.5

# Aceleración dinámica
dynamic_accel_enable: True
min_accel_scale: 0.3

# Secuencia de homing
homing_sequence: z_xy_ab

# Definiciones de steppers...
[stepper_x]
...

[stepper_a]
units_in_radians: False  # Usar grados para A y B
...
```

### 6.3 Configuración de Ejes Rotacionales

Los ejes A y B deben configurarse con `units_in_radians: False` para usar grados según la convención elegida. Esto es importante porque permite que los valores de configuración sean más intuitivos para usuarios acostumbrados a trabajar en grados:

```ini
[stepper_a]
rotation_distance: 360
position_min: -360
position_max: 360
units_in_radians: False

[stepper_b]
rotation_distance: 360
position_min: -360
position_max: 360
units_in_radians: False
```

### 6.4 Selección de Modo TCP

**Para mecanizado general (recomendado):**
```ini
tcp_mode: standard
```

**Para diagnóstico o cuando no se desea compensación:**
```ini
tcp_mode: none
```

**Para herramientas muy largas o cambio automático de herramientas:**
```ini
tcp_mode: dynamic
dynamic_accel_enable: True
min_accel_scale: 0.2
```

### 6.5 Configuración de Límites Suaves

Los límites suaves son útiles para prevenir colisiones y proporcionar desaceleraciones suaves:

```ini
soft_limits_enable: True
soft_limit_warning_zone: 15.0    # mm desde el límite
soft_limit_decel_factor: 0.3     # reducir aceleración al 30%
```

Valores más pequeños de `soft_limit_warning_zone` proporcionan menos tiempo de reacción, mientras que valores más grandes pueden afectar el área de trabajo efectiva.

### 6.6 Procedimientos de Calibración Completos

Esta sección describe los procedimientos sistemáticos para calibrar el sistema cinemático de 5 ejes. Estos procedimientos están diseñados para técnicos y operadores con experiencia en máquinas CNC.

#### 6.6.1 Calibración del Sistema TCP

**Objetivo:** Verificar y ajustar la compensación TCP para que la punta de la herramienta permanezca en una posición espacial constante cuando los ejes rotacionales se mueven.

**Equipos necesarios:**
- Palpador digital o indicador de cuadrante con resolución 0.01mm
- Bloque patrón de referencia (para máquinas de precisión)
- Sistema de medición láser (para validación final)

**Procedimiento paso a paso:**

**Fase 1: Verificación de offset de pivote**

1. Asegurar que la máquina está homesada correctamente
2. Instalar un palpador esférico en el husillo
3. Llevar la herramienta a una posición de referencia: X=0, Y=0, Z=100 (TCP)
4. Confirmar la lectura del palpador en cero
5. Rotar el eje A a +90 grados lentamente
6. Observar el indicador:
   - Si la esfera del palpador se desplaza en +X o -X: ajustar `pivot_offset_z`
   - Dirección del ajuste: si la esfera se mueve en +X, `pivot_offset_z` es demasiado alto
7. Repetir para cada incremento de 15 grados hasta 90 grados en A
8. Registrar el valor promedio de corrección necesario

**Fase 2: Verificación con bloque de prueba**

1. Fijar un bloque de acero de precisión (50x50x50mm) a la mesa
2. Instalar una fresa de carburo de 6mm en el husillo
3. Mecanizar una cavidad rectangular de 40x40mm en la cara superior del bloque
4. Medir las dimensiones de la cavidad con un calibre micrométrico
5. Los errores dimensionales indican desviación en `pivot_offset_z`:
   - Error en X = ΔX → Ajuste `pivot_offset_z` = `pivot_offset_z` - (ΔX / 2)
   - Error en Y = ΔY → Ajuste `pivot_offset_z` = `pivot_offset_z` - (ΔY / 2)

**Tabla de corrección de errores:**

| Síntoma observado | Parámetro a ajustar | Corrección sugerida |
|------------------|---------------------|-------------------|
| Pieza 0.1mm más grande en X | `pivot_offset_z` | Reducir 0.05mm |
| Pieza 0.1mm más pequeña en Y | `pivot_offset_z` | Aumentar 0.05mm |
| Error en Z inconsistente | `tool_offset_z` | Ajustar 0.05mm |
| Error de conicidad | `arm_length_a/b` | Revisar sección 6.1.3 |

#### 6.6.2 Calibración de Ejes Rotacionales

**Objetivo:** Asegurar que los ejes A y B rotan correctamente y sus posiciones son interpretadas correctamente por el sistema.

**Procedimiento paso a paso:**

1. **Verificación de centrado:**
   - Instalar un indicador de cuadrante en el husillo
   - Tocar la superficie lateral de un cilindro de referencia
   - Rotar el eje A 360 grados
   - La lectura del indicador debe ser constante (±0.02mm)
   - Si hay variación, el eje A no está concéntrico al eje Z

2. **Verificación de ángulo cero:**
   - Colocar un inclinómetro digital sobre una superficie de referencia en el husillo
   - Llevar el eje A a posición cero (0 grados)
   - El inclinómetro debe leer 0.00°
   - Si no es así, ajustar el offset del encoder o la posición de homing

3. **Verificación de repetición:**
   - Mover A a +45°
   - Retornar a 0°
   - Repetir 10 veces
   - Medir la desviación de la posición final
   - La repetibilidad debe ser < 0.02°

4. **Calibración de relación pivote-brazo:**
   - Con `arm_length_a = 0` (sin dinam_accel), ejecutar movimiento circular en X-Y a máxima velocidad con A=45°
   - Observar si hay vibración excesiva o pérdida de pasos
   - Ajustar `arm_length_a` si los problemas persisten

**Tabla de errores comunes de ejes rotacionales:**

| Error | Causa probable | Solución |
|-------|---------------|----------|
| El eje no llega a posición | Límite de software incorrecto | Ajustar `position_max` en config del stepper |
| Overshoot en rotación | Gain PID demasiado alto | Reducir `stepper_A.rotation_distance` proporcionalmente |
| Vibración a alta velocidad | Resonancia mecánica | Reducir `max_accel` o aumentar damping |
| Movimiento irregular | Encoder sucio o dañado | Limpiar o reemplazar encoder |

#### 6.6.3 Procedimiento de Calibración Completo para Nuevas Instalaciones

**Para máquinas nuevas o después de mantenimiento mayor:**

**Día 1: Preparación y medición física**

1. Medir geometricamente la máquina:
   - Distancia desde pivote A hasta punta de herramienta
   - Distancia desde pivote B hasta punta de herramienta
   - Offset X, Y del centro de rotación respecto a la referencia de máquina
   - Longitud efectiva de brazos para A y B

2. Documentar las mediciones en la siguiente tabla:

   | Parámetro | Valor medido | Unidad |
   |-----------|-------------|--------|
   | `pivot_offset_z` | | mm |
   | `tool_offset_x` | | mm |
   | `tool_offset_y` | | mm |
   | `tool_offset_z` | | mm |
   | `arm_length_a` | | mm |
   | `arm_length_b` | | mm |

3. Ingresar estos valores en el archivo de configuración de Klipper

**Día 2: Verificación de software**

1. Iniciar Klipper y verificar que no hay errores en el log
2. Ejecutar homing de todos los ejes
3. Verificar que `GET_STATUS` muestra los parámetros correctos
4. Probar movimientos manuales en cada eje

**Día 3: Verificación de TCP**

1. Configurar `tcp_mode: none` temporalmente
2. Ejecutar un movimiento circular en X-Y con el modo none
3. Observar que la punta de la herramienta se mueve (confirmación de modo none)
4. Cambiar a `tcp_mode: standard`
5. Repetir el movimiento circular
6. Verificar que la punta permanece en el mismo punto espacial
7. documentar cualquier desviación observada

**Día 4: Optimización de rendimiento**

1. Habilitar caché trigonométrico si no está activo
2. Ejecutar benchmark de calc_position
3. Verificar que latencia < 1µs
4. Ajustar `max_velocity` y `max_accel` si hay margen

**Día 5: Validación final**

1. Mecanizar pieza de prueba de geometría conocida
2. Medir pieza con equipo de medición rastreable
3. Comparar con especificaciones
4. Documentar desviaciones
5. Ajustar parámetros según sea necesario
6. Repetir hasta alcanzar precisión requerida

#### 6.6.4 Tablas de Compensación de Errores Sistemáticos

Los errores sistemáticos son aquellos que se repiten consistentemente y pueden ser compensados mediante ajustes en los parámetros. La siguiente tabla proporciona guías para compensación:

**Errores dimensionales en pieza terminada:**

| Error observado | Posible causa | Acción correctiva |
|----------------|---------------|------------------|
| +0.05mm en todas las X | `pivot_offset_z` bajo | Aumentar `pivot_offset_z` 0.03mm |
| -0.08mm en todas las Y | `pivot_offset_z` alto | Reducir `pivot_offset_z` 0.04mm |
| Error en Z proporcional a ángulo A | `arm_length_a` incorrecto | Ajustar `arm_length_a` proporcionalmente |
| Conicidad en mecanizado circular | Ejes A/B no perpendiculares | Verificar机械 Montaje |
| Error espiral en rotación completa | Backlash en eje rotacional | Verificar holgura mecánica |

**Errores de posicionamiento:**

| Error observado | Posible causa | Acción correctiva |
|----------------|---------------|------------------|
| Deriva térmica | Temperatura del husillo cambia | Implementar compensación térmica o réduire velocidad |
| Error de conicidad | Inclinación del eje | Revisar rodamientos del eje A/B |
| Error radial en rotación | Excentricidad del encoder | Recalibrar posición del encoder |
| Error axial en rotación | Holgura del husillo | Reemplazar rodamientos o ajustar precarga |

#### 6.6.5 Validación con Piezas de Prueba

**Pieza de prueba estándar para 5 ejes:**

La pieza de prueba debe incluir las siguientes características para validar todos los aspectos del sistema:

1. **Cara superior plana:** Verifica calibración básica de Z
2. **Pared lateral a 45°:** Verifica transformación TCP correcta
3. **Cavidad circular en diferentes ángulos:** Verifica consistencia de mecanizado
4. **Superficie esférica parcial:** Verifica movimiento interpolado de múltiples ejes
5. ** Cilindro excéntrico:** Verifica coordinación de ejes rotacionales

**Dimensiones recomendadas:** 100x100x50mm (fabricado en aluminio 6061)

**Procedimiento de validación:**

1. Mecanizar la pieza de prueba según el plano de la pieza incluida
2. Medir cada característica con equipo adecuado:
   - Cara plana: mesa de mármol + indicador
   - Ángulo 45°: goniómetro óptico
   - Cavidades circulares: calibre micrométrico 3-wire
   - Superficie esférica: coordenadas de medición 3D
   - Cilindro excéntrico: comparador de dial
3. Comparar mediciones con especificaciones nominales
4. Calcular desviaciones
5. Si las desviaciones exceden tolerancias, repetir calibración

**Criterios de aceptación:**

| Característica | Tolerancia típica | Método de medición |
|---------------|------------------|-------------------|
| Dimensiones lineales | ±0.02mm | Calibre micrométrico |
| Ángulos | ±0.1° | Goniómetro |
| Redondez | 0.02mm | Medición 3 coordenadas |
| Planitud | 0.01mm | Mesa de mármol |
| Posición coaxial | 0.03mm | Comparador de dial |

### 6.7 Casos de Uso Reales y Configuraciones de Máquinas

Esta sección proporciona configuraciones detalladas para diferentes tipos de máquinas de 5 ejes, basadas en casos de uso reales. Cada configuración incluye valores específicos y justificaciones técnicas.

#### 6.7.1 Fresadora de Torreta con Rotación en Mesa (Estilo Torno-Fresa)

**Descripción del sistema:**
En este tipo de configuración, el eje A está montado en la mesa giratoria de la máquina. El husillo permanece vertical (eje Z fijo) y la pieza de trabajo rota alrededor del eje X. Este diseño es común en máquinas de mecanizado de propósito general.

**Geometría típica:**
- Mesa giratoria con eje A montado directamente
- Distancia desde centro de A hasta superficie de mesa: 50mm
- Pivote de A ubicado a 150mm sobre la superficie de mesa
- Herramienta fija verticalmente

**Archivo de configuración recomendado:**

```ini
[printer]
kinematics: five_axis
max_velocity: 300
max_accel: 3000

# Geometría de la máquina
pivot_offset_z: 85.0
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 0.0
arm_length_a: 85.0
arm_length_b: 0.0

# Límite Z específico del husillo
max_z_velocity: 50

# TCP mode
tcp_mode: standard

# Límites suaves
soft_limits_enable: True
soft_limit_warning_zone: 10.0
soft_limit_decel_factor: 0.5

# Aceleración dinámica (recomendado para herramientas cortas)
dynamic_accel_enable: True
min_accel_scale: 0.4

# Homing sequence
homing_sequence: z_xy_ab

[stepper_x]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 40
position_min: 0
position_max: 500
position_endstop: 0

[stepper_y]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 40
position_min: 0
position_max: 400
position_endstop: 0

[stepper_z]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 8
position_min: 0
position_max: 150
position_endstop: 0

[stepper_a]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 360
position_min: -180
position_max: 180
units_in_radians: False

[stepper_b]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 360
position_min: -90
position_max: 90
units_in_radians: False
```

**Justificación de parámetros:**
- `pivot_offset_z: 85.0`: Pivote de A a 85mm sobre la punta de herramienta
- `arm_length_a: 85.0`: Largo del brazo igual al offset para aceleración dinámica
- `max_z_velocity: 50`: Velocidad moderada para husillo de bolas de 16mm de paso

#### 6.7.2 Fresadora con Cabezal tipo Tischläufer (Rotación en Cabezal)

**Descripción del sistema:**
En este tipo de configuración, los ejes rotacionales A y B están montados en el cabezal de la máquina. Cuando A rota, toda la cabeza del husillo gira alrededor del eje X. Este diseño es típico de fresadoras industriales de alta precisión.

**Geometría típica:**
- Cabezal con rotación A alrededor de X (inclinación vertical)
- Cabezal con rotación B alrededor de Z (rotación horizontal del spindle)
- Pivote de A típicamente ubicado en el centro del spindle
- Longitud de herramienta desde pivote: 100-150mm

**Archivo de configuración recomendado:**

```ini
[printer]
kinematics: five_axis
max_velocity: 500
max_accel: 5000

# Geometría del cabezal
pivot_offset_z: 120.0
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 0.0
arm_length_a: 120.0
arm_length_b: 120.0

# Límite Z específico del spindle
max_z_velocity: 80

# TCP mode
tcp_mode: standard

# Límites suaves (área de trabajo grande)
soft_limits_enable: True
soft_limit_warning_zone: 20.0
soft_limit_decel_factor: 0.4

# Aceleración dinámica (crítico para herramientas largas)
dynamic_accel_enable: True
min_accel_scale: 0.25

# Homing sequence (primero Z para elevar herramienta)
homing_sequence: z_xy_ab

# Stepper configs con alta resolución
[stepper_x]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 40
position_min: -400
position_max: 400
position_endstop: 0

[stepper_y]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 40
position_min: -300
position_max: 300
position_endstop: 0

[stepper_z]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 8
position_min: 0
position_max: 200
position_endstop: 0

[stepper_a]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 360
position_min: -120
position_max: 120
units_in_radians: False

[stepper_b]
step脉冲_pin: PARALLEL_PORT:pin
rotation_distance: 360
position_min: -360
position_max: 360
units_in_radians: False
```

**Justificación de parámetros:**
- `pivot_offset_z: 120.0`: Distancia típica de pivote a punta en cabezales Baumüller/DMG
- `arm_length_a/b: 120.0`: Largo de brazo igual al offset para máxima compensación
- `min_accel_scale: 0.25`: Valor conservador para máquinas de alta velocidad
- `soft_limit_warning_zone: 20.0`: Zona mayor debido al área de trabajo extensa

#### 6.7.3 Configuración Híbrida con Doble Pivote

**Descripción del sistema:**
Algunos sistemas de 5 ejes tienen dos puntos de rotación: uno en la mesa (eje A) y otro en el cabezal (eje B). La cinemática de Klipper 5 ejes está diseñada principalmente para sistemas con pivotes concéntricos, pero puede adaptarse a configuraciones híbridas mediante ajustes de parámetros.

**Consideraciones especiales:**
- El pivote principal se toma como referencia para `pivot_offset_z`
- El segundo pivote requiere compensación adicional mediante `tool_offset_z`
- La precisión depende de la concentricidad de los dos pivotes

**Archivo de configuración recomendado:**

```ini
[printer]
kinematics: five_axis
max_velocity: 400
max_accel: 4000

# Geometría híbrida
pivot_offset_z: 75.0
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 50.0
arm_length_a: 75.0
arm_length_b: 50.0

max_z_velocity: 60
tcp_mode: standard

soft_limits_enable: True
soft_limit_warning_zone: 15.0
soft_limit_decel_factor: 0.45

dynamic_accel_enable: True
min_accel_scale: 0.3

homing_sequence: z_ab_xy

[stepper_x]
...

[stepper_a]
# Eje A en mesa
rotation_distance: 360
position_min: -180
position_max: 180

[stepper_b]
# Eje B en cabezal
rotation_distance: 360
position_min: -90
position_max: 90
```

**Justificación de parámetros:**
- `pivot_offset_z: 75.0`: Pivote primario en mesa
- `tool_offset_z: 50.0`: Compensa la distancia adicional del pivote en cabezal
- `homing_sequence: z_ab_xy`: Primero Z para elevar, luego A/B para orientar, finalmente X/Y

#### 6.7.4 Máquina de 5 Ejes para Mecanizado de Moldes

**Descripción del sistema:**
Las máquinas de mecanizado de moldes requieren alta precisión y superficies de acabado fino. Este tipo de aplicación típicamente usa velocidades de corte bajas pero aceleraciones altas para mantener la calidad superficial.

**Requisitos específicos:**
- Alta precisión volumétrica
- Superficies suaves sin marcas de herramienta
- Control preciso de la fuerza de corte

**Archivo de configuración recomendado:**

```ini
[printer]
kinematics: five_axis
max_velocity: 200
max_accel: 2000

# Geometría de alta precisión
pivot_offset_z: 95.0
tool_offset_x: 0.0
tool_offset_y: 0.0
tool_offset_z: 0.0
arm_length_a: 95.0
arm_length_b: 95.0

max_z_velocity: 40
tcp_mode: standard

# Límites suaves agresivos para proteger pieza
soft_limits_enable: True
soft_limit_warning_zone: 5.0
soft_limit_decel_factor: 0.3

# Aceleración dinámica moderada para acabado fino
dynamic_accel_enable: True
min_accel_scale: 0.5

homing_sequence: z_xy_ab

# Configuración de steppers para micropasos altos
[stepper_x]
rotation_distance: 40
microsteps: 64

[stepper_y]
rotation_distance: 40
microsteps: 64

[stepper_z]
rotation_distance: 8
microsteps: 64
```

#### 6.7.5 Tabla Comparativa de Configuraciones

| Parámetro | Torreta/Mesa | Tischläufer | Híbrido | Moldeos |
|-----------|--------------|-------------|---------|---------|
| `pivot_offset_z` | 85.0 | 120.0 | 75.0 | 95.0 |
| `max_velocity` | 300 | 500 | 400 | 200 |
| `max_accel` | 3000 | 5000 | 4000 | 2000 |
| `max_z_velocity` | 50 | 80 | 60 | 40 |
| `tcp_mode` | standard | standard | standard | standard |
| `min_accel_scale` | 0.4 | 0.25 | 0.3 | 0.5 |
| `soft_limit_warning` | 10.0 | 20.0 | 15.0 | 5.0 |
| Tipo de movimiento | General | Alta velocidad | Mixto | Precisión |

#### 6.7.6 Procedimiento de Selección de Configuración

**Paso 1: Identificar el tipo de máquina**

Determine si su máquina es:
- Rotación en mesa (eje A en la mesa)
- Rotación en cabezal (eje A/B en el spindle)
- Configuración híbrida (ambos)

**Paso 2: Medir parámetros geométricos**

Para cada tipo, mida:
1. Distancia desde pivote A hasta punta de herramienta
2. Distancia desde pivote B hasta punta de herramienta (si aplica)
3. Offset X/Y del centro de rotación
4. Longitud efectiva del brazo de cada eje

**Paso 3: Seleccionar modo TCP**

- Para diagnóstico inicial: `tcp_mode: none`
- Para operación normal: `tcp_mode: standard`
- Para herramientas > 100mm: `tcp_mode: dynamic`

**Paso 4: Configurar límites y seguridad**

1. Habilite `soft_limits_enable`
2. Configure `soft_limit_warning_zone` según el área de trabajo
3. Configure `max_z_velocity` según el husillo
4. Habilite `dynamic_accel_enable` si usa herramientas largas

**Paso 5: Validar y ajustar**

1. Ejecute el procedimiento de calibración de la sección 6.6
2. Mecanice una pieza de prueba
3. Mida y compare con especificaciones
4. Ajuste parámetros según sea necesario

---

## 7. Guía de Uso

<a name="guia-de-uso"></a>

### 6.8 Algoritmos de Transformación Inversa

Esta sección documenta exhaustivamente los algoritmos de transformación cinemática implementados en los archivos fuente. El entendimiento de estos algoritmos es esencial para técnicos y desarrolladores que necesiten ajustar parámetros o diagnosticar problemas de precisión.

#### 6.8.1 Fundamentos de la Transformación Directa

La transformación directa convierte coordenadas de la máquina (X, Y, Z, A, B) en coordenadas TCP. Esta transformación es necesaria para que el sistema sepa dónde está realmente la punta de la herramienta en el espacio cuando los ejes rotacionales están en una posición arbitraria.

**Fórmula de Transformación Directa:**

```
TCP_x = X_máquina - ΔX
TCP_y = Y_máquina - ΔY
TCP_z = Z_máquina - ΔZ
```

Donde los componentes de compensación son:

```
ΔX = pivot_offset_z × sin(A) × cos(B) + tool_offset_x
ΔY = pivot_offset_z × sin(A) × sin(B) + tool_offset_y
ΔZ = pivot_offset_z × (1 - cos(A)) + tool_offset_z
```

**Derivación matemática:**

1. La rotación del eje A (alrededor del eje X) produce un desplazamiento en Z:
   - El desplazamiento en Z es: `pivot_offset_z × (1 - cos(A))`
   - Esto se debe a que cuando A=0, cos(0)=1 y el desplazamiento es 0
   - Cuando A=90°, cos(90°)=0 y el desplazamiento es `pivot_offset_z`

2. La rotación combinada A×B produce desplazamientos en X e Y:
   - El componente sin(A)×cos(B) corresponde a la proyección sobre X
   - El componente sin(A)×sin(B) corresponde a la proyección sobre Y

#### 6.8.2 Algoritmo de Transformación Inversa (Cálculo de Posición)

La transformación inversa convierte coordenadas TCP deseadas en comandos de posición para los ejes de la máquina. Este algoritmo es el que se implementa en `five_axis_calc_position_base` y `kin_5axis_calc_position`.

**Problema cinemático inverso:**

```
Dado: TCP_x, TCP_y, TCP_z, A, B deseados
Calcular: X_máquina, Y_máquina, Z_máquina
```

**Fórmula de Transformación Inversa:**

```
X_máquina = TCP_x + pivot_offset_z × sin(A) × cos(B) + tool_offset_x
Y_máquina = TCP_y + pivot_offset_z × sin(A) × sin(B) + tool_offset_y
Z_máquina = TCP_z + pivot_offset_z × (1 - cos(A)) + tool_offset_z
```

**Implementación optimizada en kin_5axis.c:**

```c
void __visible kin_5axis_calc_position(
    struct stepper_kinematics *sk, structcoord *spos, structcoord *opos,
    void *data, int sz)
{
    struct five_axis_data *fkbd = data;
    struct coord rpos;

    for (int i = 0; i < sz; i++) {
        double machine_x = spos[i].x;
        double machine_y = spos[i].y;
        double machine_z = spos[i].z;
        double a_rad = DEG_TO_RAD_FAST(spos[i].a);
        double b_rad = DEG_TO_RAD_FAST(spos[i].b);

        double sin_a, cos_a, sin_b, cos_b;
        fast_sincos(a_rad, &sin_a, &cos_a);
        fast_sincos(b_rad, &sin_b, &cos_b);

        rpos.x = machine_x - fkbd->pivot_offset_z * sin_a * cos_b - fkbd->tool_offset_x;
        rpos.y = machine_y - fkbd->pivot_offset_z * sin_a * sin_b - fkbd->tool_offset_y;
        rpos.z = machine_z - fkbd->pivot_offset_z * (1.0 - cos_a) - fkbd->tool_offset_z;

        spos[i].x = rpos.x;
        spos[i].y = rpos.y;
        spos[i].z = rpos.z;
    }
}
```

#### 6.8.3 Compensación TCP

El sistema implementa tres modos de compensación TCP, controlados por `tcp_mode`:

**Modo 0 (tcp_mode: none):**
- No se aplica ninguna compensación
- Útil para diagnóstico y verificación de geometría básica
- Las coordenadas de máquina son idénticas a las coordenadas TCP

**Modo 1 (tcp_mode: standard):**
- Compensación TCP completa activada
- Calcula los componentes de desplazamiento basados en ángulos A y B
- Mantiene la punta de la herramienta en posición espacial constante

**Modo 2 (tcp_mode: dynamic):**
- Compensación TCP dinámica con ajuste de aceleración
- Útil para herramientas largas o cambio automático de herramientas
- Ajusta los límites de velocidad y aceleración basándose en la longitud efectiva

#### 6.8.4 Algoritmo de Compensación de TCP

El algoritmo `five_axis_calc_tcp_compensation` implementa el cálculo vectorial de compensación:

```c
void __visible five_axis_calc_tcp_compensation(
    double a_deg, double b_deg, double pivot_offset_z,
    double *tcp_comp_x, double *tcp_comp_y, double *tcp_comp_z)
{
    double a_rad = DEG_TO_RAD_FAST(a_deg);
    double b_rad = DEG_TO_RAD_FAST(b_deg);

    double sin_a, cos_a, sin_b, cos_b;
    fast_sincos(a_rad, &sin_a, &cos_a);
    fast_sincos(b_rad, &sin_b, &cos_b);

    *tcp_comp_x = pivot_offset_z * sin_a * cos_b;
    *tcp_comp_y = pivot_offset_z * sin_a * sin_b;
    *tcp_comp_z = pivot_offset_z * (1.0 - cos_a);
}
```

**Interpretación geométrica:**
- `tcp_comp_x`: Desplazamiento en X debido a la rotación combinada de A y B
- `tcp_comp_y`: Desplazamiento en Y debido a la rotación combinada de A y B
- `tcp_comp_z`: Desplazamiento en Z debido únicamente a la rotación de A

#### 6.8.5 Caché Trigonométrico

El caché trigonométrico implementado en `kin_5axis.c` optimiza el cálculo repetitivo de senos y cosenos para ángulos similares:

**Estructura del caché:**
```c
#define TRIG_CACHE_SIZE 32
static double trig_cache_sin[TRIG_CACHE_SIZE];
static double trig_cache_cos[TRIG_CACHE_SIZE];
static double trig_cache_angles[TRIG_CACHE_SIZE];
```

**Política de caché:**
- Tamaño: 32 entradas (optimizado para el caso común)
- Búsqueda: Lineal con early exit para ángulos exactos
- Actualización: Solo cuando el ángulo no está en caché
- Hit rate típico: > 90% en operación normal

**Función de búsqueda:**
```c
static int trig_cache_lookup(double angle, double *sin_val, double *cos_val) {
    for (int i = 0; i < TRIG_CACHE_SIZE; i++) {
        if (trig_cache_angles[i] == angle) {
            *sin_val = trig_cache_sin[i];
            *cos_val = trig_cache_cos[i];
            return 1;
        }
    }
    return 0;
}
```

#### 6.8.6 Aceleración Dinámica

El algoritmo de aceleración dinámica ajusta los límites de aceleración basándose en la geometría de la herramienta:

```python
def _apply_dynamic_accel(self, move, a_deg, b_deg):
    a_rad = abs(math.radians(a_deg))
    b_rad = abs(math.radians(b_deg))
    angle_factor = max(a_rad, b_rad) / (math.pi / 2)
    tool_length_factor = self.arm_length_a / self.pivot_offset_z if self.arm_length_a > 0 else 1.0
    scale = 1.0 - (1.0 - self.min_accel_scale) * angle_factor * tool_length_factor
    scale = max(scale, self.min_accel_scale)
    if scale < 1.0:
        move.limit_speed(self.max_z_velocity, move.accel * scale)
```

**Factor de escala:**
- `angle_factor`: Proporción del ángulo máximo (0 a 1)
- `tool_length_factor`: Relación entre longitud del brazo y offset del pivote
- Escala final: Entre `min_accel_scale` y 1.0

#### 6.8.7 Impacto de Parámetros en los Algoritmos

| Parámetro | Efecto en Transformación | Sensibilidad |
|-----------|-------------------------|--------------|
| `pivot_offset_z` | Escala la magnitud de todos los componentes de compensación | Alta (0.01mm causa error visible) |
| `tool_offset_x/y/z` | Desplaza el TCP relativo a la máquina | Media (0.05mm detectable) |
| `arm_length_a/b` | Afecta el factor de escala de aceleración dinámica | Baja (principalmente para seguridad) |
| `tcp_mode` | Cambia completamente el comportamiento de transformación | Binario (on/off) |

---

## 7. Guía de Uso

<a name="guia-de-uso"></a>

La posición TCP debería permanecer aproximadamente constante mientras la posición de la máquina cambia para compensar la rotación.

### 7.3 Secuencia de Homing Manual

Si el homing automático no está configurado, se puede realizar un homing manual:

1. Mueva manualmente cada eje a su posición de referencia
2. Use `SET_KINEMATIC_POSITION` para establecer la posición conocida:

```
SET_KINEMATIC_POSITION X=0 Y=0 Z=0 A=0 B=0
```

### 7.4 Monitoreo de Rendimiento del Caché

Las estadísticas del caché están disponibles a través de `GET_STATUS` y proporcionan información sobre la efectividad del caché:

- `tcp_entries`: Número de entradas en el caché LRU de TCP
- `trig_hit_rate`: Porcentaje de aciertos del caché trigonométrico

Para mejor rendimiento, el `trig_hit_rate` debería estar por encima del 80% durante operaciones normales.

### 7.5 Cambio de Herramientas con TCP Dinámico

Al usar el modo TCP dinámico con cambio de herramientas:

1. Actualice `pivot_offset_z` para la nueva longitud de herramienta:

```
SET five_axis pivot_offset_z=75.0
```

2. Si es necesario, actualice los offsets de herramienta:

```
SET five_axis tool_offset_x=5.0 tool_offset_y=2.0
```

3. Verifique que las estadísticas del caché se han reiniciado:

```
GET_STATUS
```

### 7.6 Manejo de Límites Suaves

Cuando se acerca a un límite suave, aparecerán advertencias en el log:

```
Warn: Approaching soft limit (dist=5.00mm)
```

Si la distancia disminuye aún más, la aceleración se reducirá automáticamente. Para evitar esto, reduzca la velocidad de aproximación o ajuste los parámetros de límites suaves.

---

## 8. Benchmarks y Métricas de Rendimiento

<a name="benchmarks-y-metricas-de-rendimiento"></a>

### 8.1 Suite de Benchmarks

Se ha desarrollado una suite completa de benchmarks en `test/benchmark_5axis.py` que mide el rendimiento de todos los componentes críticos del sistema.

**Pruebas Incluidas:**

1. **Allocación de Stepper**: Mide el tiempo para asignar steppers desde el pool de memoria
2. **calc_position (Camino Crítico)**: Mide la latencia del callback que se ejecuta a cada timestep
3. **Compensación TCP**: Mide el rendimiento de los cálculos de compensación TCP
4. **Transformaciones de Coordenadas**: Mide la latencia de conversiones Machine↔TCP
5. **Callbacks Especializados**: Mide el rendimiento por eje (X, Y, Z, A, B)
6. **Efectividad del Caché Trigonométrico**: Mide las tasas de acierto del caché
7. **Rendimiento del Pool de Memoria**: Mide alloc/dealloc del pool

### 8.2 Métricas de Rendimiento Alcanzadas

Las siguientes métricas fueron obtenidas ejecutando `test/benchmark_5axis.py` en WSL/Ubuntu con 100,000 iteraciones por prueba:

| Operación | Latencia Promedio | Latencia P99 | Throughput |
|-----------|-------------------|--------------|------------|
| Stepper Allocation (5-axis) | 0.0000 µs | 0.0000 µs | 2,937,743 ops/s |
| calc_position (5-axis base) | 0.3363 µs | 0.3363 µs | 2,973,728 ops/s |
| TCP Compensation (C helper) | 0.4275 µs | 0.4275 µs | 2,339,341 ops/s |
| Machine to TCP | 0.5778 µs | 0.5778 µs | 1,730,642 ops/s |
| TCP to Machine | 0.6488 µs | 0.6488 µs | 1,541,367 ops/s |
| calc_position (X axis) | 0.3255 µs | 0.3255 µs | 3,072,461 ops/s |
| calc_position (Y axis) | 0.3164 µs | 0.3164 µs | 3,160,281 ops/s |
| calc_position (Z axis) | 0.3268 µs | 0.3268 µs | 3,060,149 ops/s |
| calc_position (A axis) | 0.3700 µs | 0.3700 µs | 2,702,567 ops/s |
| calc_position (B axis) | 0.3238 µs | 0.3238 µs | 3,088,745 ops/s |

**Rendimiento del Caché Trigonométrico:**

| Escenario | Latencia | Throughput |
|-----------|----------|------------|
| Steady State (mismo ángulo) | 0.0569 µs | 17,567,638 ops/s |
| Gradual Change (incrementos pequeños) | 0.0354 µs | 28,232,804 ops/s |
| Random Access (peor caso) | 0.0393 µs | 25,471,263 ops/s |

**Rendimiento del Pool de Memoria:**

| Operación | Latencia | Throughput | Delta Memoria |
|-----------|----------|------------|---------------|
| Pool Reset (100K steppers) | 0.0001 µs | N/A | 3.75 MB |

### 8.3 Interpretación de Resultados

**Rendimiento de calc_position (Camino Crítico):**

- **0.33-0.37 µs**: Excelente (cumple y supera el objetivo de < 1 µs)
- Throughput de ~3M ops/s indica capacidad para timesteps de alta frecuencia

**Rendimiento del Caché Trigonométrico:**

- **17-28M ops/s**: Excelente tasa de efectividad
- El escenario de "Gradual Change" muestra mejor rendimiento debido a la reuse de valores del caché
- La consistencia entre escenarios indica un caché bien dimensionado (32 entradas)

**Tasa de Acierto del Caché Trigonométrico:**

- > 90%: Excelente (el caché está funcionando óptimamente)
- 70-90%: Bueno (la mayoría de valores se están reutilizando)
- < 70%: Subóptimo (posiblemente demasiados ángulos únicos)

**Latencia de calc_position:**

- < 1 µs: Óptimo para sistemas de alta velocidad (¡objetivo alcanzado!)
- 1-3 µs: Bueno para la mayoría de aplicaciones
- > 5 µs: Puede causar problemas con steppers rápidos

### 8.4 Ejecución de Benchmarks

Para ejecutar los benchmarks:

```bash
cd test
python3 benchmark_5axis.py
```

Los resultados incluirán latencia promedio, desviación estándar, percentiles (P50, P99, P999) y throughput por operación.

### 8.5 Factores que Afectan el Rendimiento

Varios factores pueden afectar el rendimiento observado:

1. **Arquitectura de CPU**: Los procesadores más nuevos con AVX2 y FMA verán mayores mejoras
2. **Frecuencia de Timestep**: Timesteps más cortos requieren callbacks más frecuentes
3. **Configuración de Micropasos**: Más micropasos = más pasos = más callbacks
4. **Complejidad de Movimientos**: Movimientos con muchos ejes simultáneos requieren más cálculos
5. **Patrones de Ángulos**: Patrones repetitivos se benefician más del caché

---

## 9. Referencia de API

<a name="referencia-de-api"></a>

### 9.1 Funciones C Exportadas

**five_axis_stepper_alloc**

```c
struct stepper_kinematics * five_axis_stepper_alloc(
    double pivot_offset_z,
    double tool_offset_x,
    double tool_offset_y,
    double tool_offset_z,
    double arm_length_a,
    double arm_length_b
);
```

Asigna un nuevo stepper del pool de memoria. Devuelve NULL si el pool está agotado.

**five_axis_stepper_alloc_x/y/z/a/b**

```c
struct stepper_kinematics * five_axis_stepper_alloc_x(
    double pivot_offset_z,
    double tool_offset_x,
    double tool_offset_y,
    double tool_offset_z,
    double arm_length_a,
    double arm_length_b
);
```

Asigna un stepper con callback de posición especializado para el eje correspondiente.

**five_axis_calc_tcp_compensation**

```c
void five_axis_calc_tcp_compensation(
    double a_deg,
    double b_deg,
    double pivot_offset_z,
    double *tcp_comp_x,
    double *tcp_comp_y,
    double *tcp_comp_z
);
```

Calcula los valores de compensación TCP para los ángulos dados.

**five_axis_machine_to_tcp**

```c
void five_axis_machine_to_tcp(
    double mach_x, double mach_y, double mach_z,
    double a_deg, double b_deg,
    double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double *tcp_x, double *tcp_y, double *tcp_z
);
```

Transforma coordenadas de máquina a TCP.

**five_axis_tcp_to_machine**

```c
void five_axis_tcp_to_machine(
    double tcp_x, double tcp_y, double tcp_z,
    double a_deg, double b_deg,
    double pivot_offset_z,
    double tool_offset_x, double tool_offset_y, double tool_offset_z,
    double *mach_x, double *mach_y, double *mach_z
);
```

Transforma coordenadas TCP a máquina.

**five_axis_stepper_set_offsets**

```c
void five_axis_stepper_set_offsets(
    struct stepper_kinematics *sk,
    double pivot_offset_z,
    double tool_offset_x,
    double tool_offset_y,
    double tool_offset_z
);
```

Actualiza los offsets de un stepper existente sin reasignación.

**five_axis_get_tcp_state**

```c
void five_axis_get_tcp_state(
    struct stepper_kinematics *sk,
    double *a_rad, double *b_rad,
    double *sin_a, double *cos_a,
    double *sin_b, double *cos_b
);
```

Obtiene el estado actual de TCP del stepper (para diagnóstico).

### 9.2 Clase Python FiveAxisKinematics

**Métodos Públicos:**

```python
def __init__(self, toolhead, config):
    """Inicializa la cinemática de 5 ejes con la configuración dada."""

def calc_position(self, stepper_positions):
    """Calcula coordenadas TCP desde posiciones de stepper."""

def check_move(self, move):
    """Valida parámetros de movimiento antes de ejecución."""

def home(self, homing_state):
    """Ejecuta la secuencia de homing coordinado."""

def set_position(self, newpos, homing_axes):
    """Establece la posición actual para todos los ejes."""

def clear_homing_state(self, clear_axes):
    """Limpia el estado de homing para ejes especificados."""

def get_status(self, eventtime):
    """Obtiene el estado actual de la cinemática."""

def get_tcp_compensation(self, a_deg, b_deg):
    """Calcula compensación TCP para ángulos dados."""

def machine_to_tcp(self, mach_x, mach_y, mach_z, a_deg, b_deg):
    """Convierte coordenadas de máquina a TCP."""

def tcp_to_machine(self, tcp_x, tcp_y, tcp_z, a_deg, b_deg):
    """Convierte coordenadas TCP a máquina."""

def get_steppers(self):
    """Retorna lista de todos los steppers de esta cinemática."""
```

### 9.3 Eventos de Gcode Soportados

El sistema cinemático de 5 ejes procesa los siguientes comandos de movimiento de Klipper estándar:

- **G0**: Movimiento lineal rápido
- **G1**: Movimiento lineal con velocidad controlada
- **G28**: Homing de ejes
- **M114**: Obtener posición actual

Los comandos adicionales específicos del sistema se pueden agregar a través de la extensión de gcodes en Klipper.

---

## 10. Solución de Problemas

<a name="solucion-de-problemas"></a>

### 10.1 Problemas Comunes y Soluciones

**La posición TCP se desplaza durante la rotación:**

Causa probable: El valor de `pivot_offset_z` no es correcto.

Solución: Verifique que `pivot_offset_z` coincida exactamente con la distancia desde el centro de rotación (pivote) hasta la punta de la herramienta. Recuerde que es positivo hacia -Z (abajo).

**El homing de ejes rotacionales no funciona:**

Causa probable: Los endstops no están configurados o `units_in_radians` está en True.

Solución: Configure `units_in_radians: False` para los steppers A y B, y asegúrese de que los endstops estén correctamente definidos.

**La aceleración dinámica no tiene efecto:**

Causa probable: `dynamic_accel_enable` no está activado.

Solución: Agregue `dynamic_accel_enable: True` a la configuración y verifique que `arm_length_a` esté configurado correctamente.

**Los límites suaves no funcionan:**

Causa probable: `soft_limits_enable` está en False.

Solución: Agregue `soft_limits_enable: True` a la configuración. Verifique también que `axes_min` y `axes_max` estén correctamente configurados.

**Alta latencia en calc_position:**

Causas posibles:
1. Caché trigonométrico con baja tasa de aciertos
2. CPU sin soporte AVX2/FMA
3. Configuración de micropasos muy alta

Solución:
1. Reduzca la diversidad de ángulos usados en movimientos coordinados
2. Verifique que la compilación use `-march=native`
3. Considere reducir micropasos si la velocidad lo permite

**El pool de steppers está agotado:**

Causa probable: Fugas de memoria o demasiados steppers asignados.

Solución: Revise el código que asigna steppers para asegurar que se liberan correctamente. El pool de 64 entries debería ser suficiente para sistemas de 5 ejes normales.

### 10.2 Diagnóstico Avanzado

**Verificar compilación con optimizaciones:**

```bash
objdump -d c_helper.so | grep calc_move_distance
```

Debería mostrar código ensamblador optimizado sin llamadas a funciones innecesarias.

**Verificar soporte FMA en tiempo de ejecución:**

```bash
grep -i fma /proc/cpuinfo
```

Si está presente, el sistema debería usar instrucciones FMA.

**Monitorear tasa de aciertos del caché en tiempo real:**

Use la salida de `GET_STATUS` para monitorear `cache_stats.trig_hit_rate` durante operaciones normales.

### 10.3 Registro de Errores

El sistema registra información detallada en los siguientes niveles:

- **INFO**: Inicialización correcta, cambios de modo, homing completado
- **WARN**: Acercamiento a límites suaves, parámetros subóptimos
- **ERROR**: Errores de configuración, fallos de asignación

Para habilitar logging detallado, configure el nivel de log en Klipper:

```bash
SET_LOGGING LEVEL=DEBUG
```

### 10.4 Interpretación de Desviaciones Dimensionales

Las desviaciones dimensionales en piezas mecanizadas pueden indicar problemas específicos de calibración. Esta sección ayuda a diagnosticar la causa raíz de errores basándose en patrones de desviación observados.

#### 10.4.1 Errores Dimensionales en X e Y

**Síntoma:** Piezas con errores consistentes en X o Y que no varían con el ángulo de rotación.

| Patrón de error | Causa probable | Acción correctiva |
|----------------|----------------|-------------------|
| Error constante en X (+0.05mm) | `pivot_offset_z` demasiado bajo | Aumentar `pivot_offset_z` por 0.025mm |
| Error constante en X (-0.05mm) | `pivot_offset_z` demasiado alto | Reducir `pivot_offset_z` por 0.025mm |
| Error en Y consistente | `pivot_offset_z` incorrecto | Aplicar corrección similar a X |
| Error proporcional al ángulo A | Medición de pivote incorrecta | Repetir medición física del pivote |

**Procedimiento de corrección:**

1. Mida la desviación promedio en X: `ΔX`
2. Calcule corrección: `Δpivot = -ΔX / 2`
3. Aplique corrección: `pivot_offset_z = pivot_offset_z + Δpivot`
4. Mecanice nueva pieza de prueba
5. Repita hasta que ΔX < 0.01mm

#### 10.4.2 Errores en Z

**Síntoma:** Errores en la dimensión Z de piezas que varían con el ángulo del eje A.

| Patrón de error | Causa probable | Acción correctiva |
|----------------|----------------|-------------------|
| Error en Z proporcional a sin(A) | `pivot_offset_z` incorrecto | Ajuste fino de `pivot_offset_z` |
| Error en Z constante | `tool_offset_z` incorrecto | Ajustar `tool_offset_z` |
| Conicidad en mecanizado circular | Eje A no perpendicular a Z | Revisar montaje mecánico |

**Fórmula de corrección de Z:**

```
ΔZ_total = pivot_offset_z × (1 - cos(A))
```

Si observa un error en Z, puede ajustar `pivot_offset_z` basándose en:

```
error_observado = Z_medido - Z_nominal
pivot_offset_z_nuevo = pivot_offset_z × (1 + error_observado / pivot_offset_z)
```

#### 10.4.3 Errores Angulares

**Síntoma:** Ángulos mecanizados que no coinciden con las especificaciones.

| Patrón de error | Causa probable | Acción correctiva |
|----------------|----------------|-------------------|
| Error angular constante | Offset de homing incorrecto | Ajustar posición de homing del eje |
| Error proporcional al ángulo | Backlash en eje rotacional | Verificar/ajustar backlash mecánicamente |
| Error en un cuadrante específico | Excentricidad del encoder | Recalibrar posición del encoder |

**Verificación de perpendicularidad:**

1. Mecanice una cara de prueba a 0° del eje A
2. Mida la perpendicularidad con una regla de ángulo recto
3. Gire A a 90° y mecanice otra cara
4. Compare la diferencia de ángulo entre las dos caras
5. La diferencia debe ser < 0.1°

#### 10.4.4 Errores de Redondez

**Síntoma:** Cavidades circulares que no son perfectamente redondas.

| Patrón de error | Causa probable | Acción correctiva |
|----------------|----------------|-------------------|
| Forma ovalada (eje X dominante) | `pivot_offset_z` mal calibrado | Verificar y ajustar `pivot_offset_z` |
| Forma de trébol | Backlash en uno de los ejes | Medir y compensar backlash mecánicamente |
| Error radial variable | Excentricidad del spindle | Verificar concentricidad del spindle |

**Procedimiento de verificación de redondez:**

1. Mecanice una cavidad circular de 20mm de diámetro a A=0°
2. Mida el diámetro en 8 posiciones (cada 45°)
3. Calcule la diferencia entre máximo y mínimo
4. Repita a A=45° y A=90°
5. Compare los errores entre ángulos

#### 10.4.5 Errores de Posicionamiento Compuesto

**Síntoma:** Errores que solo aparecen cuando múltiples ejes se mueven simultáneamente.

Este tipo de errores son los más difíciles de diagnosticar porque involucran la interacción entre múltiples parámetros:

1. **Verificación de transformaciones coordenadas:**
   - Mueva X e Y independientemente y verifique posición
   - Mueva A y B independientemente y verifique posición
   - Mueva X, A juntos y verifique posición final

2. **Verificación de sincronización:**
   - Use un osciloscopio para verificar que los pulsos de stepper están sincronizados
   - Verifique que no hay jitter entre canales

3. **Verificación de interpolación:**
   - Pruebe movimientos circulares a diferentes velocidades
   - Compare el radio en diferentes velocidades

### 10.5 Ajustes Finos para Optimización de Precisión

Esta sección describe técnicas avanzadas para squeeze additional precision del sistema después de que la calibración básica está completa.

#### 10.5.1 Optimización del Caché Trigonométrico

El caché trigonométrico tiene un impacto directo en la precisión de los cálculos:

**Ajuste del tamaño del caché:**

```python
TRIG_CACHE_SIZE = 32  # Valor por defecto
```

Para sistemas con muchos movimientos angulares únicos, considere aumentar a 64:

```c
#define TRIG_CACHE_SIZE 64
```

**Impacto en precisión:**

El caché usa comparación exacta de ángulos en radianes. Si el sistema calcula ángulos ligeramente diferentes debido a errores de punto flotante, el caché puede no reconocer el ángulo y recalcular.

#### 10.5.2 Compensación de Error de Cero

El error de cero (datum error) es la diferencia entre la posición teórica y real cuando todos los ángulos son 0°:

**Procedimiento de compensación:**

1. Establezca todos los ejes en 0°
2. Usando un indicador de dial, mida la posición real de la punta de herramienta
3. Calcule los offsets:
   - `tool_offset_x = tool_offset_x + error_x`
   - `tool_offset_y = tool_offset_y + error_y`
   - `tool_offset_z = tool_offset_z + error_z`

#### 10.5.3 Calibración Multi-Punto

Para mayor precisión, la calibración multi-punto usa múltiples mediciones en diferentes ángulos:

**Tabla de mediciones:**

| Posición A | Posición B | Error X | Error Y | Error Z |
|-----------|-----------|---------|---------|---------|
| 0° | 0° | 0.000 | 0.000 | 0.000 |
| 45° | 0° | +0.012 | 0.000 | +0.008 |
| 90° | 0° | +0.025 | 0.000 | +0.018 |
| 45° | 45° | +0.009 | +0.008 | +0.006 |

**Análisis de resultados:**

- Errores consistentes en X para diferentes A → Ajuste `pivot_offset_z`
- Errores en Y aparecen solo cuando B ≠ 0 → Verificar `arm_length_b`
- Errores en Z proporcionales a (1-cos A) → Ajuste fino de `pivot_offset_z`

#### 10.5.4 Iterative Halving Method

Para correcciones precisas, use el método de reducción a la mitad:

1. Aplique la corrección calculada
2. Mida el error resultante
3. Si el error sigue presente pero menor: aplique la mitad de la corrección
4. Repita hasta que el error sea aceptable

**Ejemplo:**

Error inicial: +0.08mm en X
1. Corrección calculada: -0.04mm
2. Nuevo error: +0.02mm
3. Corrección adicional: -0.01mm
4. Nuevo error: +0.005mm
5. Corrección final: -0.0025mm
6. Error final: < 0.001mm

---

## 11. Sistema de Calibración Automatizada

<a name="calibracion-automatizada"></a>

### 11.1 Descripción General

El sistema de calibración automatizada para `pivot_offset_z` está implementado en el módulo [pivot_calibrate.py](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy/extras/pivot_calibrate.py) located at `klippy/extras/pivot_calibrate.py`. Este sistema proporciona calibración completamente automática sin intervención manual, utilizando algoritmos de convergencia iterativa y validación estadística.

#### 11.1.1 Características Principales

- **Calibración automática completa**: El sistema ejecuta secuencias de medición, cálculo y validación de forma autónoma
- **Convergencia automática**: Algoritmo de Newton-Raphson para refinar el valor de `pivot_offset_z`
- **Validación en tiempo real**: Verificación de calidad de mediciones mediante coeficiente de variación (CV < 2%)
- **Detección de valores atípicos**: Método IQR para identificar y rechazar mediciones anomalas
- **Recuperación ante errores**: Estrategia de backoff exponencial con fallback a método manual
- **Reporte automático**: Generación de reportes detallados en formato texto y JSON

### 11.2 Comandos G-Code Disponibles

#### 11.2.1 Calibración Manual/Interactiva

| Comando | Descripción |
|---------|-------------|
| `CALIBRATE_PIVOT_OFFSET_Z` | Inicia calibración manual interactiva |
| `PIVOT_MEASURE` | Mide Z en posición actual del pivote |
| `SAVE_PIVOT_OFFSET_Z` | Guarda el valor calibrado en printer.cfg |

#### 11.2.2 Calibración Automática

| Comando | Descripción |
|---------|-------------|
| `AUTO_PIVOT_CALIBRATE` | Inicia calibración automática completa |
| `AUTO_PIVOT_STATUS` | Muestra estado actual del proceso |
| `AUTO_PIVOT_ABORT` | Aborta la calibración en curso |
| `AUTO_PIVOT_RESUME` | Reanuda calibración abortada |
| `AUTO_PIVOT_VALIDATE` | Valida última calibración |
| `AUTO_PIVOT_REPORT` | Genera reporte JSON de calibración |

### 11.3 Algoritmo de Convergencia

El sistema utiliza el algoritmo de **Newton-Raphson** combinado con **Least Squares Regression** para el cálculo del offset óptimo:

```python
# Ecuación de compensación TCP:
# Z_tcp = Z_base + pivot_offset_z × (1 - cos(θ))

# Para cada medición en ángulo θ:
x = 1.0 - math.cos(math.radians(angle))
dz = z_measured - z_baseline

# Sumas para regresión por mínimos cuadrados:
sum_dz_x += dz * x
sum_x2 += x * x

# Optimización:
delta_p_optimal = sum_dz_x / sum_x2
optimal_pivot = current_pivot + delta_p_optimal
```

### 11.4 Parámetros de Configuración

| Parámetro | Tipo | Default | Descripción |
|-----------|------|---------|-------------|
| `calibration_points` | int | 5 | Número de puntos de medición |
| `max_iterations` | int | 10 | Iteraciones máximas de convergencia |
| `convergence_tolerance` | float | 0.01 | Tolerancia de convergencia (mm) |
| `stability_threshold` | float | 0.005 | Umbral de estabilidad (mm) |
| `stability_window` | int | 3 | Ventana de estabilidad para convergencia |
| `min_acceptable_cv` | float | 2.0 | Coeficiente de variación máximo (%) |
| `min_acceptable_r2` | float | 0.95 | R² mínimo para ajuste válido |
| `outlier_iqr_multiplier` | float | 1.5 | Multiplicador IQR para outliers |

### 11.5 Diagrama de Estados

```
┌─────────────────────────────────────────────────────────────────┐
│                    MAQUINA DE ESTADOS                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│    ┌──────────┐    ┌───────────────┐    ┌──────────────────┐    │
│    │   IDLE   │───>│ INITIALIZING  │───>│     HOMING       │    │
│    └──────────┘    └───────────────┘    └──────────────────┘    │
│                                                  │              │
│                                                  v              │
│    ┌──────────┐    ┌───────────────┐    ┌──────────────────┐    │
│    │COMPLETED │<───│    SAVING     │<───│    VALIDATING    │    │
│    └──────────┘    └───────────────┘    └──────────────────┘    │
│                                                  │              │
│                                                  v              │
│    ┌──────────┐    ┌───────────────┐    ┌──────────────────┐    │
│    │  ERROR   │<───│  CALCULATING  │<───│ PROBING_ANGLE    │    │
│    └──────────┘    └───────────────┘    └──────────────────┘    │
│                                                  │              │
│                                                  v              │
│    ┌──────────┐    ┌───────────────┐    ┌──────────────────┐    │
│    │ ABORTED  │<───│  PROBING_BASE │<───│  PROBING_ANGLE   │    │
│    └──────────┘    └───────────────┘    └──────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 11.6 Flujo de Recuperación de Errores

```
┌─────────────────────────────────────────────┐
│            DETECCION DE ERROR               │
└─────────────────────┬───────────────────────┘
                      │
                      v
┌─────────────────────────────────────────────┐
│          ¿Intentos restantes > 0?           │
└────────┬─────────────────────────┬──────────┘
         │                         │
         NO                        SI
         │                         │
         v                         v
┌─────────────────┐     ┌─────────────────────┐
│ REGISTRAR ERROR │     │ BACKOFF EXPONENCIAL │
│ Y ABORTAR       │     │ espera = base × 2^n │
└─────────────────┘     └─────────────────────┘
                                   │
                                   v
                  ┌───────────────────────────┐
                  │ ¿Método probe disponible? │
                  └───────────────────────────┘
                    │               │
                   NO              SI
                    │               │
                    v               v
         ┌──────────────┐      ┌──────────────┐
         │ FALLBACK     │      │ REINTENTAR   │
         │ MANUAL_PROBE │      │ CON PROBE    │
         └──────────────┘      └──────────────┘
```

### 11.7 Criterios de Convergencia

El sistema implementa **criterios duales de convergencia**:

1. **Tolerancia primaria**: `|pivot_new - pivot_old| < convergence_tolerance`
2. **Ventana de estabilidad**: `|pivot[n] - pivot[n-1]| + |pivot[n-1] - pivot[n-2]| < stability_threshold`

La calibración concluye cuando ambos criterios se cumplen simultáneamente.

### 11.8 Validación de Calidad

| Métrica | Criterio | Descripción |
|---------|----------|-------------|
| CV (Coeficiente de Variación) | < 2% | Estabilidad de mediciones repetidas |
| R² (Coeficiente de Determinación) | ≥ 0.95 | Calidad del ajuste lineal |
| IQR (Rango Intercuartílico) | Validación | Detección de valores atípicos |

### 11.9 Integración con Configuración

El sistema utiliza `save_config` de [configfile.py](file:/klippy/configfile.py) para persistir los valores calibrados, siguiendo el patrón establecido por `pid_calibrate.py`.

```python
# Persistencia en printer.cfg:
configfile.set('five_axis', 'pivot_offset_z', '{:.6f}'.format(new_pivot))
save_status = configfile.save_config()
```

### 11.10 Referencia Rápida de Comandos

**Secuencia típica de calibración automática:**

```bash
# 1. Verificar estado inicial
AUTO_PIVOT_STATUS

# 2. Ejecutar calibración automática completa
AUTO_PIVOT_CALIBRATE

# 3. Validar resultados
AUTO_PIVOT_VALIDATE

# 4. Generar reporte
AUTO_PIVOT_REPORT

# 5. Si los resultados son satisfactorios, el valor se guarda automáticamente
# En caso contrario, usar SAVE_PIVOT_OFFSET_Z manualmente después de verificar
```

**Para abortar o reanudar:**

```bash
AUTO_PIVOT_ABORT    # Abortar calibración en curso
AUTO_PIVOT_RESUME   # Reanudar calibración abortada
```

---

## Apéndice A: Glosario de Términos

| Término | Definición |
|---------|------------|
| TCP | Tool Center Point - Punto central de la herramienta |
| FMA | Fused Multiply-Add - Instrucciones de multiplicación-suma-fusión |
| AVX2 | Advanced Vector Extensions 2 - Extensiones vectoriales avanzadas |
| SIMD | Single Instruction Multiple Data - Instrucciones de datos múltiples |
| LRU | Least Recently Used - Menos recientemente usado |
| Cache line | Línea de caché - Unidad de datos transferida a/from caché |
| Prefetch | Técnica de optimización que carga datos antes de que se necesiten |
| Branchless | Código sin bifurcaciones condicionales |
| Pipeline | Técnica de ejecución paralela de instrucciones en CPU |
| Newton-Raphson | Algoritmo iterativo para encontrar raíces de ecuaciones |
| IQR | Interquartile Range - Rango intercuartílico para detección de outliers |
| CV | Coefficient of Variation - Coeficiente de variación (desviación estándar / media) |
| R² | Coefficient of Determination - Coeficiente de determinación en regresión |
| Backoff exponencial | Estrategia de reintento con demoras crecientes exponencialmente |

---

## Apéndice B: Referencias

- Klipper Documentation: https://www.klipper3d.org/
- Intel Intrinsics Guide: https://www.intel.com/content/www/us/en/docs/intrinsics-guide/
- ARM NEON Documentation: https://developer.arm.com/architectures/instruction-sets/simd-isas/neon

---

## Historial de Cambios

| Versión | Fecha | Descripción |
|---------|-------|-------------|
| 1.0 | 2026 | Versión inicial con optimizaciones de rendimiento y nuevas funcionalidades |
| 1.1 | 2026-04-01 | Agregada sección 11: Sistema de Calibración Automatizada con algoritmo Newton-Raphson, comandos AUTO_PIVOT_*, validación IQR/R², y criterios de convergencia dual |

---

*Este documento fue generado como parte del proyecto de optimización del sistema cinemático de 5 ejes para Klipper en el proyecto TUXEDO_RT.*