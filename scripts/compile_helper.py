#!/usr/bin/env python3
"""
Script para forzar la compilación de c_helper.so usando el mecanismo de Python
Este script llama a get_ffi() que a su vez invoca check_build_c_library()
"""

import sys
import os

# Añadir el directorio klippy al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'klippy'))

try:
    from chelper import get_ffi
    
    print("Iniciando compilación de c_helper.so mediante Python...")
    
    # Esta llamada forzará la compilación ya que get_ffi() llama a check_build_c_library()
    ffi, ffi_lib = get_ffi()
    
    if ffi_lib is not None:
        print("✅ Compilación exitosa! c_helper.so ha sido compilado correctamente.")
        print(f"Biblioteca CFFI cargada: {ffi_lib}")
    else:
        print("❌ Error: No se pudo compilar la biblioteca")
        
except ImportError as e:
    print(f"❌ Error de importación: {e}")
    sys.exit(1)
    
except Exception as e:
    print(f"❌ Error durante la compilación: {e}")
    sys.exit(1)