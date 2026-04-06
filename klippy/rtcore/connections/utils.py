# RT Core Connection Utilities
# TUXEDO_RT: Funciones de utilidad para resets de hardware y mantenimiento
import logging, serial
import util

def arduino_reset(serialport, reactor):
    """Realiza un reset estilo Arduino (toggle DTR)"""
    try:
        ser = serial.Serial(serialport, 2400, timeout=0, exclusive=True)
        ser.read(1)
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = True
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = False
        reactor.pause(reactor.monotonic() + 0.100)
        ser.close()
    except:
        logging.exception("Error during arduino_reset on %s", serialport)

def cheetah_reset(serialport, reactor):
    """Realiza un reset específico para placas Fysetc Cheetah v1.2"""
    try:
        ser = serial.Serial(baudrate=2400, timeout=0, exclusive=True)
        ser.port = serialport
        ser.rts = True
        ser.open()
        ser.read(1)
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = True
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = False
        reactor.pause(reactor.monotonic() + 0.100)
        ser.rts = False
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = True
        reactor.pause(reactor.monotonic() + 0.100)
        ser.dtr = False
        reactor.pause(reactor.monotonic() + 0.100)
        ser.close()
    except:
        logging.exception("Error during cheetah_reset on %s", serialport)

