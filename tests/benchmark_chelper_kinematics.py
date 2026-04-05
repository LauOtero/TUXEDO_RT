import sys
import time

# Mock objects needed to initialize Klipper C helper
sys.path.insert(0, 'klippy')
import chelper

ffi_main, ffi_lib = chelper.get_ffi()

def benchmark_kinematics(name, alloc_func, *args):
    # Allocate stepper kinematics
    sk = ffi_main.gc(alloc_func(*args), ffi_lib.free)
    
    # We want to benchmark the C function `itersolve_calc_position_from_coord`
    # Klipper exposes:
    # double itersolve_calc_position_from_coord(struct stepper_kinematics *sk, double x, double y, double z);
    # This internally creates a dummy move and calls `calc_position_cb`.
    
    iters = 1000000
    t0 = time.time()
    for _ in range(iters):
        ffi_lib.itersolve_calc_position_from_coord(sk, 10.0, 20.0, 30.0)
    t1 = time.time()
    print(f"{name}: {t1 - t0:.4f}s for {iters} calls")

def main():
    print("Benchmarking C Kinematics (calc_position)...")
    benchmark_kinematics("Cartesian (X)", ffi_lib.cartesian_stepper_alloc, b'x')
    benchmark_kinematics("CoreXY (+)", ffi_lib.corexy_stepper_alloc, b'+')
    benchmark_kinematics("CoreXZ (+)", ffi_lib.corexz_stepper_alloc, b'+')
    benchmark_kinematics("Delta", ffi_lib.delta_stepper_alloc, 100.0, 0.0, 0.0)
    benchmark_kinematics("Deltesian", ffi_lib.deltesian_stepper_alloc, 100.0, 0.0)
    benchmark_kinematics("Extruder", ffi_lib.extruder_stepper_alloc)
    benchmark_kinematics("Generic Cartesian", ffi_lib.generic_cartesian_stepper_alloc, 1.0, 1.0, 0.0)
    benchmark_kinematics("Polar (r)", ffi_lib.polar_stepper_alloc, b'r')
    benchmark_kinematics("Rotary Delta", ffi_lib.rotary_delta_stepper_alloc, 20.0, 20.0, 0.5, 100.0, 100.0)
    benchmark_kinematics("Winch", ffi_lib.winch_stepper_alloc, 10.0, 10.0, 10.0)

if __name__ == "__main__":
    main()
