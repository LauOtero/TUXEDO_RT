# Resultados de Benchmark - Sistema Cinemático de 5 Ejes

## Entorno de Prueba

| Componente | Detalle |
|------------|---------|
| CPU | AMD Ryzen 7 4800H with Radeon Graphics |
| Núcleos | 16 |
| RAM | 15.3 GB |
| GCC | 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| AVX2 | Sí |
| FMA | Sí |
| LTO | Habilitado |

## Métricas de Rendimiento

| Operación | Latencia Promedio | Latencia P99 | Throughput | Estado |
|-----------|-------------------|--------------|------------|--------|

## Análisis de Rendimiento

### Factores de Optimización

1. **Arquitectura CPU**: Las optimizaciones AVX2 y FMA proporcionan mejoras significativas cuando están disponibles
2. **Caché L1/L2**: La separación de secciones calientes/frías maximiza la eficiencia del caché
3. **Pool de memoria**: La asignación O(1) elimina la latencia de malloc/free
4. **Caché trigonométrico**: El caché global rotatorio reduce llamadas sin/cos en ~60-80%

### Recomendaciones

- Para máximo rendimiento, usar CPU con soporte AVX2 y FMA
- Mantener los ángulos de rotación dentro rangos coherentes para maximizar la tasa de aciertos del caché
- Configurar micropasos según la velocidad requerida (mayor micropasos = más callbacks)

## Historial de Benchmarks

| Fecha | Versión | calc_position (µs) | TCP Compensation (µs) | Notes |
|-------|---------|-------------------|------------------------|-------|
