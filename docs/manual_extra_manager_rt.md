# Manual Completo y Detallado de ExtraManager (TUXEDO_RT)

El `ExtraManager` de TUXEDO_RT proporciona una API declarativa, robusta y type-safe para crear extras/plugins integrados con el núcleo de tiempo real (`rtcore`). Este framework oculta la complejidad del manejo de hilos, limpieza de recursos y compatibilidad hacia atrás, permitiendo a los desarrolladores enfocarse únicamente en la lógica de negocio. Este manual cubre exhaustivamente todas las clases, métodos, propiedades y patrones de uso.

## Hello-World Plugin en 5 Líneas

Crea un archivo `mi_extra.py` en `klippy/extras/`:

```python
from extra_manager import ExtraInterface

class MiExtra(ExtraInterface):
    def on_start(self):
        self.register_rt_task("mi_tarea", lambda: print("¡Hola RT!"), priority=60)

# Exportar como Extra
Extra = MiExtra
```

**Explicación:**
1. Importas `ExtraInterface` desde `extra_manager`.
2. Defines tu clase heredando de `ExtraInterface`.
3. Usas el hook `on_start()` para registrar una tarea RT.
4. Exportas la clase como `Extra` para que el `ExtraManager` la detecte automáticamente.

## Arquitectura General

### ExtraLifecycle

La clase base `ExtraLifecycle` define los hooks del ciclo de vida que todos los plugins deben implementar. Estos hooks permiten una inicialización ordenada y gestión de recursos sin intervención manual del desarrollador.

#### Hooks del Ciclo de Vida Detallados

##### `on_load() -> None`
- **Cuándo se llama:** Inmediatamente después de la instanciación del plugin, durante la carga inicial.
- **Propósito:** Configuración inicial, lectura de parámetros de `printer.cfg`, inicialización de estados internos.
- **Notas:** Ideal para operaciones que no requieren otros objetos del sistema. No acceder a objetos como `toolhead` aquí, ya que pueden no estar disponibles.
- **Ejemplo detallado:**
  ```python
  def on_load(self) -> None:
      # Leer parámetros de configuración
      self.max_speed = self.config.getfloat('max_speed', 100.0, above=0.)
      self.enable_logging = self.config.getboolean('enable_logging', True)
      # Inicializar estructuras de datos
      self.data_buffer = []
      self.is_initialized = False
      self.logger.info(f"Plugin cargado: max_speed={self.max_speed}, logging={self.enable_logging}")
  ```

##### `on_init() -> None`
- **Cuándo se llama:** Después de resolver dependencias topológicas y antes de `on_start()`.
- **Propósito:** Interacción con otros plugins ya cargados, configuración de dependencias cruzadas.
- **Notas:** Aquí es seguro acceder a otros objetos del sistema. El orden de inicialización respeta las dependencias declaradas en `get_dependencies()`.
- **Ejemplo detallado:**
  ```python
  def on_init(self):
      # Acceder a dependencias
      self.toolhead_obj = self.lookup_object('toolhead')
      self.gcode_obj = self.lookup_object('gcode')
      # Verificar compatibilidad
      if hasattr(self.toolhead_obj, 'get_kinematics'):
          self.kinematics = self.toolhead_obj.get_kinematics()
      else:
          self.logger.warning("Toolhead no soporta cinemáticas avanzadas")
      # Configurar callbacks o suscripciones
      self.is_initialized = True
      self.logger.debug("Plugin inicializado correctamente")
  ```

##### `on_start() -> None`
- **Cuándo se llama:** Cuando todos los plugins están listos y el sistema inicia completamente.
- **Propósito:** Iniciar tareas de tiempo real, bucles de monitoreo, conexiones externas.
- **Notas:** Aquí se deben registrar tareas RT y comenzar operaciones activas. El sistema está completamente operativo.
- **Ejemplo detallado:**
  ```python
  def on_start(self):
      # Registrar tareas RT
      self.register_rt_task("sensor_monitor", self._monitor_sensors, priority=70)
      self.register_rt_task("data_processor", self._process_data, priority=50)
      # Iniciar conexiones
      self.start_network_connection()
      # Configurar temporizadores
      self.status_timer = self.reactor.register_timer(self._update_status, self.reactor.monotonic() + 1.0)
      self.logger.info("Plugin iniciado: tareas RT registradas")
  ```

