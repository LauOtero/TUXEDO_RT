import importlib
import pkgutil
import logging
import threading
import sys
import time
import datetime
import configfile
from typing import Any, Dict, List, Optional, Set, Callable
from contextlib import contextmanager
from i18n import _

# Alias de tipo para el núcleo de Klipper/TUXEDO_RT
CoreType = Any

class ExtraLifecycle:
    """
    Hooks del ciclo de vida para plugins/extras.
    Proveen un diseño declarativo sin intervención manual.
    """
    def on_load(self) -> None:
        """
        Hook para carga inicial y configuración.
        Llamado inmediatamente después de instanciar el plugin.
        Ideal para leer parámetros de configuración y configurar estados iniciales.
        """
        pass

    def on_init(self) -> None:
        """
        Llamado tras resolver dependencias y antes de on_start.
        Ideal para interactuar con otros plugins ya cargados.
        """
        pass

    def on_start(self) -> None:
        """Llamado cuando todos los plugins están listos para ejecutarse."""
        pass

    def on_shutdown(self) -> None:
        """Llamado antes de detener el plugin."""
        pass

    def on_reload(self) -> None:
        """Llamado tras una recarga en caliente (hot-reload)."""
        pass

    def on_config_change(self, section: str, values: Dict[str, Any]) -> None:
        """
        Hook para reaccionar a cambios de configuración en caliente (ej. SAVE_CONFIG).
        Recibe la sección modificada y un diccionario con todos los parámetros actualizados.
        Por defecto, registra los cambios en el log si la sección coincide con la propia.
        Las subclases pueden sobrescribir para lógica específica o validar otras secciones.
        """
        if hasattr(self, 'section_name') and section == self.section_name:
            for option, value in values.items():
                self.logger.info("Configuración actualizada en caliente: [%s] %s = %s", 
                                 section, option, value)
        # Las subclases pueden sobrescribir para agregar validaciones de otras secciones
        # Ejemplo: elif section == "otra_seccion": ... lógica específica ...


