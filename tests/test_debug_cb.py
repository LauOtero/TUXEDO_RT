#!/usr/bin/env python3
"""Debug test to verify calc_position_cb is set correctly."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

from chelper import get_ffi
ffi, lib = get_ffi()

print("Allocating stepper...")
sk = lib.five_axis_stepper_alloc(50.0, 0.0, 0.0, 0.0, 100.0, 100.0)
print(f"Stepper allocated: {sk}")

print("\nAllocating struct move...")
m = ffi.new("struct move *")
print(f"Move allocated: {m}")

print("\nInitializing move fields...")
m.start_pos.x = 50.0
m.start_pos.y = 30.0
m.start_pos.z = 10.0
m.move_t = 1000.0
m.start_v = 0.0
m.half_accel = 0.0
print(f"Move initialized: x={m.start_pos.x}, y={m.start_pos.y}, z={m.start_pos.z}")

print("\nCalling itersolve_calc_position_from_coord...")
result = lib.itersolve_calc_position_from_coord(sk, 50.0, 30.0, 10.0)
print(f"Result: {result}")

print("\nTest PASSED!")