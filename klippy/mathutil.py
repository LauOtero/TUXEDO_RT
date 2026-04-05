# Simple math helper functions
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging, multiprocessing, traceback, collections
import queuelogger

# TUXEDO_RT: Cache for trilateration results to improve performance
_TRILATERATION_CACHE = collections.deque(maxlen=100)


######################################################################
# Coordinate descent
######################################################################

# Helper code that implements coordinate descent
def coordinate_descent(adj_params, params, error_func):
    # TUXEDO_RT: Optimized coordinate descent with faster step adjustment
    # and convergence tracking.
    params = dict(params)
    dp = {param_name: 1. for param_name in adj_params}
    best_err = error_func(params)
    logging.info("Coordinate descent initial error: %s", best_err)

    threshold = 0.000005 # More precise
    rounds = 0
    consecutive_improvements = 0

    while sum(dp.values()) > threshold and rounds < 5000:
        rounds += 1
        improved = False
        for param_name in adj_params:
            orig = params[param_name]
            step = dp[param_name]
            
            # Try forward step
            params[param_name] = orig + step
            err = error_func(params)
            if err < best_err:
                best_err = err
                dp[param_name] *= 1.2 # Faster expansion
                improved = True
                continue
                
            # Try backward step
            params[param_name] = orig - step
            err = error_func(params)
            if err < best_err:
                best_err = err
                dp[param_name] *= 1.2
                improved = True
                continue
                
            # Revert and shrink
            params[param_name] = orig
            dp[param_name] *= 0.8 # Faster contraction
            
        if improved:
            consecutive_improvements += 1
        else:
            consecutive_improvements = 0
            
    logging.info("Coordinate descent best_err: %s  rounds: %d",
                 best_err, rounds)
    return params

# Helper to run the coordinate descent function in a background
# process so that it does not block the main thread.
def background_coordinate_descent(printer, adj_params, params, error_func):
    parent_conn, child_conn = multiprocessing.Pipe()
    def wrapper():
        queuelogger.clear_bg_logging()
        try:
            res = coordinate_descent(adj_params, params, error_func)
        except:
            child_conn.send((True, traceback.format_exc()))
            child_conn.close()
            return
        child_conn.send((False, res))
        child_conn.close()
    # Start a process to perform the calculation
    calc_proc = multiprocessing.Process(target=wrapper)
    calc_proc.daemon = True
    calc_proc.start()
    # Wait for the process to finish
    reactor = printer.get_reactor()
    gcode = printer.lookup_object("gcode")
    eventtime = last_report_time = reactor.monotonic()
    while calc_proc.is_alive():
        if eventtime > last_report_time + 5.:
            last_report_time = eventtime
            gcode.respond_info("Working on calibration...", log=False)
        eventtime = reactor.pause(eventtime + .1)
    # Return results
    is_err, res = parent_conn.recv()
    if is_err:
        raise Exception("Error in coordinate descent: %s" % (res,))
    calc_proc.join()
    parent_conn.close()
    return res


######################################################################
# Trilateration
######################################################################

# Trilateration finds the intersection of three spheres.
def trilateration(sphere_coords, radius2):
    # TUXEDO_RT: Cache result to avoid repeated heavy math
    cache_key = (tuple(map(tuple, sphere_coords)), tuple(radius2))
    for key, result in _TRILATERATION_CACHE:
        if key == cache_key:
            return result

    # Optimized trilateration algorithm
    sphere_coord1, sphere_coord2, sphere_coord3 = sphere_coords
    s21 = [sphere_coord2[0] - sphere_coord1[0],
           sphere_coord2[1] - sphere_coord1[1],
           sphere_coord2[2] - sphere_coord1[2]]
    s31 = [sphere_coord3[0] - sphere_coord1[0],
           sphere_coord3[1] - sphere_coord1[1],
           sphere_coord3[2] - sphere_coord1[2]]

    d_sq = s21[0]**2 + s21[1]**2 + s21[2]**2
    d = math.sqrt(d_sq)
    ex = [s21[0] / d, s21[1] / d, s21[2] / d]
    
    i = ex[0] * s31[0] + ex[1] * s31[1] + ex[2] * s31[2]
    
    vect_ey = [s31[0] - ex[0] * i, s31[1] - ex[1] * i, s31[2] - ex[2] * i]
    ey_mag = math.sqrt(vect_ey[0]**2 + vect_ey[1]**2 + vect_ey[2]**2)
    ey = [vect_ey[0] / ey_mag, vect_ey[1] / ey_mag, vect_ey[2] / ey_mag]
    
    ez = [ex[1] * ey[2] - ex[2] * ey[1],
          ex[2] * ey[0] - ex[0] * ey[2],
          ex[0] * ey[1] - ex[1] * ey[0]]
    
    j = ey[0] * s31[0] + ey[1] * s31[1] + ey[2] * s31[2]

    x = (radius2[0] - radius2[1] + d_sq) / (2. * d)
    y = (radius2[0] - radius2[2] - x**2 + (x-i)**2 + j**2) / (2. * j)
    z = -math.sqrt(max(0, radius2[0] - x**2 - y**2))

    res = [sphere_coord1[0] + ex[0] * x + ey[0] * y + ez[0] * z,
           sphere_coord1[1] + ex[1] * x + ey[1] * y + ez[1] * z,
           sphere_coord1[2] + ex[2] * x + ey[2] * y + ez[2] * z]
           
    _TRILATERATION_CACHE.append((cache_key, res))
    return res