##### `on_shutdown() -> None`
- **Cuándo se llama:** Durante el apagado del sistema o recarga del plugin.
- **Propósito:** Liberación de recursos, detener tareas, guardar estado persistente.
- **Notas:** Debe ser idempotente y manejar excepciones. No asumir que otros objetos están disponibles.
- **Ejemplo detallado:**
  ```python
  def on_shutdown(self):
      # Señalar parada a hilos RT
      self.stop_signal.set()
      # Cancelar temporizadores
      if hasattr(self, 'status_timer'):
          self.reactor.unregister_timer(self.status_timer)
      # Cerrar conexiones
      self.close_network_connection()
      # Guardar estado si necesario
      self.save_persistent_state()
      self.logger.info("Plugin detenido limpiamente")
  ```

##### `on_reload() -> None`
- **Cuándo se llama:** Después de una recarga en caliente (hot-reload) del módulo.
- **Propósito:** Reconfigurar el plugin tras recarga, restaurar estado si es necesario.
- **Notas:** Similar a `on_load()`, pero para reinicialización. Llamado antes de `on_init()` en recargas.
- **Ejemplo detallado:**
  ```python
  def on_reload(self):
      # Recargar configuración
      self.on_load()
      # Restaurar estado si es posible
      self.restore_state_from_backup()
      # Reconfigurar dependencias si cambiaron
      self.reconfigure_dependencies()
      self.logger.info("Plugin recargado en caliente")
  ```

##### `on_config_change(section: str, values: Dict[str, Any]) -> None`
- **Cuándo se llama:** Cuando cambian parámetros en `printer.cfg` (ej. comando `SAVE_CONFIG`).
- **Propósito:** Actualización en tiempo real de configuración sin reinicio.
- **Parámetros:**
  - `section`: Nombre de la sección modificada (string).
  - `values`: Diccionario con parámetros actualizados {option: value}.
- **Notas:** Los valores están ya parseados y validados. Se acumulan cambios para notificación en batch.
- **Ejemplo detallado:**
  ```python
  def on_config_change(self, section: str, values: dict) -> None:
      if hasattr(self, 'section_name') and section == self.section_name:
          for option, value in values.items():
              if option == 'max_speed':
                  self.max_speed = float(value)
                  self.logger.info(f"Velocidad máxima actualizada a {self.max_speed}")
              elif option == 'enable_logging':
                  self.enable_logging = bool(value)
                  if self.enable_logging:
                      self.logger.setLevel(logging.DEBUG)
                  else:
                      self.logger.setLevel(logging.INFO)
      elif section == "stepper_x":  # Ejemplo de monitoreo de otra sección
          if 'max_velocity' in values:
              self.adjust_to_stepper_velocity(values['max_velocity'])
      # Log general
      self.logger.debug(f"Cambios en [{section}]: {list(values.keys())}")
  ```

### ExtraInterface

`ExtraInterface` hereda de `ExtraLifecycle` y proporciona una interfaz completa para interactuar con Klipper de forma RT-safe y declarativa.

#### Variables de Clase

##### `allows_multiple_instances: bool = False`
- **Propósito:** Controla si el plugin permite múltiples instancias.
- **Valores:**
  - `False` (predeterminado): Una instancia por impresora (Singleton).
  - `True`: Múltiples instancias para secciones como `[servo s1]`, `[servo s2]`.
- **Uso:** Configurar en la clase o al exportar: `Extra = MiClase(True)`.
- **Ejemplo:**
  ```python
  class MiServo(ExtraInterface):
      allows_multiple_instances = True  # Permitir múltiples servos
  ```

#### Constructor y Atributos de Instancia

##### `__init__(self, config: Any)`
- **Parámetros:** `config` - Objeto de configuración de la sección.
- **Inicializa:**
  - `self.config`: Referencia al config.
  - `self.printer`: Printer principal.
  - `self.section_name`: Nombre de la sección (ej. `'mi_extra'`).
  - `self.module_name`: Nombre del módulo.
  - `self.logger`: Logger específico.
  - `self._lock`: RLock para thread-safety.
  - `self.stop_signal`: Evento para detener tareas RT.
  - Suscripción automática a cambios de config.
- **Notas:** No sobrescribir a menos que sea necesario; usar hooks de ciclo de vida.

#### Propiedades (Lazy Loading - Carga Perezosa)

##### `gcode -> Any`
- **Descripción:** Acceso al sistema de comandos G-code.
- **Uso:** Enviar respuestas, registrar comandos.
- **Ejemplo:**
  ```python
  self.gcode.respond_info("Mensaje informativo")
  self.gcode.register_command('MI_CMD', self.cmd_handler)
  ```

