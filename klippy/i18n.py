import json
import os
import threading
import logging

class I18nSystem:
    """
    Sistema de internacionalización (i18n) con soporte de recarga en caliente,
    detección de idioma y manejo de recursos externos (archivos JSON).
    """
    def __init__(self, locales_dir="locales", default_lang="en"):
        self.locales_dir = locales_dir
        self.default_lang = default_lang
        self.current_lang = default_lang
        self.translations = {}
        self.lock = threading.Lock()
        self.logger = logging.getLogger('I18nSystem')
        
        self.load_translations(self.default_lang)

    def load_translations(self, lang_code):
        """Carga los recursos de un idioma desde el directorio 'locales' y 'extras/locales'."""
        filepath = os.path.join(self.locales_dir, f"{lang_code}.json")
        extras_locales_dir = os.path.join(os.path.dirname(self.locales_dir), "extras", "locales")
        merged_data = {}
        
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    merged_data.update(json.load(f))
            else:
                self.logger.warning(f"Archivo de idioma principal no encontrado: {filepath}. Usando fallback o llaves base.")
                
            # Cargar traducciones de los extras
            if os.path.exists(extras_locales_dir):
                for filename in os.listdir(extras_locales_dir):
                    if filename.endswith(f".{lang_code}.json"):
                        extra_filepath = os.path.join(extras_locales_dir, filename)
                        try:
                            with open(extra_filepath, 'r', encoding='utf-8') as f:
                                extra_data = json.load(f)
                                # Mezclar recursivamente o a nivel superior
                                for k, v in extra_data.items():
                                    if k in merged_data and isinstance(merged_data[k], dict) and isinstance(v, dict):
                                        merged_data[k].update(v)
                                    else:
                                        merged_data[k] = v
                        except Exception as e:
                            self.logger.error(f"Error cargando archivo de idioma de extra '{extra_filepath}': {e}")
                            
            with self.lock:
                self.translations = merged_data
                self.current_lang = lang_code
                self.logger.info(f"Idioma '{lang_code}' cargado exitosamente (incluyendo extras).")
        except Exception as e:
            self.logger.error(f"Error general cargando idioma '{lang_code}': {e}")

    def hot_reload(self, new_lang=None):
        """Recarga en caliente el idioma actual o cambia a uno nuevo sin reiniciar el sistema."""
        lang_to_load = new_lang if new_lang else self.current_lang
        self.logger.info(f"Recargando traducciones (Hot Reload) para el idioma: {lang_to_load}")
        self.load_translations(lang_to_load)

    def get_text(self, key, **kwargs):
        """
        Obtiene el texto traducido y lo formatea.
        Soporta claves anidadas separadas por punto (ej: 'errors.motor_stalled').
        """
        keys = key.split('.')
        with self.lock:
            current_dict = self.translations
            for k in keys:
                if isinstance(current_dict, dict) and k in current_dict:
                    current_dict = current_dict[k]
                else:
                    return f"[{key}]" # Fallback si no existe la clave
            
            # Si se encuentran argumentos, formatear
            if isinstance(current_dict, str):
                try:
                    return current_dict.format(**kwargs)
                except KeyError as e:
                    self.logger.warning(f"Falta el argumento de formato {e} en la traducción '{key}'")
                    return current_dict
            return str(current_dict)

    def format_number(self, number, format_type="decimal"):
        """Manejo de formatos regionales para números (simplificado)."""
        # Dependiendo del current_lang, se pueden aplicar separadores de miles/decimales específicos
        if self.current_lang == "es":
            return f"{number:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        return f"{number:,.2f}"

# Ejemplo de función global _() para facilitar el uso en el código
i18n_instance = None

def init_i18n(locales_dir):
    global i18n_instance
    i18n_instance = I18nSystem(locales_dir)

def _(key, **kwargs):
    if i18n_instance:
        return i18n_instance.get_text(key, **kwargs)
    return key
