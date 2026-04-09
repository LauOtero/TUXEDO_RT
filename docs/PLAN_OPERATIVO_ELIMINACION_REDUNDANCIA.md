# Plan Operativo: Eliminación de Redundancia Python vs C (TUXEDO_RT)

## 1. Resumen y Objetivos Estratégicos

Este plan establece el marco de trabajo para la eliminación gradual de la duplicación de lógica entre los módulos Python (`klippy/`) y el núcleo acelerado en C (`klippy/chelper/`). El objetivo central es reducir el overhead en el hot-path de comunicación MCU-Host, mejorando el rendimiento determinista del sistema TUXEDO_RT.

### 1.1 Objetivos Cuantificables

| Objetivo | Métrica | Valor Actual | Meta | Fecha Objetivo |
|----------|---------|--------------|------|----------------|
| Reducción de líneas duplicadas | Líneas de código redundante | ~310 | -30% (→ ~217 líneas) | 2026-05-31 |
| Mejora en tiempo de compilación | Tiempo de build `chelper` (s) | ~45s | -20% (→ ~36s) | 2026-05-31 |
| Reducción de latencia G-code | µs por comando parseado | ~12µs | -25% (→ ~9µs) | 2026-05-31 |
| Cobertura de tests | % de código cubierto por tests | ~65% | +15% (→ ~80%) | 2026-06-30 |

### 1.2 Alcance de la Migración

**Archivos en scope (Python → C)**:

| Prioridad | Archivo Python | Archivo C Equivalente | Impacto |
|-----------|---------------|----------------------|---------|
| **Alta** | `mcu_stats.py` | `conn_manager.c` | Latencia, overhead |
| **Alta** | `clocksync.py` | `conn_manager.c` | Sincronización, RTT |
| **Media** | `mathutil.py` | `pyhelper.c` | Inlining stats |
| **Baja** | `util.py` | `pyhelper.c` | Utilidades menores |

---

## 2. Roles y Responsabilidades

| Rol | Responsable | Funciones Clave |
|-----|-------------|-----------------|
| **Arquitecto Técnico** | Tech Lead | Supervisión global, decisiones de diseño, revisión de PRs |
| **Ingeniero C/RT** | Dev C | Implementación en `chelper`, optimización de memoria, perfiles |
| **Ingeniero Python** | Dev Python | Refactorización de `gcode.py`, `msgproto.py`, integración CFFI |
| **QA / Tester** | QA Engineer | Diseño y ejecución de benchmarks, tests de regresión |

---

## 3. Cronograma Detallado con Hitos

### Sprint 1: Consolidación RTT (2026-04-07 → 2026-04-30)

**Hito 1.1: Exposición de métricas RTT desde C** (2026-04-14)
- [ ] Implementar `conn_get_rtt_stats()` en `conn_manager.c`
- [ ] Definir `defs_conn_get_rtt_stats` en `__init__.py`
- [ ] Verificar símbolo exportado: `nm c_helper.so | grep conn_get_rtt`
- **Criterio de éxito**: Función accesible desde Python via CFFI

**Hito 1.2: Integración en mcu_stats.py** (2026-04-21)
- [ ] Modificar `mcu_stats.py` para consumir `srtt`, `rttvar`, `rto` desde C
- [ ] Eliminar cálculo duplicado en `_handle_mcu_stats`
- [ ] Ejecutar `tests/benchmark_rtt.py` — validar mejora >15%
- **Criterio de éxito**: Benchmark muestra reducción medible de overhead

**Hito 1.3: Validación y documentación** (2026-04-28)
- [ ] Tests de regresión en WSL/Ubuntu — 0 errores críticos
- [ ] Actualizar `docs/ARQUITECTURA_TUXEDO_RT.md` con nuevos endpoints C
- [ ] Pull Request Merged con revisión de Arch-Tech
- **Criterio de éxito**: PR aprobado, métricas validadas

### Sprint 2: Inlining de Funciones Estadísticas (2026-05-01 → 2026-05-31)

**Hito 2.1: Profileado de mathutil.py** (2026-05-05)
- [ ] Integrar `cProfile` en `mathutil.py` para identificar bottlenecks
- [ ] Ejecutar `tests/benchmark_mathutil.py` — baseline de rendimiento
- [ ] Identificar funciones con >5% de tiempo CPU
- **Criterio de éxito**: Informe de profiling generado

**Hito 2.2: Migración de stats_variance/stddev** (2026-05-12)
- [ ] Implementar `stats_variance` y `stats_stddev` en `pyhelper.c`
- [ ] Modificar `mathutil.py` para delegar a C
- [ ] Benchmark comparativo — validar speedup >2x
- **Criterio de éxito**: Speedup confirmado vía tests

