from __future__ import annotations

import concurrent.futures
import copy
import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import game
import protocol_model
from checker import check
from oracle import exact
from runtime import ControllerStore, ReceiverStore, recover_pending
from trace_check import check_runtime
from workload import calibrate, load_rows, to_job, windows


def one_job(score: int = 2) -> dict:
    return {
        "jobs": [{"score": score, "accept_cost": 2, "fallback_cost": 4,
                  "accept_time": 1, "fallback_time": 2}],
        "drift": 1, "crashes": 1, "unsafe": 1, "late": 0, "cost": 6,
        "recovery_time": 1, "deadline": 3,
    }


def three_jobs() -> dict:
    return {
        "jobs": [
            {"score": 2, "accept_cost": 2, "fallback_cost": 4, "accept_time": 2, "fallback_time": 4},
            {"score": 0, "accept_cost": 2, "fallback_cost": 5, "accept_time": 2, "fallback_time": 4},
            {"score": 1, "accept_cost": 3, "fallback_cost": 5, "accept_time": 3, "fallback_time": 5},
        ],
        "drift": 1, "crashes": 2, "unsafe": 1, "late": 0, "cost": 16,
        "recovery_time": 2, "deadline": 10,
    }


class ContractTests(unittest.TestCase):
    def test_synthesizer_checker_oracle_agree(self) -> None:
        spec = three_jobs()
        cert = game.synthesize(spec)
        self.assertTrue(cert["feasible"])
        self.assertEqual(cert["worst_cost"], exact(spec))
        self.assertTrue(check(spec, cert))

    def test_infeasible_record_is_independently_recomputed(self) -> None:
        spec = one_job(0)
        spec.update(unsafe=0, late=0, cost=0)
        cert = game.synthesize(spec)
        self.assertFalse(cert["feasible"])
        self.assertIsNone(exact(spec))
        self.assertTrue(check(spec, cert))
        forged = dict(cert, policy={"0,0,0,0,0,0": {}})
        self.assertFalse(check(spec, forged))

    def test_state_limit_is_not_reported_as_infeasibility(self) -> None:
        previous = game.MAX_STATES
        try:
            game.MAX_STATES = 1
            with self.assertRaisesRegex(RuntimeError, "state limit"):
                game.synthesize(three_jobs())
        finally:
            game.MAX_STATES = previous

    def test_certificate_mutation_is_rejected(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        root = game.encode(game.initial(spec))
        bad = copy.deepcopy(cert)
        bad["policy"][root]["outcomes"][0]["charge"] += 1
        self.assertFalse(check(spec, bad))
        bad = copy.deepcopy(cert)
        bad["policy"][root]["action"] = "skip"
        self.assertFalse(check(spec, bad))

    def test_checker_rejects_visited_state_mutation(self) -> None:
        spec = three_jobs()
        cert = game.synthesize(spec)
        bad = copy.deepcopy(cert)
        bad["visited_states"] += 1
        self.assertFalse(check(spec, bad))

    def test_runtime_rejects_invalid_certificate_without_creating_database(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        bad = copy.deepcopy(cert)
        bad["visited_states"] += 1
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            with self.assertRaisesRegex(ValueError, "valid feasible certificate"):
                ControllerStore(path, spec, bad)
            self.assertFalse(path.exists())

    def test_close_before_execute_fences_late_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td) / "receiver.db")
            self.assertEqual(receiver.close_attempt("a", "0" * 32)["status"], "canceled")
            self.assertEqual(receiver.execute_once("a", "admission", "job", 2, 1)["status"], "canceled")
            self.assertEqual(receiver.effects(), {})
            receiver.close()


    def test_receiver_rejects_malformed_recovery_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td) / "receiver.db")
            for ticket in (None, "", "0" * 31, "0" * 33, "G" * 32, "-" * 32):
                with self.subTest(ticket=ticket):
                    with self.assertRaisesRegex(ValueError, "invalid identifier or recovery ticket"):
                        receiver.close_attempt("a", ticket)  # type: ignore[arg-type]
            receiver.close()

    def test_execute_before_close_recovers_one_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td) / "receiver.db")
            first = receiver.execute_once("a", "admission", "job", 2, 1)
            again = receiver.execute_once("a", "admission", "job", 2, 1)
            closed = receiver.close_attempt("a", "0" * 32)
            self.assertEqual(first, again)
            for field in ("id", "status", "adapter", "job", "cost", "unsafe"):
                self.assertEqual(first[field], closed[field])
            self.assertEqual(first["via"], "execute")
            self.assertIsNone(first["ticket"])
            self.assertEqual(closed["via"], "close")
            self.assertEqual(closed["ticket"], "0" * 32)
            self.assertEqual(receiver.effects(), {"a": (2, 1)})
            receiver.close()

    def test_attempt_id_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td) / "receiver.db")
            receiver.execute_once("a", "cache", "job", 2, 0)
            with self.assertRaisesRegex(ValueError, "conflicting"):
                receiver.execute_once("a", "cache", "other", 2, 0)
            receiver.close()

    def test_pending_reservation_survives_restart_and_closes(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            key, _, _, _ = controller.reserve()
            controller.close()
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            receipts = recover_pending(controller, receiver)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(
                {name: receipts[0][name] for name in ("id", "status", "adapter", "job", "cost", "unsafe", "via")},
                {"id": key, "status": "canceled", "adapter": None, "job": None,
                 "cost": 0, "unsafe": 0, "via": "close"},
            )
            self.assertRegex(receipts[0]["ticket"], r"^[0-9a-f]{32}$")
            self.assertEqual(controller.pending(), [])
            controller.close(); receiver.close()

    def test_attempt_ids_are_namespaced_across_controller_databases(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = ControllerStore(root / "first.db", spec, cert)
            second = ControllerStore(root / "second.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key_a, action_a, cost_a, _ = first.reserve()
            key_b, action_b, cost_b, _ = second.reserve()
            self.assertNotEqual(key_a, key_b)
            self.assertNotEqual(first.controller_id, second.controller_id)
            unsafe_a = int(spec["jobs"][0]["score"] == 0) if action_a == "accept" else 0
            unsafe_b = int(spec["jobs"][0]["score"] == 0) if action_b == "accept" else 0
            adapter_a, target_a = first.attempt_descriptor(key_a)
            adapter_b, target_b = second.attempt_descriptor(key_b)
            first.settle(receiver.execute_once(key_a, adapter_a, target_a, cost_a, unsafe_a))
            second.settle(receiver.execute_once(key_b, adapter_b, target_b, cost_b, unsafe_b))
            self.assertEqual(len(receiver.effects()), 2)
            first.close(); second.close(); receiver.close()

    def test_controller_identifier_persists_and_cannot_be_rebound(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            controller = ControllerStore(path, spec, cert, controller_id="scope-a")
            self.assertEqual(controller.controller_id, "scope-a")
            controller.close()
            reopened = ControllerStore(path, spec, cert)
            self.assertEqual(reopened.controller_id, "scope-a")
            reopened.close()
            with self.assertRaisesRegex(ValueError, "different controller identifier"):
                ControllerStore(path, spec, cert, controller_id="scope-b")

    def test_persisted_controller_binds_adapter(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            controller = ControllerStore(path, spec, cert, adapter="cache")
            self.assertEqual(controller.adapter, "cache")
            controller.close()
            ControllerStore(path, spec, cert, adapter="cache").close()
            with self.assertRaisesRegex(ValueError, "different adapter"):
                ControllerStore(path, spec, cert, adapter="admission")

    def test_settle_rejects_wrong_reserved_adapter_or_target(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert, adapter="tier")
            receiver = ReceiverStore(root / "receiver.db")
            key, action, cost, _ = controller.reserve()
            unsafe = int(spec["jobs"][0]["score"] == 0) if action == "accept" else 0
            bad = {"id": key, "status": "done", "adapter": "admission", "job": "wrong",
                   "cost": cost, "unsafe": unsafe, "via": "execute", "ticket": None}
            with self.assertRaisesRegex(ValueError, "adapter and target"):
                controller.settle(bad)
            adapter, target = controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key, adapter, target, cost, unsafe))
            self.assertTrue(check_runtime(spec, cert, controller.snapshot(),
                                          receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_recovery_ticket_requires_post_charge_receiver_query(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, _, _, _ = controller.reserve()
            premature = receiver.close_attempt(key, "0" * 32)
            ticket = controller.record_recovery(key)
            with self.assertRaisesRegex(ValueError, "latest charged recovery ticket"):
                controller.settle(premature)
            confirmed = receiver.close_attempt(key, ticket)
            controller.settle(confirmed)
            # The logical action remains selected after cancellation; realize it.
            key2, action, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key2)
            unsafe = int(spec["jobs"][0]["score"] == 0) if action == "accept" else 0
            controller.settle(receiver.execute_once(key2, adapter, target, cost, unsafe))
            self.assertTrue(check_runtime(spec, cert, controller.snapshot(),
                                          receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_replay_rejects_controller_identity_and_target_tampering(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert,
                                         controller_id="audit-scope")
            receiver = ReceiverStore(root / "receiver.db")
            key, action, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key)
            unsafe = int(spec["jobs"][0]["score"] == 0) if action == "accept" else 0
            controller.settle(receiver.execute_once(key, adapter, target, cost, unsafe))
            snapshot = controller.snapshot()
            outcomes, effects = receiver.outcomes(), receiver.effects()
            self.assertTrue(check_runtime(spec, cert, snapshot, outcomes, effects))
            bad_id = copy.deepcopy(snapshot)
            bad_id["controller_id"] = "other-scope"
            self.assertFalse(check_runtime(spec, cert, bad_id, outcomes, effects))
            bad_target = copy.deepcopy(snapshot)
            reserve = next(e for e in bad_target["events"] if e["kind"] == "reserve")
            reserve["target"] = "job-999"
            self.assertFalse(check_runtime(spec, cert, bad_target, outcomes, effects))
            controller.close(); receiver.close()

    def test_replay_rejects_recovery_ticket_tampering(self) -> None:
        spec = one_job(score=2)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, _, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key)
            receiver.execute_once(key, adapter, target, cost, 0)
            ticket = controller.record_recovery(key)
            controller.settle(receiver.close_attempt(key, ticket))
            snapshot = controller.snapshot()
            outcomes, effects = receiver.outcomes(), receiver.effects()
            self.assertTrue(check_runtime(spec, cert, snapshot, outcomes, effects))
            bad = copy.deepcopy(snapshot)
            settle = next(e for e in bad["events"] if e["kind"] == "settle")
            settle["ticket"] = "f" * 32 if ticket != "f" * 32 else "e" * 32
            self.assertFalse(check_runtime(spec, cert, bad, outcomes, effects))
            controller.close(); receiver.close()

    def test_terminal_duplicate_receipt_preserves_origin(self) -> None:
        spec = one_job(score=2)
        spec.update(drift=0, unsafe=0)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, _, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key)
            direct = receiver.execute_once(key, adapter, target, cost, 0)
            controller.settle(direct)
            controller.settle(direct)
            recovered_view = receiver.close_attempt(key, "0" * 32)
            with self.assertRaisesRegex(ValueError, "latest charged recovery ticket"):
                controller.settle(recovered_view)
            controller.close(); receiver.close()

    def test_concurrent_controller_initialization_is_atomic(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            barrier = threading.Barrier(2)

            def open_once() -> str:
                barrier.wait(timeout=5)
                controller = ControllerStore(path, spec, cert)
                try:
                    return controller.controller_id
                finally:
                    controller.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                identifiers = list(pool.map(lambda _: open_once(), range(2)))
            self.assertEqual(len(set(identifiers)), 1)

    def test_persisted_controller_binds_spec_and_certificate(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            ControllerStore(path, spec, cert).close()
            changed = copy.deepcopy(spec)
            changed["deadline"] += 1
            with self.assertRaisesRegex(ValueError, "bound"):
                ControllerStore(path, changed, game.synthesize(changed))

    def test_complete_runtime_record_passes_independent_replay(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, action, cost, _ = controller.reserve()
            unsafe = int(spec["jobs"][0]["score"] == 0) if action == "accept" else 0
            adapter, target = controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key, adapter, target, cost, unsafe))
            snapshot = controller.snapshot()
            self.assertTrue(check_runtime(spec, cert, snapshot, receiver.outcomes(), receiver.effects()))
            corrupt = copy.deepcopy(snapshot)
            corrupt["events"][-1]["actual"][0] += 1
            self.assertFalse(check_runtime(spec, cert, corrupt, receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_replay_rejects_tampered_certificate_even_when_events_are_unchanged(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert, adapter="cache")
            receiver = ReceiverStore(root / "receiver.db")
            key, action, cost, _ = controller.reserve()
            unsafe = int(spec["jobs"][0]["score"] == 0) if action == "accept" else 0
            adapter, target = controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key, adapter, target, cost, unsafe))
            bad = copy.deepcopy(cert)
            bad["visited_states"] += 1
            self.assertFalse(check_runtime(spec, bad, controller.snapshot(),
                                           receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_lost_reply_restart_consumes_crash_budget_once(self) -> None:
        spec = one_job(score=2)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, action, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key)
            receiver.execute_once(key, adapter, target, cost, 0)
            controller.close()
            controller = ControllerStore(root / "controller.db", spec, cert)
            ticket = controller.record_recovery(key)
            controller.settle(receiver.close_attempt(key, ticket))
            self.assertEqual(controller.state()[2], spec["crashes"] - 1)
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["attempts"][key]["recoveries"], 1)
            self.assertTrue(check_runtime(spec, cert, snapshot, receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_fresh_identifiers_repeat_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td) / "receiver.db")
            receiver.execute_once("old", "admission", "job", 3, 1)
            receiver.execute_once("new", "admission", "job", 3, 1)
            self.assertEqual(len(receiver.effects()), 2)
            self.assertGreater(sum(c for c, _ in receiver.effects().values()), 4)
            receiver.close()

    def test_declared_crash_budget_exhaustion_is_detected(self) -> None:
        spec = one_job()
        spec["crashes"] = 0
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, _, _, _ = controller.reserve()
            with self.assertRaisesRegex(ValueError, "crash budget exhausted"):
                controller.record_recovery(key)
            self.assertEqual(controller.pending(), [key])
            self.assertEqual(controller.snapshot()["attempts"][key]["recoveries"], 0)
            controller.close(); receiver.close()

    def test_accept_reservation_uses_exact_declared_unsafe_envelope(self) -> None:
        spec = one_job(score=2)
        spec.update(drift=0, unsafe=0)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            controller = ControllerStore(Path(td) / "controller.db", spec, cert)
            _, action, _, upper_unsafe = controller.reserve()
            self.assertEqual(action, "accept")
            self.assertEqual(upper_unsafe, 0)
            controller.close()

    def test_controller_databases_pass_integrity_checks(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            controller = ControllerStore(path, spec, cert)
            controller.ensure_selected()
            self.assertEqual(controller.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(controller.db.execute("PRAGMA foreign_key_check").fetchall(), [])
            controller.close()

    def test_public_excerpts_and_cross_trace_mapping_are_frozen(self) -> None:
        code_path = ROOT / "data" / "azure_llm_code_excerpt.csv"
        conv_path = ROOT / "data" / "azure_llm_conv_excerpt.csv"
        code_rows = load_rows(code_path)
        conv_rows = load_rows(conv_path)
        self.assertEqual(len(code_rows), 128)
        self.assertEqual(len(conv_rows), 128)
        self.assertEqual((code_rows[0]["context"], code_rows[0]["generated"]), (4808, 10))
        self.assertEqual((conv_rows[0]["context"], conv_rows[0]["generated"]), (374, 44))
        calibration = calibrate(code_rows)
        self.assertEqual(calibration, {
            "schema": 2, "rows": 128,
            "context_q33": 583, "context_q67": 2647,
            "generated_q33": 9, "generated_q67": 18,
            "gap_q33_ms": 48, "gap_q67_ms": 169,
            "context_unit": 1650, "generated_unit": 13,
            "recovery_time": 2, "deadline": 4,
        })
        self.assertNotEqual(calibration, calibrate(conv_rows))
        self.assertEqual(to_job(code_rows[0], calibration), {
            "score": 0, "accept_cost": 5, "fallback_cost": 7,
            "accept_time": 3, "fallback_time": 5,
        })
        self.assertEqual(to_job(conv_rows[0], calibration), {
            "score": 1, "accept_cost": 4, "fallback_cost": 7,
            "accept_time": 4, "fallback_time": 6,
        })
        code_windows = windows(code_path, 8, calibration)
        conv_windows = windows(conv_path, 8, calibration)
        self.assertEqual(len(code_windows), 16)
        self.assertEqual(len(conv_windows), 16)
        self.assertEqual(
            {score: sum(job["score"] == score for window in conv_windows for job in window)
             for score in (0, 1, 2)},
            {0: 16, 1: 58, 2: 54},
        )



    def test_concurrent_reservations_leave_only_one_pending(self) -> None:
        spec = one_job(score=2)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            ControllerStore(path, spec, cert).close()
            barrier = threading.Barrier(2)

            def reserve_once() -> tuple[str, str]:
                controller = ControllerStore(path, spec, cert)
                try:
                    barrier.wait(timeout=5)
                    key, _, _, _ = controller.reserve()
                    return ("ok", key)
                except ValueError as exc:
                    return ("error", str(exc))
                finally:
                    controller.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: reserve_once(), range(2)))
            self.assertEqual(sum(kind == "ok" for kind, _ in outcomes), 1)
            self.assertEqual(sum(kind == "error" for kind, _ in outcomes), 1)
            controller = ControllerStore(path, spec, cert)
            self.assertEqual(len(controller.pending()), 1)
            controller.close()


    def test_concurrent_recovery_consumes_one_budget_token(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            controller = ControllerStore(path, spec, cert)
            key, _, _, _ = controller.reserve()
            controller.close()
            barrier = threading.Barrier(2)

            def recover_once() -> str:
                local = ControllerStore(path, spec, cert)
                try:
                    barrier.wait(timeout=5)
                    local.record_recovery(key)
                    return "ok"
                except ValueError:
                    return "rejected"
                finally:
                    local.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: recover_once(), range(2)))
            self.assertEqual(outcomes.count("ok"), 1)
            self.assertEqual(outcomes.count("rejected"), 1)
            controller = ControllerStore(path, spec, cert)
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["attempts"][key]["recoveries"], 1)
            self.assertEqual(sum(e["kind"] == "recover" for e in snapshot["events"]), 1)
            self.assertEqual(controller.pending(), [key])
            controller.close()

    def test_concurrent_reject_is_applied_once(self) -> None:
        spec = one_job(score=0)
        spec.update(drift=0, unsafe=0, late=1, cost=1)
        cert = game.synthesize(spec)
        self.assertTrue(cert["feasible"])
        self.assertEqual(cert["policy"][game.encode(game.initial(spec))]["action"], "reject")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "controller.db"
            ControllerStore(path, spec, cert).close()
            barrier = threading.Barrier(2)

            def reject_once() -> str:
                controller = ControllerStore(path, spec, cert)
                try:
                    barrier.wait(timeout=5)
                    controller.apply_reject()
                    return "ok"
                except ValueError:
                    return "rejected"
                finally:
                    controller.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: reject_once(), range(2)))
            self.assertEqual(outcomes.count("ok"), 1)
            self.assertEqual(outcomes.count("rejected"), 1)
            controller = ControllerStore(path, spec, cert)
            self.assertTrue(controller.done())
            reject_events = [e for e in controller.events() if e["kind"] == "reject"]
            self.assertEqual(len(reject_events), 1)
            controller.close()


    def test_lower_physical_cost_is_conservatively_charged(self) -> None:
        spec = one_job(score=2)
        spec.update(drift=0, unsafe=0, cost=6)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, action, declared_cost, _ = controller.reserve()
            self.assertEqual(action, "accept")
            adapter, target = controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key, adapter, target, declared_cost - 1, 0))
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["state"][5], spec["cost"] - declared_cost)
            self.assertTrue(check_runtime(spec, cert, snapshot, receiver.outcomes(), receiver.effects()))
            controller.close(); receiver.close()

    def test_replay_rejects_orphan_receiver_records(self) -> None:
        spec = one_job(score=2)
        cert = game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            controller = ControllerStore(root / "controller.db", spec, cert)
            receiver = ReceiverStore(root / "receiver.db")
            key, _, cost, _ = controller.reserve()
            adapter, target = controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key, adapter, target, cost, 0))
            snapshot = controller.snapshot()
            outcomes = receiver.outcomes()
            effects = receiver.effects()
            outcomes["orphan"] = {"status": "canceled", "adapter": None, "job": None,
                                  "cost": 0, "unsafe": 0}
            self.assertFalse(check_runtime(spec, cert, snapshot, outcomes, effects))
            controller.close(); receiver.close()

    def test_boolean_values_do_not_pass_integer_schema(self) -> None:
        spec = one_job()
        cert = game.synthesize(spec)
        bad = copy.deepcopy(spec)
        bad["crashes"] = True
        self.assertFalse(check(bad, cert))
        with self.assertRaises(ValueError):
            game.synthesize(bad)

    def test_receipt_closed_protocol_model_is_safe_and_completable(self) -> None:
        model = protocol_model.explore("receipt_closed", max_crashes=2)
        self.assertEqual(model["reachable_states"], 79)
        self.assertEqual(model["explored_edges"], 91)
        self.assertEqual(model["violation_kinds"], [])
        self.assertTrue(model["all_states_complete_without_more_crashes"])
        self.assertEqual(model["max_no_further_crash_steps"], 6)

    def test_protocol_model_finds_expected_mutant_counterexamples(self) -> None:
        result = protocol_model.run_all(max_crashes=2)
        models = result["models"]
        self.assertEqual(
            len(models["send_before_reserve"]["shortest_counterexamples"]
                ["effect_without_durable_reservation"]["trace"]), 2
        )
        self.assertEqual(
            len(models["fresh_retry"]["shortest_counterexamples"]["duplicate_effect"]["trace"]), 8
        )
        self.assertEqual(
            len(models["query_without_fence"]["shortest_counterexamples"]["duplicate_effect"]["trace"]), 8
        )
        self.assertEqual(
            len(models["settle_before_charge"]["shortest_counterexamples"]
                ["settled_before_recovery_charge"]["trace"]), 6
        )

    def test_ambiguity_witness_has_identical_local_observations(self) -> None:
        witness = protocol_model.ambiguity_witness()
        worlds = witness["worlds"]
        self.assertEqual(
            worlds["effect_not_executed"]["controller_observation"],
            worlds["effect_executed_reply_lost"]["controller_observation"],
        )
        self.assertIn(
            "two effects",
            witness["deterministic_choices"]["retry_with_fresh_identifier"]
                   ["effect_executed_reply_lost"],
        )
        self.assertIn(
            "progress violated",
            witness["deterministic_choices"]["do_not_retry"]["effect_not_executed"],
        )



if __name__ == "__main__":
    unittest.main()