##### `toolhead -> Any`
- **Descripción:** Control de movimientos y estado de la herramienta.
- **Uso:** Movimientos, homing, estado de posición.
- **Ejemplo:**
  ```python
  pos = self.toolhead.get_position()
  self.toolhead.manual_move([100, None, None], 50.0)
  ```

##### `rt_core -> Any`
- **Descripción:** Núcleo de tiempo real para tareas avanzadas.
- **Uso:** Registro de hilos RT de bajo nivel.
- **Ejemplo:**
  ```python
  self.rt_core.register_thread("custom", my_function, priority=80)
  ```

##### `reactor -> Any`
- **Descripción:** Reactor de eventos para temporización precisa.
- **Uso:** Temporizadores, eventos asíncronos.
- **Ejemplo:**
  ```python
  timer = self.reactor.register_timer(callback, self.reactor.monotonic() + 1.0)
  ```

##### `configfile -> Any`
- **Descripción:** Acceso al sistema de archivos de configuración.
- **Uso:** Lecturas adicionales, suscripciones a cambios.
- **Ejemplo:**
  ```python
  other_config = self.configfile.get_section('other_section')
  self.configfile.register_callback('global', self.global_config_handler)
  ```

##### `pins -> Any`
- **Descripción:** Gestión de pines hardware.
- **Uso:** Configuración de pines digitales/analógicos.
- **Ejemplo:**
  ```python
  pin = self.pins.setup_pin('digital_out', 'PA0')
  pin.setup_max_duration(0.1)
  ```

##### `kinematics -> Any`
- **Descripción:** Acceso directo a las cinemáticas actuales de la impresora.
- **Uso:** Cálculos de posición, información de steppers.
- **Ejemplo:**
  ```python
  kin = self.kinematics
  stepper_pos = {s.get_name(): s.get_commanded_position() for s in kin.get_steppers()}
  current_pos = kin.calc_position(stepper_pos)
  ```

##### `probe -> Optional[Any]`
- **Descripción:** Objeto probe para mediciones de altura (opcional).
- **Uso:** Calibración automática, mediciones Z.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.probe:
      self.probe.run_probe(self.gcode, {'samples': 3})
  else:
      self.respond_info("No probe configured")
  ```

##### `bed_mesh -> Optional[Any]`
- **Descripción:** Sistema de malla de cama (opcional).
- **Uso:** Corrección de deformaciones de la cama.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.bed_mesh:
      mesh_data = self.bed_mesh.get_mesh()
      self.respond_info(f"Mesh points: {len(mesh_data)}")
  ```

##### `safe_z_home -> Optional[Any]`
- **Descripción:** Sistema de homing Z seguro (opcional).
- **Uso:** Homing Z con movimientos seguros.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.safe_z_home:
      self.safe_z_home.home_z()
  ```

##### `idle_timeout -> Optional[Any]`
- **Descripción:** Control de timeout de inactividad (opcional).
- **Uso:** Gestión de energía, apagado automático.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.idle_timeout:
      self.idle_timeout.set_timeout(300)  # 5 minutos
  ```

##### `pause_resume -> Optional[Any]`
- **Descripción:** Sistema de pausa/reanudación (opcional).
- **Uso:** Control de impresión, pausas de emergencia.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.pause_resume:
      self.pause_resume.send_pause_command()
  ```

##### `manual_probe -> Optional[Any]`
- **Descripción:** Sistema de calibración manual (opcional).
- **Uso:** Asistentes de calibración interactiva.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.manual_probe:
      helper = self.manual_probe.ManualProbeHelper(self.printer, self.gcode, self.calibration_callback)
  ```

##### `query_endstops -> Optional[Any]`
- **Descripción:** Consulta de finales de carrera (opcional).
- **Uso:** Verificación de estado de endstops.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.query_endstops:
      endstop_states = self.query_endstops.query_endstops()
      self.respond_info(f"Endstops: {endstop_states}")
  ```

##### `firmware_retraction -> Optional[Any]`
- **Descripción:** Sistema de retracción por firmware (opcional).
- **Uso:** Control de retracción automática.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.firmware_retraction:
      self.firmware_retraction.retract()
  ```

##### `start_time -> float`
- **Descripción:** Tiempo de inicio de la impresora (timestamp Unix).
- **Uso:** Cálculos de tiempo transcurrido, logging con timestamps.
- **Ejemplo:**
  ```python
  elapsed = time.time() - self.start_time
  self.logger.info(f"Impresora activa por {elapsed:.1f} segundos")
  ```

