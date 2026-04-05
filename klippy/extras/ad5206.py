# AD5206 digipot code
#
# Copyright (C) 2017,2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import bus
from extra_manager import ExtraInterface

class ad5206(ExtraInterface):
    def __init__(self, config):
        super().__init__(config)

    def on_load(self) -> None:
        self.logger = logging.getLogger(self.section_name)
        self.spi = bus.MCU_SPI_from_config(
            self.config, 0, pin_option="enable_pin", default_speed=25000000)
        self.scale = self.config.getfloat('scale', 1., above=0.)
        for i in range(6):
            val = self.config.getfloat('channel_%d' % (i+1,), None,
                                  minval=0., maxval=self.scale)
            if val is not None:
                self.set_register(i, int(val * 256. / self.scale + .5))
    
    def on_start(self) -> None:
        self.logger.info("AD5206 '%s' iniciado.", self.section_name)

    def on_shutdown(self) -> None:
        self.logger.info("Apagando AD5206 '%s' de forma segura...", self.section_name)
        try:
            for i in range(6):
                self.set_register(i, 0) # Establecer todos los canales a 0
        except Exception as e:
            self.logger.error("Error al apagar AD5206 '%s' en shutdown: %s", 
                              self.section_name, e)

    def on_reload(self) -> None:
        self.logger.info("AD5206 '%s' recargado exitosamente. Reaplicando configuración.", self.section_name)
        self.on_load() # Reaplicar la lógica de carga para actualizar la configuración

    def set_register(self, reg, value):
        self.spi.spi_send([reg, value])

Extra = ad5206(True)
