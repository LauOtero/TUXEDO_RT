# RT Core Transport Factory
# TUXEDO_RT: Factoría dinámica para instanciar transportes automáticamente
import os
import importlib
import logging

class TransportFactory:
    def __init__(self):
        self.transports = {}
        self._load_transports()

    def _load_transports(self):
        """Carga dinámicamente todos los archivos que empiezan por 'transport_'"""
        dirname = os.path.dirname(__file__)
        for filename in os.listdir(dirname):
            if filename.startswith("transport_") and filename.endswith(".py"):
                module_name = filename[:-3]
                try:
                    # Importar el módulo de forma relativa usando el paquete actual
                    module = importlib.import_module(".%s" % module_name, __package__)
                    # Buscar clases que hereden de RTTransport (o simplemente por convención de nombre)
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and attr_name.endswith("Transport") and attr_name != "RTTransport":
                            self.transports[module_name] = attr
                            logging.info("Transporte cargado dinámicamente: %s (%s)", module_name, attr_name)
                except Exception as e:
                    logging.error("Error cargando transporte %s: %s", module_name, str(e))

    def get_available_params(self):
        """Retorna todos los parámetros posibles de todos los transportes"""
        all_params = {}
        for transport_class in self.transports.values():
            all_params.update(transport_class.CONFIG_PARAMS)
        return all_params

    def identify_transport(self, config):
        """Identifica qué transporte usar basándose en las claves presentes en config"""
        for name, transport_class in self.transports.items():
            # Si todos los parámetros requeridos están presentes, es este
            required_params = [k for k, v in transport_class.CONFIG_PARAMS.items() if v[2]]
            if all(config.get(param, None) is not None for param in required_params):
                return transport_class
        return None

    def create_transport(self, reactor, config, mcu_name=""):
        """Crea la instancia del transporte adecuado basándose en la configuración"""
        transport_class = self.identify_transport(config)
        if transport_class is None:
            return None

        # Extraer parámetros dinámicamente
        kwargs = {'reactor': reactor, 'mcu_name': mcu_name}
        for param, (ptype, pdefault, prequired) in transport_class.CONFIG_PARAMS.items():
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

        return transport_class(**kwargs)

    # Métodos heredados por compatibilidad si es necesario, pero ahora usan el sistema dinámico
    def create_uart(self, reactor, serialport, baud, rts=True, mcu_name=""):
        from .transport_uart import UARTTransport
        return UARTTransport(reactor, serialport, baud, rts, mcu_name)

    def create_can(self, reactor, uuid, nodeid, iface="can0", mcu_name=""):
        from .transport_can import CANTransport
        return CANTransport(reactor, uuid, nodeid, iface, mcu_name)

    def create_pipe(self, reactor, filename, mcu_name=""):
        from .transport_pipe import PipeTransport
        return PipeTransport(reactor, filename, mcu_name)

    def create_file(self, reactor, debugoutput, dictionary, mcu_name=""):
        from .transport_file import FileTransport
        return FileTransport(reactor, debugoutput, dictionary, mcu_name)
