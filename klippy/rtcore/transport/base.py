# RT Core Transport Interface
# TUXEDO_RT: Definición de la interfaz base para transportes de tiempo real

class RTTransport:
    # TUXEDO_RT: Diccionario de parámetros que espera este transporte en [mcu]
    # Formato: { 'nombre_parametro': (tipo, default_valor, es_requerido) }
    CONFIG_PARAMS = {}

    def __init__(self, reactor, mcu_name=""):
        self.reactor = reactor
        self.mcu_name = mcu_name
        self.warn_prefix = "mcu '%s': " % (mcu_name,) if mcu_name else ""

    def open(self):
        """Abre el puerto o medio de comunicación"""
        raise NotImplementedError

    def close(self):
        """Cierra el puerto y libera recursos"""
        raise NotImplementedError

    def get_fd(self):
        """Retorna el descriptor de archivo para serialqueue"""
        raise NotImplementedError

    def get_type(self):
        """Retorna el tipo de transporte (b'u', b'c', b'f')"""
        raise NotImplementedError

    def get_info(self):
        """Retorna información detallada para logs"""
        raise NotImplementedError

    def setup_serialqueue(self, ffi_lib, serialqueue):
        """Configuración específica en la cola de mensajes C (opcional)"""
        pass

    def get_client_id(self):
        """ID del cliente para CAN o protocolos compartidos (default 0)"""
        return 0