##### `state -> str`
- **Descripción:** Estado actual de la impresora.
- **Uso:** Verificación de estado antes de operaciones.
- **Valores comunes:** `'ready'`, `'printing'`, `'error'`, `'shutdown'`.
- **Ejemplo:**
  ```python
  if self.state == 'ready':
      self.respond_info("Impresora lista para comandos")
  elif self.state == 'printing':
      self.respond_info("Impresora ocupada imprimiendo")
  ```

##### `stats -> Any`
- **Descripción:** Objeto de estadísticas de la impresora.
- **Uso:** Información de rendimiento, contadores, métricas.
- **Ejemplo:**
  ```python
  uptime = self.stats.get_uptime()
  print_count = self.stats.get_print_count()
  self.respond_info(f"Uptime: {uptime}s, Impresiones: {print_count}")
  ```

##### `temp_store -> Any`
- **Descripción:** Almacenamiento de datos de temperatura.
- **Uso:** Acceso a historial de temperaturas, análisis térmico.
- **Ejemplo:**
  ```python
  temp_history = self.temp_store.get_temp_history('extruder', 300)  # últimos 5 minutos
  avg_temp = sum(t for _, t in temp_history) / len(temp_history)
  ```

##### `mcu -> Optional[Any]`
- **Descripción:** MCU principal de la impresora (opcional).
- **Uso:** Comunicación directa con hardware, operaciones de bajo nivel.
- **Retorna:** `None` si no hay MCU principal.
- **Ejemplo:**
  ```python
  if self.mcu:
      mcu_freq = self.mcu.get_freq()
      self.respond_info(f"Frecuencia MCU: {mcu_freq}Hz")
  ```

##### `steppers -> List[Any]`
- **Descripción:** Lista de objetos stepper de la impresora.
- **Uso:** Control directo de motores, diagnóstico de steppers.
- **Ejemplo:**
  ```python
  for stepper in self.steppers:
      name = stepper.get_name()
      pos = stepper.get_commanded_position()
      self.logger.debug(f"Stepper {name}: posición {pos}")
  ```

##### `heaters -> Dict[str, Any]`
- **Descripción:** Diccionario de calefactores disponibles.
- **Uso:** Control de temperaturas, monitoreo térmico.
- **Ejemplo:**
  ```python
  for name, heater in self.heaters.items():
      temp = heater.get_temp()
      target = heater.get_target_temp()
      self.respond_info(f"{name}: {temp:.1f}°C / {target:.1f}°C")
  ```

##### `uptime -> float`
- **Descripción:** Tiempo de funcionamiento de la impresora en segundos.
- **Uso:** Cálculos de tiempo transcurrido, métricas de uptime.
- **Ejemplo:**
  ```python
  uptime_hours = self.uptime / 3600
  self.respond_info(f"Uptime: {uptime_hours:.1f} horas")
  ```

##### `version -> str`
- **Descripción:** Versión de Klipper instalada.
- **Uso:** Verificación de compatibilidad, logging de versión.
- **Ejemplo:**
  ```python
  self.logger.info(f"Klipper version: {self.version}")
  ```

##### `hostname -> str`
- **Descripción:** Nombre del host del sistema.
- **Uso:** Identificación del sistema, logging.
- **Ejemplo:**
  ```python
  self.respond_info(f"Sistema: {self.hostname}")
  ```

##### `cpu_info -> Dict[str, Any]`
- **Descripción:** Información detallada del CPU.
- **Uso:** Monitoreo de rendimiento del sistema.
- **Ejemplo:**
  ```python
  cpu_temp = self.cpu_info.get('cpu_temp', 'N/A')
  self.respond_info(f"Temperatura CPU: {cpu_temp}")
  ```

##### `mem_info -> Dict[str, Any]`
- **Descripción:** Información de memoria del sistema.
- **Uso:** Monitoreo de uso de memoria.
- **Ejemplo:**
  ```python
  mem_usage = self.mem_info.get('mem_usage', 0)
  self.respond_info(f"Uso de memoria: {mem_usage}%")
  ```

##### `load_average -> List[float]`
- **Descripción:** Carga promedio del sistema (1min, 5min, 15min).
- **Uso:** Monitoreo de carga del sistema.
- **Ejemplo:**
  ```python
  load_1min = self.load_average[0]
  self.respond_info(f"Carga sistema (1min): {load_1min:.2f}")
  ```

