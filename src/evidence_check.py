"""Read-only cross-check of the exact retained pilot and fault database packet.

This checks local SQLite rows against the accompanying JSON and replay semantics.
It does not authenticate either representation or prove cross-database real-time
ordering, storage durability, or completeness against an external execution.
No runtime store is constructed: those constructors create tables and enable WAL.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from trace_check import check_runtime

ADAPTERS = ("admission", "cache", "tier")
# The exact declared campaign, not inferred from whichever files survive.
SCHEDULES = {
    "none": [],
    "pre_effect": ["after_reserve"],
    "post_effect": ["after_execute"],
    "close_interrupted": ["after_reserve", "after_close"],
    "recover_interrupted": ["after_reserve", "after_recover"],
    "two_pre_effect": ["after_reserve", "after_reserve"],
    "pre_then_post": ["after_reserve", "after_execute"],
    "post_then_pre": ["after_execute", "after_reserve"],
    "two_post_effect": ["after_execute", "after_execute"],
    "post_settle": ["after_settle"],
}


class EvidenceError(ValueError):
    """A required record is absent, malformed, or inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _json_text(text: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in obj, f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def invalid_constant(token: str) -> Any:
        raise EvidenceError(f"non-finite JSON constant: {token}")

    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def load_json(path: Path) -> Any:
    require(path.is_file() and not path.is_symlink(), f"missing/linked record: {path}")
    require(path.stat().st_size <= 96 * 1024 * 1024, f"oversized record: {path}")
    return _json_text(path.read_text())


def equal(actual: Any, expected: Any, context: str) -> None:
    # JSON canonicalization also distinguishes false from 0 and 1.0 from 1.
    def canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    require(canonical(actual) == canonical(expected), f"{context}: records differ")


def expected_database_paths() -> tuple[Path, ...]:
    roots = [Path("pilot/runtime")]
    roots += [Path("faults/cases") / f"{a}-{s}" for a in ADAPTERS for s in SCHEDULES]
    return tuple(p / name for p in roots for name in ("controller.db", "receiver.db"))


def _schema(db: sqlite3.Connection, role: str, label: str) -> None:
    require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            f"{label}: integrity check failed")
    require(db.execute("PRAGMA foreign_key_check").fetchall() == [],
            f"{label}: foreign-key check failed")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = ({"meta", "jobs", "attempts", "audit", "sqlite_sequence"}
                if role == "controller" else
                {"outcomes", "effects", "queue_effects", "cache_state", "tier_state",
                 "audit", "sqlite_sequence"})
    require(tables == expected, f"{label}: unexpected table inventory")
    if role == "controller":
        indexes = {r[1]: r for r in db.execute("PRAGMA index_list('attempts')")}
        index = indexes.get("one_pending_attempt")
        require(index is not None and index[2] == 1 and index[4] == 1,
                f"{label}: missing unique partial one-pending index")
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='one_pending_attempt'").fetchone()
        require(sql is not None and "WHERE state='pending'" in sql[0],
                f"{label}: wrong one-pending predicate")
        require(db.execute("SELECT COUNT(*) FROM attempts WHERE state='pending'").fetchone()[0] <= 1,
                f"{label}: multiple pending attempts")
    else:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='outcomes'").fetchone()[0]
        require("state='done' AND adapter IS NOT NULL AND job IS NOT NULL" in sql,
                f"{label}: terminal-state constraint missing")


def _events(db: sqlite3.Connection, label: str) -> list[dict[str, Any]]:
    rows = db.execute("SELECT seq,event FROM audit ORDER BY seq").fetchall()
    equal([row[0] for row in rows], list(range(1, len(rows) + 1)),
          f"{label}: audit sequence")
    return [_json_text(row[1]) for row in rows]


