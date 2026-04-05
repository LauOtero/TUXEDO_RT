# Ejemplo de Notificador Multiidioma para TUXEDO_RT

from extra_manager import ExtraInterface
from i18n import _, init_i18n
import os
import logging

class I18nNotifier(ExtraInterface):
    def __init__(self, config):
        # Inicialización
        super().__init__(config.get_printer())
        self.printer = config.get_printer()
        
        # Cargar traducciones locales del extra
        self.locales_path = os.path.join(os.path.dirname(__file__), 'locales')
        # Si el sistema i18n ya está inicializado, cargará estas automáticamente
        
        # Suscribirse a eventos de la impresora
        self.printer.register_event_handler("klippy:mcu_identify", self._handle_mcu_identify)
        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_mcu_identify(self):
        """MCU identificada. Registrar en logs con el idioma actual."""
        logging.info(_("i18n_notifier.mcu_found"))

    def _handle_connect(self):
        """Conexión establecida."""
        logging.info(_("i18n_notifier.connected"))

    def _handle_ready(self):
        """Impresora lista."""
        # Enviar notificación a la interfaz de usuario (ej. Fluidd/Mainsail)
        msg = _("i18n_notifier.printer_ready")
        self.printer.get_reactor().register_callback(lambda: self.printer.send_event("gcode:respond", msg))

    def get_status(self, eventtime):
        """Estado para Moonraker."""
        return {
            "version": "1.0",
            "locales_loaded": os.path.exists(self.locales_path)
        }

def load_config(config):
    return I18nNotifier(config)