##### `network_info -> Dict[str, Any]`
- **Descripción:** Información de red del sistema.
- **Uso:** Diagnóstico de conectividad.
- **Ejemplo:**
  ```python
  ip_address = self.network_info.get('ip_address', 'N/A')
  self.respond_info(f"IP: {ip_address}")
  ```

##### `system_info -> Dict[str, Any]`
- **Descripción:** Información general del sistema.
- **Uso:** Información del hardware y software.
- **Ejemplo:**
  ```python
  distro = self.system_info.get('distro', 'Unknown')
  self.respond_info(f"Distribución: {distro}")
  ```

##### `proc_stats -> Dict[str, Any]`
- **Descripción:** Estadísticas del proceso de Klipper.
- **Uso:** Monitoreo de rendimiento del proceso.
- **Ejemplo:**
  ```python
  cpu_percent = self.proc_stats.get('cpu_percent', 0)
  self.respond_info(f"CPU Klipper: {cpu_percent}%")
  ```

##### `throttle_info -> Dict[str, Any]`
- **Descripción:** Información de throttling del sistema (Raspberry Pi).
- **Uso:** Detección de problemas de alimentación/thermal.
- **Ejemplo:**
  ```python
  if self.throttle_info:
      throttled = self.throttle_info.get('throttled', False)
      if throttled:
          self.respond_info("¡Advertencia: Sistema throttled!")
  ```

##### `motion_report -> Optional[Any]`
- **Descripción:** Sistema de reportes de movimiento (opcional).
- **Uso:** Información detallada de movimientos.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.motion_report:
      motion_data = self.motion_report.get_status()
      self.respond_info(f"Motion: {motion_data}")
  ```

##### `exclude_object -> Optional[Any]`
- **Descripción:** Sistema de exclusión de objetos (opcional).
- **Uso:** Cancelación selectiva de objetos en impresión.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.exclude_object:
      self.exclude_object.exclude_object("object_name")
  ```

##### `print_stats -> Optional[Any]`
- **Descripción:** Estadísticas de impresión (opcional).
- **Uso:** Información de trabajos de impresión.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.print_stats:
      stats = self.print_stats.get_status()
      total_jobs = stats.get('total_jobs', 0)
      self.respond_info(f"Trabajos totales: {total_jobs}")
  ```

##### `virtual_sdcard -> Optional[Any]`
- **Descripción:** Tarjeta SD virtual (opcional).
- **Uso:** Gestión de archivos de impresión.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.virtual_sdcard:
      file_list = self.virtual_sdcard.get_file_list()
      self.respond_info(f"Archivos disponibles: {len(file_list)}")
  ```

##### `display -> Optional[Any]`
- **Descripción:** Sistema de pantalla (opcional).
- **Uso:** Control de pantallas LCD/OLED.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.display:
      self.display.send_text("Mensaje en pantalla")
  ```

##### `webhooks -> Optional[Any]`
- **Descripción:** Sistema de webhooks (opcional).
- **Uso:** Comunicación con interfaces web.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.webhooks:
      self.webhooks.call_remote_method("notify", message="Hola")
  ```

##### `database -> Optional[Any]`
- **Descripción:** Base de datos del sistema (opcional).
- **Uso:** Almacenamiento persistente de datos.
- **Retorna:** `None` si no está configurado.
- **Ejemplo:**
  ```python
  if self.database:
      data = self.database.get_item("my_key", "default")
      self.respond_info(f"Dato almacenado: {data}")
  ```

#### Métodos de Ciclo de Vida Adicionales

##### `register_gcode_commands() -> None`
- **Propósito:** Registro centralizado de comandos G-code.
- **Cuándo:** Durante la carga inicial.
- **Ejemplo:**
  ```python
  def register_gcode_commands(self):
      self.register_command('START_MONITOR', self.cmd_start_monitor, "Inicia monitoreo")
      self.register_command('STOP_MONITOR', self.cmd_stop_monitor, "Detiene monitoreo")
  ```

##### `get_dependencies() -> List[str]`
- **Propósito:** Declarar dependencias de otros plugins.
- **Retorno:** Lista de nombres de secciones dependientes.
- **Ejemplo:** `return ['toolhead', 'gcode', 'kinematics']`

#### API Declarativa de Tiempo Real

