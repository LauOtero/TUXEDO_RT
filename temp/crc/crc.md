### 📊 Análisis Profundo de Arquitecturas & Mapeo Hardware

| Familia SBC | CPU Core | Extensión HW CRC | Instrucciones | Notas de Rendimiento |
|-------------|----------|------------------|---------------|----------------------|
| **x86_64 / Intel** | Core / Xeon | SSE4.2 + PCLMUL | `_mm_crc32_u8/u64`, `_mm_clmulepi64_si128` | CRC32 IEEE: 1 ciclo/8B. CRC32C: PCLMUL >15 GB/s |
| **x86_64 / AMD** | Zen 2/3+ | SSE4.2 + PCLMUL | Idéntico | Zen3+ optimiza PCLMUL (CRC32C nativo) |
| **ARMv8-A (Rockchip, BTT CB2, RPi5)** | Cortex-A76/A55 | ARMv8 CRC Extension | `__crc32b/h/w/d` | CRC32 IEEE: 1 ciclo/8B. CRC32C requiere SW/NEON |
| **ARMv8-A (Allwinner, OrangePi, Khadas)** | Cortex-A53/A55 | ARMv8 CRC Extension | `__crc32b/h/w/d` | Disponible en kernels ≥5.10 con `CONFIG_ARCH_HAS_CRC32` |
| **NXP i.MX8 / Coral** | Cortex-A53 | ARMv8 CRC + Crypto Extension | `__crc32*` + `__vcvtaq_*` | Ideal para entornos industriales PREEMPT_RT |

**Nota sobre "1 ciclo de reloj"**: Físicamente, un cálculo CRC de N bytes requiere al menos `⌈N/8⌉` ciclos en hardware moderno. Con vectorización y prefetch, se alcanzan **>20 GB/s** en x86 y **>12 GB/s** en ARMv8, con latencia determinista de `O(1)` por bloque de 64B.

---
### 🛠️ Guía de Compilación & Uso

#### Flags de Compilación por Arquitectura
```bash
# x86_64 (Intel/AMD)
gcc -O3 -march=native -ftree-vectorize -flto -o crc_test main.c crc_utils.c

# ARMv8 (RPi5, Orange Pi 5, BTT CB2)
gcc -O3 -march=armv8-a+crc -mtune=cortex-a76 -ftree-vectorize -flto -o crc_test main.c crc_utils.c

# Tiempo Real Determinista (PREEMPT_RT)
gcc -O3 -march=native -fno-omit-frame-pointer -fno-asynchronous-unwind-tables -static -o crc_test main.c crc_utils.c
```

---
### 📈 Notas Críticas de Rendimiento & Determinismo

1. **Latencia vs Throughput**: Las instrucciones `__crc32d` / `_mm_crc32_u64` procesan **8 bytes en 1 ciclo**. Con prefetch y alineación, se alcanzan ~15-25 GB/s en x86 y ~10-18 GB/s en ARMv8.
2. **PREEMPT_RT**: El kernel RT reduce jitter de interrupciones a <10µs. En user-space, `mlockall()` + `SCHED_FIFO` + buffers pre-faulted garantizan que `crc32_compute` **nunca haga page fault** ni sea preemptado.
3. **Zero-Copy/DMA**: El código espera buffers alineados a 64B. Si usas `io_uring` o `mmap` de dispositivo, asegúrate de que el driver use `DMA_BIDIRECTIONAL` y que la memoria esté en `CMA` o `HUGETLBFS` para evitar TLB misses.
4. **CRC32C**: ARMv8 **no** tiene instrucción nativa para Castagnoli. Se usa tabla optimizada con desenrollado x8. Para PCLMUL en x86, compila con `-mpclmul` y reemplaza `crc32c_sw_fallback` con `_mm_clmulepi64_si128` (implementación disponible bajo demanda).
5. **Determinismo**: No hay ramas dependientes de datos. Todos los loops son de longitud fija. Las tablas están en `const` (se mapean a `RODATA` cacheable). Ideal para control de motores, buses industriales o streaming de sensores.

