#!/usr/bin/env python3
"""Debug test to verify calc_position_cb works with many iterations."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'klippy'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

from chelper import get_ffi
ffi, lib = get_ffi()

print("Allocating stepper...")
sk = lib.five_axis_stepper_alloc(50.0, 0.0, 0.0, 0.0, 100.0, 100.0)
print(f"Stepper allocated: {sk}")

ITERATIONS = 100000

print(f"\nCalling itersolve_calc_position_from_coord {ITERATIONS} times...")
for i in range(ITERATIONS):
    result = lib.itersolve_calc_position_from_coord(sk, 50.0, 30.0, 10.0)
    if i % 10000 == 0:
        print(f"  Iteration {i}: result = {result}")

print(f"\nFinal result: {result}")
print("\nTest PASSED!")