def _read_controller(db: sqlite3.Connection, spec: dict[str, Any], cert: dict[str, Any],
                     record: dict[str, Any], label: str) -> dict[str, Any]:
    meta = dict(db.execute("SELECT key,value FROM meta"))
    require(set(meta) == {"state", "initial_budget", "bound_spec", "bound_certificate",
                          "controller_id", "adapter"}, f"{label}: metadata inventory")
    equal(_json_text(meta["bound_spec"]), spec, f"{label}: bound specification")
    equal(_json_text(meta["bound_certificate"]), cert, f"{label}: bound certificate")
    equal(_json_text(meta["initial_budget"]), [spec["cost"], spec["unsafe"]],
          f"{label}: initial budget")
    state = _json_text(meta["state"])
    require(isinstance(state, list) and len(state) == 6 and
            all(type(x) is int and x >= 0 for x in state), f"{label}: malformed state")
    attempts = {}
    columns = ("id,job_index,action,adapter,target,state,upper_cost,upper_unsafe,"
               "recoveries,recovery_ticket,actual_cost,actual_unsafe")
    for r in db.execute(f"SELECT {columns} FROM attempts ORDER BY id"):
        require((r[10] is None) == (r[11] is None), f"{label}: partial actual quantities")
        attempts[r[0]] = {"job": r[1], "action": r[2], "adapter": r[3], "target": r[4],
                          "status": r[5], "upper": [r[6], r[7]], "recoveries": r[8],
                          "ticket": r[9], "actual": [r[10], r[11]] if r[10] is not None else None}
    snapshot = {"controller_id": meta["controller_id"], "adapter": meta["adapter"],
                "state": state, "done": state[0] == len(spec["jobs"]),
                "attempts": attempts, "events": _events(db, label)}
    equal(snapshot, record["snapshot"], f"{label}: state/events/attempt snapshot")
    # jobs is not part of the JSON snapshot. Reconstruct it from validated events
    # rather than leaving its counters and terminal state unchecked.
    expected_jobs: dict[int, list[Any]] = {}
    for event in snapshot["events"]:
        kind = event["kind"]
        if kind == "select":
            i = event["job"]
            require(i not in expected_jobs, f"{label}: duplicate job selection")
            expected_jobs[i] = [i, event["action"], "selected", 0, 0]
        elif kind == "reserve":
            expected_jobs[event["job"]][4] += 1
        elif kind == "recover":
            expected_jobs[event["job"]][3] += 1
        elif kind == "reject":
            expected_jobs[event["job"]][2] = "done"
        elif kind == "settle" and event["status"] == "done":
            expected_jobs[attempts[event["id"]]["job"]][2] = "done"
    observed_jobs = db.execute(
        "SELECT job_index,action,state,crashes,next_attempt FROM jobs ORDER BY job_index"
    ).fetchall()
    equal(observed_jobs, [expected_jobs[i] for i in sorted(expected_jobs)],
          f"{label}: job state/counters")
    return snapshot


def _read_receiver(db: sqlite3.Connection, record: dict[str, Any], label: str
                   ) -> tuple[dict[str, Any], dict[str, Any]]:
    outcomes = {r[0]: {"status": r[1], "adapter": r[2], "job": r[3],
                        "cost": r[4], "unsafe": r[5]}
                for r in db.execute("SELECT id,state,adapter,job,cost,unsafe FROM outcomes ORDER BY id")}
    equal(outcomes, record["receiver_outcomes"], f"{label}: terminal outcomes")
    effect_rows = db.execute("SELECT id,adapter,job,cost,unsafe FROM effects ORDER BY id").fetchall()
    # JSON effects carry only numeric quantities. Cross-bind their adapter and
    # target columns to DONE rows; otherwise a wrong-target physical row escapes
    # a comparison that drops those columns.
    expected_effects = [(key, out["adapter"], out["job"], out["cost"], out["unsafe"])
                        for key, out in sorted(outcomes.items()) if out["status"] == "done"]
    equal(effect_rows, expected_effects, f"{label}: DONE/effect adapter/target binding")
    effects = {r[0]: [r[3], r[4]] for r in effect_rows}
    equal(effects, record["effects"], f"{label}: effect quantities")

    # One pending attempt serializes terminal installation for these local
    # complete histories. There is one receiver audit entry per first terminal
    # installation; repeated close/execute calls do not add entries.
    expected_audit = []
    for event in record["snapshot"]["events"]:
        if event["kind"] != "settle":
            continue
        key = event["id"]
        outcome = outcomes[key]
        if outcome["status"] == "done":
            expected_audit.append({"kind": "done", "id": key, "adapter": outcome["adapter"],
                                   "job": outcome["job"], "cost": outcome["cost"],
                                   "unsafe": outcome["unsafe"]})
        else:
            expected_audit.append({"kind": "canceled", "id": key})
    audit = _events(db, label)
    equal(audit, expected_audit, f"{label}: terminal audit")
    require(len(audit) == len(outcomes), f"{label}: terminal audit not one-to-one")

    # Check the actual locally represented side effects as well as the generic
    # effects ledger. No external queue/cache/tier or production semantics are
    # inferred from these tables.
    done = [e for e in audit if e["kind"] == "done"]
    queue = [(e["id"], e["job"]) for e in done if e["adapter"] == "admission"]
    equal(db.execute("SELECT seq,id,job FROM queue_effects ORDER BY seq").fetchall(),
          [(i + 1, key, job) for i, (key, job) in enumerate(queue)],
          f"{label}: admission local effects")
    equal(dict(db.execute("SELECT key,value FROM cache_state")),
          {e["job"]: "computed" for e in done if e["adapter"] == "cache"},
          f"{label}: cache local effects")
    equal(dict(db.execute("SELECT key,location FROM tier_state")),
          {e["job"]: "fallback" for e in done if e["adapter"] == "tier"},
          f"{label}: tier local effects")
    return outcomes, effects