##### `register_rt_task(name: str, target: Callable[[], None], priority: int = 50, is_critical: bool = False) -> None`
- **Parámetros:**
  - `name`: Nombre único de la tarea.
  - `target`: Función a ejecutar (debe ser RT-safe).
  - `priority`: Prioridad (1-99, mayor = más alta).
  - `is_critical`: Si es crítico para el sistema.
- **Notas:** El framework maneja registro y limpieza automática.
- **Ejemplo:**
  ```python
  def on_start(self):
      self.register_rt_task("temp_monitor", self._monitor_temperature, priority=70, is_critical=True)
  
  def _monitor_temperature(self):
      while not self.stop_signal.is_set():
          temp = self.read_sensor()
          if temp > self.max_temp:
              self.handle_overheat()
          self.sleep(0.1)
  ```

##### `cleanup_rt_tasks() -> None`
- **Propósito:** Limpieza automática de recursos RT.
- **Cuándo:** Internamente durante shutdown/recarga.
- **Notas:** No llamar manualmente.

##### `rt_context() -> ContextManager`
- **Propósito:** Context manager para operaciones thread-safe.
- **Ejemplo:**
  ```python
  with self.rt_context():
      self.shared_data['counter'] += 1
      self.process_shared_data()
  ```

##### `main_loop(interval: float = 1.0) -> None`
- **Propósito:** Bucle principal predeterminado.
- **Ejemplo:**
  ```python
  def main_loop(self, interval):
      while self.rt_core.running and not self.stop_signal.is_set():
          self.update_status()
          self.check_conditions()
          self.sleep(interval)
  ```

##### `register_rt_main_task(name: Optional[str] = None, interval: float = 1.0) -> None`
- **Propósito:** Registrar `main_loop` como tarea RT.
- **Ejemplo:** `self.register_rt_main_task("main", 0.5)`

#### Helpers de Alto Nivel

##### `register_command(name: str, callback: Any, desc: str = "") -> None`
- **Ejemplo:** `self.register_command('TEST', self.cmd_test, "Comando de prueba")`

##### `respond_info(msg: str) -> None`
- **Ejemplo:** `self.respond_info("Operación completada exitosamente")`

##### `error(msg: str) -> Exception`
- **Ejemplo:** `raise self.error("Error crítico detectado")`

##### `check_homed(axes: str = "xyz") -> None`
- **Ejemplo:** `self.check_homed("xy")`

##### `get_current_pos() -> List[float]`
- **Retorno:** [x, y, z, a, b] posición actual.
- **Ejemplo:** `pos = self.get_current_pos(); x, y, z = pos[:3]`

##### `get_probe() -> Optional[Any]`
- **Ejemplo:** `probe = self.get_probe(); if probe: probe.run_probe(...)`

##### `get_kinematics() -> Any`
- **Ejemplo:** `kin = self.get_kinematics(); pivot = kin.pivot_offset_z`

##### `wait_moves() -> None`
- **Ejemplo:** `self.wait_moves()  # Esperar a que terminen movimientos`

##### `home() -> None`
- **Ejemplo:** `self.home()  # Homing completo`

##### `lookup_object(name: str) -> Any`
- **Ejemplo:** `obj = self.lookup_object('temperature_sensor my_sensor')`

##### `create_manual_probe(gcmd: Any, callback: Any) -> Any`
- **Ejemplo:** `self.create_manual_probe(gcmd, self._on_probe_result)`

##### `get_time() -> float`
- **Ejemplo:** `timestamp = self.get_time()`

##### `get_formatted_time(fmt: str = "%Y%m%d-%H%M%S") -> str`
- **Ejemplo:** `ts = self.get_formatted_time()`

##### `get_iso_time() -> str`
- **Ejemplo:** `iso = self.get_iso_time()`

##### `sleep(seconds: float) -> None`
- **Ejemplo:** `self.sleep(1.0)`

##### `wait(seconds: float, message: Optional[str] = None) -> None`
- **Ejemplo:** `self.wait(2.0, "Esperando calibración...")`

##### `manual_move(pos: List[Optional[float]], speed: float, wait_time: float = 0.0, message: Optional[str] = None) -> None`
- **Parámetros:**
  - `pos`: Lista de posiciones [x, y, z, a, b] (None para no mover).
  - `speed`: Velocidad en mm/min.
  - `wait_time`: Espera antes del movimiento.
  - `message`: Mensaje durante la espera.
- **Ejemplo:** `self.manual_move([100, 100, 50], 60.0, wait_time=1.0, message="Moviendo a posición...")`

