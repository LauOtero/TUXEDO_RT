# RT Core Connection Factory
# TUXEDO_RT: Factoría dinámica para instanciar conexiones automáticamente
import os
import importlib
import logging

class ConnectionFactory:
    def __init__(self):
        self.connections = {}
        self._load_connections()

    def _load_connections(self):
        """Carga dinámicamente todos los archivos que empiezan por 'conn_'"""
        dirname = os.path.dirname(__file__)
        for filename in os.listdir(dirname):
            if filename.startswith("conn_") and filename.endswith(".py"):
                module_name = filename[:-3]
                try:
                    # Importar el módulo de forma relativa usando el paquete actual
                    module = importlib.import_module(".%s" % module_name, __package__)
                    # Buscar clases que hereden de RTConnection (o simplemente por convención de nombre)
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and attr_name.endswith("Connection") and attr_name != "RTConnection":
                            self.connections[module_name] = attr
                            logging.info("Conexión cargada dinámicamente: %s (%s)", module_name, attr_name)
                except Exception as e:
                    logging.error("Error cargando conexión %s: %s", module_name, str(e))

    def get_available_params(self):
        """Retorna todos los parámetros posibles de todas las conexiones"""
        all_params = {}
        for connection_class in self.connections.values():
            all_params.update(connection_class.CONFIG_PARAMS)
        return all_params

    def identify_connection(self, config):
        """Identifica qué conexión usar basándose en las claves presentes en config"""
        for name, connection_class in self.connections.items():
            # Si todos los parámetros requeridos están presentes, es este
            required_params = [k for k, v in connection_class.CONFIG_PARAMS.items() if v[2]]
            if all(config.get(param, None) is not None for param in required_params):
                return connection_class
        return None

    def create_connection(self, reactor, config, mcu_name=""):
        """Crea la instancia de la conexión adecuada basándose en la configuración"""
        connection_class = self.identify_connection(config)
        if connection_class is None:
            return None

        # Extraer parámetros dinámicamente
        kwargs = {'reactor': reactor, 'mcu_name': mcu_name}
        for param, (ptype, pdefault, prequired) in connection_class.CONFIG_PARAMS.items():
            if ptype == 'int':
                val = config.getint(param, pdefault)
            elif ptype == 'bool':
                val = config.getboolean(param, pdefault)
            else:
                val = config.get(param, pdefault)
            
            # Mapear nombres de parámetros a los argumentos de __init__ si es necesario
            # En este caso, trataremos de pasarlos tal cual o mapear los conocidos
            arg_name = param
            if param == 'serial': arg_name = 'serialport'
            if param == 'canbus_uuid': arg_name = 'uuid'
            if param == 'canbus_interface': arg_name = 'iface'
            
            kwargs[arg_name] = val

        return connection_class(**kwargs)

    # Métodos heredados por compatibilidad si es necesario, pero ahora usan el sistema dinámico
    def create_uart(self, reactor, serialport, baud, rts=True, mcu_name=""):
        from .conn_serial import SerialConnection
        return SerialConnection(reactor, serialport, baud, rts, mcu_name)

    def create_can(self, reactor, uuid, nodeid, iface="can0", mcu_name=""):
        from .conn_can import CANConnection
        return CANConnection(reactor, uuid, nodeid, iface, mcu_name)

    def create_pipe(self, reactor, filename, mcu_name=""):
        from .conn_pipe import PipeConnection
        return PipeConnection(reactor, filename, mcu_name)

    def create_file(self, reactor, debugoutput, dictionary, mcu_name=""):
        from .conn_file import FileConnection
        return FileConnection(reactor, debugoutput, dictionary, mcu_name)