**Hito 2.3: Limpieza y verificación** (2026-05-26)
- [ ] Eliminar código redundante en `mathutil.py`
- [ ] Tests de precisión — confirmar resultados idénticos Python vs C
- [ ] Documentar cambios en `CHANGELOG_TUXEDO_RT.md`
- **Criterio de éxito**: Δlíneas duplicadas = -30% alcanzado

### Sprint 3: Monitoreo Continuo (2026-06-01 → 2026-06-30)

**Hito 3.1: Sistema de alertas** (2026-06-07)
- [ ] Implementar script `scripts/detect_redundancy.py`
- [ ] Integrar en CI/CD (GitHub Actions)
- [ ] Alertar si nuevas líneas duplicadas >10
- **Criterio de éxito**: Alerta automática funcional

**Hito 3.2: Cobertura de tests** (2026-06-21)
- [ ] Aumentar cobertura de `tests/` a >80%
- [ ] Tests de fuzzing para parsing G-code
- [ ] Tests de carga para conexión CAN/UART
- **Criterio de éxito**: Coverage >80% en `coverage report`

**Hito 3.3: Documentación final** (2026-06-28)
- [ ] Actualizar `docs/ARQUITECTURA_TUXEDO_RT.md`
- [ ] Generar `docs/REPORTE_FINAL_ELIMINACION.md`
- [ ] Revisión final de Tech Lead
- **Criterio de éxito**: Proyecto entregado dentro de specs

---

## 4. Análisis de Impacto en Módulos Dependientes

### 4.1 Impacto por Cambio

| Cambio Propuesto | Módulos Dependientes | Tipo de Impacto | Mitigación |
|-----------------|----------------------|-----------------|------------|
| `conn_get_rtt_stats()` | `mcu_stats.py`, `connhdl.py` | API break (aggiunta) | Deprecación suave con fallback |
| Eliminación `stats_variance` Python | `mathutil.py`, tests | Break if called directly | Mantener wrapper con delegazione C |
| Modificación `_handle_clock` | `clocksync.py`, `mcu.py` | Lógica compartida | Test de regresión obligatorio |

### 4.2 Matriz de Dependencias

```
conn_manager.c
    ├── conn_get_rtt_stats() [NUEVO]
    ├── update_receive_seq()
    └── srtt, rttvar, rto [NUEVO]
            │
            ▼
mcu_stats.py (consume métricas)
    │
    ▼
clocksync.py (calcula RTT independientemente — NO ELIMINAR)
```

---

## 5. Estrategia de Refactorización Segura

### 5.1 Principios de Branching

1. **Branch por feature**: Cada tarea tiene su propio branch (`feature/rtt-stats`, `feature/mathutil-inline`)
2. **Commit atómico**: Un commit = una tarea completada
3. **Code review obligatorio**: Mínimo 1 approve antes de merge
4. **Tag de versión**: `v1.x.y-redundancy` para releases de pruebas

### 5.2 Protocolo de Rollback

| Escenario | Acción |
|-----------|--------|
| Benchmark degrada >10% | Revertir branch, investigar causa |
| Test de regresión falla | Bloquear merge, debug immediate |
| Error en producción | `git revert <commit>`, hotfix branch |

### 5.3 Control de Versiones

```bash
# Estructura de branches
main                 # Producción estable
├── develop          # Integración continua
│   ├── feature/rtt-stats           # Sprint 1
│   ├── feature/mathutil-inline     # Sprint 2
│   └── feature/monitoring-system   # Sprint 3
└── hotfix/*       # Correcciones urgentes
```

---

## 6. Pruebas de Regresión Automatizadas

### 6.1 Suite de Tests

| Test | Ubicación | Frecuencia | Criterio de Éxito |
|------|-----------|------------|-------------------|
| Unit tests G-code | `tests/test_gcode.py` | Cada commit | 100% pass |
| Benchmark parsing | `tests/benchmark_redundancy.py` | Daily | Latencia < 15µs |
| Benchmark RTT | `tests/benchmark_rtt.py` | Weekly | Δrtt < 1ns |
| Cobertura | `coverage report` | Weekly | >80% |
| Linting | `ruff check klippy/` | Cada commit | 0 errors |

### 6.2 Pipeline CI/CD

