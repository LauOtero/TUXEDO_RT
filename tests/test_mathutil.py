import unittest, math
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '../klippy'))
import mathutil

class TestMathUtil(unittest.TestCase):
    def test_matrix_3x1(self):
        m1 = [1, 2, 3]
        m2 = [4, 5, 6]
        self.assertEqual(mathutil.matrix_add(m1, m2), [5, 7, 9])
        self.assertEqual(mathutil.matrix_sub(m1, m2), [-3, -3, -3])
        self.assertEqual(mathutil.matrix_mul(m1, 2), [2, 4, 6])
        self.assertEqual(mathutil.matrix_dot(m1, m2), 1*4 + 2*5 + 3*6)
        self.assertEqual(mathutil.matrix_magsq(m1), 1+4+9)
        self.assertEqual(mathutil.matrix_cross(m1, m2), [-3, 6, -3])

    def test_matrix_3x3(self):
        m = [[1, 2, 3], [0, 1, 4], [5, 6, 0]]
        det = mathutil.matrix_det(m)
        self.assertNotEqual(det, 0)
        inv = mathutil.matrix_inv(m)
        # Check if inv * m is identity
        def mat_mul_3x3(A, B):
            C = [[0]*3 for _ in range(3)]
            for i in range(3):
                for j in range(3):
                    C[i][j] = sum(A[i][k] * B[k][j] for k in range(3))
            return C
        res = mat_mul_3x3(inv, m)
        for i in range(3):
            for j in range(3):
                expected = 1 if i == j else 0
                self.assertAlmostEqual(res[i][j], expected, places=5)

    def test_trilateration(self):
        sphere_coords = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        # intersection at (0.5, 0.5, -sqrt(0.5))
        r2 = [1.0, 1.0, 1.0] # All radius^2 = 1.0
        res = mathutil.trilateration(sphere_coords, r2)
        # (0.5)^2 + (0.5)^2 + (-sqrt(0.5))^2 = 0.25 + 0.25 + 0.5 = 1.0
        self.assertAlmostEqual(res[0], 0.5)
        self.assertAlmostEqual(res[1], 0.5)
        self.assertAlmostEqual(res[2], -math.sqrt(0.5))

    def test_coordinate_descent(self):
        def error_func(params):
            return (params['x'] - 5)**2 + (params['y'] - 10)**2
        adj_params = ['x', 'y']
        params = {'x': 0.0, 'y': 0.0}
        res = mathutil.coordinate_descent(adj_params, params, error_func)
        self.assertAlmostEqual(res['x'], 5.0, places=3)
        self.assertAlmostEqual(res['y'], 10.0, places=3)

    def test_stats(self):
        data = [1, 2, 3, 4, 5]
        self.assertEqual(mathutil.stats_mean(data), 3.0)
        self.assertEqual(mathutil.stats_variance(data), 2.0)
        self.assertAlmostEqual(mathutil.stats_stddev(data), math.sqrt(2.0))

    def test_quat(self):
        axis = [0, 0, 1]
        angle = math.pi / 2
        q = mathutil.quat_from_axis_angle(axis, angle)
        v = [1, 0, 0]
        res = mathutil.quat_rotate(q, v)
        self.assertAlmostEqual(res[0], 0.0)
        self.assertAlmostEqual(res[1], 1.0)
        self.assertAlmostEqual(res[2], 0.0)

    def test_integral(self):
        def f(x): return x*x
        res = mathutil.numerical_integral_simpson(f, 0, 1, n=100)
        self.assertAlmostEqual(res, 1.0/3.0, places=5)

    def test_derivative(self):
        def f(x): return x*x
        res = mathutil.numerical_derivative(f, 2.0)
        self.assertAlmostEqual(res, 4.0, places=5)

if __name__ == "__main__":
    unittest.main()
