# RT Core File Transport implementation
# TUXEDO_RT: Implementación de transporte para modo depuración / archivo
import logging
from .base import RTTransport

class FileTransport(RTTransport):
    CONFIG_PARAMS = {
        'debugoutput': ('str', None, True),
        'dictionary': ('str', None, True)
    }

    def __init__(self, reactor, debugoutput, dictionary, mcu_name=""):
        super(FileTransport, self).__init__(reactor, mcu_name)
        self.debugoutput = debugoutput
        self.dictionary = dictionary

    def open(self):
        logging.info("%sStarting file transport (debug mode)", self.warn_prefix)
        # El archivo ya viene abierto desde mcu.py
        return True

    def close(self):
        if self.debugoutput is not None:
            self.debugoutput.close()
            self.debugoutput = None

    def get_fd(self):
        if self.debugoutput is not None:
            return self.debugoutput.fileno()
        return -1

    def get_type(self):
        return b'f'

    def get_info(self):
        return "File (debug output)"

    def setup_serialqueue(self, ffi_lib, serialqueue):
        # El procesamiento del diccionario se hace en serialhdl._start_session
        pass
