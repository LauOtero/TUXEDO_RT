#!/usr/bin/env python3
"""TMC Optimizer Motor - Extra de Klipper"""

# Configurar rutas para importación
import sys
sys.path.insert(0, "D:\Mi Mundo\PROYECTOS 3D ACTUALES\PROYECTOS 3D ACTUALES\KLIPPER\EXTRAS\klipper_tmc_octimizer_pro")

# Importar el módulo principal
from tmc_optimizer_motor_main import load_config

def load_config(config):
    """Punto de entrada estándar para extras de Klipper."""
    return load_config(config)
