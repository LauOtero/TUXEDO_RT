import time
import math

class MockMove:
    def __init__(self, end_pos, axes_d, move_d):
        self.end_pos = end_pos
        self.axes_d = axes_d
        self.move_d = move_d
    def limit_speed(self, v, a):
        pass
    def move_error(self, msg=""):
        return Exception(msg)

class MockRail:
    def __init__(self, name):
        self.name = name
    def get_name(self):
        return self.name

class CartesianMock:
    def __init__(self):
        self.limits = [(0.0, 300.0), (0.0, 300.0), (0.0, 300.0)]
        self.max_z_velocity = 10.0
        self.max_z_accel = 100.0
        self.rails = [MockRail("stepper_x"), MockRail("stepper_y"), MockRail("stepper_z")]
        self.dc_module = None
        self.rail_names = [r.get_name() for r in self.rails]
        
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
            return
            
        self._check_endstops(move)
        z_ratio = move.move_d / abs(axes_d2)
        move.limit_speed(
            self.max_z_velocity * z_ratio, self.max_z_accel * z_ratio)

    def calc_position(self, stepper_positions):
        names = self.rail_names
        return [stepper_positions[names[0]], stepper_positions[names[1]], stepper_positions[names[2]]]


class CoreXYMock(CartesianMock):
    def calc_position(self, stepper_positions):
        names = self.rail_names
        p0 = stepper_positions[names[0]]
        p1 = stepper_positions[names[1]]
        p2 = stepper_positions[names[2]]
        return [0.5 * (p0 + p1), 0.5 * (p0 - p1), p2]

class DeltaMock:
    def __init__(self):
        self.limit_xy2 = 90000.0
        self.max_xy2 = 90000.0
        self.max_z = 300.0
        self.min_z = 0.0
        self.limit_z = 250.0
        self.home_position = [0.0, 0.0, 300.0]
        self.max_z_velocity = 10.0
        self.need_home = False
    
    def check_move(self, move):
        end_pos = move.end_pos
        end_xy2 = end_pos[0]**2 + end_pos[1]**2
        if end_xy2 <= self.limit_xy2 and not move.axes_d[2]:
            return
        if self.need_home:
            raise move.move_error("Must home first")
        end_z = end_pos[2]
        limit_xy2 = self.max_xy2
        if end_z > self.limit_z:
            limit_xy2 = min(limit_xy2, (self.max_z - end_z)**2)
        if end_xy2 > limit_xy2 or end_z > self.max_z or end_z < self.min_z:
            if (end_pos[:2] != self.home_position[:2]
                or end_z < self.min_z or end_z > self.home_position[2]):
                raise move.move_error()
            limit_xy2 = -1.
        if move.axes_d[2]:
            move.limit_speed(self.max_z_velocity, 100.0)
            limit_xy2 = -1.
        self.limit_xy2 = limit_xy2

def benchmark_kinematics():
    cart = CartesianMock()
    corexy = CoreXYMock()
    delta = DeltaMock()
    
    move_xy = MockMove([150.0, 150.0, 10.0, 0.0], [1.0, 1.0, 0.0, 0.0], 1.414)
    move_z = MockMove([150.0, 150.0, 11.0, 0.0], [0.0, 0.0, 1.0, 0.0], 1.0)
    
    positions = {"stepper_x": 100.0, "stepper_y": 100.0, "stepper_z": 10.0, "stepper_a": 100.0, "stepper_b": 100.0, "stepper_c": 100.0}
    
    iters = 1000000
    
    for name, kin in [("Cartesian", cart), ("CoreXY", corexy), ("Delta", delta)]:
        t0 = time.time()
        for _ in range(iters):
            kin.check_move(move_xy)
            kin.check_move(move_z)
        t_check = time.time() - t0
        
        if hasattr(kin, 'calc_position'):
            t0 = time.time()
            for _ in range(iters):
                kin.calc_position(positions)
            t_calc = time.time() - t0
            print(f"{name} - check_move: {t_check:.4f}s, calc_position: {t_calc:.4f}s")
        else:
            print(f"{name} - check_move: {t_check:.4f}s")

if __name__ == "__main__":
    benchmark_kinematics()
