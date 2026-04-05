# Ejemplo de Monitor de Temperatura en Tiempo Real para TUXEDO_RT

from extra_manager import ExtraInterface
from rtcore.rt_core import RealTimeCore
from i18n import _
import logging

class RTTempMonitor(ExtraInterface):
    def __init__(self, config):
        # Inicialización obligatoria con la impresora
        super().__init__(config.get_printer())
        self.printer = config.get_printer()
        
        # Leer configuración del printer.cfg
        self.sensor_name = config.get('sensor', 'extruder')
        self.max_temp = config.getfloat('max_temp', 300.0)
        
        # Registrar este extra en el sistema de tolerancia a fallos
        self.printer.fault_tolerance.register_watchdog("rt_temp_monitor")
        
        # Iniciar el hilo de monitoreo con prioridad de tiempo real
        self.rt_core = self.printer.rt_core
        self.printer.get_reactor().register_callback(self._start_monitoring)

    def _start_monitoring(self, eventtime):
        """Inicia el monitoreo en un hilo separado con SCHED_FIFO."""
        import threading
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def _monitor_loop(self):
        """Bucle de monitoreo de alta prioridad."""
        # Establecer prioridad de tiempo real (85 es alta)
        self.rt_core.set_realtime_priority(priority=85)
        
        logging.info(_("rt_temp_monitor.starting", sensor=self.sensor_name))
        
        while True:
            try:
                # Simulación de lectura de sensor (en un caso real se consultaría el objeto heater)
                current_temp = 250.0 # Valor de ejemplo
                
                if current_temp > self.max_temp:
                    # Notificar error localizado
                    msg = _("rt_temp_monitor.overheat", sensor=self.sensor_name, temp=current_temp)
                    self.printer.invoke_shutdown(msg)
                
                # Actualizar latido para el watchdog
                self.printer.fault_tolerance.update_heartbeat("rt_temp_monitor")
                
                # Pausa controlada (evitar sleep estándar en RT si es posible)
                self.printer.get_reactor().pause(0.1)
                
            except Exception as e:
                logging.error(_("rt_temp_monitor.error", error=str(e)))
                break

    def get_status(self, eventtime):
        """Método requerido por Moonraker."""
        return {
            "sensor": self.sensor_name,
            "max_allowed": self.max_temp,
            "is_active": True
        }

def load_config(config):
    return RTTempMonitor(config)