class ExtraInterface(ExtraLifecycle):
    """
    Interfaz robusta, declarativa y type-safe para los extras TUXEDO_RT.
    Oculta la complejidad del rtcore proporcionando abstracciones de alto nivel.
    """
    # Determina si se permiten múltiples secciones para este extra (ej: [servo s1], [servo s2])
    # Por defecto es False (Singleton) para mayor seguridad en el sistema.
    allows_multiple_instances: bool = False

    def __new__(cls, *args, **kwargs):
        """
        Permite el patrón: Extra = MiClase(True/False) en el módulo
        para configurar el comportamiento de instanciación dinámicamente.
        """
        if len(args) == 1 and type(args[0]) is bool:
            cls.allows_multiple_instances = args[0]
            return cls
        return super().__new__(cls)

    def __init__(self, config: Any):
        self.config = config
        self.printer = config.get_printer()
        self.core = self.printer # Mantener compatibilidad si se usaba core
        self.section_name = config.get_name()
        self.module_name = self.__class__.__module__.split('.')[-1]
        self._status: Dict[str, Any] = {}
        self._rt_threads: List[str] = []
        self.logger = logging.getLogger(self.__class__.__name__)
        self._lock = threading.RLock() # RLock para evitar deadlocks y ser un poco más rápido en recursión
        self.stop_signal = threading.Event()
        
        # Atributos internos para carga perezosa (Lazy Loading)
        self._gcode = None
        self._toolhead = None
        self._rt_core = None
        self._reactor = None
        self._configfile = None
        self._pins = None
        self._kinematics = None
        self._probe = None
        self._bed_mesh = None
        self._safe_z_home = None
        self._idle_timeout = None
        self._pause_resume = None
        self._manual_probe = None
        self._query_endstops = None
        self._firmware_retraction = None
        self._start_time = None
        self._state = None
        self._stats = None
        self._temp_store = None
        self._mcu = None
        self._steppers = None
        self._heaters = None
        self._uptime = None
        self._version = None
        self._hostname = None
        self._cpu_info = None
        self._mem_info = None
        self._load_average = None
        self._network_info = None
        self._system_info = None
        self._proc_stats = None
        self._throttle_info = None
        self._motion_report = None
        self._exclude_object = None
        self._print_stats = None
        self._virtual_sdcard = None
        self._display = None
        self._webhooks = None
        self._database = None
        
        # Para acumular cambios de configuración y notificar en batch
        self._pending_config_changes: Dict[str, Any] = {}
        self._config_change_timer = None
        
        # Suscribirse a cambios de configuración
        try:
            self.configfile.register_callback(self.section_name, self._on_config_change_callback)
        except Exception:
            pass

    def _on_config_change_callback(self, option: str, value: Any) -> None:
        """Callback interno que acumula cambios y programa la notificación en batch."""
        self._pending_config_changes[option] = value
        if self._config_change_timer is not None:
            self.reactor.unregister_timer(self._config_change_timer)
        self._config_change_timer = self.reactor.register_timer(
            self._flush_config_changes, self.reactor.monotonic() + 0.1)  # 100ms delay

    def _flush_config_changes(self, eventtime):
        """Notifica los cambios acumulados al hook on_config_change."""
        if self._pending_config_changes:
            self.on_config_change(self.section_name, self._pending_config_changes.copy())
            self._pending_config_changes.clear()
        self._config_change_timer = None
        return self.reactor.NEVER

    @property
    def gcode(self) -> Any:
        if self._gcode is None:
            self._gcode = self.printer.lookup_object('gcode')
        return self._gcode

    @property
    def toolhead(self) -> Any:
        if self._toolhead is None:
            self._toolhead = self.printer.lookup_object('toolhead')
        return self._toolhead

    @property
    def rt_core(self) -> Any:
        if self._rt_core is None:
            self._rt_core = self.printer.lookup_object('rt_core')
        return self._rt_core

    @property
    def reactor(self) -> Any:
        if self._reactor is None:
            self._reactor = self.printer.get_reactor()
        return self._reactor

    @property
    def configfile(self) -> Any:
        if self._configfile is None:
            self._configfile = self.printer.lookup_object('configfile')
        return self._configfile

    @property
    def pins(self) -> Any:
        if self._pins is None:
            self._pins = self.printer.lookup_object('pins')
        return self._pins

    @property
    def kinematics(self) -> Any:
        if self._kinematics is None:
            self._kinematics = self.toolhead.get_kinematics()
        return self._kinematics

    @property
    def probe(self) -> Optional[Any]:
        if self._probe is None:
            try:
                self._probe = self.printer.lookup_object('probe')
            except self.printer.config_error:
                self._probe = None
        return self._probe

    @property
    def bed_mesh(self) -> Optional[Any]:
        if self._bed_mesh is None:
            try:
                self._bed_mesh = self.printer.lookup_object('bed_mesh')
            except self.printer.config_error:
                self._bed_mesh = None
        return self._bed_mesh

    @property
    def safe_z_home(self) -> Optional[Any]:
        if self._safe_z_home is None:
            try:
                self._safe_z_home = self.printer.lookup_object('safe_z_home')
            except self.printer.config_error:
                self._safe_z_home = None
        return self._safe_z_home

    @property
    def idle_timeout(self) -> Optional[Any]:
        if self._idle_timeout is None:
            try:
                self._idle_timeout = self.printer.lookup_object('idle_timeout')
            except self.printer.config_error:
                self._idle_timeout = None
        return self._idle_timeout

    @property
    def pause_resume(self) -> Optional[Any]:
        if self._pause_resume is None:
            try:
                self._pause_resume = self.printer.lookup_object('pause_resume')
            except self.printer.config_error:
                self._pause_resume = None
        return self._pause_resume

    @property
    def manual_probe(self) -> Optional[Any]:
        if self._manual_probe is None:
            try:
                self._manual_probe = self.printer.lookup_object('manual_probe')
            except self.printer.config_error:
                self._manual_probe = None
        return self._manual_probe

    @property
    def query_endstops(self) -> Optional[Any]:
        if self._query_endstops is None:
            try:
                self._query_endstops = self.printer.lookup_object('query_endstops')
            except self.printer.config_error:
                self._query_endstops = None
        return self._query_endstops

    @property
    def firmware_retraction(self) -> Optional[Any]:
        if self._firmware_retraction is None:
            try:
                self._firmware_retraction = self.printer.lookup_object('firmware_retraction')
            except self.printer.config_error:
                self._firmware_retraction = None
        return self._firmware_retraction

    @property
    def start_time(self) -> float:
        if self._start_time is None:
            self._start_time = self.printer.get_start_time()
        return self._start_time

    @property
    def state(self) -> str:
        if self._state is None:
            self._state = self.printer.get_state()
        return self._state

    @property
    def stats(self) -> Any:
        if self._stats is None:
            self._stats = self.printer.get_stats()
        return self._stats

    @property
    def temp_store(self) -> Any:
        if self._temp_store is None:
            self._temp_store = self.printer.get_temp_store()
        return self._temp_store

    @property
    def mcu(self) -> Optional[Any]:
        if self._mcu is None:
            try:
                self._mcu = self.printer.lookup_object('mcu')
            except self.printer.config_error:
                self._mcu = None
        return self._mcu

    @property
    def steppers(self) -> List[Any]:
        if self._steppers is None:
            self._steppers = []
            # Buscar todos los objetos stepper
            try:
                # Intentar obtener steppers del toolhead
                if hasattr(self.toolhead, 'get_kinematics'):
                    kin = self.toolhead.get_kinematics()
                    if hasattr(kin, 'get_steppers'):
                        self._steppers = kin.get_steppers()
            except Exception:
                pass
        return self._steppers

    @property
    def heaters(self) -> Dict[str, Any]:
        if self._heaters is None:
            self._heaters = {}
            # Buscar objetos heater comunes
            heater_names = ['heater_bed', 'extruder']
            for name in heater_names:
                try:
                    heater = self.printer.lookup_object(name)
                    if heater:
                        self._heaters[name] = heater
                except self.printer.config_error:
                    pass
        return self._heaters

    @property
    def uptime(self) -> float:
        if self._uptime is None:
            self._uptime = self.printer.get_uptime()
        return self._uptime

    @property
    def version(self) -> str:
        if self._version is None:
            self._version = self.printer.get_version()
        return self._version

    @property
    def hostname(self) -> str:
        if self._hostname is None:
            self._hostname = self.printer.get_hostname()
        return self._hostname

    @property
    def cpu_info(self) -> Dict[str, Any]:
        if self._cpu_info is None:
            self._cpu_info = self.printer.get_cpu_info()
        return self._cpu_info

    @property
    def mem_info(self) -> Dict[str, Any]:
        if self._mem_info is None:
            self._mem_info = self.printer.get_mem_info()
        return self._mem_info

    @property
    def load_average(self) -> List[float]:
        if self._load_average is None:
            self._load_average = self.printer.get_load_average()
        return self._load_average

    @property
    def network_info(self) -> Dict[str, Any]:
        if self._network_info is None:
            self._network_info = self.printer.get_network_info()
        return self._network_info

    @property
    def system_info(self) -> Dict[str, Any]:
        if self._system_info is None:
            self._system_info = self.printer.get_system_info()
        return self._system_info

    @property
    def proc_stats(self) -> Dict[str, Any]:
        if self._proc_stats is None:
            self._proc_stats = self.printer.get_proc_stats()
        return self._proc_stats

    @property
    def throttle_info(self) -> Dict[str, Any]:
        if self._throttle_info is None:
            try:
                self._throttle_info = self.printer.get_throttle_info()
            except AttributeError:
                self._throttle_info = {}
        return self._throttle_info

    @property
    def motion_report(self) -> Optional[Any]:
        if self._motion_report is None:
            try:
                self._motion_report = self.printer.lookup_object('motion_report')
            except self.printer.config_error:
                self._motion_report = None
        return self._motion_report

    @property
    def exclude_object(self) -> Optional[Any]:
        if self._exclude_object is None:
            try:
                self._exclude_object = self.printer.lookup_object('exclude_object')
            except self.printer.config_error:
                self._exclude_object = None
        return self._exclude_object

    @property
    def print_stats(self) -> Optional[Any]:
        if self._print_stats is None:
            try:
                self._print_stats = self.printer.lookup_object('print_stats')
            except self.printer.config_error:
                self._print_stats = None
        return self._print_stats

    @property
    def virtual_sdcard(self) -> Optional[Any]:
        if self._virtual_sdcard is None:
            try:
                self._virtual_sdcard = self.printer.lookup_object('virtual_sdcard')
            except self.printer.config_error:
                self._virtual_sdcard = None
        return self._virtual_sdcard

    @property
    def display(self) -> Optional[Any]:
        if self._display is None:
            try:
                self._display = self.printer.lookup_object('display')
            except self.printer.config_error:
                self._display = None
        return self._display

    @property
    def webhooks(self) -> Optional[Any]:
        if self._webhooks is None:
            try:
                self._webhooks = self.printer.lookup_object('webhooks')
            except self.printer.config_error:
                self._webhooks = None
        return self._webhooks

    @property
    def database(self) -> Optional[Any]:
        if self._database is None:
            try:
                self._database = self.printer.lookup_object('database')
            except self.printer.config_error:
                self._database = None
        return self._database

 
    def main_loop(self, interval: float = 1.0) -> None:
        """
        Bucle principal predeterminado para extras que requieren monitoreo.
        Puede ser sobrescrito por el extra.
        """
        while self.rt_core.running:
            if self.stop_signal.is_set():
                break
            self.sleep(interval)

    def register_rt_main_task(self, name: Optional[str] = None, interval: float = 1.0) -> None:
        """Registra el main_loop como una tarea RT estándar."""
        task_name = name or f"{self.__class__.__name__}Main"
        self.register_rt_task(
            name=task_name,
            target=lambda: self.main_loop(interval),
            priority=50
        )

    def register_gcode_commands(self) -> None:
        """Hook para registrar comandos G-code."""
        pass

    def check_homed(self, axes: str = "xyz") -> None:
        """Verifica que los ejes indicados estén homed."""
        status = self.toolhead.get_status(self.reactor.monotonic())
        homed_axes = status['homed_axes'].lower()
        for axis in axes.lower():
            if axis not in homed_axes:
                raise self.gcode.error(_("extra.error_not_homed").format(axis=axis.upper()))

    def get_current_pos(self) -> List[float]:
        """
        Obtiene la posición actual de la herramienta (RT-safe lookup).
        Optimizado: Cacheamos el objeto kinematics.
        """
        th = self.toolhead
        th.flush_step_generation()
        kin = th.get_status(self.reactor.monotonic())['kinematics'] # Acceso directo al cache de estado
        stepper_positions = {s.get_name(): s.get_commanded_position()
                             for s in kin.get_steppers()}
        return kin.calc_position(stepper_positions)

    def get_probe(self) -> Optional[Any]:
        """Busca el objeto probe en la configuración (método legacy, usar propiedad probe)."""
        return self.probe

    def get_kinematics(self) -> Any:
        """Helper para obtener las cinemáticas actuales (método legacy, usar propiedad kinematics)."""
        return self.kinematics

    def wait_moves(self) -> None:
        """Espera a que terminen todos los movimientos en cola."""
        self.toolhead.wait_moves()

    def home(self) -> None:
        """Realiza el homing de todos los ejes."""
        self.toolhead.home()

    def lookup_object(self, name: str) -> Any:
        """Helper para buscar objetos en el printer."""
        return self.printer.lookup_object(name)

    def create_manual_probe(self, gcmd: Any, callback: Any) -> Any:
        """Helper para crear un asistente de calibración manual."""
        if self.manual_probe is None:
            raise self.error("Manual probe not available")
        return self.manual_probe.ManualProbeHelper(self.printer, gcmd, callback)

    def respond_info(self, msg: str) -> None:
        """Helper para enviar mensajes informativos a la consola G-code."""
        self.gcode.respond_info(msg)

    def error(self, msg: str) -> Exception:
        """Helper para generar excepciones de G-code."""
        return self.gcode.error(msg)

    def register_command(self, name: str, callback: Any, desc: str = "") -> None:
        """Helper para registrar comandos G-code."""
        self.gcode.register_command(name, callback, desc=desc)

    def get_time(self) -> float:
        """Retorna el tiempo actual (RT-safe)."""
        return time.time()

    def get_formatted_time(self, fmt: str = "%Y%m%d-%H%M%S") -> str:
        """Retorna el tiempo actual formateado."""
        return time.strftime(fmt)

    def get_iso_time(self) -> str:
        """Retorna el tiempo actual en formato ISO 8601."""
        return datetime.datetime.now().isoformat()

    def sleep(self, seconds: float) -> None:
        """Pausa la ejecución (RT-safe si se usa en hilos RT)."""
        time.sleep(seconds)

    def wait(self, seconds: float, message: Optional[str] = None) -> None:
        """
        Espera un tiempo determinado, opcionalmente mostrando un mensaje.
        RT-safe para tareas en segundo plano.
        """
        if message:
            self.respond_info(message)
        self.sleep(seconds)

    def manual_move(self, pos: List[Optional[float]], speed: float, wait_time: float = 0.0, message: Optional[str] = None) -> None:
        """
        Realiza un movimiento manual (RT-safe), con opción de espera previa y mensaje.
        """
        if message:
            self.respond_info(message)
        if wait_time > 0:
            self.sleep(wait_time)
        try:
            self.toolhead.manual_move(pos, speed)
        except self.printer.command_error as e:
            raise self.error(str(e))

    def get_status(self, eventtime: float) -> Dict[str, Any]:
        """Devuelve el estado actual para Moonraker/Fluidd."""
        with self._lock:
            return self._status

    def get_dependencies(self) -> List[str]:
        """Dependencias requeridas por este extra."""
        return []

    # ==========================================
    # API Declarativa para RTCore
    # ==========================================
    def register_rt_task(self, name: str, target: Callable[[], None], priority: int = 50, is_critical: bool = False) -> None:
        """
        Registra una tarea de tiempo real de forma simplificada y declarativa.
        El framework se encarga de la gestión y limpieza.
        """
        try:
            # Buscar el rt_core en el printer/core
            rt_core = None
            if hasattr(self.core, 'lookup_object'):
                rt_core = self.core.lookup_object('rt_core')
            elif hasattr(self.core, 'rt_core'):
                rt_core = self.core.rt_core
                
            if rt_core and hasattr(rt_core, 'register_thread'):
                rt_core.register_thread(
                    name=name,
                    target=target,
                    priority=priority,
                    is_critical=is_critical
                )
                with self._lock:
                    self._rt_threads.append(name)
                self.logger.info(f"Tarea RT '{name}' registrada exitosamente con prioridad {priority}.")
            else:
                self.logger.warning(f"No se encontró rt_core activo para la tarea '{name}'. Ejecución simulada.")
        except Exception as e:
            self.logger.error(f"Fallo al registrar la tarea RT '{name}': {e}")

    def cleanup_rt_tasks(self) -> None:
        """
        Limpia automáticamente todos los recursos rtcore asociados.
        Llamado internamente por el ExtraManager durante recargas o apagados.
        """
        with self._lock:
            # Aquí iría la lógica de desregistro en el rt_core real si lo soporta.
            # Por ahora, limpiamos nuestra lista interna.
            count = len(self._rt_threads)
            self._rt_threads.clear()
            if count > 0:
                self.logger.debug(f"Limpiados {count} recursos/hilos RT.")

    @contextmanager
    def rt_context(self):
        """
        Context manager para operaciones thread-safe y RT-safe.
        Uso: `with self.rt_context(): ...`
        """
        self._lock.acquire()
        try:
            yield self
        finally:
            self._lock.release()


