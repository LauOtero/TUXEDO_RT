# Support for "neopixel" leds, fully integrated with TUXEDO_RT ExtraLifecycle
#
# Copyright (C) 2019-2022  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import led
from extra_manager import ExtraInterface

BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000

BIT_MAX_TIME = 0.000004
RESET_MIN_TIME = 0.000050

MAX_MCU_SIZE = 500  # Sanity check on LED chain length

class PrinterNeoPixel(ExtraInterface):
    """
    Controlador para LEDs NeoPixel, integrado con el ciclo de vida de TUXEDO_RT.
    Hereda de ExtraInterface para una gestión robusta de recargas, 
    inicio y apagado seguro.
    """
    # Permitimos múltiples instancias de neopixel (e.g. [neopixel led1], [neopixel led2])
    allows_multiple_instances = True

    def on_load(self) -> None:
        """Hook del ciclo de vida: Configuración inicial del plugin."""
        # Configurar neopixel
        pin_params = self.pins.lookup_pin(self.config.get('pin'))
        self.mcu = pin_params['chip']
        self.oid = self.mcu.create_oid()
        self.pin = pin_params['pin']
        self.mcu.register_config_callback(self.build_config)
        self.neopixel_update_cmd = self.neopixel_send_cmd = None
        
        # Construir mapa de colores
        chain_count = self.config.getint('chain_count', 1, minval=1)
        color_order = self.config.getlist("color_order", ["GRB"])
        if len(color_order) == 1:
            color_order = [color_order[0]] * chain_count
        if len(color_order) != chain_count:
            raise self.config.error("color_order does not match chain_count")
            
        color_indexes = []
        for lidx, co in enumerate(color_order):
            if sorted(co) not in (sorted("RGB"), sorted("RGBW")):
                raise self.config.error(f"Invalid color_order '{co}'")
            color_indexes.extend([(lidx, "RGBW".index(c)) for c in co])
            
        self.color_map = list(enumerate(color_indexes))
        if len(self.color_map) > MAX_MCU_SIZE:
            raise self.config.error("neopixel chain too long")
            
        # Inicializar los datos de color
        self.led_helper = led.LEDHelper(self.config, self.update_leds, chain_count)
        self.color_data = bytearray(len(self.color_map))
        self.update_color_data(self.led_helper.get_status()['color_data'])
        self.old_color_data = bytearray([d ^ 1 for d in self.color_data])
        
        # Registrar evento clásico de Klipper como fallback/compatibilidad
        self.printer.register_event_handler("klippy:connect", self._handle_connect)

    def on_start(self) -> None:
        """Hook del ciclo de vida: Llamado cuando todos los plugins están listos."""
        self.logger.info("NeoPixel '%s' iniciado (pin: %s, LEDs: %d)", 
                         self.section_name, self.pin, len(self.color_map) // 3)

    def on_shutdown(self) -> None:
        """Hook del ciclo de vida: Llamado antes de detener el plugin.
        Se asegura de apagar los LEDs para evitar que se queden encendidos."""
        self.logger.info("Apagando NeoPixel '%s' de forma segura...", self.section_name)
        try:
            # Poner todos los colores a 0 (apagado)
            for i in range(len(self.color_data)):
                self.color_data[i] = 0
            self.send_data()
        except Exception as e:
            self.logger.error("Error al apagar NeoPixel '%s' en shutdown: %s", 
                              self.section_name, e)

    def on_reload(self) -> None:
        """Hook del ciclo de vida: Llamado tras una recarga en caliente."""
        self.logger.info("NeoPixel '%s' recargado exitosamente.", self.section_name)

    def _handle_connect(self):
        """Callback de conexión de Klipper. Envia los datos iniciales al MCU."""
        self.send_data()

    def build_config(self):
        """Callback del MCU para construir la configuración inicial del hardware."""
        bmt = self.mcu.seconds_to_clock(BIT_MAX_TIME)
        rmt = self.mcu.seconds_to_clock(RESET_MIN_TIME)
        self.mcu.add_config_cmd("config_neopixel oid=%d pin=%s data_size=%d"
                                " bit_max_ticks=%d reset_min_ticks=%d"
                                % (self.oid, self.pin, len(self.color_data),
                                   bmt, rmt))
        cmd_queue = self.mcu.alloc_command_queue()
        self.neopixel_update_cmd = self.mcu.lookup_command(
            "neopixel_update oid=%c pos=%hu data=%*s", cq=cmd_queue)
        self.neopixel_send_cmd = self.mcu.lookup_query_command(
            "neopixel_send oid=%c", "neopixel_result oid=%c success=%c",
            oid=self.oid, cq=cmd_queue)

    def update_color_data(self, led_state):
        """Actualiza el buffer interno de colores."""
        color_data = self.color_data
        for cdidx, (lidx, cidx) in self.color_map:
            color_data[cdidx] = int(led_state[lidx][cidx] * 255. + 0.5)

    def send_data(self, print_time=None):
        """Envia el buffer de colores al MCU, optimizando las transferencias por lotes."""
        old_data, new_data = self.old_color_data, self.color_data
        if new_data == old_data:
            return
            
        # Buscar la posición de todos los bytes que han cambiado
        diffs = [[i, 1] for i, (n, o) in enumerate(zip(new_data, old_data)) if n != o]
                 
        # Agrupar cambios que están cerca para reducir comandos al MCU
        for i in range(len(diffs)-2, -1, -1):
            pos, count = diffs[i]
            nextpos, nextcount = diffs[i+1]
            if pos + 5 >= nextpos and nextcount < 16:
                diffs[i][1] = nextcount + (nextpos - pos)
                del diffs[i+1]
                
        # Transmitir cambios
        ucmd = self.neopixel_update_cmd.send
        for pos, count in diffs:
            ucmd([self.oid, pos, new_data[pos:pos+count]],
                 reqclock=BACKGROUND_PRIORITY_CLOCK)
                 
        old_data[:] = new_data
        
        # Instruir al MCU para actualizar físicamente los LEDs
        minclock = 0
        if print_time is not None:
            minclock = self.mcu.print_time_to_clock(print_time)
            
        scmd = self.neopixel_send_cmd.send
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
            
        # Intentar hasta 8 veces para asegurar la recepción
        for _ in range(8):
            params = scmd([self.oid], minclock=minclock,
                          reqclock=BACKGROUND_PRIORITY_CLOCK)
            if params['success']:
                break
        else:
            self.logger.warning("NeoPixel '%s': La actualización no fue exitosa", self.section_name)

    def update_leds(self, led_state, print_time):
        """Llamado por LEDHelper para aplicar un nuevo estado."""
        self.update_color_data(led_state)
        self.send_data(print_time)

    def get_status(self, eventtime=None):
        """Retorna el estado actual del LED."""
        return self.led_helper.get_status(eventtime)


# Exportamos Extra para el ExtraManager moderno (Factory Pattern TUXEDO_RT)
Extra = PrinterNeoPixel(True) # True = prefix

