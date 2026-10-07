"""Owned portable hand controls and Cartesian literal-reference regressions."""
import copy
from itertools import product
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import literal_fixed
import open_loop
import game
from compare_oracle_fixed import evaluate, summarise, validate_cases


def fixture():
    return dict(jobs=[dict(score=2, accept_cost=2, fallback_cost=4,
                           accept_time=1, fallback_time=1)],
                drift=0, crashes=0, unsafe=0, late=0, cost=6,
                recovery_time=1, deadline=1)


class OracleFixedTests(unittest.TestCase):
    def test_hand_accept_fallback_reject_and_infeasible(self):
        spec = fixture()
        self.assertEqual(literal_fixed.solve(spec)['worst_cost'], 2)
        spec['drift'] = 1
        self.assertEqual(literal_fixed.solve(spec)['actions'], ['fallback'])
        spec['late'] = 1
        self.assertEqual(literal_fixed.solve(spec)['worst_cost'], 0)
        spec.update(late=0, cost=0)
        self.assertFalse(literal_fixed.solve(spec)['feasible'])

    def test_flips_include_both_directions_and_global_budget(self):
        spec = fixture()
        spec['jobs'][0]['score'] = 0
        spec['drift'] = 1
        self.assertEqual(literal_fixed.bounds(spec, ['accept']), [2, 1, 0])
        spec['jobs'] = [dict(fixture()['jobs'][0]) for _ in range(2)]
        self.assertEqual(literal_fixed.bounds(spec, ['accept', 'accept']), [4, 1, 0])

    def test_deadline_strictness_global_crashes_and_rejection(self):
        spec = fixture()
        spec['jobs'] *= 2
        spec.update(crashes=1, deadline=2)
        self.assertEqual(literal_fixed.bounds(spec, ['accept', 'accept']), [4, 0, 0])
        spec['recovery_time'] = 2
        self.assertEqual(literal_fixed.bounds(spec, ['accept', 'accept']), [4, 0, 1])
        self.assertEqual(literal_fixed.bounds(spec, ['reject', 'accept']), [2, 0, 2])
        spec['recovery_time'] = 0
        self.assertEqual(literal_fixed.bounds(spec, ['accept', 'accept']), [4, 0, 0])

    def test_literal_has_no_production_imports_or_expected_values(self):
        import ast
        tree = ast.parse((ROOT / 'src/literal_fixed.py').read_text(encoding='utf-8'))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertEqual(imports, ['itertools'])
        self.assertFalse(any(isinstance(node, ast.Import) for node in ast.walk(tree)))

    def test_strict_input_types_and_domain_refusal(self):
        for field, value in [('drift', True), ('cost', -1), ('deadline', 0)]:
            bad = fixture(); bad[field] = value
            with self.assertRaises(ValueError): literal_fixed.solve(bad)
        bad = fixture(); bad['jobs'][0]['score'] = 2.0
        with self.assertRaises(ValueError): literal_fixed.solve(bad)
        bad = fixture(); bad['crashes'] = 3
        with self.assertRaisesRegex(RuntimeError, 'incomplete, not infeasible'):
            literal_fixed.solve(bad)

    def test_resource_refusal_is_incomplete_not_false(self):
        with patch.object(literal_fixed, 'MAX_WORLDS', 0):
            with self.assertRaisesRegex(RuntimeError, 'incomplete, not infeasible'):
                literal_fixed.solve(fixture())
        row = dict(case=0, spec=fixture(), oracle_cost=2, feasible=True)
        with patch.object(game, 'MAX_STATES', 0):
            result = evaluate(row)
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('adaptive', result)

    def test_complete_ordered_denominator_is_required(self):
        row = dict(case=0, spec=fixture(), oracle_cost=2, feasible=True)
        with self.assertRaises(ValueError): validate_cases([row])
        rows = [dict(row, case=i) for i in range(1200)]
        validate_cases(rows)
        rows[-1]['case'] = 0
        with self.assertRaises(ValueError): validate_cases(rows)
        with self.assertRaises(ValueError): summarise([])

    def test_unfiltered_two_job_cartesian_reference(self):
        count = 0
        for score, d, f, u, late, cost, rho, deadline in product(
                range(3), range(3), range(3), range(3), range(3), (0, 3, 6), (0, 2), (1, 3)):
            spec = fixture()
            spec['jobs'].append(dict(score=0, accept_cost=1, fallback_cost=3,
                                     accept_time=2, fallback_time=1))
            spec['jobs'][0]['score'] = score
            spec.update(drift=d, crashes=f, unsafe=u, late=late, cost=cost,
                        recovery_time=rho, deadline=deadline)
            original = copy.deepcopy(spec)
            literal = literal_fixed.solve(spec)
            fixed = open_loop.solve(spec)
            self.assertEqual({k: literal[k] for k in fixed}, fixed)
            self.assertEqual(spec, original)
            count += 1
        self.assertEqual(count, 2916)


if __name__ == '__main__':
    unittest.main()
