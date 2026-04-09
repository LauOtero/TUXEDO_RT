# Marco Operativo: Plan de Acción y Mitigación de Redundancia (TUXEDO_RT)

## 1. Resumen Ejecutivo
Este documento establece el marco operativo para la ejecución del **Plan de Acción de Redundancia** en el proyecto TUXEDO_RT. El propósito principal es consolidar la arquitectura de tiempo real eliminando la duplicidad de lógica entre los módulos de Python y el núcleo acelerado en C (`chelper`), garantizando un rendimiento determinista, menor latencia y un uso optimizado de la memoria.

## 2. Objetivos Específicos y Medibles
*   **Reducción de Latencia (O1)**: Disminuir el tiempo de despacho de comandos G-code en al menos un 40% delegando el parsing crítico exclusivamente a `gcode_parser.c`.
*   **Eficiencia de Memoria (O2)**: Reducir el consumo de memoria en el procesamiento de colas en un 12% mediante la eliminación de buffers intermedios en Python y el uso intensivo de `__slots__`.
*   **Consolidación de Protocolo (O3)**: Alcanzar un 100% de uso del motor C para la codificación VLQ y validación CRC16, eliminando los fallbacks en `msgproto.py`.
*   **Estabilidad RT (O4)**: Mantener 0 regresiones funcionales en la comunicación MCU-Host durante el proceso de refactorización.

## 3. Roles y Responsabilidades
| Rol | Responsabilidades Clave | Asignación |
| :--- | :--- | :--- |
| **Líder Técnico / Arquitecto** | Supervisión global de la arquitectura, revisión de PRs, validación de diseño C/Python. | *Tech Lead* |
| **Ingeniero RT (C/C++)** | Mantenimiento de `chelper`, optimización de memoria compartida, despacho SIMD. | *Dev RT* |
| **Ingeniero Backend (Python)** | Refactorización de `gcode.py`, `msgproto.py`, `clocksync.py`. Integración CFFI. | *Dev Python* |
| **QA / Tester** | Diseño y ejecución de benchmarks de rendimiento, pruebas de integración en WSL/Ubuntu. | *QA Engineer* |

## 4. Cronograma e Hitos (Timeline)
El plan se ejecutará en 4 fases secuenciales (Sprints de 1 semana):

*   **Hito 1: Refactorización G-code (Semana 1)**
    *   *Entregables*: `gcode.py` optimizado, eliminación de `_process_commands_legacy` en el hot-path.
    *   *Criterio de Éxito*: Benchmark de G-code muestra reducción de latencia > 30%.
*   **Hito 2: Consolidación de Protocolo (Semana 2)**
    *   *Entregables*: `msgproto.py` forzando codificación en C, `ultracrc.c` validado.
    *   *Criterio de Éxito*: Eliminación de fallbacks en Python; pruebas unitarias en verde.
*   **Hito 3: Sincronización y Estadísticas (Semana 3)**
    *   *Entregables*: `clocksync.py` y `mcu_stats.py` consumiendo métricas directas de `conn_manager.c`.
    *   *Criterio de Éxito*: Precisión de RTT mejorada a resolución de nanosegundos.
*   **Hito 4: Limpieza y Documentación (Semana 4)**
    *   *Entregables*: Reportes finales, limpieza de `mathutil.py`, código estabilizado.

## 5. Recursos Necesarios
*   **Tecnológicos**: Entorno Linux/WSL (Ubuntu) para compilación GCC, herramientas de profiling (`perf`, `valgrind`), simuladores de MCU.
*   **Humanos**: 2 Ingenieros Senior (C/Python), 1 QA.
*   **Financieros/Infra**: Instancias de CI/CD (GitHub Actions) con runners Linux para pruebas automatizadas de rendimiento.

## 6. Indicadores de Seguimiento y Evaluación (KPIs)
1.  **Latencia Media de Parsing (ms)**: Medida antes y después de los cambios en `gcode.py`.
2.  **Sobrecarga de CPU (%)**: Medición del proceso Python principal durante ráfagas de 100,000 comandos.
3.  **Tasa de Fallbacks a Python (eventos/seg)**: Debe llegar a 0 para operaciones VLQ y CRC.
4.  **Cobertura de Tests (%)**: Mantenida o incrementada en el directorio `tests/`.

## 7. Mecanismos de Comunicación y Reporte
*   **Reuniones de Sincronización**: Dailies de 15 minutos para blockers.
*   **Reportes de Progreso**: Documentos Markdown generados al final de cada Hito, alojados en `docs/`.
*   **Alertas**: Notificaciones automatizadas vía CI/CD ante regresiones de rendimiento.

## 8. Gestión de Riesgos y Contingencias
| Riesgo | Probabilidad | Impacto | Estrategia de Mitigación | Contingencia |
| :--- | :--- | :--- | :--- | :--- |
| **Fallo en compilación de `chelper` en nuevos entornos** | Media | Alto | Mantener flags de compilación condicionales y dependencias limpias. | Fallback temporal a implementación escalar en C (sin SIMD). |
| **Regresión de rendimiento por sobrecarga de CFFI** | Baja | Crítico | Benchmarks estrictos por cada commit. Minimizar cruces de frontera C/Python. | Agrupar llamadas a C (Batching) para amortizar el costo de CFFI. |
| **Desincronización de relojes MCU por cambios en stats** | Media | Alto | Pruebas de simulación con alta latencia inyectada. | Revertir cambios en `clocksync.py` independientemente de otras fases. |

## 9. Procesos de Revisión y Ajuste Continuo
1.  **Pre-condición**: Según la regla del proyecto, **toda optimización requiere un test de rendimiento antes y después**.
2.  **Validación**: Ejecución en WSL/Ubuntu obligatoria.
3.  **Aprobación**: Los Pull Requests deben evidenciar las métricas del benchmark para ser integrados.

---
*Documento generado y mantenido por el Asistente Técnico TUXEDO_RT.*
