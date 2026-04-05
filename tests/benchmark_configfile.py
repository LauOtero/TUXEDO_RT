
import time
import sys
import os
from unittest.mock import MagicMock
import logging

# Mock Klipper environment
sys.modules['logging'] = MagicMock()

# Import the ConfigFileReader from the local file
import importlib.util
spec = importlib.util.spec_from_file_location("configfile", "klippy/configfile.py")
configfile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configfile)

def create_mock_config(num_sections=100, options_per_section=20, num_includes=5):
    config_dir = "tests/mock_configs"
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)
    
    main_cfg = os.path.abspath(os.path.join(config_dir, "printer.cfg"))
    with open(main_cfg, "w") as f:
        for i in range(num_includes):
            inc_name = f"include_{i}.cfg"
            inc_path = os.path.abspath(os.path.join(config_dir, inc_name))
            f.write(f"[include {inc_name}]\n")
            with open(inc_path, "w") as inc_f:
                for j in range(num_sections // num_includes):
                    inc_f.write(f"[section_{i}_{j}]\n")
                    for k in range(options_per_section):
                        inc_f.write(f"option_{k}: {k}\n")
                    inc_f.write("\n")
    
    return main_cfg

def run_benchmark():
    print("Generating mock configuration...")
    main_cfg = create_mock_config(num_sections=500, options_per_section=30, num_includes=10)
    
    reader = configfile.ConfigFileReader()
    
    print(f"Benchmarking ConfigFileReader.build_fileconfig_with_includes with {main_cfg}...")
    
    # Read raw data first to isolate parsing
    with open(main_cfg, 'r') as f:
        data = f.read()
    
    iterations = 5
    start_time = time.time()
    for i in range(iterations):
        # We need to simulate the environment where filename is used for relative includes
        reader.build_fileconfig_with_includes(data, main_cfg)
    end_time = time.time()
    
    avg_time = (end_time - start_time) / iterations
    print(f"Average time to build config with includes: {avg_time:.4f}s")
    
    # Benchmark ConfigWrapper access
    print("Benchmarking ConfigWrapper access...")
    cf = reader.build_fileconfig_with_includes(data, main_cfg)
    printer = MagicMock()
    access_tracking = {}
    wrapper = configfile.ConfigWrapper(printer, cf, access_tracking, "section_0_0")
    
    iterations = 100000
    start_time = time.time()
    for i in range(iterations):
        wrapper.get("option_0")
        wrapper.getint("option_1", 0)
    end_time = time.time()
    
    avg_access = (end_time - start_time) / iterations
    print(f"Average time per option access (ConfigWrapper): {avg_access*1000000:.2f}us")

if __name__ == "__main__":
    run_benchmark()
