# Ejemplo de Gestor de Memoria para Telemetría de Alta Frecuencia en TUXEDO_RT

from extra_manager import ExtraInterface
from rtcore.memory_manager import ObjectFactory, DeterministicMemoryPool
from i18n import _
import logging
import time

class RTTelemetryPool(ExtraInterface):
    def __init__(self, config):
        # Inicialización
        super().__init__(config.get_printer())
        self.printer = config.get_printer()
        
        # Obtener el gestor de memoria pre-asignada
        self.memory_factory = self.printer.memory_factory
        
        # Crear un pool de diccionarios para telemetría
        # initial_size=1000 diccionarios listos para usar
        self.telemetry_pool = DeterministicMemoryPool(dict, initial_size=1000)
        
        # Iniciar el registro de datos periódico
        self.interval = config.getfloat('interval', 0.001) # 1ms de frecuencia
        self.printer.get_reactor().register_timer(self._telemetry_timer)

    def _telemetry_timer(self, eventtime):
        """Tarea periódica que usa el pool para evitar GC de Python."""
        
        # Adquirir un diccionario del pool (sin asignación dinámica)
        data = self.telemetry_pool.acquire()
        data.clear() # Limpiar el contenido previo
        
        # Poblar con datos de telemetría (ej. posición de ejes)
        toolhead = self.printer.lookup_object('toolhead')
        pos = toolhead.get_position()
        
        data['time'] = eventtime
        data['x'] = pos[0]
        data['y'] = pos[1]
        data['z'] = pos[2]
        
        # Procesar los datos (ej. enviar a un buffer circular o log)
        # self.process_data(data)
        
        # Liberar el objeto de vuelta al pool para su reutilización
        self.telemetry_pool.release(data)
        
        # Continuar la tarea periódica
        return eventtime + self.interval

    def get_status(self, eventtime):
        """Estado para Moonraker."""
        return {
            "pool_size": self.telemetry_pool.get_total_size(),
            "available_objects": self.telemetry_pool.get_available_count(),
            "interval_ms": self.interval * 1000
        }

def load_config(config):
    return RTTelemetryPool(config)
