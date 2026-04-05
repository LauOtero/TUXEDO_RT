import time, math, random
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '../klippy'))
import mathutil

def benchmark_matrix_ops():
    print("Benchmarking Matrix Ops (1M iterations)...")
    m1 = [1.0, 2.0, 3.0]
    m2 = [4.0, 5.0, 6.0]
    s = 2.5
    
    start = time.time()
    for _ in range(1000000):
        mathutil.matrix_add(m1, m2)
        mathutil.matrix_sub(m1, m2)
        mathutil.matrix_mul(m1, s)
        mathutil.matrix_dot(m1, m2)
        mathutil.matrix_cross(m1, m2)
        mathutil.matrix_magsq(m1)
    end = time.time()
    print(f"Matrix 3x1 ops: {end - start:.4f}s")

def benchmark_matrix3x3_ops():
    print("Benchmarking Matrix 3x3 Ops (100k iterations)...")
    m = [[1., 2., 3.], [4., 5., 6.], [7., 8., 10.]]
    
    start = time.time()
    for _ in range(100000):
        mathutil.matrix_det(m)
        mathutil.matrix_inv(m)
    end = time.time()
    print(f"Matrix 3x3 ops: {end - start:.4f}s")

def benchmark_trilateration():
    print("Benchmarking Trilateration (100k iterations)...")
    sphere_coords = [[0, 0, 0], [10, 0, 0], [0, 10, 0]]
    radius2 = [100, 100, 100]
    
    start = time.time()
    for _ in range(100000):
        try:
            mathutil.trilateration(sphere_coords, radius2)
        except:
            pass
    end = time.time()
    print(f"Trilateration: {end - start:.4f}s")

def benchmark_coordinate_descent():
    print("Benchmarking Coordinate Descent (100 iterations)...")
    def error_func(params):
        # Rosenbrock function-like error
        x, y = params['x'], params['y']
        return (1 - x)**2 + 100 * (y - x**2)**2
    
    adj_params = ['x', 'y']
    params = {'x': 0.0, 'y': 0.0}
    
    start = time.time()
    for _ in range(100):
        mathutil.coordinate_descent(adj_params, params, error_func)
    end = time.time()
    print(f"Coordinate Descent: {end - start:.4f}s")

if __name__ == "__main__":
    benchmark_matrix_ops()
    benchmark_matrix3x3_ops()
    benchmark_trilateration()
    benchmark_coordinate_descent()