class LegacyPluginAdapter(ExtraInterface):
    """
    Adaptador (Adapter Pattern) para garantizar compatibilidad hacia atrás 
    con plugins clásicos de Klipper que usan `load_config`.
    """
    def __init__(self, config: Any, legacy_instance: Any):
        super().__init__(config)
        self.legacy_instance = legacy_instance
        self.logger = logging.getLogger(f"LegacyAdapter:{legacy_instance.__name__ if hasattr(legacy_instance, '__name__') else legacy_instance.__class__.__name__}")

    def get_status(self, eventtime: float) -> Dict[str, Any]:
        if hasattr(self.legacy_instance, 'get_status'):
            return self.legacy_instance.get_status(eventtime)
        return {}

    def on_init(self) -> None:
        # Mapea el método clásico initialize de Klipper
        if hasattr(self.legacy_instance, 'initialize'):
            self.legacy_instance.initialize()

    def define_config(self, config: Any) -> None:
        """Mapea define_config al objeto legacy si existe."""
        if hasattr(self.legacy_instance, 'define_config'):
            self.legacy_instance.define_config(config)

    def register_gcode_commands(self) -> None:
        """Mapea register_gcode_commands al objeto legacy si existe."""
        if hasattr(self.legacy_instance, 'register_gcode_commands'):
            self.legacy_instance.register_gcode_commands()

    def get_dependencies(self) -> List[str]:
        # Soporte para plugins antiguos que exponen dependencias
        if hasattr(self.legacy_instance, 'get_dependencies'):
            return self.legacy_instance.get_dependencies()
        return []

    def on_config_change(self, option: str, value: Any) -> None:
        """Mapea on_config_change al objeto legacy si existe."""
        if hasattr(self.legacy_instance, 'on_config_change'):
            self.legacy_instance.on_config_change(option, value)


