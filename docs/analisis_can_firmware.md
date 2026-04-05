# Análisis Exhaustivo de Firmware CAN (Classic, FD, XL)

## 1. Introducción y Arquitectura General
El firmware analizado compone la capa de enlace y transporte para comunicaciones CAN en un entorno embebido (presumiblemente para microcontroladores soportados por Klipper). La implementación destaca por su compatibilidad retroactiva y soporte avanzado para tres generaciones de protocolos CAN, cumpliendo con las normativas ISO pertinentes:
- **CAN 2.0 (Classic):** ISO 11898-1:2003
- **CAN FD (Flexible Data-rate):** ISO 11898-1:2015
- **CAN XL (eXtra Long):** ISO 11898-1:2024

La arquitectura se divide en tres componentes principales:
1. **`canbus.h` / `canbus.c`:** Define las estructuras unificadas de mensajes, abstracción de hardware, límites de longitud (DLC) y la API principal de envío/recepción.
2. **`canbus_autoneg.h` / `canbus_autoneg.c`:** Implementa una máquina de estados finitos (FSM) muy robusta para la autonegociación de la versión del protocolo y la velocidad de bits de datos (BRS), garantizando la coexistencia pacífica en el bus.
3. **`canserial.h` / `canserial.c`:** Implementa un protocolo de puerto serie virtual sobre CAN (Serial-over-CAN) utilizando técnicas de doble búfer (shadow buffering) seguras para IRQ, permitiendo transferencias de gran volumen y baja latencia.

---

## 2. Estructura y Protocolos de Comunicación (`canbus.c` / `canbus.h`)

### 2.1. Trama CAN Unificada
Se define una estructura unificada `canbus_msg` que soporta las tres generaciones:
- **Identificador (ID):** Soporta 11 bits (Standard) y 29 bits (Extended - EFF), aunque CAN XL restringe el uso a 11 bits según la normativa.
- **DLC y Longitudes:**
  - CAN 2.0: Hasta 8 bytes.
  - CAN FD: Hasta 64 bytes (conversión no lineal mediante tablas).
  - CAN XL: Hasta 2048 bytes (fórmula: `DLC = len - 1`).
- **Flags:** Bitmask que indica atributos como `RTR`, `EFF`, `FD`, `BRS` (Bit Rate Switch), `ESI`, y `XL`.
- **Cabecera XL (`canxl_hdr`):** Integrada en la estructura con campos `sdt` (Service Data Unit Type, ej. `CANXL_SDT_ADMIN`), `vcid` (Virtual CAN ID) y `af` (Acceptance Field).

### 2.2. Validación de Tramas y Compatibilidad Descendente
La función `canbus_validate_msg()` adapta la trama saliente al modo de bus activo:
- Si se intenta enviar una trama XL en un bus que ha negociado Classic o FD, se hace *downgrade*: se elimina la cabecera XL, se trunca la carga útil (a 64 u 8 bytes) y se envían los datos posibles.
- Garantiza que el flag BRS no se aplique si el modo activo es `CANBUS_MODE_FD_NO_BRS`.
- Asegura que el padding para CAN XL (alineación de 4 bytes) sea considerado.

---

## 3. Máquina de Estados de Autonegociación (`canbus_autoneg.c`)

La autonegociación se ejecuta de manera "pasiva-primero", mitigando la disrupción de un bus existente.

### 3.1. Fases de Autonegociación
1. **Fase 0: IDLE_WAIT:** Espera a que el bus esté inactivo (11 bits recesivos). Si detecta tráfico, salta a OBSERVING. Si hay silencio, sondea activamente en orden descendente (XL -> FD -> Classic).
2. **Fase 1: OBSERVING:** Escucha pasivamente el bus para clasificar tramas. Si observa una trama XL, escala a `XL_PROBE`. Si ve FD, escala a `FD_PROBE`.
3. **Fase 2, 3 y 4: PROBES (Sondeos):**
   - **XL_PROBE:** Envía una trama XL (DLC=0, 1 byte payload). Si hay éxito (ACK), bloquea en modo XL. Si falla (error de formato en nodos FD o ausencia de nodos XL), degrada a FD.
   - **FD_PROBE:** Sondea tramas FD sin BRS.
   - **BRS_PROBE:** Si el hardware lo soporta y FD tiene éxito, prueba con BRS activo.
