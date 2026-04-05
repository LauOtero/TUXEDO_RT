import sys
import os

# Add paths
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(base_dir, 'klippy'))

# Mocking Klipper's config and printer objects
class MockConfig:
    def __init__(self):
        self.printer = MockPrinter()
        
    def get_printer(self):
        return self.printer
        
    def getfloat(self, name, default=0.0, above=None, minval=None, maxval=None):
        return default
        
    def getfloatlist(self, name, default=None):
        return default or [0.0, 30.0, 45.0, 60.0, 90.0]
        
    def getint(self, name, default=0, minval=None, maxval=None):
        return default

class MockGCode:
    def register_command(self, cmd, callback, desc=None):
        pass
    def respond_info(self, msg):
        print(f"GCode Info: {msg}")

class MockRTCore:
    def __init__(self):
        self.running = True
    def register_thread(self, name, target, priority=50, is_critical=False):
        print(f"RTCore: Thread '{name}' registered with priority {priority}.")

class MockPrinter:
    def __init__(self):
        self.gcode = MockGCode()
        self.rt_core = MockRTCore()
        self.objects = {
            'gcode': self.gcode,
            'toolhead': None,
            'rt_core': self.rt_core,
            'probe': None
        }
        
    def lookup_object(self, name):
        if name in self.objects:
            return self.objects[name]
        raise Exception(f"Object {name} not found")
        
    class config_error(Exception):
        pass

# Initialize i18n
import i18n
i18n.init_i18n(os.path.join(base_dir, 'klippy', 'locales'))

# Test loading the module
try:
    from extras.pivot_calibrate import load_config
    config = MockConfig()
    pivot_calibrate_module = load_config(config)
    print("Module 'pivot_calibrate' loaded successfully.")
    
    pivot_calibrate_module.initialize()
    print("Module initialized successfully.")
    
    status = pivot_calibrate_module.get_status(None)
    print(f"Status retrieved: {status}")
    
    sys.exit(0)
except Exception as e:
    print(f"Failed to load/initialize module: {e}")
    sys.exit(1)
