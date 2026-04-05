# Code for handling the kinematics of corexy robots
#
# Copyright (C) 2017-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math
import stepper

class CoreXYKinematics:
    def __init__(self, toolhead, config):
        # Setup axis rails
        self.rails = [stepper.LookupMultiRail(config.getsection('stepper_' + n))
                      for n in 'xyz']
        for s in self.rails[1].get_steppers():
            self.rails[0].get_endstops()[0][0].add_stepper(s)
        for s in self.rails[0].get_steppers():
            self.rails[1].get_endstops()[0][0].add_stepper(s)
        self.rails[0].setup_itersolve('corexy_stepper_alloc', b'+')
        self.rails[1].setup_itersolve('corexy_stepper_alloc', b'-')
        self.rails[2].setup_itersolve('cartesian_stepper_alloc', b'z')
        for s in self.get_steppers():
            s.set_trapq(toolhead.get_trapq())
        # Setup boundary checks
        max_velocity, max_accel = toolhead.get_max_velocity()
        self.max_z_velocity = config.getfloat(
            'max_z_velocity', max_velocity, above=0., maxval=max_velocity)
        self.max_z_accel = config.getfloat(
            'max_z_accel', max_accel, above=0., maxval=max_accel)
        self.limits = [(1.0, -1.0)] * 3
        ranges = [r.get_range() for r in self.rails]
        self.axes_min = toolhead.Coord([r[0] for r in ranges])
        self.axes_max = toolhead.Coord([r[1] for r in ranges])
        self.rail_names = [r.get_name() for r in self.rails]
    def get_steppers(self):
        return [s for rail in self.rails for s in rail.get_steppers()]
    def calc_position(self, stepper_positions):
        names = self.rail_names
        p0 = stepper_positions[names[0]]
        p1 = stepper_positions[names[1]]
        p2 = stepper_positions[names[2]]
        return [0.5 * (p0 + p1), 0.5 * (p0 - p1), p2]
    def set_position(self, newpos, homing_axes):
        for i, rail in enumerate(self.rails):
            rail.set_position(newpos)
            if "xyz"[i] in homing_axes:
                self.limits[i] = rail.get_range()
    def clear_homing_state(self, clear_axes):
        for axis, axis_name in enumerate("xyz"):
            if axis_name in clear_axes:
                self.limits[axis] = (1.0, -1.0)
    def home(self, homing_state):
        # Each axis is homed independently and in order
        for axis in homing_state.get_axes():
            rail = self.rails[axis]
            # Determine movement
            position_min, position_max = rail.get_range()
            hi = rail.get_homing_info()
            homepos = [None, None, None, None]
            homepos[axis] = hi.position_endstop
            forcepos = list(homepos)
            if hi.positive_dir:
                forcepos[axis] -= 1.5 * (hi.position_endstop - position_min)
            else:
                forcepos[axis] += 1.5 * (position_max - hi.position_endstop)
            # Perform homing
            homing_state.home_rails([rail], forcepos, homepos)
    def _check_endstops(self, move):
        end_pos = move.end_pos
        axes_d = move.axes_d
        limits = self.limits
        for i in (0, 1, 2):
            if axes_d[i]:
                pos = end_pos[i]
                l, h = limits[i]
                if pos < l or pos > h:
                    if l > h:
                        raise move.move_error("Must home axis first")
                    raise move.move_error()
    def check_move(self, move):
        limits = self.limits
        end_pos = move.end_pos
        xpos, ypos = end_pos[0], end_pos[1]
        lx, hx = limits[0]
        ly, hy = limits[1]
        if xpos < lx or xpos > hx or ypos < ly or ypos > hy:
            self._check_endstops(move)
        
        axes_d = move.axes_d
        axes_d2 = axes_d[2]
        if not axes_d2:
            # Normal XY move - use defaults
            return
        # Move with Z - update velocity and accel for slower Z axis
        self._check_endstops(move)
        z_ratio = move.move_d / abs(axes_d2)
        move.limit_speed(
            self.max_z_velocity * z_ratio, self.max_z_accel * z_ratio)
    def get_status(self, eventtime):
        axes = [a for a, (l, h) in zip("xyz", self.limits) if l <= h]
        return {
            'homed_axes': "".join(axes),
            'axis_minimum': self.axes_min,
            'axis_maximum': self.axes_max,
        }

def load_kinematics(toolhead, config):
    return CoreXYKinematics(toolhead, config)