```yaml
# .github/workflows/redundancy_audit.yml
name: Redundancy Audit
on: [push, pull_request]
jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run benchmarks
        run: python3 tests/benchmark_redundancy.py
      - name: Compare results
        run: python3 scripts/compare_benchmarks.py
      - name: Check duplication
        run: python3 scripts/detect_redundancy.py
```

---

## 7. Documentación de Arquitectura

### 7.1 Estructura de Documentos

| Documento | Ubicación | Descripción |
|-----------|-----------|-------------|
| Arquitectura principal | `docs/ARQUITECTURA_TUXEDO_RT.md` | Vista general del sistema |
| Redundancia | `docs/REPORTE_REDUNDANCIA_TUXEDO_RT.md` | Hallazgos de auditoría |
| Este plan | `docs/PLAN_OPERATIVO_ELIMINACION.md` | Este documento |
| Changelog | `docs/CHANGELOG_TUXEDO_RT.md` | Historial de cambios |

### 7.2 Actualización de Documentación

- **Antes de cada merge**: Actualizar `docs/ARQUITECTURA_TUXEDO_RT.md` si hay cambios en APIs
- **Al completar Sprint**: Generar minireporte en `docs/SPRINT_X_REPORT.md`
- **Al finalizar proyecto**: `docs/REPORTE_FINAL_ELIMINACION.md` con métricas finales

---

## 8. Sistema de Monitoreo Continuo

### 8.1 Métricas a Monitorear

| Métrica | Herramienta | Alerta si... |
|---------|-------------|--------------|
| Líneas duplicadas | `scripts/detect_redundancy.py` | >10 nuevas líneas/mes |
| Tiempo de compilación | `time make -C klippy/chelper` | >50s promedio |
| Latencia G-code | `tests/benchmark_redundancy.py` | >15µs/comando |
| Cobertura de tests | `coverage report` | <75% |

### 8.2 scripts/detect_redundancy.py

```python
#!/usr/bin/env python3
"""
Detecta nueva redundancia entre Python y C.
Ejecutar: python3 scripts/detect_redundancy.py
"""
import subprocess
import re

# Buscar funciones duplicadas en Python
py_funcs = subprocess.run(
    ["grep", "-rn", "def.*srtt\\|def.*rttvar\\|def.*rto", "klippy/"],
    capture_output=True, text=True
).stdout

# Buscar equivalentes en C
c_funcs = subprocess.run(
    ["grep", "-rn", "srtt\\|rttvar\\|rto", "klippy/chelper/"],
    capture_output=True, text=True
).stdout

# Reportar duplicados
duplicates = set(py_funcs.split()) & set(c_funcs.split())
if duplicates:
    print(f"⚠️ Posibles duplicados detectados: {duplicates}")
    exit(1)
else:
    print("✅ Sin duplicados detectados")
    exit(0)
```

---

## 9. Reportes de Progreso

### 9.1 Formato de Reporte Semanal

```markdown
## Reporte Semanal — Semana X (YYYY-MM-DD)

### Resumen Ejecutivo
Breve descripción del progreso esta semana.

### Métricas
| Métrica | Anterior | Actual | Δ |
|---------|----------|--------|---|
| Líneas duplicadas | X | Y | -Z% |
| Tiempo compilación | Xs | Ys | -Z% |
| Latencia G-code | Xµs | Yµs | -Z% |

### Hitos Completados
- [ ] Hito 1.1: ...
- [ ] Hito 1.2: ...

### Bloqueos
- Ninguno / Descripción de blockers

### Próxima Semana
- Plan para Sprint siguiente

### Riesgos Identificados
- Ninguno / Descripción de riesgos

---
*Reportado por: [Nombre] — [Fecha]*
```

### 9.2 Canal de Comunicación

- **Daily standup**: 15 min, Discord/Slack #tuxedo-rt-dev
- **Reporte semanal**: Every Friday, 18:00 UTC, en #tuxedo-rt-reports
- **Reviews técnicos**: PR reviews en GitHub, respuesta <24h

---

## 10. Checklist de Entrega Final

Al completar el plan, verificar:

- [ ] ~30% reducción en líneas duplicadas (≥217 líneas)
- [ ] ~20% mejora en tiempo de compilación (≤36s)
- [ ] ~25% mejora en latencia G-code (≤9µs)
- [ ] Cobertura de tests ≥80%
- [ ] 0 errores en CI/CD pipeline
- [ ] Documentación actualizada
- [ ] Code review aprobado por Tech Lead
- [ ] Pull Request merged a `develop`

---

*Plan generado por el Asistente Técnico TUXEDO_RT.*
*Versión: 1.0.0*
*Fecha: 2026-04-07*
