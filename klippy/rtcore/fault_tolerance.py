import time
import threading
import logging
import collections

class FaultTolerantCore:
    """
    Gestión de tolerancia a fallos, watchdog timers, checkpoints de estado
    para recuperación rápida y aislamiento de fallos por módulo.
    """
    def __init__(self, check_interval=0.1):
        self.check_interval = check_interval
        self.watchdogs = {}
        self.checkpoints = {}
        self.metrics = collections.defaultdict(list)
        self.running = False
        self.logger = logging.getLogger('FaultTolerance')
        self.monitor_thread = None
        self.lock = threading.Lock()

    def register_watchdog(self, module_name, timeout=1.0):
        """
        Registra un watchdog para un módulo. Si el módulo no envía
        un 'heartbeat' en el tiempo 'timeout', se considera en fallo.
        """
        with self.lock:
            self.watchdogs[module_name] = {
                'timeout': timeout,
                'last_heartbeat': time.time(),
                'status': 'OK'
            }
            self.logger.info(f"Watchdog registrado para el módulo '{module_name}' (Timeout: {timeout}s)")

    def heartbeat(self, module_name):
        """Módulo informa que sigue vivo y operando correctamente."""
        with self.lock:
            if module_name in self.watchdogs:
                self.watchdogs[module_name]['last_heartbeat'] = time.time()
                self.watchdogs[module_name]['status'] = 'OK'

    def save_checkpoint(self, module_name, state_dict):
        """Guarda un checkpoint del estado actual para una recuperación rápida."""
        with self.lock:
            self.checkpoints[module_name] = {
                'timestamp': time.time(),
                'state': state_dict.copy()
            }
            self.logger.debug(f"Checkpoint guardado para el módulo '{module_name}'")

    def load_checkpoint(self, module_name):
        """Recupera el último checkpoint válido para aislamiento y reinicio del módulo."""
        with self.lock:
            if module_name in self.checkpoints:
                self.logger.info(f"Recuperando checkpoint de '{module_name}'")
                return self.checkpoints[module_name]['state']
            return None

    def record_metric(self, metric_name, value):
        """Métricas de rendimiento en tiempo real con históricos."""
        with self.lock:
            timestamp = time.time()
            self.metrics[metric_name].append((timestamp, value))
            
            # Limitar histórico para evitar sobreconsumo de memoria
            if len(self.metrics[metric_name]) > 1000:
                self.metrics[metric_name] = self.metrics[metric_name][-1000:]

    def _monitor_loop(self):
        """Bucle determinista de monitoreo de estado y watchdogs."""
        while self.running:
            current_time = time.time()
            with self.lock:
                for name, wd in self.watchdogs.items():
                    if wd['status'] == 'OK' and (current_time - wd['last_heartbeat']) > wd['timeout']:
                        wd['status'] = 'FAILED'
                        self.logger.critical(f"¡Fallo detectado en el módulo '{name}'! Timeout de Watchdog.")
                        # Aquí se puede invocar una estrategia de aislamiento o recuperación
                        # usando los checkpoints guardados.
                        self.recover_module(name)
                        
            time.sleep(self.check_interval)

    def recover_module(self, module_name):
        """Intenta recuperar un módulo utilizando su último checkpoint conocido."""
        self.logger.warning(f"Iniciando recuperación del módulo '{module_name}'...")
        # Lógica de reinicio/recuperación
        # Si falla, se escala a un apagado de emergencia de la máquina.

    def start_monitoring(self):
        """Inicia el hilo de monitoreo del watchdog."""
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="WatchdogMonitor")
        self.monitor_thread.start()
        self.logger.info("Monitor de tolerancia a fallos iniciado.")

    def stop_monitoring(self):
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join()