4. **Fase 5: LOCKED:** El protocolo está fijado. El sistema monitorea errores para reiniciar la negociación si la degradación de la red lo exige.
5. **Fase 6: BUSOFF_RECOVERY:** Gestión de recuperación tras fallo masivo (Bus-off) respetando la contención de tiempo de ISO 11898-1.

### 3.2. Gestión de Concurrencia (IRQ vs Tareas)
El código sigue un paradigma estricto de seguridad en tiempo real:
- El contexto de interrupción (IRQ) *nunca* muta el estado de la FSM directamente. Se comunica con el contexto de tarea (Task) mediante operaciones atómicas (`AN_FADD_RLX`, `AN_CAS`, `AN_STORE_REL`).
- Las colisiones se evitan asegurando que los contadores de observación y los flags de error (`busoff_pending`, `form_error_pending`) sean atómicos.

---

## 4. Implementación de Capa de Enlace Serial (`canserial.c`)

Klipper envía comandos en formato de texto/binario que deben ser empaquetados en tramas CAN.

### 4.1. Double-Buffer "Shadow" para Transmisión
Se implementa un mecanismo de "Shadow double-buffer" (`struct tx_shadow`):
- Existen dos mitades físicas por nivel de prioridad.
- La tarea de fondo (TX Task) vacía la "mitad activa", mientras que el encolador de mensajes (`console_sendf()`) llena la "mitad inactiva".
- Cuando la mitad activa se vacía, se realiza un intercambio atómico (swap). Esto elimina por completo la necesidad de operaciones lentas como `memmove` dentro del contexto IRQ, asegurando una latencia de transmisión extremadamente baja y determinista.

### 4.2. Construcción Dinámica de Tramas (XL, FD, Classic)
En la función `tx_drain_one()`, los datos en búfer se segmentan según el `max_frame_dlen` negociado:
- **En modo CAN XL:** Se aprovechan los 2048 bytes. Se inyecta la cabecera con `SDT = CANXL_SDT_ADMIN` y `AF = id_asignado`. Se rellena con ceros para alinear a 4 bytes.
- **En modo FD/Classic:** Se envían paquetes de 64 u 8 bytes.

### 4.3. Recepción (RX) y Filtrado
- Se implementa un búfer circular de 4096 bytes para RX.
- Se retiene el puntero de lectura (`parse_pos`) entre invocaciones de la tarea RX, evitando rescaneos redundantes del mismo bloque (mejora PERF-3).
- **Canal de Administración:** Comandos críticos como `QUERY_UNASSIGNED`, `SET_NODEID` y peticiones de Bootloader usan un ID específico (`CANBUS_ID_ADMIN` = 0x3f0) y siempre se envían/reciben como tramas Classic CAN para asegurar que cualquier nodo en el bus pueda procesarlas independientemente de la negociación actual.

---

## 5. Gestión de Errores y Estadísticas

El sistema rastrea minuciosamente los errores definidos en ISO 11898-1 §12.1.4:
- Errores de Stuff, Form, ACK, CRC (y CRC32 para XL) y errores de bit.
- Desbordamientos de FIFO.
- Latencia mínima, máxima y promedio de TX medida en microsegundos usando el temporizador del hardware.
- Si los errores consecutivos superan `AUTONEG_RENEGOTIATE_ERRORS` (16), se fuerza una renegociación del bus, adaptándose dinámicamente si un nodo lento entra a la red repentinamente.

---

## 6. Conclusión
El código analizado presenta una arquitectura de grado industrial (senior engineer level) fuertemente orientada al tiempo real duro. La inclusión de CAN XL y autonegociación dinámica sobre hardware mixto requiere que el software host (Klipper Python) posea implementaciones análogas de desempaquetado, soporte de flags BRS/XL y capacidad de manejar cargas útiles de 2048 bytes, manteniendo el mecanismo determinista.