##### `get_status(eventtime: float) -> Dict[str, Any]`
- **Propósito:** Estado para Moonraker/Fluidd.
- **Ejemplo:**
  ```python
  def get_status(self, eventtime):
      return {
          'active': self.is_active,
          'temperature': self.current_temp,
          'last_update': self.last_update_time
      }
  ```

## Características Avanzadas

### Soporte para Múltiples Instancias
- **Singleton:** Una instancia por impresora.
- **Múltiples:** Para periféricos (ej. `[servo left]`, `[servo right]`).
- **Configuración:** `allows_multiple_instances = True` en la clase.

### Recarga en Caliente (Hot Reload)
- Limpieza automática de recursos RT.
- Recarga de módulo con `importlib.reload`.
- Restauración de estado si implementado.

### Compatibilidad y Patrones de Carga
1. **TUXEDO_RT:** `Extra = MiClase` (recomendado).
2. **Factory:** `create_plugin(config)`.
3. **Legacy:** `load_config(config)` vía `LegacyPluginAdapter`.

### LegacyPluginAdapter
- Adapta plugins clásicos de Klipper.
- Mapea métodos legacy a la nueva interfaz.
- Soporte para `on_config_change` legacy (por opción individual).

### ExtraManager
- Gestión centralizada de plugins.
- Resolución de dependencias topológicas.
- Carga automática desde directorios.
- Recarga en caliente.

## Ejemplo Completo y Avanzado

