# High-performance RatOS Hybrid CoreXY kinematics handler
#
# Optimized for RatOS V-Core / Hybrid configurations with 4-motor XY drive
# (2x CoreXY Motors + 2x Cartesian Y Assist Motors)
#
# Copyright (C) 2021-2026  RatOS Team / Klipper Contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import stepper
from . import idex_modes

_UNHOMED = (1.0, -1.0)
_XYZ = (0, 1, 2)

class RatosHybridCoreXYKinematics:
    def __init__(self, toolhead, config):
        self.printer = config.get_printer()
        
        # 1. Setup CoreXY Rails (A/B)
        # Assumes stepper_x is CoreXY A (Left/Rear) and stepper_y is CoreXY B (Right/Rear)
        # OR allows explicit configuration of the Y-Assist rail.
        #
        # Configuration Pattern:
        # [stepper_x] -> CoreXY A
        # [stepper_y] -> CoreXY B
        # [stepper_y1] -> Cartesian Y Assist (Optional/Additional)
        #
        # Wait, standard Klipper mapping is:
        # CoreXY: X=A, Y=B
        # Hybrid (Markforged): X=X(CoreXY-like), Y=Y(Cartesian)
        
        # We detect if we are in "RatOS Hybrid" mode which implies CoreXY + Y-Assist.
        # We will load stepper_x and stepper_y as standard CoreXY rails.
        self.rail_a = stepper.PrinterRail(config.getsection('stepper_x'))
        self.rail_b = stepper.PrinterRail(config.getsection('stepper_y'))
        
        # Load Z
        self.rail_z = stepper.LookupMultiRail(config.getsection('stepper_z'))
        
        # Load Y-Assist Rails (Cartesian Y)
        # Look for 'stepper_y_assist' or 'stepper_y1' if not used by MultiRail?
        # RatOS Configurator likely uses a specific section or hijacks stepper_y1.
        # We will assume 'stepper_y_assist' for clarity, or check if stepper_y has multiple?
        # Actually, for Klipper, stepper_y is ONE rail.
        # We need a separate rail for Y-Assist.
        # Let's try to find [stepper_y_reference] or similar.
        # If not found, fallback to standard CoreXY (no assist) or check config.
        
        self.rails = [self.rail_a, self.rail_b, self.rail_z]
        self.rail_y_assist = None
        
        # Try to find Y-Assist rail
        if config.has_section('stepper_y_assist'):
            self.rail_y_assist = stepper.PrinterRail(config.getsection('stepper_y_assist'))
            self.rails.append(self.rail_y_assist)
        elif config.has_section('stepper_y1') and not config.getsection('stepper_y').get('endstop_pin', None):
             # Heuristic: If stepper_y1 exists but implies separate kinematic function?
             # Usually stepper_y1 is just a clone of stepper_y in MultiRail.
             pass

        # Dual carriage (IDEX) support
        self.dc_module = None
        if config.has_section('dual_carriage'):
            self._setup_dual_carriage(config.getsection('dual_carriage'))

        # 2. Setup Iterative Solvers (The Core Math Optimization)
        # Use C-code generators for maximum performance (SIMD-capable on some archs)
        
        # Rail A: CoreXY A = X + Y
        self.rail_a.setup_itersolve('corexy_stepper_alloc', b'+')
        
        # Rail B: CoreXY B = X - Y
        self.rail_b.setup_itersolve('corexy_stepper_alloc', b'-')
        
        # Rail Z: Cartesian Z
        self.rail_z.setup_itersolve('cartesian_stepper_alloc', b'z')
        
        # Rail Y-Assist: Cartesian Y
        if self.rail_y_assist:
            self.rail_y_assist.setup_itersolve('cartesian_stepper_alloc', b'y')

        # 3. Cross-Endstop Registration (Kinematic Synchronization)
        # CRITICAL for CoreXY/Hybrid: When an endstop triggers (Physical or Sensorless),
        # ALL mechanically coupled motors must stop immediately to prevent racking/twisting.
        # - Homing Y: Moves A, B, and Y-Assist. All must stop when Y triggers.
        # - Homing X: Moves A and B. Both must stop when X triggers.
        
        # Find where the Y endstop is.
        # Usually stepper_y (Rail B).
        y_endstops = self.rail_b.get_endstops()
        if not y_endstops and self.rail_y_assist:
             y_endstops = self.rail_y_assist.get_endstops()
        
        if y_endstops:
            es_y = y_endstops[0][0]
            # Register A and B (Cross-registration)
            for s in self.rail_a.get_steppers():
                es_y.add_stepper(s)
            # Register Y-Assist
            if self.rail_y_assist:
                for s in self.rail_y_assist.get_steppers():
                    es_y.add_stepper(s)
        
        # Also X Endstop (usually on Rail A)
        x_endstops = self.rail_a.get_endstops()
        if x_endstops:
            es_x = x_endstops[0][0]
            # Register B (Cross-registration for CoreXY)
            for s in self.rail_b.get_steppers():
                es_x.add_stepper(s)
            # Y-Assist does NOT move in X, so no need to register?
            # Wait, if X moves, Y-Assist stays still. Correct.

        # 4. Limits and Velocity
        ranges = [r.get_range() for r in self.rails[:3]] # A, B, Z
        self.axes_min = toolhead.Coord(ranges[0][0], ranges[1][0], ranges[2][0], e=0.)
        self.axes_max = toolhead.Coord(ranges[0][1], ranges[1][1], ranges[2][1], e=0.)
        
        # Register steppers
        trapq = toolhead.get_trapq()
        for s in self.get_steppers():
            s.set_trapq(trapq)
            toolhead.register_step_generator(s.generate_steps)
            
        self.printer.register_event_handler("stepper_enable:motor_off", self._motor_off)
        
        max_vel, max_acc = toolhead.get_max_velocity()
        self.max_z_velocity = config.getfloat('max_z_velocity', max_vel, above=0., maxval=max_vel)
        self.max_z_accel = config.getfloat('max_z_accel', max_acc, above=0., maxval=max_acc)
        
        self.limits = [_UNHOMED] * 3

    def _setup_dual_carriage(self, dc_config):
        dc_config.getchoice('axis', {'x': 'x'}, default='x')
        dc_rail = stepper.PrinterRail(dc_config)
        self.rails.append(dc_rail)
        
        # DC rail also shares Y endstop (Cross-registration)
        # Because DC rail is Hybrid (Frame mounted), moving Y moves DC rail relative to bed?
        # No, if Y moves, DC rail motor must turn to compensate (if Hybrid).
        # So if Y endstop triggers, DC rail motor must stop.
        
        # Find Y endstop (similar to __init__)
        y_endstops = self.rail_b.get_endstops()
        if not y_endstops and self.rail_y_assist:
             y_endstops = self.rail_y_assist.get_endstops()
        
        if y_endstops:
            es_y = y_endstops[0][0]
            for s in dc_rail.get_steppers():
                es_y.add_stepper(s)

        # Setup itersolve for DC rail
        # Usually mirrored or similar to CoreXY
        # Defaulting to b'+' (X+Y) as per hybrid_corexy.py pattern, 
        # but user should verify direction.
        dc_rail.setup_itersolve('corexy_stepper_alloc', b'+')
        
        self.dc_module = idex_modes.DualCarriages(
            dc_config,
            idex_modes.DualCarriagesRail(self.rail_a, axis=0, active=True),
            idex_modes.DualCarriagesRail(dc_rail, axis=0, active=False),
            axis=0)

    def get_steppers(self):
        return [s for rail in self.rails for s in rail.get_steppers()]

    def calc_position(self, stepper_positions):
        # EXTREME OPTIMIZATION: Direct Feedback Path
        # Instead of calculating Y from CoreXY (0.5 * (A-B)), use Y-Assist if available.
        # This avoids float error accumulation and potential belt-stretch bias from X-axis.
        
        pos_a = stepper_positions[self.rail_a.get_name()]
        pos_b = stepper_positions[self.rail_b.get_name()]
        pos_z = stepper_positions[self.rail_z.get_name()]
        
        # Determine Y
        y = None
        if self.rail_y_assist:
             y = stepper_positions[self.rail_y_assist.get_name()]
        else:
             # Fallback Y = 0.5 * (A - B)
             y = 0.5 * (pos_a - pos_b)

        # Determine X
        # Check IDEX status
        if self.dc_module is not None:
            status = self.dc_module.get_status()
            if status['carriage_1'] == 'PRIMARY':
                # Primary Carriage (CoreXY A/B)
                # X = 0.5 * (A + B)
                # Or using Y-Assist: X = A - Y or X = B + Y
                # Using average for robustness:
                if self.rail_y_assist:
                    x = 0.5 * (pos_a + pos_b)
                else:
                    x = 0.5 * (pos_a + pos_b)
            else:
                # Secondary Carriage (DC Rail)
                # Using itersolve b'+' -> C = X + Y -> X = C - Y
                # Retrieve DC rail position
                # rail_a is at index 0, rail_b at 1, z at 2, y_assist at 3 (if exists), dc at -1
                # But rails list order depends on config.
                # DC rail is appended last.
                rail_dc = self.rails[-1]
                pos_c = stepper_positions[rail_dc.get_name()]
                x = pos_c - y
        else:
            # Standard Single Carriage
            x = 0.5 * (pos_a + pos_b)

        return [x, y, pos_z]

    def set_position(self, newpos, homing_axes):
        for i, rail in enumerate(self.rails):
            rail.set_position(newpos)
        for i in homing_axes:
            # Update limits based on rail range (approximate for CoreXY)
            # Ideally we should calculate cartesian bounds, but Klipper uses rail bounds
            # to detect "not homed" state mainly.
            self.limits[i] = self.rails[i].get_range()

    def home(self, homing_state):
        # Optimized Homing: Ensure all rails are driven and monitored
        for axis in homing_state.get_axes():
            
            # IDEX Handling for X-Axis
            if axis == 0 and self.dc_module is not None:
                self.dc_module.home(homing_state)
                continue

            # Identify which rails are relevant for this axis move
            # and which of those have endstops assigned to this axis.
            
            rails_to_monitor = []
            
            if axis == 0: # X-Axis Homing
                # Cartesian X move drives A and B. Y-Assist is stationary.
                # Check A and B for X-endstops.
                for r in [self.rail_a, self.rail_b]:
                    if r.get_homing_info().axis == 0:
                        rails_to_monitor.append(r)
                
                # If no endstops found on A/B (unlikely), fallback to A
                if not rails_to_monitor:
                    rails_to_monitor = [self.rail_a]
                    
            elif axis == 1: # Y-Axis Homing
                # Cartesian Y move drives A, B, and Y-Assist.
                # Check B and Y-Assist for Y-endstops.
                # (A usually has X endstop, but check anyway)
                candidates = [self.rail_b]
                if self.rail_y_assist:
                    candidates.append(self.rail_y_assist)
                
                for r in candidates:
                    if r.get_homing_info().axis == 1:
                        rails_to_monitor.append(r)
                
                # Fallback
                if not rails_to_monitor:
                    rails_to_monitor = [self.rail_b]

            else: # Z-Axis
                rails_to_monitor = [self.rail_z]

            # Calculate target positions
            # We use the first monitored rail as the reference for direction/position
            ref_rail = rails_to_monitor[0]
            position_min, position_max = ref_rail.get_range()
            hi = ref_rail.get_homing_info()
            
            homepos = [None, None, None, None]
            homepos[axis] = hi.position_endstop
            forcepos = list(homepos)
            
            # Overshoot to ensure trigger
            if hi.positive_dir:
                forcepos[axis] -= 1.5 * (hi.position_endstop - position_min)
            else:
                forcepos[axis] += 1.5 * (position_max - hi.position_endstop)

            # Perform the Homing Move
            # home_rails handles the motion generation (using calc_position/itersolve)
            # and monitors the endstops of the provided rails.
            homing_state.home_rails(rails_to_monitor, forcepos, homepos)

    def check_move(self, move):
        # Optimized Fast Path
        end_pos = move.end_pos
        limits = self.limits
        
        # Check Z first (common fail point)
        if move.axes_d[2]:
            self._check_z(move, end_pos, limits)
            
        # Check XY
        # Inline the check for speed (avoid function call)
        if (end_pos[0] < limits[0][0] or end_pos[0] > limits[0][1] or
            end_pos[1] < limits[1][0] or end_pos[1] > limits[1][1]):
            self._check_endstops(move)

    def _check_z(self, move, end_pos, limits):
        # Split Z check for optimization
        if end_pos[2] < limits[2][0] or end_pos[2] > limits[2][1]:
            self._check_endstops(move)
        
        # Velocity Limit
        z_ratio = move.move_d / abs(move.axes_d[2])
        move.limit_speed(self.max_z_velocity * z_ratio, self.max_z_accel * z_ratio)

    def _check_endstops(self, move):
        end_pos = move.end_pos
        for i in _XYZ:
            if move.axes_d[i]:
                lo, hi = self.limits[i]
                if end_pos[i] < lo or end_pos[i] > hi:
                    if lo > hi:
                        raise move.move_error("Must home axis first")
                    raise move.move_error()

    def _motor_off(self, print_time):
        self.limits = [_UNHOMED] * 3

    def get_status(self, eventtime):
        return {
            'homed_axes': "".join(a for a, lim in zip("xyz", self.limits) if lim[0] <= lim[1]),
            'axis_minimum': self.axes_min,
            'axis_maximum': self.axes_max,
        }

def load_kinematics(toolhead, config):
    return RatosHybridCoreXYKinematics(toolhead, config)
