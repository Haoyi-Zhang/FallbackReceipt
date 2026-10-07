"""Portable, owned finite checks of fixed-sequence refusal and witnesses.

The reference enumerates concrete flip/recovery worlds, not prefix envelopes,
dominance labels, or the Cartesian comparator's closed-form thresholds.
"""
from __future__ import annotations

import copy
import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from frontier import solve_frontier
from open_loop import solve


def fixture():
    return dict(jobs=[dict(score=2, accept_cost=1, fallback_cost=2,
                           accept_time=1, fallback_time=1)],
                drift=0, crashes=0, unsafe=0, late=1, cost=2,
                recovery_time=0, deadline=1)


def concrete_counts(spec, actions):
    """Coordinate maxima over all legal concrete worlds of this action list."""
    n = len(actions)
    charge = sum(job[action + "_cost"] for job, action in zip(spec["jobs"], actions)
                 if action != "reject")
    maximum_unsafe = maximum_late = 0
    for flips in itertools.product((0, 1), repeat=n):
        if sum(flips) > spec["drift"] or any(
                flip and action != "accept" for flip, action in zip(flips, actions)):
            continue
        for recoveries in itertools.product(range(spec["crashes"] + 1), repeat=n):
            if sum(recoveries) > spec["crashes"] or any(
                    r and action == "reject" for r, action in zip(recoveries, actions)):
                continue
            unsafe = late = 0
            for job, action, flip, r in zip(spec["jobs"], actions, flips, recoveries):
                if action == "reject":
                    late += 1
                else:
                    unsafe += (int(job["score"] == 0) ^ flip) if action == "accept" else 0
                    duration = job[action + "_time"] + r * spec["recovery_time"]
                    late += int(duration > spec["deadline"])
            maximum_unsafe = max(maximum_unsafe, unsafe)
            maximum_late = max(maximum_late, late)
    return charge, maximum_unsafe, maximum_late


def independent_optimum(spec):
    feasible = []
    for actions in itertools.product(("accept", "fallback", "reject"), repeat=len(spec["jobs"])):
        charge, unsafe, late = concrete_counts(spec, actions)
        if charge <= spec["cost"] and unsafe <= spec["unsafe"] and late <= spec["late"]:
            feasible.append((charge, actions))
    return None if not feasible else min(charge for charge, _ in feasible)


def finite_contracts():
    a = dict(score=2, accept_cost=1, fallback_cost=2, accept_time=1, fallback_time=1)
    b = dict(score=0, accept_cost=2, fallback_cost=1, accept_time=2, fallback_time=1)
    c = dict(score=1, accept_cost=1, fallback_cost=1, accept_time=1, fallback_time=2)
    for jobs in ([a], [a, b], [b, c], [c, a, b]):
        for d, f, rho, deadline, u, late, cost in itertools.product(
                range(2), range(2), range(2), (1, 2), range(2), range(3), (0, 2, 4)):
            yield dict(jobs=copy.deepcopy(jobs), drift=d, crashes=f, unsafe=u,
                       late=late, cost=cost, recovery_time=rho, deadline=deadline)


class FrontierLimitTests(unittest.TestCase):
    def test_incomparable_width_is_no_decision_not_infeasibility(self):
        spec = fixture()
        original = copy.deepcopy(spec)
        expected = dict(feasible=True, worst_cost=0, actions=["reject"],
                        expanded_labels=3, dominated_labels=1,
                        maximum_frontier=2, frontier_widths=[2])
        self.assertEqual(solve_frontier(spec), expected)
        self.assertEqual(solve_frontier(spec, limit=2), expected)
        self.assertEqual(independent_optimum(spec), 0)
        self.assertEqual(solve(spec)["worst_cost"], 0)
        with self.assertRaisesRegex(RuntimeError, "^frontier resource limit, not infeasibility$"):
            solve_frontier(spec, limit=1)
        self.assertEqual(spec, original)

    def test_cap_is_after_dominance_and_hard_budget_filter(self):
        spec = fixture()
        spec["late"] = 0
        original = copy.deepcopy(spec)
        expected = dict(feasible=True, worst_cost=1, actions=["accept"],
                        expanded_labels=3, dominated_labels=1,
                        maximum_frontier=1, frontier_widths=[1])
        self.assertEqual(solve_frontier(spec, limit=1), expected)
        self.assertEqual(independent_optimum(spec), 1)
        self.assertEqual(solve(spec)["actions"], ["accept"])
        # Equal-charge and equal-vector ties also keep the canonical accept.
        spec["jobs"][0]["fallback_cost"] = 1
        self.assertEqual(solve_frontier(spec, limit=1), expected)
        spec["jobs"][0]["fallback_cost"] = 2
        self.assertEqual(spec, original)

    def test_actual_infeasibility_has_a_result_not_a_resource_exception(self):
        spec = fixture()
        spec.update(late=0, cost=0)
        original = copy.deepcopy(spec)
        self.assertIsNone(independent_optimum(spec))
        self.assertEqual(solve_frontier(spec, limit=1),
                         dict(feasible=False, worst_cost=None, actions=None,
                              expanded_labels=3, dominated_labels=0,
                              maximum_frontier=1, frontier_widths=[0]))
        self.assertFalse(solve(spec)["feasible"])
        self.assertEqual(spec, original)

    def test_independent_worlds_and_exact_width_boundary(self):
        count = 0
        for spec in finite_contracts():
            original = copy.deepcopy(spec)
            result = solve_frontier(spec)
            optimum = independent_optimum(spec)
            self.assertEqual((result["feasible"], result["worst_cost"]),
                             (optimum is not None, optimum))
            self.assertEqual((solve(spec)["feasible"], solve(spec)["worst_cost"]),
                             (optimum is not None, optimum))
            if result["feasible"]:
                charge, unsafe, late = concrete_counts(spec, result["actions"])
                self.assertEqual(charge, optimum)
                self.assertLessEqual(unsafe, spec["unsafe"])
                self.assertLessEqual(late, spec["late"])
            width = result["maximum_frontier"]
            self.assertEqual(solve_frontier(spec, limit=width), result)
            if width > 1:
                with self.assertRaisesRegex(RuntimeError, "^frontier resource limit, not infeasibility$"):
                    solve_frontier(spec, limit=width - 1)
            self.assertEqual(spec, original)
            count += 1
        self.assertEqual(count, 1152)

    def test_invalid_limits_and_diagnostic_precedence(self):
        spec = fixture()
        for limit in (True, False, 0, -1, 1.0, "1", None):
            with self.assertRaisesRegex(ValueError, "^positive frontier limit required$"):
                solve_frontier(spec, limit=limit)
        with self.assertRaisesRegex(ValueError, "^invalid finite contract$"):
            solve_frontier(None, limit=0)
        bad = copy.deepcopy(spec)
        bad["jobs"][0]["accept_time"] = True
        with self.assertRaisesRegex(ValueError, "^positive frontier limit required$"):
            solve_frontier(bad, limit=0)
        with self.assertRaisesRegex(ValueError, "^positive integer envelopes required$"):
            solve_frontier(bad, limit=1)

    def test_vector_limit_remains_distinct_and_input_is_unchanged(self):
        for key in ("drift", "crashes"):
            spec = fixture()
            spec[key] = 1025
            original = copy.deepcopy(spec)
            with self.assertRaisesRegex(RuntimeError, "^budget-vector resource limit, not infeasibility$"):
                solve_frontier(spec, limit=1)
            self.assertEqual(spec, original)


if __name__ == "__main__":
    unittest.main()
