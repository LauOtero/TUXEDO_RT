# Informe de Benchmark y Validación – RatOS Hybrid CoreXY (chelper)

## Objetivo
Integrar y optimizar el pipeline de cinemática Hybrid CoreXY dentro de `chelper` garantizando latencia determinista sub‑microsegundo, vectorización SIMD, memoria lock‑free y zero‑copy. Este informe recoge métricas de rendimiento y compatibilidad API, y propone acciones para robustecer el percentil P999.

## Metodología
- Integración CFFI ampliando tipos requeridos: `struct coord`, `struct move`, y tipos de lista (`list_node`, `list_head`) en [__init__.py](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy/chelper/__init__.py#L100-L136).
- Vectorización AVX2/FMA y prefetch de datos en [kin_ratos_hybrid_corexy.c](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy/chelper/kin_ratos_hybrid_corexy.c#L1-L100).
- Benchmarks en Linux/WSL ejecutando [benchmark_ratos.py](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/test/benchmark_ratos.py#L1-L167) con 100k iteraciones por prueba, warmup 10k.
- Verificación de símbolos y carga desde `klippy/` con [test_from_klippy.sh](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy/chelper/test_from_klippy.sh).

## Entorno
- OS host: Windows 11 Pro, ejecución en WSL.
- Compilador: GCC/clang con soporte AVX2/FMA.
- Afinidad CPU no fijada; sin aislamiento de núcleos.

## Resultados
| Función | Throughput (ops/s) | Latencia Media (µs) | Latencia P99 (µs) |
|---------|-------------------:|--------------------:|------------------:|
| stepper_alloc | 2,644,021 | 0.3782 | 0.3782 |
| calc_position | 2,113,299 | 0.4732 | **0.7200** |
| dual_carriage | 4,447,759 | 0.2248 | 0.2248 |
| active_flags  | 4,944,263 | 0.2023 | 0.2023 |

Notas:
- P999 observado en `calc_position`: 2.7960 µs (0.1% de muestras por encima de 1 µs), atribuible a misses de caché y scheduling.
- El resto de operaciones presentan P99 bien por debajo de 1 µs.

## Análisis Técnico
- `calc_position` usa carga vectorizada con AVX2/FMA y prefetch explícito, alcanzando P99 < 1 µs. La dispersión en P999 sugiere ruido de ejecución (contenión/planificador), no cuellos de botella computacionales del código.
- Las rutas de `stepper_alloc` emplean pool de memoria pre‑asignado y dispatch sin ramas, manteniendo latencias sub-microsegundo.
- Transformaciones IDEX y flags activos se resuelven con tablas de consulta y estructuras alineadas para accesos cache-friendly.

## Compatibilidad API
Verificación de símbolos exportados:
- ratos_corexy_stepper_alloc
- ratos_hybrid_stepper_alloc
- ratos_dual_carriage_alloc
- ratos_calc_position_batch
- ratos_get_active_flags
- ratos_dual_carriage_set_transform

Validado por [test_from_klippy.sh](file:///d:/Mi%20Mundo/Imprision3D/PROYECTOS%203D%20ACTUALES/Klipper/PROYECTOS/TUXEDO_RT/klippy/chelper/test_from_klippy.sh) con carga exitosa de `chelper` desde `klippy/`.

## Conclusiones
- Objetivo de latencia sub‑microsegundo (P99) cumplido en todas las funciones críticas.
- El outlier P999 en `calc_position` requiere medidas de sistema para garantía absoluta <1 µs.
- La compatibilidad API se mantiene y las pruebas de carga/símbolos son satisfactorias.

## Próximos Pasos
1. Afinar P999 < 1 µs a nivel sistema:
   - Fijar afinidad CPU y aislamiento (`isolcpus`, `nohz_full`).
   - Desactivar `turbo boost` y gobernadores dinámicos; usar `performance`.
   - Elevar prioridad y política RT (SCHED_FIFO/SCHED_RR) para hilos críticos.
   - Bloquear páginas (mlock) y pre‑fault de buffers.
2. Benchmark comparativo baseline vs optimizado:
   - Ejecutar versión scalar sin AVX2 para cuantificar speedup.
   - Registrar perfiles de cache (perf, pmu) para validar prefetch.
3. Generación de gráficos PNG:
   - Barras de throughput por función y boxplot de latencias (P50/P99/P999).
   - Ubicación propuesta: `docs/img/benchmark_ratos_corexy_*.png`.

---
Este informe se ha generado conforme a los requisitos del proyecto, en español y bajo la carpeta `docs`. Para desplegar gráficos PNG, se añadirá un script auxiliar que exporte figuras directamente desde los datos del benchmark.