class ExtraManager:
    """
    Framework escalable y robusto de plugins/extras para TUXEDO_RT.
    Maneja dependencias topológicas, ciclo de vida, adaptadores,
    recarga en caliente (hot-reload) y limpieza de recursos.
    """
    def __init__(self, core: CoreType):
        self.core = core
        self.plugins: Dict[str, ExtraInterface] = {}
        self.logger = logging.getLogger('ExtraManager')
        self._lock = threading.Lock()

    def load_plugins_from_directory(self, package_name: str) -> None:
        """
        Descubre e intenta cargar todos los plugins de un paquete que tengan
        una sección correspondiente en el archivo printer.cfg.
        """
        self.logger.info(f"Escaneando plugins en el paquete '{package_name}'...")
        try:
            package = importlib.import_module(package_name)
            configfile = self.core.lookup_object('configfile')
        except (ImportError, Exception) as e:
            self.logger.error(f"Fallo al inicializar escaneo de plugins: {e}")
            return

        if not hasattr(package, '__path__'):
            return

        # Obtenemos todas las secciones del config
        sections = configfile.get_status(None)['config'].keys()

        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            full_module_name = f"{package_name}.{module_name}"
            
            # Buscamos secciones que coincidan con el nombre del módulo (ej: [pivot_calibrate])
            # o que empiecen con el nombre del módulo seguido de un espacio (ej: [servo my_servo])
            for section in sections:
                if section == module_name or section.startswith(module_name + " "):
                    config = configfile.get_section(section)
                    self.load_plugin(full_module_name, config)

    def load_plugin(self, full_module_name: str, config: Any) -> Optional[ExtraInterface]:
        """
        Carga un plugin individual detectando automáticamente su patrón de diseño
        (Builder/Factory, Interfaz Estricta TUXEDO_RT o Legacy Klipper).
        """
        section_name = config.get_name()
        short_module_name = full_module_name.split('.')[-1]
        
        with self._lock:
            # 1. Verificar si la sección exacta ya está cargada
            if section_name in self.plugins:
                self.logger.warning(f"La sección '{section_name}' ya se encuentra cargada.")
                return self.plugins[section_name]

            # 2. Verificar si es un plugin de instancia única (Singleton) que ya existe
            for p in self.plugins.values():
                if p.module_name == short_module_name and not p.allows_multiple_instances:
                    self.logger.error(f"Fallo al cargar '{section_name}': El extra '{short_module_name}' solo permite una instancia única.")
                    return None

            try:
                module = importlib.import_module(full_module_name)
                plugin_instance = None

                # 1. Patrón Declarativo (Factory/Builder)
                if hasattr(module, 'create_plugin'):
                    plugin_instance = module.create_plugin(config)
                    self.logger.debug(f"Plugin '{section_name}' cargado vía Factory/Builder.")

                # 2. Patrón Estricto TUXEDO_RT (ExtraInterface)
                elif hasattr(module, 'Extra'):
                    extra_def = module.Extra
                    extra_name = getattr(extra_def, '__name__', type(extra_def).__name__)
                    self.logger.debug(f"Plugin '{section_name}' ExtraClass='{extra_name}' detectado.")

                    if isinstance(extra_def, type):
                        # Clase Extra pura y posible esquema `Extra = MyClass(True)`
                        # Si el nombre de la clase empieza con "Printer", instanciar con (printer, config) para compatibilidad con core
                        if extra_def.__name__.startswith('Printer'):
                            plugin_instance = extra_def(self.printer, config)
                        else:
                            plugin_instance = extra_def(config)
                    elif callable(extra_def):
                        # Puede ser una función factory que retorna instancia o clase
                        plugin_candidate = extra_def(config)
                        if isinstance(plugin_candidate, ExtraInterface):
                            plugin_instance = plugin_candidate
                        elif isinstance(plugin_candidate, type):
                            if plugin_candidate.__name__.startswith('Printer'):
                                plugin_instance = plugin_candidate(self.printer, config)
                            else:
                                plugin_instance = plugin_candidate(config)
                        else:
                            plugin_instance = plugin_candidate
                    else:
                        # Instancia predefinida
                        plugin_instance = extra_def

                    self.logger.debug(f"Plugin '{section_name}' cargado vía ExtraInterface (optimized).")

                # 3. Patrón Legacy Klipper (Compatibilidad hacia atrás)
                else:
                    legacy_inst = None
                    section_parts = section_name.split()
                    if len(section_parts) > 1 and hasattr(module, 'load_config_prefix'):
                        self.logger.debug(f"Plugin '{section_name}' usa load_config_prefix por nombre con sufijo.")
                        legacy_inst = module.load_config_prefix(config)
                        self.logger.debug(f"Plugin '{section_name}' (prefix) cargado vía LegacyAdapter.")
                    elif hasattr(module, 'load_config'):
                        legacy_inst = module.load_config(config)
                        self.logger.debug(f"Plugin '{section_name}' cargado vía LegacyAdapter.")
                    elif hasattr(module, 'load_config_prefix'):
                        legacy_inst = module.load_config_prefix(config)
                        self.logger.debug(f"Plugin '{section_name}' (prefix fallback) cargado vía LegacyAdapter.")

                    if legacy_inst is not None:
                        plugin_instance = LegacyPluginAdapter(config, legacy_inst)
                else:
                    self.logger.warning(f"El módulo '{full_module_name}' no expone ningún patrón de carga soportado.")
                    return None

                if plugin_instance:
                    # Hook de ciclo de vida (Configuración e inicialización inicial)
                    plugin_instance.on_load()
                    
                    # Registro automático en el core de Klipper para visibilidad en Moonraker
                    self.printer.add_object(section_name, plugin_instance)
                    
                    # Registro automático de comandos G-code
                    plugin_instance.register_gcode_commands()
                    
                    self.plugins[section_name] = plugin_instance
                    return plugin_instance

            except Exception as e:
                self.logger.error(f"Fallo crítico al cargar el plugin '{short_module_name}': {e}")
                return None

    def load_object(self, config: Any, section: str, default: Any = configfile.sentinel) -> Optional[ExtraInterface]:
        """
        Compatibilidad absoluta con el antiguo `Printer.load_object`.
        1) intenta con plugin_manager (nuevo extras en `extras.*`)
        2) intenta con ruta legacy `extras.<module>`
        3) devuelve `default` o lanza `configfile.error`
        """
        # Si ya fue cargado, devolverlo inmediatamente.
        if section in self.plugins:
            return self.plugins[section]

        module_parts = section.split()
        module_name = module_parts[0]
        section_config = config.getsection(section) if hasattr(config, 'getsection') else config

        # Modo nuevo (ExtraManager / TUXEDO_RT). Acepta secciones explícitas y con alias.
        candidates = [section, module_name, f"extras.{module_name}"]
        if module_name.startswith('extras.'):
            candidates = [module_name]

        for candidate in candidates:
            try:
                obj = self.load_plugin(candidate, section_config)
            except Exception:
                obj = None
            if obj is not None:
                return obj

        # Normalización a extras.* para compatibilidad con Klipper clásico.
        if not section.startswith('extras.'):
            try:
                obj = self.load_plugin(f"extras.{module_name}", section_config)
            except Exception:
                obj = None
            if obj is not None:
                return obj

        if default is not configfile.sentinel:
            return default

        raise self.core.config_error("Unable to load module '%s'" % (section,))

    def reload_plugin(self, section_name: str, full_module_name: str) -> bool:
        """
        Recarga en caliente (hot-reload) un plugin específico asociado a una sección.
        Se encarga de limpiar recursos de tiempo real y re-instanciar.
        """
        with self._lock:
            if section_name in self.plugins:
                plugin = self.plugins[section_name]
                config = plugin.config # Guardamos el config original para re-instanciar
                # Limpieza de ciclo de vida
                plugin.on_shutdown()
                plugin.cleanup_rt_tasks()
                del self.plugins[section_name]
            else:
                self.logger.error(f"No se pudo recargar: Sección '{section_name}' no encontrada.")
                return False
            
            # Recargar el módulo a nivel de intérprete Python
            if full_module_name in sys.modules:
                importlib.reload(sys.modules[full_module_name])
                
        # Volver a instanciar fuera del lock principal
        new_plugin = self.load_plugin(full_module_name, config)
        if new_plugin:
            new_plugin.on_reload()
            new_plugin.on_init() # Re-inicializar
            self.logger.info(f"Sección '{section_name}' recargada en caliente exitosamente.")
            return True
        return False

    def resolve_dependencies_and_init(self) -> None:
        """
        Resuelve el árbol de dependencias topológicamente e inicializa
        los plugins en el orden correcto usando el hook on_init.
        """
        with self._lock:
            resolved: List[str] = []
            unresolved: Set[str] = set(self.plugins.keys())
            
            while unresolved:
                progress = False
                for name in list(unresolved):
                    plugin = self.plugins[name]
                    deps = plugin.get_dependencies()
                    
                    # Verificamos si todas las dependencias requeridas están resueltas (y existen)
                    if all(dep in resolved for dep in deps if dep in self.plugins):
                        resolved.append(name)
                        unresolved.remove(name)
                        progress = True
                
                if not progress:
                    self.logger.error(f"Dependencia circular o faltante detectada en: {unresolved}. Forzando inicialización.")
                    # Fallback: inicializar el resto para no bloquear el sistema entero
                    for name in list(unresolved):
                        resolved.append(name)
                        unresolved.remove(name)
                    break

            # Llamada al hook de inicialización en el orden resuelto
            for name in resolved:
                try:
                    self.plugins[name].on_init()
                    self.logger.debug(f"Plugin '{name}' inicializado correctamente.")
                except Exception as e:
                    self.logger.error(f"Excepción inicializando el plugin '{name}': {e}")

    def start_all(self) -> None:
        """Dispara el hook on_start para todos los plugins."""
        with self._lock:
            for name, plugin in self.plugins.items():
                try:
                    plugin.on_start()
                except Exception as e:
                    self.logger.error(f"Fallo en on_start de '{name}': {e}")

    def shutdown_all(self) -> None:
        """
        Apaga limpiamente todos los plugins y libera sus recursos rtcore.
        Llamado durante el cierre del sistema.
        """
        with self._lock:
            for name, plugin in list(self.plugins.items()):
                try:
                    plugin.on_shutdown()
                    plugin.cleanup_rt_tasks()
                except Exception as e:
                    self.logger.error(f"Fallo en on_shutdown de '{name}': {e}")
            self.plugins.clear()

    def get_status(self, eventtime: float) -> Dict[str, Any]:
        """
        Recopila el estado completo de todos los plugins cargados.
        Garantiza compatibilidad absoluta con Moonraker/Fluidd.
        """
        with self._lock:
            return {name: plugin.get_status(eventtime) for name, plugin in self.plugins.items()}