def verify_pair(case: Path, adapter: str, cuts: list[str]) -> dict[str, Any]:
    """Check one complete local history without modifying either database."""
    label = str(case)
    try:
        spec = load_json(case / "spec.json")
        cert = load_json(case / "certificate.json")
        record = load_json(case / "runtime.json")
        require(record["valid"] is True, f"{label}: invalid runtime flag")
        equal(record["adapter"], adapter, f"{label}: case adapter")
        equal(record["cuts"], cuts, f"{label}: case cuts")
        equal(record["snapshot"]["adapter"], adapter, f"{label}: snapshot adapter")
        require(check_runtime(spec, cert, record["snapshot"], record["receiver_outcomes"],
                              record["effects"]), f"{label}: JSON history replay rejected")
        require(record["snapshot"]["done"] is True, f"{label}: incomplete execution")
        dbs = {}
        for role in ("controller", "receiver"):
            path = case / f"{role}.db"
            require(path.is_file() and not path.is_symlink(), f"missing/linked database: {path}")
            for suffix in ("-wal", "-shm", "-journal"):
                require(not Path(str(path) + suffix).exists(), f"{path}: side-file dependency")
            uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                _schema(db, role, f"{label}/{role}")
                if role == "controller":
                    dbs[role] = _read_controller(db, spec, cert, record, f"{label}/{role}")
                else:
                    dbs[role] = _read_receiver(db, record, f"{label}/{role}")
        require(check_runtime(spec, cert, dbs["controller"], *dbs["receiver"]),
                f"{label}: database-derived history replay rejected")
        return record
    except EvidenceError:
        raise
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, IndexError) as exc:
        raise EvidenceError(f"{label}: malformed or unreadable evidence: {exc}") from exc


def verify_database_evidence(root: Path) -> dict[str, int]:
    """Require exactly the pilot pair plus 3 adapters x 10 fault-schedule pairs."""
    root = Path(root)
    expected = set(expected_database_paths())
    actual = {p.relative_to(root) for p in root.rglob("*.db")}
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    require(not missing and not extra,
            f"database inventory mismatch; missing={[str(p) for p in missing]}; "
            f"unexpected={[str(p) for p in extra]}")
    for pattern in ("*.db-wal", "*.db-shm", "*.db-journal"):
        require(not list(root.rglob(pattern)), f"result packet has SQLite side files: {pattern}")
    expected_cases = {f"{a}-{s}" for a in ADAPTERS for s in SCHEDULES}
    actual_cases = {p.name for p in (root / "faults/cases").iterdir()}
    require(actual_cases == expected_cases, "fault case inventory mismatch")

    pilot_case = root / "pilot/runtime"
    pilot = verify_pair(pilot_case, "admission", ["after_execute"])
    for filename in ("spec.json", "certificate.json", "runtime.json"):
        equal(load_json(root / "pilot" / filename), load_json(pilot_case / filename),
              f"pilot duplicated {filename}")
    pilot_summary = load_json(root / "pilot/summary.json")
    for key, value in {"runtime_valid": True,
                       "runtime_attempts": len(pilot["snapshot"]["attempts"]),
                       "runtime_effects": len(pilot["effects"])}.items():
        equal(pilot_summary[key], value, f"pilot summary {key}")

    schedules = load_json(root / "faults/schedules.json")
    require(isinstance(schedules, list) and len(schedules) == len(expected_cases),
            "fault schedule summary count mismatch")
    summaries = {f'{row["adapter"]}-{row["schedule"]}': row for row in schedules}
    require(set(summaries) == expected_cases, "fault schedule summary inventory mismatch")
    for adapter in ADAPTERS:
        for schedule, cuts in SCHEDULES.items():
            name = f"{adapter}-{schedule}"
            record = verify_pair(root / "faults/cases" / name, adapter, cuts)
            expected_summary = {"adapter": adapter, "schedule": schedule, "cuts": cuts,
                                "attempts": len(record["snapshot"]["attempts"]),
                                "effects": len(record["effects"]),
                                "launches": len(record["launches"]),
                                "final_state": record["snapshot"]["state"], "valid": True}
            equal(summaries[name], expected_summary, f"{name}: schedule summary")
    return {"sqlite_databases_checked": len(expected), "database_json_pairs_checked": 31,
            "database_local_effect_pairs_checked": 31}
