# RT Core Pipe Connection implementation
# TUXEDO_RT: Implementación de conexión vía Pipe / Unix Domain Socket
import os, logging
from .base import RTConnection

class PipeConnection(RTConnection):
    CONFIG_PARAMS = {
        'filename': ('str', None, True)
    }

    def __init__(self, reactor, filename, mcu_name=""):
        super(PipeConnection, self).__init__(reactor, mcu_name)
        self.filename = filename
        self.serial_dev = None

    def open(self):
        logging.info("%sStarting pipe connect on %s", self.warn_prefix, self.filename)
        start_time = self.reactor.monotonic()
        while 1:
            if self.reactor.monotonic() > start_time + 90.:
                raise Exception(self.warn_prefix + "Unable to connect to pipe")
            try:
                fd = os.open(self.filename, os.O_RDWR | os.O_NOCTTY)
                self.serial_dev = os.fdopen(fd, 'rb+', 0)
            except OSError as e:
                logging.warning("%sUnable to open pipe: %s",
                                self.warn_prefix, e)
                self.reactor.pause(self.reactor.monotonic() + 5.)
                continue
            break
        return True

    def close(self):
        if self.serial_dev is not None:
            self.serial_dev.close()
            self.serial_dev = None

    def get_fd(self):
        if self.serial_dev is not None:
            return self.serial_dev.fileno()
        return -1

    def get_type(self):
        return b'u' # Los pipes se tratan como UART/Stream en serialqueue

    def get_info(self):
        return "Pipe %s" % (self.filename,)

