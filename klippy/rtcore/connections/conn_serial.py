# RT Core Serial Connection implementation
# TUXEDO_RT: Implementación de conexión Serial / USB Serial
import os, logging, serial
from .base import RTConnection
import util

class SerialConnection(RTConnection):
    CONFIG_PARAMS = {
        'serial': ('str', None, True),
        'baud': ('int', 250000, False),
        'rts': ('bool', True, False),
        'usb_optimize': ('bool', True, False)
    }

    def __init__(self, reactor, serialport, baud, rts=True, usb_optimize=True,
                 mcu_name=""):
        super(SerialConnection, self).__init__(reactor, mcu_name)
        self.serialport = serialport
        self.baud = baud
        self.rts = rts
        self.usb_optimize = usb_optimize
        self.serial_dev = None

    def open(self):
        """Abre el puerto serial con reintentos y soporte STK500v2"""
        logging.info("%sStarting Serial connect on %s", self.warn_prefix, self.serialport)
        start_time = self.reactor.monotonic()
        while 1:
            if self.reactor.monotonic() > start_time + 90.:
                raise Exception(self.warn_prefix + "Unable to connect to Serial")
            try:
                self.serial_dev = serial.Serial(baudrate=self.baud, timeout=0,
                                               exclusive=True)
                self.serial_dev.port = self.serialport
                self.serial_dev.rts = self.rts
                self.serial_dev.open()
            except (OSError, IOError, serial.SerialException) as e:
                logging.warning("%sUnable to open serial port: %s",
                                self.warn_prefix, e)
                self.reactor.pause(self.reactor.monotonic() + 5.)
                continue
            
            # Reset AVR si es necesario
            self.stk500v2_leave()
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
        """Retorna tipo de conexión: CONN_TYPE_SERIAL (0x01)"""
        from klippy import connhdl
        return connhdl.CONN_TYPE_SERIAL

    def get_info(self):
        return "Serial %s at %d baud" % (self.serialport, self.baud)

    def detect_usb_speed(self):
        """TUXEDO_RT: Detecta la velocidad USB a través de sysfs"""
        try:
            # Resolvemos el symlink (ej. /dev/serial/by-id/...) a tty
            real_path = os.path.realpath(self.serialport)
            basename = os.path.basename(real_path)
            sysfs_path = os.path.join("/sys/class/tty", basename, "device")
            if not os.path.exists(sysfs_path):
                return "unknown"
            
            # Subir por el árbol de sysfs hasta encontrar un nodo USB que tenga 'speed'
            current_path = sysfs_path
            while current_path != "/sys/devices":
                speed_file = os.path.join(current_path, "speed")
                if os.path.exists(speed_file):
                    with open(speed_file, "r") as f:
                        speed = f.read().strip()
                    return speed
                current_path = os.path.dirname(current_path)
        except Exception as e:
            logging.debug("%sError detectando velocidad USB: %s", self.warn_prefix, e)
        return "unknown"

    def setup_serialqueue(self, ffi_lib, serialqueue):
        """TUXEDO_RT: Configura el perfil USB en la cola serial C"""
        max_blocks = 12 # Default (Original Klipper)
        
        if not self.usb_optimize:
            logging.info("%sUSB optimization disabled (usb_optimize=False). "
                         "Using original Klipper payload profile.",
                         self.warn_prefix)
        else:
            speed = self.detect_usb_speed()
            if speed in ("1.5", "12"):
                max_blocks = 1   # USB 1.x (Full Speed) -> 64 bytes max bulk
                logging.info("%sDetected USB Full-Speed (1.x) - Optimizing payload to 64 bytes", self.warn_prefix)
            elif speed == "480":
                max_blocks = 8   # USB 2.0 (High Speed) -> 512 bytes max bulk
                logging.info("%sDetected USB High-Speed (2.0) - Optimizing payload to 512 bytes", self.warn_prefix)
            elif speed in ("5000", "10000"):
                max_blocks = 16  # USB 3.x (SuperSpeed) -> 1024 bytes max bulk
                logging.info("%sDetected USB SuperSpeed (3.x) - Optimizing payload to 1024 bytes", self.warn_prefix)
            else:
                logging.debug("%sUSB speed not detected or not applicable (speed=%s), using default payload", self.warn_prefix, speed)

        try:
            ffi_lib.serialqueue_set_usb_profile(serialqueue, max_blocks)
        except AttributeError:
            logging.warning("%schelper: serialqueue_set_usb_profile not found", self.warn_prefix)

    def stk500v2_leave(self):
        """Intenta sacar un AVR stk500v2 del modo programador"""
        logging.debug("%sStarting stk500v2 leave sequence", self.warn_prefix)
        util.clear_hupcl(self.serial_dev.fileno())
        origbaud = self.serial_dev.baudrate
        # Dummy speed reset
        self.serial_dev.baudrate = 2400
        self.serial_dev.read(1)
        # Sequence leave
        self.serial_dev.baudrate = 115200
        self.reactor.pause(self.reactor.monotonic() + 0.100)
        self.serial_dev.read(4096)
        self.serial_dev.write(b'\x1b\x01\x00\x01\x0e\x11\x04')
        self.reactor.pause(self.reactor.monotonic() + 0.050)
        res = self.serial_dev.read(4096)
        logging.debug("%sGot %s from stk500v2", self.warn_prefix, repr(res))
        self.serial_dev.baudrate = origbaud