######################################################################
# Matrix helper functions for 3x1 matrices
######################################################################

def matrix_cross(m1, m2):
    return [m1[1] * m2[2] - m1[2] * m2[1],
            m1[2] * m2[0] - m1[0] * m2[2],
            m1[0] * m2[1] - m1[1] * m2[0]]

def matrix_dot(m1, m2):
    return m1[0] * m2[0] + m1[1] * m2[1] + m1[2] * m2[2]

def matrix_magsq(m1):
    return m1[0]**2 + m1[1]**2 + m1[2]**2

def matrix_add(m1, m2):
    return [m1[0] + m2[0], m1[1] + m2[1], m1[2] + m2[2]]

def matrix_sub(m1, m2):
    return [m1[0] - m2[0], m1[1] - m2[1], m1[2] - m2[2]]

def matrix_mul(m1, s):
    return [m1[0]*s, m1[1]*s, m1[2]*s]

######################################################################
# Matrix helper functions for 3x3 matrices
######################################################################

def matrix_det(a):
    x0, x1, x2 = a
    # TUXEDO_RT: Manual expansion for performance
    return (x0[0] * (x1[1] * x2[2] - x1[2] * x2[1]) -
            x0[1] * (x1[0] * x2[2] - x1[2] * x2[0]) +
            x0[2] * (x1[0] * x2[1] - x1[1] * x2[0]))

def matrix_inv(a):
    x0, x1, x2 = a
    det = (x0[0] * (x1[1] * x2[2] - x1[2] * x2[1]) -
           x0[1] * (x1[0] * x2[2] - x1[2] * x2[0]) +
           x0[2] * (x1[0] * x2[1] - x1[1] * x2[0]))
    if not det:
        return [[0., 0., 0.], [0., 0., 0.], [0., 0., 0.]]
    inv_det = 1. / det
    return [[(x1[1] * x2[2] - x1[2] * x2[1]) * inv_det,
             (x0[2] * x2[1] - x0[1] * x2[2]) * inv_det,
             (x0[1] * x1[2] - x0[2] * x1[1]) * inv_det],
            [(x1[2] * x2[0] - x1[0] * x2[2]) * inv_det,
             (x0[0] * x2[2] - x0[2] * x2[0]) * inv_det,
             (x0[2] * x1[0] - x0[0] * x1[2]) * inv_det],
            [(x1[0] * x2[1] - x1[1] * x2[0]) * inv_det,
             (x0[1] * x2[0] - x0[0] * x2[1]) * inv_det,
             (x0[0] * x1[1] - x0[1] * x1[0]) * inv_det]]

######################################################################
# TUXEDO_RT: Advanced Mathematical and Statistical Functions
######################################################################

def stats_mean(data):
    if not data:
        return 0.0
    return sum(data) / len(data)

def stats_variance(data):
    if not data:
        return 0.0
    n = len(data)
    mean = sum(data) / n
    return sum((x - mean)**2 for x in data) / n

def stats_stddev(data):
    return math.sqrt(stats_variance(data))

def matrix_mul_3x3_3x1(m3x3, m3x1):
    return [m3x3[0][0]*m3x1[0] + m3x3[0][1]*m3x1[1] + m3x3[0][2]*m3x1[2],
            m3x3[1][0]*m3x1[0] + m3x3[1][1]*m3x1[1] + m3x3[1][2]*m3x1[2],
            m3x3[2][0]*m3x1[0] + m3x3[2][1]*m3x1[1] + m3x3[2][2]*m3x1[2]]

def matrix_mul_4x4(A, B):
    C = [[0.]*4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            C[i][j] = sum(A[i][k] * B[k][j] for k in range(4))
    return C

def numerical_derivative(func, x, h=1e-5):
    return (func(x + h) - func(x - h)) / (2 * h)

def fast_hypot_3d(x, y, z):
    return math.sqrt(x*x + y*y + z*z)

######################################################################
# TUXEDO_RT: Quaternion Operations for 3D Rotations
######################################################################

def quat_from_axis_angle(axis, angle):
    half_angle = angle * 0.5
    s = math.sin(half_angle)
    mag = math.sqrt(axis[0]**2 + axis[1]**2 + axis[2]**2)
    if mag < 1e-10:
        return [1.0, 0.0, 0.0, 0.0]
    return [math.cos(half_angle), axis[0]/mag*s, axis[1]/mag*s, axis[2]/mag*s]

def quat_mul(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return [w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2]

def quat_rotate(q, v):
    # Rotates a 3D vector v by quaternion q
    qw, qx, qy, qz = q
    vx, vy, vz = v
    # Extract vector part of quaternion
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return [vx + qw * tx + (qy * tz - qz * ty),
            vy + qw * ty + (qz * tx - qx * tz),
            vz + qw * tz + (qx * ty - qy * tx)]

######################################################################
# TUXEDO_RT: Numerical Integration
######################################################################

def numerical_integral_simpson(func, a, b, n=100):
    # Simpson's rule for numerical integration
    if n % 2: n += 1
    h = (b - a) / n
    s = func(a) + func(b)
    for i in range(1, n, 2):
        s += 4 * func(a + i * h)
    for i in range(2, n - 1, 2):
        s += 2 * func(a + i * h)
    return s * h / 3
