import unittest, math, sys, os
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))
with patch.dict(sys.modules, {
    'mcu': MagicMock(),
    'chelper': MagicMock(),
    'kinematics': MagicMock(),
    'kinematics.extruder': MagicMock(),
    'util': MagicMock(),
    'termios': MagicMock(),
    'fcntl': MagicMock(),
    'pty': MagicMock(),
}):
    import toolhead

class TestToolheadOptimized(unittest.TestCase):
    def setUp(self):
        self.mock_toolhead = MagicMock()
        self.mock_toolhead.max_accel = 3000.0
        self.mock_toolhead.max_velocity = 300.0
        self.mock_toolhead.junction_deviation = 0.01
        self.mock_toolhead.mcr_pseudo_accel = 1500.0
        self.mock_toolhead.extra_axes = []

    def test_move_init(self):
        start_pos = (0., 0., 0., 0.)
        end_pos = (10., 0., 0., 0.)
        speed = 100.0
        move = toolhead.Move(self.mock_toolhead, start_pos, end_pos, speed)
        
        self.assertEqual(move.move_d, 10.0)
        self.assertEqual(move.max_cruise_v2, 100.0 * 100.0)
        self.assertTrue(move.is_kinematic_move)
        self.assertEqual(move.axes_r[0], 1.0)

    def test_move_calc_junction(self):
        # Move 1: X 0 to 10
        move1 = toolhead.Move(self.mock_toolhead, (0., 0., 0., 0.), (10., 0., 0., 0.), 100.0)
        # Move 2: Y 0 to 10 (90 degree turn)
        move2 = toolhead.Move(self.mock_toolhead, (10., 0., 0., 0.), (10., 10., 0., 0.), 100.0)
        
        move2.calc_junction(move1)
        # 90 deg turn should have some junction speed
        self.assertGreater(move2.max_start_v2, 0.)
        self.assertLess(move2.max_start_v2, move1.max_cruise_v2)

    def test_move_set_junction(self):
        move = toolhead.Move(self.mock_toolhead, (0., 0., 0., 0.), (10., 0., 0., 0.), 100.0)
        v2 = 100.0 * 100.0
        move.set_junction(v2, v2, v2)
        
        self.assertEqual(move.start_v, 100.0)
        self.assertEqual(move.cruise_v, 100.0)
        self.assertEqual(move.end_v, 100.0)
        self.assertEqual(move.accel_t, 0.)
        self.assertEqual(move.decel_t, 0.)
        self.assertAlmostEqual(move.cruise_t, 0.1)

    def test_lookahead_queue(self):
        laq = toolhead.LookAheadQueue()
        # Create a series of moves
        move1 = toolhead.Move(self.mock_toolhead, (0., 0., 0., 0.), (10., 0., 0., 0.), 100.0)
        move2 = toolhead.Move(self.mock_toolhead, (10., 0., 0., 0.), (20., 0., 0., 0.), 100.0)
        
        laq.add_move(move1)
        laq.add_move(move2)
        
        moves = laq.flush()
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0], move1)
        self.assertEqual(moves[1], move2)
        
        # Check that start/end velocities are correct for a straight line
        self.assertAlmostEqual(moves[0].start_v, 0., places=5)
        self.assertAlmostEqual(moves[1].end_v, 0., places=5)
        # In a straight line, the junction between move1 and move2 should be at max speed
        self.assertAlmostEqual(moves[0].end_v, 100.0)
        self.assertAlmostEqual(moves[1].start_v, 100.0)

    def test_toolhead_stats(self):
        # Mock dependencies for ToolHead
        config = MagicMock()
        printer = MagicMock()
        config.get_printer.return_value = printer
        class FakeReactor:
            def monotonic(self): return 0.0
            def assert_no_pause(self):
                class FakeContext:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                return FakeContext()
        reactor = FakeReactor()
        printer.get_reactor.return_value = reactor
        mcu = MagicMock()
        printer.lookup_object.return_value = mcu
        motion_queuing = MagicMock()
        printer.load_object.return_value = motion_queuing
        
        mcu.estimated_print_time.return_value = 1.0
        motion_queuing.calc_step_gen_restart.return_value = 1.0

        config.getfloat.side_effect = lambda name, default=None, **kwargs: {
            'max_velocity': 300.0,
            'max_accel': 3000.0,
            'minimum_cruise_ratio': 0.5,
            'square_corner_velocity': 5.0
        }.get(name, default)
        config.get.return_value = "cartesian"
        toolhead.chelper.get_ffi.return_value = (MagicMock(), MagicMock())

        with patch('importlib.import_module') as mock_import:
            mock_kin = MagicMock()
            mock_import.return_value.load_kinematics.return_value = mock_kin
            th = toolhead.ToolHead(config)
        
        th.print_time = 2.0
        
        # Manually add a move to check stats
        move = toolhead.Move(th, (0., 0., 0., 0.), (10., 0., 0., 0.), 100.0)
        move.set_junction(0., 100.0 * 100.0, 0.)
        
        # We need to manually call _process_lookahead with the move
        th.lookahead.add_move(move)
        th._process_lookahead()
        
        status = th.get_status(0.)
        self.assertEqual(status['total_distance'], 10.0)
        self.assertEqual(status['total_moves'], 1)
        self.assertGreater(status['avg_velocity'], 0.)
        self.assertEqual(status['is_stalled'], False)

if __name__ == '__main__':
    unittest.main()
