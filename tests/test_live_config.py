
import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import configparser

# Añadir el path para importar los módulos de klippy
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))

import configfile
from extra_manager import ExtraInterface
from extras.pivot_calibrate import PivotCalibrate

class TestLiveConfigUpdate(unittest.TestCase):
    def setUp(self):
        self.printer = MagicMock()
        self.config_data = "[printer]\nkinematics: cartesian\n"
        self.cfg_file = "test_config.cfg"
        with open(self.cfg_file, 'w') as f:
            f.write(self.config_data)
        
        self.printer.get_start_args.return_value = {'config_file': self.cfg_file}
        self.gcode = MagicMock()
        
        # En Klipper real, PrinterConfig es lo que se registra como 'configfile'
        self.cf_obj = configfile.PrinterConfig(self.printer)
        
        # Inicializar fileconfig para evitar AttributeError
        self.cf_obj.autosave.fileconfig = configparser.RawConfigParser()
        self.cf_obj.autosave.fileconfig.add_section('pivot_calibrate')
        self.cf_obj.autosave.fileconfig.set('pivot_calibrate', 'calibration_tolerance', '0.001')
        self.cf_obj.autosave.fileconfig.set('pivot_calibrate', 'max_pivot_offset', '1000.0')
        
        self.printer.lookup_object.side_effect = lambda obj, default=None: {
            'gcode': self.gcode,
            'configfile': self.cf_obj
        }.get(obj, default)

    def tearDown(self):
        if os.path.exists(self.cfg_file):
            os.remove(self.cfg_file)
        if os.path.exists(self.cfg_file + "_autosave"):
            os.remove(self.cfg_file + "_autosave")
        # Limpiar backups creados durante el test
        import glob
        for f in glob.glob(self.cfg_file + "-*"):
            os.remove(f)

    def test_config_wrapper_cache_invalidation(self):
        # Simular un ConfigWrapper para una sección
        fileconfig = MagicMock()
        fileconfig.has_option.return_value = True
        fileconfig.get.return_value = "original_value"
        
        wrapper = configfile.ConfigWrapper(self.printer, fileconfig, {}, "test_section")
        
        # Primera lectura, debe cachear
        val1 = wrapper.get("test_option")
        self.assertEqual(val1, "original_value")
        self.assertEqual(wrapper._cache[(fileconfig.get, "test_option")], "original_value")
        
        # Notificar cambio a través de PrinterConfig
        self.cf_obj.set("test_section", "test_option", "new_value", notify=True)
        
        # El cache debería estar vacío para esa opción
        self.assertNotIn((fileconfig.get, "test_option"), wrapper._cache)
        
        # Siguiente lectura debería llamar al parser de nuevo
        fileconfig.get.return_value = "new_value"
        val2 = wrapper.get("test_option")
        self.assertEqual(val2, "new_value")

    def test_save_config_no_restart(self):
        # Preparar cambios pendientes
        self.cf_obj.set("test_section", "option1", "value1")
        
        # Simular comando SAVE_CONFIG RESTART=0
        gcmd = MagicMock()
        # Mocking gcmd.get_int to return 0 for RESTART
        gcmd.get_int.side_effect = lambda name, default: 0 if name == 'RESTART' else default
        
        self.cf_obj.autosave.cmd_SAVE_CONFIG(gcmd)
        
        # Verificar que se escribió el archivo
        with open(self.cfg_file, 'r') as f:
            content = f.read()
            self.assertIn("[test_section]", content)
            self.assertIn("option1 = value1", content)
            
        # Verificar que NO se solicitó reinicio
        self.gcode.request_restart.assert_not_called()
        
        # Verificar que se notificaron los cambios
        gcmd.respond_info.assert_any_call("Live update complete. No restart required.")

    def test_pivot_calibrate_live_update(self):
        # Crear un ConfigWrapper para la sección pivot_calibrate manualmente
        config = configfile.ConfigWrapper(self.printer, self.cf_obj.autosave.fileconfig, {}, "pivot_calibrate")
        
        # Instanciar PivotCalibrate
        pc = PivotCalibrate(config)
        pc.on_load()
        
        self.assertEqual(pc.tolerance, 0.001)
        self.assertEqual(pc.quality_validator.tolerance, 0.001)
        
        # Cambiar el valor a través de PrinterConfig.set con notify=True
        new_tolerance = 0.005
        self.cf_obj.set("pivot_calibrate", "calibration_tolerance", str(new_tolerance), notify=True)
        
        # Verificar que se actualizó el estado interno de PivotCalibrate automáticamente
        self.assertEqual(pc.tolerance, 0.005)
        self.assertEqual(pc.quality_validator.tolerance, 0.005)
        self.assertEqual(pc.convergence_checker.primary_tolerance, 0.005)
        self.assertEqual(pc.convergence_checker.secondary_tolerance, 0.025)

if __name__ == '__main__':
    unittest.main()
