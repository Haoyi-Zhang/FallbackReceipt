"""Small boundary examples for the manuscript's objective and policy definitions."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from checker import check
from game import encode, initial, successors, synthesize
from oracle import exact


def single_job(accept_cost=10, fallback_cost=5, late=0):
    return {"jobs": [{"score": 2, "accept_cost": accept_cost, "fallback_cost": fallback_cost,
                      "accept_time": 1, "fallback_time": 1}],
            "drift": 0, "crashes": 0, "unsafe": 0, "late": late, "cost": 10,
            "recovery_time": 0, "deadline": 1}


class CostSemanticsTests(unittest.TestCase):
    def test_fixed_sequence_charge_does_not_fix_actual_receiver_cost(self):
        from open_loop import solve
        from runtime import ControllerStore, ReceiverStore
        from trace_check import check_runtime

        spec = single_job()
        spec["crashes"] = 1
        cert = synthesize(spec)
        fixed = solve(spec)
        self.assertTrue(check(spec, cert))
        self.assertEqual(fixed["actions"], ["fallback"])
        self.assertEqual(fixed["worst_cost"], cert["worst_cost"])
        actual_totals, certified_totals = [], []
        # Two admissible executions; no causal cost effect of recovery is assumed.
        for actual_cost, recover in ((1, False), (4, True)):
            with self.subTest(actual_cost=actual_cost, recover=recover):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    controller = ControllerStore(root / "controller.db", spec, cert)
                    receiver = ReceiverStore(root / "receiver.db")
                    try:
                        key, action, envelope, _ = controller.reserve()
                        self.assertEqual(action, fixed["actions"][0])
                        self.assertEqual(envelope, 5)
                        receipt = receiver.execute_once(
                            key, *controller.attempt_descriptor(key), actual_cost, 0)
                        if recover:
                            controller.close()
                            controller = ControllerStore(root / "controller.db", spec, cert)
                            ticket = controller.record_recovery(key)
                            receipt = receiver.close_attempt(key, ticket)
                        controller.settle(receipt)
                        self.assertTrue(check_runtime(spec, cert, controller.snapshot(),
                                                      receiver.outcomes(), receiver.effects()))
                        self.assertEqual(len(receiver.effects()), 1)
                        actual_totals.append(sum(e[0] for e in receiver.effects().values()))
                        certified_totals.append(spec["cost"] - controller.state()[5])
                        self.assertEqual(controller.state()[2], spec["crashes"] - int(recover))
                    finally:
                        controller.close()
                        receiver.close()
        self.assertEqual(actual_totals, [1, 4])
        self.assertEqual(certified_totals, [5, 5])
        self.assertTrue(all(actual <= bound for actual, bound
                            in zip(actual_totals, certified_totals)))

    def test_certified_optimum_need_not_minimize_actual_receiver_cost(self):
        spec = single_job()
        root = initial(spec)
        cert = synthesize(spec)
        self.assertTrue(check(spec, cert))
        self.assertEqual(exact(spec), 5)
        self.assertEqual(cert["worst_cost"], 5)
        self.assertEqual(cert["policy"][encode(root)]["action"], "fallback")
        actual = {"accept": 1, "fallback": 5}
        certified = {a: spec["jobs"][0][f"{a}_cost"] for a in actual}
        for action in actual:
            outcomes = successors(spec, root, action)
            self.assertTrue(all(min(nxt[1:]) >= 0 for nxt, _, _ in outcomes))
            self.assertTrue(all(charge == certified[action] for _, charge, _ in outcomes))
            self.assertLessEqual(actual[action], certified[action])
            self.assertLessEqual(certified[action], spec["cost"])
        self.assertEqual(min(certified, key=certified.get), "fallback")
        self.assertEqual(min(actual, key=actual.get), "accept")
        self.assertEqual(actual["accept"], 1)
        self.assertTrue(all(min(nxt[1:]) < 0 for nxt, _, _ in successors(spec, root, "reject")))
        # This explicitly specified receiver is admissible without making its
        # accept upper bound attainable. No universal tightness is assumed.

    def test_restricted_policy_can_reject_a_safe_accept_for_lower_charge(self):
        spec = single_job(accept_cost=1, late=1)
        root = initial(spec)
        self.assertTrue(all(min(nxt[1:]) >= 0 and charge == 1
                            for nxt, charge, _ in successors(spec, root, "accept")))
        # This is exactly the candidate construction used by trace_phase:
        # one score-prescribed physical action PLUS reject at each state.
        cert = synthesize(spec, {0: ("accept", "reject")})
        self.assertTrue(cert["feasible"])
        self.assertEqual(cert["policy"][encode(root)]["action"], "reject")
        self.assertEqual(cert["worst_cost"], 0)
        self.assertEqual(successors(spec, root, "reject")[0][1], 0)

    def test_restricted_reject_choice_can_respond_to_remaining_budget(self):
        spec = single_job(accept_cost=1, late=1)
        with_late_budget = synthesize(spec, {0: ("accept", "reject")})
        without_late_budget = copy.deepcopy(spec)
        without_late_budget["late"] = 0
        without = synthesize(without_late_budget, {0: ("accept", "reject")})
        self.assertEqual(with_late_budget["policy"][encode(initial(spec))]["action"], "reject")
        self.assertEqual(without["policy"][encode(initial(without_late_budget))]["action"], "accept")


if __name__ == "__main__":
    unittest.main()
