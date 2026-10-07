"""Evidence completeness regressions. Every mutation is made in a disposable copy."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from evidence_check import EvidenceError, expected_database_paths, verify_database_evidence
from verify_results import verify_model


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="receipt-evidence-")
        cls.addClassCleanup(cls.tmp.cleanup)
        # Include URI-significant characters to exercise a correctly escaped URI.
        cls.root = Path(cls.tmp.name) / ("packet # read only" if os.name == 'nt' else "packet #? read only")
        shutil.copytree(ROOT / "results", cls.root)

    def sql_rejected(self, rel, statement, parameters=(), message=""):
        path = self.root / rel
        original = path.read_bytes()
        try:
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute(statement, parameters)
            with self.assertRaisesRegex(EvidenceError, message):
                verify_database_evidence(self.root)
        finally:
            path.write_bytes(original)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)

    def test_all_31_pairs_match_without_writes(self):
        paths = expected_database_paths()
        before = {p: (self.root / p).read_bytes() for p in paths}
        result = verify_database_evidence(self.root)
        self.assertEqual(result["sqlite_databases_checked"], 62)
        self.assertEqual(result["database_json_pairs_checked"], 31)
        self.assertEqual(result["database_local_effect_pairs_checked"], 31)
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in paths})
        self.assertFalse(list(self.root.rglob("*.db-wal")))
        self.assertFalse(list(self.root.rglob("*.db-shm")))
        self.assertFalse(list(self.root.rglob("*.db-journal")))

    def test_each_of_62_required_database_omissions_is_rejected(self):
        for rel in expected_database_paths():
            with self.subTest(database=str(rel)):
                path = self.root / rel
                saved = path.with_suffix(".held")
                path.rename(saved)
                try:
                    with self.assertRaisesRegex(EvidenceError, "database inventory mismatch; missing="):
                        verify_database_evidence(self.root)
                finally:
                    saved.rename(path)

    def test_unexpected_database_is_rejected(self):
        path = self.root / "unexpected.db"
        path.write_bytes(b"")
        try:
            with self.assertRaisesRegex(EvidenceError, "unexpected=.*unexpected.db"):
                verify_database_evidence(self.root)
        finally:
            path.unlink()

    def test_side_file_is_rejected(self):
        path = self.root / "pilot/runtime/receiver.db-wal"
        path.write_bytes(b"disposable-negative-control")
        try:
            with self.assertRaisesRegex(EvidenceError, "side files"):
                verify_database_evidence(self.root)
        finally:
            path.unlink()

    def test_missing_done_effect_is_rejected_for_each_adapter(self):
        for adapter in ("admission", "cache", "tier"):
            with self.subTest(adapter=adapter):
                self.sql_rejected(f"faults/cases/{adapter}-none/receiver.db",
                                  "DELETE FROM effects WHERE id=(SELECT id FROM effects ORDER BY id LIMIT 1)",
                                  message="DONE/effect")

    def test_effect_target_and_adapter_are_not_dropped_from_check(self):
        for column, value in (("job", "wrong-target"), ("adapter", "wrong-adapter")):
            with self.subTest(column=column):
                self.sql_rejected("pilot/runtime/receiver.db",
                                  f"UPDATE effects SET {column}=? WHERE id=(SELECT id FROM effects LIMIT 1)",
                                  (value,), "DONE/effect adapter/target")

    def test_wrong_terminal_target_is_rejected(self):
        self.sql_rejected("pilot/runtime/receiver.db",
                          "UPDATE outcomes SET job='wrong-target' WHERE state='done'",
                          message="terminal outcomes")

    def test_controller_attempt_target_and_recovery_count_are_bound(self):
        for column, value in (("target", "wrong-target"), ("recoveries", 99)):
            with self.subTest(column=column):
                self.sql_rejected("pilot/runtime/controller.db",
                                  f"UPDATE attempts SET {column}=? WHERE id=(SELECT id FROM attempts LIMIT 1)",
                                  (value,), "state/events/attempt snapshot")

    def test_bound_spec_certificate_namespace_and_state_are_checked(self):
        mutations = [
            ("bound_spec", "{}", "bound specification"),
            ("bound_certificate", "{}", "bound certificate"),
            ("controller_id", "different-controller", "snapshot"),
            ("adapter", "cache", "snapshot"),
            ("state", "[3,1,1,0,0,6]", "snapshot"),
            ("initial_budget", "[99,1]", "initial budget"),
        ]
        for key, value, message in mutations:
            with self.subTest(key=key):
                self.sql_rejected("pilot/runtime/controller.db", "UPDATE meta SET value=? WHERE key=?",
                                  (value, key), message)

    def test_job_counters_and_state_are_reconstructed(self):
        for column, value in (("crashes", 99), ("next_attempt", 99), ("state", "selected")):
            with self.subTest(column=column):
                self.sql_rejected("pilot/runtime/controller.db",
                                  f"UPDATE jobs SET {column}=? WHERE job_index=0", (value,),
                                  "job state/counters")

    def test_controller_event_omission_is_rejected(self):
        self.sql_rejected("pilot/runtime/controller.db",
                          "DELETE FROM audit WHERE seq=(SELECT MAX(seq) FROM audit)",
                          message="state/events/attempt snapshot")

    def test_receiver_event_omission_is_rejected(self):
        self.sql_rejected("pilot/runtime/receiver.db",
                          "DELETE FROM audit WHERE seq=(SELECT MAX(seq) FROM audit)",
                          message="terminal audit")

    def test_local_adapter_effects_are_checked(self):
        for adapter, statement, expected in (
            ("admission", "DELETE FROM queue_effects", "admission local effects"),
            ("cache", "UPDATE cache_state SET value='wrong-value'", "cache local effects"),
            ("tier", "UPDATE tier_state SET location='wrong-location'", "tier local effects"),
        ):
            with self.subTest(adapter=adapter):
                self.sql_rejected(f"faults/cases/{adapter}-none/receiver.db", statement,
                                  message=expected)

    def test_pilot_duplicate_exports_must_agree(self):
        path = self.root / "pilot/runtime.json"
        original = path.read_bytes()
        try:
            data = json.loads(original)
            data["adapter"] = "cache"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(EvidenceError, "pilot duplicated runtime.json"):
                verify_database_evidence(self.root)
        finally:
            path.write_bytes(original)

    def test_full_cli_rejects_missing_database_before_recomputing(self):
        path = self.root / "faults/cases/tier-two_post_effect/receiver.db"
        saved = path.with_suffix(".held")
        path.rename(saved)
        try:
            result = subprocess.run([sys.executable, "-S", str(ROOT / "verify_results.py"),
                                     "--results", str(self.root)], capture_output=True, text=True,
                                    timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("database inventory mismatch", result.stderr)
            self.assertFalse(path.exists())
        finally:
            saved.rename(path)

    def test_database_phase_cli_rejects_deleted_done_effect(self):
        path = self.root / "pilot/runtime/receiver.db"
        original = path.read_bytes()
        try:
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute("DELETE FROM effects")
            result = subprocess.run([sys.executable, "-S", str(ROOT / "verify_results.py"),
                                     "--phase", "databases", "--results", str(self.root)],
                                    capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("DONE/effect", result.stderr)
        finally:
            path.write_bytes(original)


class CurrentRunStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="receipt-current-storage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("pilot", "faults"):
            shutil.copytree(ROOT / "results" / name, self.root / name)

    def owned_database(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.execute("CREATE TABLE owned_probe(value TEXT)")
                db.execute("INSERT INTO owned_probe VALUES (?)", (rel,))
        return path

    def test_current_run_stores_do_not_expand_canonical_inventory(self):
        paths = expected_database_paths()
        before = {p: (self.root / p).read_bytes() for p in paths}
        probes = [self.owned_database(rel) for rel in (
            "current/example/controller.db", "current/example/receiver.db",
            "current/reproduced/pilot/runtime/controller.db",
            "current/reproduced/pilot/runtime/receiver.db",
            "current/reproduced/faults/cases/tier-none/controller.db",
            "current/reproduced/faults/cases/tier-none/receiver.db",
        )]
        current_before = {p: p.read_bytes() for p in probes}
        self.assertEqual(verify_database_evidence(self.root), {
            "sqlite_databases_checked": 62, "database_json_pairs_checked": 31,
            "database_local_effect_pairs_checked": 31,
        })
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in paths})
        self.assertEqual(current_before, {p: p.read_bytes() for p in probes})

    def test_unknown_stores_at_historical_and_current_roots_are_rejected(self):
        for rel in (
            "pilot/runtime/unexpected.db", "pilot/unlisted/controller.db",
            "faults/cases/tier-none/unexpected.db",
            "faults/cases/unlisted/receiver.db",
            "pilot/runtime/current/controller.db",
            "current/example/unexpected.db",
            "current/reproduced/faults/cases/unlisted/controller.db",
            "current/unlisted/controller.db",
        ):
            with self.subTest(database=rel):
                path = self.owned_database(rel)
                try:
                    with self.assertRaisesRegex(EvidenceError, "database inventory mismatch") as caught:
                        verify_database_evidence(self.root)
                    self.assertIn("unexpected=", str(caught.exception))
                    self.assertIn(repr(str(Path(rel))), str(caught.exception))
                finally:
                    path.unlink()


class ModelCommandTests(unittest.TestCase):
    def test_documented_flat_model_commands_and_corruptions(self):
        with tempfile.TemporaryDirectory(prefix="receipt-model-command-") as tmp:
            root = Path(tmp)
            out = root / "model"
            generated = subprocess.run([sys.executable, "-S", str(ROOT / "reproduce.py"),
                                        "--phase", "model", "--output", str(out)],
                                       cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual({p.name for p in out.iterdir()}, {"model.json", "summary.json"})
            command = [sys.executable, "-S", str(ROOT / "verify_results.py"),
                       "--phase", "model", "--results", str(out)]
            verified = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(verify_model(out)["protocol_model_states"], 79)
            for filename in ("model.json", "summary.json"):
                with self.subTest(record=filename):
                    path = out / filename
                    original = path.read_bytes()
                    try:
                        data = json.loads(original)
                        if filename == "model.json":
                            data["models"]["receipt_closed"]["reachable_states"] += 1
                        else:
                            data["correct_reachable_states"] += 1
                        path.write_text(json.dumps(data))
                        failed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                                text=True, timeout=30)
                        self.assertNotEqual(failed.returncode, 0)
                        self.assertIn("RESULT VERIFICATION FAILED", failed.stderr)
                    finally:
                        path.write_bytes(original)
                    path.unlink()
                    try:
                        missing = subprocess.run(command, cwd=ROOT, capture_output=True,
                                                 text=True, timeout=30)
                        self.assertNotEqual(missing.returncode, 0)
                    finally:
                        path.write_bytes(original)
            self.assertFalse((out / "pilot").exists())
            self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True,
                                           text=True, timeout=30).returncode, 0)


if __name__ == "__main__":
    unittest.main()
