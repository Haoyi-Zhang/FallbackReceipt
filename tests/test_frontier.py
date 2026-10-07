import copy
import itertools
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from frontier import solve_frontier
from open_loop import solve, envelope, worst_counts


class FrontierTests(unittest.TestCase):
    def test_small_exhaustive_contracts(self):
        count = 0
        jobs = [dict(score=2, accept_cost=1, fallback_cost=3, accept_time=1, fallback_time=3),
                dict(score=0, accept_cost=2, fallback_cost=1, accept_time=3, fallback_time=1),
                dict(score=1, accept_cost=1, fallback_cost=2, accept_time=2, fallback_time=2)]
        for d, f, u, l, c, rho in itertools.product(range(3), range(3), range(4), range(4), range(7), range(3)):
            spec = dict(jobs=jobs, drift=d, crashes=f, unsafe=u, late=l, cost=c, recovery_time=rho, deadline=2)
            old, new = solve(spec), solve_frontier(spec)
            self.assertEqual((old['feasible'], old['worst_cost']), (new['feasible'], new['worst_cost']))
            if new['feasible']:
                charge, unsafe, late = worst_counts(envelope(jobs, new['actions'], 2, rho), d, f)
                self.assertEqual(charge, new['worst_cost'])
                self.assertLessEqual(unsafe, u)
                self.assertLessEqual(late, l)
            count += 1
        self.assertEqual(count, 3024)

    def test_invalid_representation(self):
        spec = dict(jobs=[dict(score=1, accept_cost=1, fallback_cost=2, accept_time=1, fallback_time=2)],
                    drift=0, crashes=0, unsafe=0, late=0, cost=2, recovery_time=1, deadline=2)
        for value in (None, [], {'jobs': []}):
            with self.assertRaises(ValueError):
                solve_frontier(value)
        for key in ('deadline', 'drift', 'crashes'):
            bad = copy.deepcopy(spec)
            bad[key] = True
            with self.assertRaises(ValueError):
                solve_frontier(bad)
        bad = copy.deepcopy(spec)
        bad['jobs'] = [None]
        with self.assertRaises(ValueError):
            solve_frontier(bad)

    def test_budget_resource_limit_is_no_decision(self):
        spec = dict(jobs=[dict(score=1, accept_cost=1, fallback_cost=2, accept_time=1, fallback_time=2)],
                    drift=1025, crashes=0, unsafe=0, late=0, cost=2, recovery_time=1, deadline=2)
        with self.assertRaises(RuntimeError):
            solve_frontier(spec)


if __name__ == '__main__':
    unittest.main()