```python
from extra_manager import ExtraInterface
import time
import logging

class MonitorAvanzado(ExtraInterface):
    allows_multiple_instances = False  # Singleton
    
    def on_load(self):
        # Configuración inicial detallada
        self.max_temp = self.config.getfloat('max_temp', 250.0)
        self.interval = self.config.getfloat('interval', 1.0)
        self.enable_alerts = self.config.getboolean('enable_alerts', True)
        self.alert_threshold = self.config.getfloat('alert_threshold', 220.0)
        self.log_level = self.config.get('log_level', 'INFO').upper()
        
        # Inicializar estado
        self.is_active = False
        self.current_temp = 0.0
        self.alert_count = 0
        self.measurements = []
        self.last_alert_time = 0
        
        # Configurar logging
        self.logger.setLevel(getattr(logging, self.log_level, logging.INFO))
    
    def register_gcode_commands(self):
        self.register_command('MONITOR_START', self.cmd_start, "Inicia monitoreo de temperatura")
        self.register_command('MONITOR_STOP', self.cmd_stop, "Detiene monitoreo")
        self.register_command('MONITOR_STATUS', self.cmd_status, "Muestra estado del monitor")
        self.register_command('MONITOR_RESET', self.cmd_reset, "Resetea contadores")
    
    def on_init(self):
        # Verificar dependencias usando propiedades lazy loading
        self.temp_sensor = self.lookup_object('temperature_sensor extruder')
        if not self.temp_sensor:
            self.logger.error("Sensor de temperatura no encontrado")
            return
        
        # Verificar componentes opcionales disponibles
        if self.probe:
            self.logger.info("Probe disponible para calibraciones")
        if self.bed_mesh:
            self.logger.info("Bed mesh disponible para correcciones")
        if self.idle_timeout:
            self.logger.info("Idle timeout disponible para gestión de energía")
        
        self.logger.info("Dependencias verificadas correctamente")
    
    def on_start(self):
        self.register_rt_main_task("temp_monitor", self.interval)
        self.logger.info("Monitor de temperatura iniciado")
    
    def main_loop(self, interval):
        while self.rt_core.running and not self.stop_signal.is_set():
            if self.is_active:
                self.current_temp = self.temp_sensor.get_temp()
                self.measurements.append((self.get_time(), self.current_temp))
                
                # Mantener solo últimas 100 mediciones
                if len(self.measurements) > 100:
                    self.measurements.pop(0)
                
                # Verificar alertas
                if self.enable_alerts and self.current_temp > self.alert_threshold:
                    self.handle_alert()
                
                # Log periódico
                if len(self.measurements) % 10 == 0:
                    avg_temp = sum(t for _, t in self.measurements[-10:]) / 10
                    self.logger.debug(f"Temperatura promedio: {avg_temp:.1f}°C")
            
            self.sleep(interval)
    
    def on_config_change(self, section, values):
        if hasattr(self, 'section_name') and section == self.section_name:
            for opt, val in values.items():
                if opt == 'max_temp':
                    self.max_temp = float(val)
                elif opt == 'interval':
                    self.interval = float(val)
                elif opt == 'enable_alerts':
                    self.enable_alerts = bool(val)
                elif opt == 'alert_threshold':
                    self.alert_threshold = float(val)
                elif opt == 'log_level':
                    self.log_level = val.upper()
                    self.logger.setLevel(getattr(logging, self.log_level, logging.INFO))
            self.logger.info(f"Configuración actualizada: {values}")
    
    def on_shutdown(self):
        self.is_active = False
        self.stop_signal.set()
        self.save_measurements_to_file()
        self.logger.info("Monitor detenido")
    
    def handle_alert(self):
        current_time = self.get_time()
        if current_time - self.last_alert_time > 60:  # Una alerta por minuto
            self.alert_count += 1
            self.respond_info(f"ALERTA: Temperatura alta {self.current_temp:.1f}°C")
            self.last_alert_time = current_time
    
    def save_measurements_to_file(self):
        # Implementación de guardado persistente
        pass
    
    def cmd_start(self, gcmd):
        self.is_active = True
        self.respond_info("Monitoreo de temperatura iniciado")
    
    def cmd_stop(self, gcmd):
        self.is_active = False
        self.respond_info("Monitoreo detenido")
    
    def cmd_status(self, gcmd):
        status = self.get_status(self.reactor.monotonic())
        self.respond_info(f"Estado: {status}")
        
        # Mostrar información de componentes disponibles
        components = []
        if self.probe: components.append("probe")
        if self.bed_mesh: components.append("bed_mesh")
        if self.safe_z_home: components.append("safe_z_home")
        if self.idle_timeout: components.append("idle_timeout")
        if self.pause_resume: components.append("pause_resume")
        if self.firmware_retraction: components.append("firmware_retraction")
        
        if components:
            self.respond_info(f"Componentes disponibles: {', '.join(components)}")
        else:
            self.respond_info("No hay componentes opcionales disponibles")
            
        # Mostrar información del sistema usando nuevas propiedades
        uptime = time.time() - self.start_time
        self.respond_info(f"Estado impresora: {self.state}, Uptime: {uptime:.1f}s")
        
        if self.mcu:
            try:
                freq = self.mcu.get_freq()
                self.respond_info(f"Frecuencia MCU: {freq}Hz")
            except:
                pass
                
        if self.steppers:
            self.respond_info(f"Steppers encontrados: {len(self.steppers)}")
            
        if self.heaters:
            heater_info = []
            for name, heater in self.heaters.items():
                try:
                    temp = heater.get_temp()
                    target = heater.get_target_temp()
                    heater_info.append(f"{name}: {temp:.1f}/{target:.1f}°C")
                except:
                    pass
            if heater_info:
                self.respond_info(f"Calefactores: {', '.join(heater_info)}")
                
        # Información del sistema
        self.respond_info(f"Versión Klipper: {self.version}")
        self.respond_info(f"Host: {self.hostname}")
        
        if self.cpu_info:
            cpu_temp = self.cpu_info.get('cpu_temp', 'N/A')
            self.respond_info(f"CPU Temp: {cpu_temp}")
            
        if self.mem_info:
            mem_usage = self.mem_info.get('mem_usage', 0)
            self.respond_info(f"Memoria: {mem_usage}%")
            
        if self.load_average:
            load_1min = self.load_average[0]
            self.respond_info(f"Carga (1min): {load_1min:.2f}")
            
        if self.throttle_info and self.throttle_info.get('throttled', False):
            self.respond_info("⚠️ Sistema throttled!")
            
        if self.print_stats:
            try:
                stats = self.print_stats.get_status()
                total_jobs = stats.get('total_jobs', 0)
                self.respond_info(f"Trabajos totales: {total_jobs}")
            except:
                pass
    
    def cmd_reset(self, gcmd):
        self.alert_count = 0
        self.measurements.clear()
        self.respond_info("Contadores reseteados")
    
    def get_status(self, eventtime):
        return {
            'active': self.is_active,
            'current_temp': self.current_temp,
            'max_temp': self.max_temp,
            'alert_count': self.alert_count,
            'measurements_count': len(self.measurements),
            'last_measurement': self.measurements[-1] if self.measurements else None
        }

# Exportar
Extra = MonitorAvanzado
```

Este framework eleva el desarrollo de plugins a un nivel profesional, con énfasis en robustez, mantenibilidad y rendimiento RT.
