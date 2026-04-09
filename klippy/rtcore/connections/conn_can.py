# RT Core CAN Connection implementation
# TUXEDO_RT: Implementación de conexión CAN Bus para Klipper
import os, logging
try:
    from .base import RTConnection
except ImportError:
    from base import RTConnection


class CANConnection(RTConnection):
    CONFIG_PARAMS = {
        'canbus_uuid': ('str', None, True),
        'canbus_interface': ('str', 'can0', False),
        'nodeid': ('int', 0, False),
        'canbus_mode': ('str', 'classic', False),
        'autoneg_retries': ('int', 3, False),
        'xl_sdt': ('int', 1, False)
    }

    def __init__(self, reactor, uuid, nodeid, iface="can0", mcu_name="", canbus_mode="classic", autoneg_retries=3, xl_sdt=1):
        super(CANConnection, self).__init__(reactor, mcu_name)
        self.uuid_str = uuid
        self.nodeid = nodeid
        self.iface = iface
        self.canbus_mode = canbus_mode
        self.autoneg_retries = autoneg_retries
        self.xl_sdt = xl_sdt
        self.bus = None
        self.txid = nodeid * 2 + 256
        self.uuid = self._parse_uuid(uuid)
        self.warn_prefix = "mcu '%s': " % (mcu_name,)


    def _parse_uuid(self, uuid_str):
        try:
            uuid = int(uuid_str, 16)
        except ValueError:
            uuid = -1
        if uuid < 0 or uuid > 0xffffffffffff:
            raise Exception(self.warn_prefix + "Invalid CAN uuid")
        return [(uuid >> (40 - i*8)) & 0xff for i in range(6)]

    def open(self):
        import can
        import socket
        import struct

        CANBUS_ID_ADMIN = 0x3f0
        CMD_SET_NODEID = 0x01
        filters = [{"can_id": self.txid+1, "can_mask": 0x7ff, "extended": False}]
        set_id_cmd = [CMD_SET_NODEID] + self.uuid + [self.nodeid]
        set_id_msg = can.Message(arbitration_id=CANBUS_ID_ADMIN,
                                 data=set_id_cmd, is_extended_id=False)
        
        logging.info("%sStarting CAN connect on %s (uuid: %s)", 
                     self.warn_prefix, self.iface, self.uuid_str)
        start_time = self.reactor.monotonic()
        while 1:
            if self.reactor.monotonic() > start_time + 90.:
                raise Exception(self.warn_prefix + "Unable to connect to CAN")
            try:
                self.bus = can.interface.Bus(channel=self.iface,
                                             can_filters=filters,
                                             bustype='socketcan')

                
                # TUXEDO_RT: Enable CAN FD and CAN XL on the raw socket
                try:
                    fd = self.bus.fileno()
                    CAN_RAW_FD_FRAMES = 5
                    CAN_RAW_XL_FRAMES = 8
                    # Enable FD
                    socket.fromfd(fd, socket.AF_CAN, socket.SOCK_RAW).setsockopt(socket.SOL_CAN_RAW, CAN_RAW_FD_FRAMES, 1)
                    # Enable XL (may fail on older kernels, so we ignore errors)
                    try:
                        socket.fromfd(fd, socket.AF_CAN, socket.SOCK_RAW).setsockopt(socket.SOL_CAN_RAW, CAN_RAW_XL_FRAMES, 1)
                    except OSError:
                        pass
                except Exception as e:
                    logging.warning("Could not enable advanced CAN frames: %s", e)

                self.bus.send(set_id_msg)
                # bus.close logic for socketcan/python-can
                self.bus.close = self.bus.shutdown
            except (can.CanError, os.error, IOError) as e:
                logging.warning("%sUnable to open CAN port: %s",
                                self.warn_prefix, e)
                self.reactor.pause(self.reactor.monotonic() + 5.)
                continue

            break
        return True

    def close(self):
        if self.bus is not None:
            self.bus.shutdown()
            self.bus = None

    def get_fd(self):
        if self.bus is not None:
            return self.bus.fileno()
        return -1

    def get_type(self):
        return b'c'

    def get_info(self):
        return "CAN on %s (nodeid: 0x%x)" % (self.iface, self.nodeid)

    def get_client_id(self):
        return self.txid

    def setup_serialqueue(self, ffi_lib, serialqueue):
        # TUXEDO_RT: Pasar parámetros de autonegociación al chelper
        # canbus_mode mapping: classic=0, fd_no_brs=1, fd_brs=2, xl=3
        mode_map = {'classic': 0, 'fd_no_brs': 1, 'fd_brs': 2, 'xl': 3}
        mode = mode_map.get(self.canbus_mode.lower(), 0)
        
        # Asumiendo que existen estas funciones en el FFI (deberían estar expuestas en conn_manager.c)
        try:
            ffi_lib.conn_set_can_params(serialqueue, mode, self.autoneg_retries, self.xl_sdt)
        except AttributeError:
            logging.warning("chelper: conn_set_can_params not found")


