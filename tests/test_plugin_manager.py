import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import configfile

# Añadir el directorio base de klippy al path para que las importaciones funcionen
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(base_dir, 'klippy'))

from extra_manager import ExtraManager, ExtraInterface, LegacyPluginAdapter

class DummyLegacyPlugin:
    def __init__(self, core):
        self.core = core
        self.initialized = False
        
    def initialize(self):
        self.initialized = True
        
    def get_status(self, eventtime):
        return {"status": "legacy_ok"}
        
    def get_dependencies(self):
        return []

class DummyTuxedoPlugin(ExtraInterface):
    def __init__(self, core):
        super().__init__(core)
        self.inited = False
        self.started = False
        self.reloaded = False
        
    def on_init(self):
        self.inited = True
        
    def on_start(self):
        self.started = True
        
    def on_reload(self):
        self.reloaded = True
        
    def get_dependencies(self):
        return ["legacy_plugin"]

class DummyBuilderPlugin(ExtraInterface):
    pass

class TestExtraManager(unittest.TestCase):
    def setUp(self):
        self.core = MagicMock()
        # Simulamos rt_core dentro del core para probar la API declarativa
        self.rt_core = MagicMock()
        self.core.lookup_object.return_value = self.rt_core
        self.core.config_error = configfile.error
        self.pm = ExtraManager(self.core)

    def test_legacy_adapter(self):
        """Prueba que el adaptador Legacy funciona correctamente."""
        legacy = DummyLegacyPlugin(self.core)
        adapter = LegacyPluginAdapter(self.core, legacy)
        
        adapter.on_init()
        self.assertTrue(legacy.initialized)
        
        status = adapter.get_status(0.0)
        self.assertEqual(status, {"status": "legacy_ok"})
        self.assertEqual(adapter.get_dependencies(), [])

    def test_plugin_lifecycle_and_rt_tasks(self):
        """Prueba los hooks de ciclo de vida y la gestión declarativa de tareas RT."""
        plugin = DummyTuxedoPlugin(self.core)
        self.pm.plugins['tuxedo_plugin'] = plugin
        
        # Test hook on_init y on_start
        self.pm.resolve_dependencies_and_init()
        self.assertTrue(plugin.inited)
        
        self.pm.start_all()
        self.assertTrue(plugin.started)
        
        # Test API Declarativa de RT
        plugin.register_rt_task("test_task", lambda: None, priority=60)
        self.rt_core.register_thread.assert_called_once()
        self.assertEqual(len(plugin._rt_threads), 1)
        
        # Test hook on_shutdown y limpieza
        self.pm.shutdown_all()
        self.assertEqual(len(plugin._rt_threads), 0)
        self.assertEqual(len(self.pm.plugins), 0)

    @patch('importlib.import_module')
    def test_load_plugin_legacy_pattern(self, mock_import):
        """Prueba la carga automática de un plugin con patrón antiguo."""
        mock_module = MagicMock()
        mock_module.load_config.return_value = DummyLegacyPlugin(self.core)
        del mock_module.Extra
        del mock_module.create_plugin
        mock_import.return_value = mock_module

        plugin = self.pm.load_plugin('fake.legacy', 'legacy_plugin')
        self.assertIsInstance(plugin, LegacyPluginAdapter)
        self.assertIn('legacy_plugin', self.pm.plugins)

    @patch('importlib.import_module')
    def test_load_plugin_tuxedo_pattern(self, mock_import):
        """Prueba la carga automática de un plugin con interfaz estricta."""
        mock_module = MagicMock()
        mock_module.Extra = DummyTuxedoPlugin
        del mock_module.load_config
        del mock_module.create_plugin
        mock_import.return_value = mock_module

        plugin = self.pm.load_plugin('fake.tuxedo', 'tuxedo_plugin')
        self.assertIsInstance(plugin, DummyTuxedoPlugin)
        self.assertIn('tuxedo_plugin', self.pm.plugins)
        
    @patch('importlib.import_module')
    def test_load_plugin_builder_pattern(self, mock_import):
        """Prueba la carga automática de un plugin con Factory/Builder."""
        mock_module = MagicMock()
        mock_module.create_plugin.return_value = DummyBuilderPlugin(self.core)
        mock_import.return_value = mock_module

        plugin = self.pm.load_plugin('fake.builder', 'builder_plugin')
        self.assertIsInstance(plugin, DummyBuilderPlugin)
    @patch('importlib.import_module')
    def test_load_plugin_legacy_prefix_priority(self, mock_import):
        """Prueba que load_config_prefix tiene prioridad si la sección tiene sufijo."""
        mock_module = MagicMock()
        mock_module.load_config.return_value = DummyLegacyPlugin(self.core)
        mock_module.load_config_prefix.return_value = DummyLegacyPlugin(self.core)
        mock_import.return_value = mock_module

        section_config = MagicMock()
        section_config.get_name.return_value = 'my_legacy foo'

        plugin = self.pm.load_plugin('extras.my_legacy', section_config)

        mock_module.load_config_prefix.assert_called_once_with(section_config)
        self.assertIsInstance(plugin, LegacyPluginAdapter)

    @patch('importlib.import_module')
    def test_load_plugin_extra_class_factory(self, mock_import):
        """Prueba compatibilidad con Extra class + nombre Printer.*"""
        class PrinterSample(ExtraInterface):
            def __init__(self, config):
                super().__init__(config)
        mock_module = MagicMock()
        mock_module.Extra = PrinterSample
        mock_import.return_value = mock_module

        section_config = MagicMock()
        section_config.get_name.return_value = 'printer_sample'

        plugin = self.pm.load_plugin('extras.printer_sample', section_config)

        self.assertIsInstance(plugin, PrinterSample)
    def test_load_object_compatibility(self):
        """Prueba la carga de objetos vía `load_object` combinando antiguo/nuevo."""
        fake_obj = MagicMock()
        self.pm.load_plugin = MagicMock(side_effect=[None, None, fake_obj])

        config = MagicMock()
        config.getsection.return_value = MagicMock()

        obj = self.pm.load_object(config, 'test_module foo')
        self.assertEqual(obj, fake_obj)
        self.pm.load_plugin.assert_any_call('test_module foo', config.getsection('test_module foo'))
        self.pm.load_plugin.assert_any_call('test_module', config.getsection('test_module foo'))
        self.pm.load_plugin.assert_any_call('extras.test_module', config.getsection('test_module foo'))

    def test_load_object_default_fallback(self):
        """Prueba que se devuelve el default cuando no se encuentra el módulo."""
        self.pm.load_plugin = MagicMock(return_value=None)
        config = MagicMock()
        config.getsection.return_value = MagicMock()

        result = self.pm.load_object(config, 'missing_module', default='okay')
        self.assertEqual(result, 'okay')

    def test_load_object_error(self):
        """Prueba que lanza excepción si no existe y no hay default."""
        self.pm.load_plugin = MagicMock(return_value=None)
        config = MagicMock()
        config.getsection.return_value = MagicMock()

        with self.assertRaises(configfile.error):
            self.pm.load_object(config, 'missing_module')

    def test_hot_reload(self):
        """Prueba la recarga en caliente de módulos."""
        plugin = DummyTuxedoPlugin(self.core)
        plugin.register_rt_task("old_task", lambda: None)
        self.pm.plugins['tuxedo'] = plugin
        
        with patch.object(self.pm, 'load_plugin') as mock_load:
            new_plugin = DummyTuxedoPlugin(self.core)
            mock_load.return_value = new_plugin
            
            # Simulamos que el módulo está cargado
            sys.modules['fake.tuxedo'] = MagicMock()
            
            with patch('importlib.reload') as mock_reload:
                res = self.pm.reload_plugin('tuxedo', 'fake.tuxedo')
                
                self.assertTrue(res)
                mock_reload.assert_called_once()
                # Verifica limpieza de la instancia antigua
                self.assertEqual(len(plugin._rt_threads), 0)
                # Verifica hook de reload en la nueva instancia
                self.assertTrue(new_plugin.reloaded)
                self.assertTrue(new_plugin.inited)

    def test_dependency_resolution(self):
        """Prueba la topología de inicialización y detección circular."""
        legacy = DummyLegacyPlugin(self.core)
        tuxedo = DummyTuxedoPlugin(self.core)
        
        self.pm.plugins['legacy_plugin'] = LegacyPluginAdapter(self.core, legacy)
        self.pm.plugins['tuxedo_plugin'] = tuxedo
        
        self.pm.resolve_dependencies_and_init()
        
        self.assertTrue(legacy.initialized)
        self.assertTrue(tuxedo.inited)
        
    def test_get_status(self):
        """Prueba la recopilación de estados para la UI."""
        plugin = DummyTuxedoPlugin(self.core)
        self.pm.plugins['tuxedo'] = plugin
        plugin._status = {"health": "good"}
        
        status = self.pm.get_status(123.45)
        self.assertEqual(status['tuxedo'], {"health": "good"})

if __name__ == '__main__':
    unittest.main()
