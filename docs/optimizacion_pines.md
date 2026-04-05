# Optimización del Sistema de Gestión de Pines (pins.py)

Este documento detalla las mejoras de rendimiento y las nuevas funcionalidades implementadas en el módulo `pins.py` para TUXEDO_RT.

## **Mejoras de Rendimiento**

1. **Caché de Parseo (`_parse_cache`)**:
   - Se ha implementado un mecanismo de caché en `PrinterPins.parse_pin`.
   - Dado que los archivos de configuración suelen referenciar los mismos pines múltiples veces (ej. para step, dir, enable, endstops), el caché evita operaciones redundantes de limpieza de strings, splits y validaciones.
   - Rendimiento: Reduce el tiempo de inicialización en configuraciones complejas con cientos de pines.

2. **Validación de Caracteres Optimizada**:
   - Se reemplazó la creación de listas temporales en la validación de pines (`[c for c in ... if c in pin]`) por una expresión `any(c in pin for c in ...)` más eficiente que se detiene al primer match.
   - Mejora la eficiencia del CPU durante el parseo inicial.

3. **Copia de Parámetros**:
   - El caché retorna una copia de los parámetros para asegurar que modificaciones accidentales en un módulo no afecten a otros que referencien el mismo pin.

## **Nuevas Funcionalidades**

1. **Validación Avanzada de Capacidades**:
   - El método `setup_pin` ahora incluye un "hook" de validación: `chip.validate_pin_type(pin, pin_type)`.
   - Esto permite que las implementaciones de MCU verifiquen si un pin físico realmente soporta la función solicitada (ej. si un pin tiene hardware PWM antes de asignarlo).

2. **Herramientas de Debugging**:
   - Se añadió el método `get_debug_info()`.
   - Retorna un diccionario estructurado con todos los chips registrados, pines activos, sus tipos de uso (share_type) y configuraciones de polaridad/pullup.
   - Útil para herramientas de diagnóstico y validación de conflictos en tiempo de ejecución.

3. **Mejor Rastreo de Metadatos**:
   - `setup_pin` ahora registra automáticamente el tipo de pin en el sistema de compartición (`share_type`), mejorando la precisión de la información de debug y la detección de conflictos.

4. **Robustez en Reset**:
   - `reset_pin_sharing` ahora limpia el caché de parseo para garantizar consistencia si se recarga la configuración de pines dinámicamente.

## **Pruebas de Validación**

Se ha creado un set de pruebas unitarias en `tests/test_pins_optimized.py` que verifica:
- Correcto funcionamiento del caché (identidad vs valor).
- Activación del hook de validación en los chips.
- Generación correcta de información de debug.
- Detección de errores en pines con caracteres inválidos o espacios.
- Gestión de conflictos de compartición de pines.

---
*Documentación generada para TUXEDO_RT - Nivel Ingeniero Senior.*
