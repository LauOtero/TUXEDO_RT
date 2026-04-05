import sys
import time
import os
import random
import math

# Añadir el directorio klippy al path para importar chelper
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'klippy')))
import chelper

# Función de benchmarking genérica
def benchmark_function(name, func, iterations, *args):
    # Warm-up
    func(*args)

    start_time = time.perf_counter()
    for _ in range(iterations):
        func(*args)
    end_time = time.perf_counter()

    elapsed_time = end_time - start_time
    avg_time = elapsed_time / iterations

    if avg_time < 1e-9:
        unit_str = f"{avg_time * 1e9:.2f}ns"
    elif avg_time < 1e-6:
        unit_str = f"{avg_time * 1e6:.2f}μs"
    elif avg_time < 1e-3:
        unit_str = f"{avg_time * 1e3:.2f}ms"
    else:
        unit_str = f"{avg_time:.2f}s"

    print(f"  {name}: {unit_str}/iter")
    return avg_time

# Callback de cinemática cartesiana simulada para itersolve
# Esta función simula el comportamiento de kin_cartesian.c
# Se definirá como una función Python normal y se convertirá a callback C en tiempo de ejecución.
def dummy_cartesian_calc_position_py(sk, m, move_time):
    # En una cinemática cartesiana simple, la posición del motor es directamente
    # la coordenada del eje correspondiente.
    # Para simplificar el benchmark, solo necesitamos un cálculo básico.
    # Aquí simulamos un cálculo de posición para el eje X.
    # La lógica real de Klipper es más compleja, pero para el benchmark de itersolve
    # solo necesitamos que el callback devuelva un valor numérico.
    move_dist = (m.start_v + m.half_accel * move_time) * move_time
    return m.start_pos.x + m.axes_r.x * move_dist

def run_itersolve_benchmarks():
    print("=== Benchmarking C Itersolve Functions ===")

    ffi_main, ffi_lib = chelper.get_ffi()
    if ffi_lib is None:
        print("Error: No se pudo cargar la biblioteca C. Asegúrate de que GCC esté disponible y el módulo C se compile correctamente.")
        return

    # --- Definiciones CFFI adicionales no incluidas en chelper/__init__.py ---
    # Solo definimos lo que no está ya en defs_all de chelper.
    # Las definiciones de itersolve.h (enum AF_X, AF_Y, etc. y struct stepper_kinematics)
    # ya deberían estar en chelper.FFI_main.C después de la última modificación.
    # Por lo tanto, esta sección puede estar vacía o contener solo definiciones muy específicas
    # que no estén en chelper/__init__.py.
    # Para este caso, no necesitamos añadir nada aquí, ya que las constantes AF_X, etc.
    # y la estructura stepper_kinematics ya están en chelper.FFI_main.C
    ffi_main.cdef("""
        // Las definiciones de itersolve.h (enum AF_X, AF_Y, etc. y struct stepper_kinematics)
        // ya deberían estar en chelper.FFI_main.C después de la última modificación.
        // Por lo tanto, esta sección puede estar vacía o contener solo definiciones muy específicas
        // que no estén en chelper/__init__.py.
        // Para este caso, no necesitamos añadir nada aquí, ya que las constantes AF_X, etc.
        // y la estructura stepper_kinematics ya están en chelper.FFI_main.C
    """)

    # --- Simulación de datos de entrada ---
    # Crear instancias de las estructuras C
    sk = ffi_main.new("struct stepper_kinematics *")
    print(f"sk: {sk}")
    print(f"Tamaño de struct list_node (CFFI): {ffi_main.sizeof('struct list_node')} bytes")
    print(f"Tamaño de struct list_head (CFFI): {ffi_main.sizeof('struct list_head')} bytes")
    print(f"Offset de move.node (CFFI): {ffi_main.offsetof('struct move', 'node')} bytes")
    tq = ffi_lib.trapq_alloc()
    print(f"tq: {tq}")
    sc = ffi_lib.stepcompress_alloc(ffi_main.NULL) # msg_queue puede ser NULL para este benchmark
    print(f"sc: {sc}")

    # Inicializar stepper_kinematics
    sk.step_dist = 0.0025 # Ejemplo: 1/400 mm por paso
    sk.commanded_pos = 0.0
    sk.sc = sc
    sk.last_flush_time = 0.0
    sk.last_move_time = 0.0
    sk.tq = tq
    
    # Acceder a las constantes AF_X, etc. a través de ffi_lib
    sk.active_flags = ffi_main.cast("int", ffi_lib.AF_X) # Solo eje X activo
    sk.gen_steps_pre_active = 0.0
    sk.gen_steps_post_active = 0.0
    # Asignar el callback simulado después de que ffi_main esté disponible
    sk.calc_position_cb = ffi_main.callback("double(struct stepper_kinematics *sk, struct move *m, double move_time)", dummy_cartesian_calc_position_py)

    # Inicializar stepcompress
    ffi_lib.stepcompress_fill(sc, 0, 0, 0, 0) # oid, max_error, msgtags pueden ser 0 para el benchmark

    # Añadir un movimiento al trapq usando la API de alto nivel
    # void trapq_append(struct trapq *tq, double print_time
    #                   , double accel_t, double cruise_t, double decel_t
    #                   , double start_pos_x, double start_pos_y, double start_pos_z
    #                   , double axes_r_x, double axes_r_y, double axes_r_z
    #                   , double start_v, double cruise_v, double accel);
    ffi_lib.trapq_append(tq, 0.0,  # print_time
                         0.0, 1.0, 0.0,  # accel_t, cruise_t, decel_t
                         0.0, 0.0, 0.0,  # start_pos_x, start_pos_y, start_pos_z
                         100.0, 0.0, 0.0, # axes_r_x, axes_r_y, axes_r_z
                         0.0, 100.0, 0.0) # start_v, cruise_v, accel


    # Establecer trapq en stepper_kinematics
    ffi_lib.itersolve_set_trapq(sk, tq, sk.step_dist)
    ffi_lib.itersolve_set_position(sk, 0.0, 0.0, 0.0) # Resetear posición inicial

    # --- Benchmarks ---
    print("\n--- Itersolve_generate_steps Benchmarks ---")
    # Simular la generación de pasos para un movimiento
    # Iteraciones bajas porque itersolve_generate_steps es intensivo
    benchmark_function("itersolve_generate_steps (1s move)", ffi_lib.itersolve_generate_steps, 100, sk, sc, 1.0)

    print("\n--- Itersolve_calc_position_from_coord Benchmarks ---")
    # Simular el cálculo de posición a partir de coordenadas
    benchmark_function("itersolve_calc_position_from_coord (0,0,0)", ffi_lib.itersolve_calc_position_from_coord, 100000, sk, 0.0, 0.0, 0.0)
    benchmark_function("itersolve_calc_position_from_coord (10,20,30)", ffi_lib.itersolve_calc_position_from_coord, 100000, sk, 10.0, 20.0, 30.0)

    # Liberar memoria C
    ffi_lib.trapq_free(tq)
    ffi_lib.stepcompress_free(sc)


if __name__ == '__main__':
    run_itersolve_benchmarks()
