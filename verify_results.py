#!/usr/bin/env python3
"""Recheck retained receipt-closed fallback results.

The verifier recomputes claim-bearing counts, certificate validity, the small
oracle, independent runtime replay for the pilot, and descriptive quantiles.
Wall/CPU/RSS and timing samples are permitted to differ across clean replays.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
import csv
import itertools
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from checker import check
from game import ACTIONS, encode, initial, successors, synthesize
from oracle import exact
from protocol_model import explore as explore_protocol
from protocol_model import run_all as run_protocol_models
from trace_check import check_runtime
from workload import calibrate, load_rows, make_spec, to_job, windows


def load(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > 96 * 1024 * 1024:
        raise ValueError(f"missing or oversized record: {path}")
    return json.loads(path.read_text())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        raise ValueError("empty sample")
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def close(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)




def evaluate_exported_policy(spec: dict[str, Any], cert: dict[str, Any]) -> int | None:
    """Evaluate a serialized policy under a possibly stricter true model."""
    memo: dict[tuple[int, ...], int | None] = {}

    def visit(state: tuple[int, int, int, int, int, int]) -> int | None:
        if min(state[1:]) < 0:
            return None
        if state[0] == len(spec["jobs"]):
            return 0
        if state in memo:
            return memo[state]
        row = cert.get("policy", {}).get(encode(state))
        if not isinstance(row, dict) or row.get("action") not in ACTIONS:
            memo[state] = None
            return None
        totals: list[int] = []
        for nxt, charge, _ in successors(spec, state, row["action"]):
            sub = visit(nxt)
            if sub is None:
                memo[state] = None
                return None
            totals.append(charge + sub)
        memo[state] = max(totals) if totals else None
        return memo[state]

    return visit(initial(spec))

def verify(root: Path) -> dict[str, Any]:
    if not __debug__:
        raise RuntimeError("verification cannot run with Python assertions disabled")
    # Pilot: three mutually independent checks for the finite value and one
    # independent audit replay for the integrated local runtime.
    pilot = root / "pilot"
    spec = load(pilot / "spec.json")
    cert = load(pilot / "certificate.json")
    runtime = load(pilot / "runtime.json")
    assert cert["feasible"] and check(spec, cert)
    assert exact(spec) == cert["worst_cost"] == 10
    assert check_runtime(spec, cert, runtime["snapshot"], runtime["receiver_outcomes"], runtime["effects"])
    assert runtime["snapshot"]["done"] and runtime["valid"]

    # Exact small-instance oracle campaign.
    oracle_rows = load(root / "oracle" / "cases.json")
    assert len(oracle_rows) == 1200
    feasible = 0
    for row in oracle_rows:
        value = exact(row["spec"])
        regenerated = synthesize(row["spec"])
        assert value == row["oracle_cost"] == regenerated["worst_cost"]
        assert row["feasible"] == (value is not None) == regenerated["feasible"]
        assert (not regenerated["feasible"]) or check(row["spec"], regenerated)
        feasible += int(row["feasible"])
    oracle_summary = load(root / "oracle" / "summary.json")
    assert feasible == oracle_summary["feasible"] == 911
    assert oracle_summary["infeasible"] == 289 and oracle_summary["mismatches"] == 0

    micro_rows = read_csv(root / "oracle" / "micro_exhaustive.csv")
    assert len(micro_rows) == 23040
    micro_feasible = 0
    for row in micro_rows:
        micro_spec = {
            "jobs": [{
                "score": int(row["score"]),
                "accept_cost": int(row["accept_cost"]),
                "fallback_cost": int(row["fallback_cost"]),
                "accept_time": int(row["accept_time"]),
                "fallback_time": int(row["fallback_time"]),
            }],
            "drift": int(row["drift"]), "crashes": int(row["crashes"]),
            "unsafe": int(row["unsafe"]), "late": int(row["late"]),
            "cost": int(row["cost"]), "recovery_time": int(row["recovery_time"]),
            "deadline": int(row["deadline"]),
        }
        value = exact(micro_spec)
        regenerated = synthesize(micro_spec)
        recorded_value = None if row["oracle_cost"] == "" else int(row["oracle_cost"])
        assert value == recorded_value == regenerated["worst_cost"]
        recorded_feasible = bool(int(row["feasible"]))
        assert recorded_feasible == (value is not None) == regenerated["feasible"]
        assert int(row["visited_states"]) == regenerated["visited_states"]
        assert int(row["policy_rows"]) == len(regenerated["policy"])
        assert (not regenerated["feasible"]) or check(micro_spec, regenerated)
        micro_feasible += int(recorded_feasible)
    assert oracle_summary["micro_exhaustive_cases"] == 23040
    assert oracle_summary["micro_exhaustive_feasible"] == micro_feasible == 16864
    assert oracle_summary["micro_exhaustive_infeasible"] == 6176
    assert oracle_summary["micro_exhaustive_mismatches"] == 0

    # Certificate and producer mutation campaigns.
    structural = load(root / "mutants" / "structural_value.json")
    semantic = load(root / "mutants" / "semantic.json")
    assert len(structural) == 2072 and all(row["rejected"] for row in structural)
    assert {row["kind"] for row in structural} == {
        "root_value", "missing_root", "unknown_action", "extra_row",
        "charge", "outcome_meta", "visited_states",
    }
    assert len(semantic) == 592
    semantic_counts: dict[str, dict[str, int]] = {}
    for kind in sorted({row["kind"] for row in semantic}):
        subset = [row for row in semantic if row["kind"] == kind]
        semantic_counts[kind] = {"cases": len(subset), "rejected": sum(row["rejected"] for row in subset)}
    assert semantic_counts == {
        "ignore_recovery_time": {"cases": 296, "rejected": 49},
        "invert_score_classes": {"cases": 296, "rejected": 132},
    }

    # Independent bounded protocol model and shortest counterexamples.
    retained_model = load(root / "model" / "model.json")
    recomputed_model = run_protocol_models(max_crashes=2)
    assert {key: value for key, value in retained_model.items()
            if key != "receipt_closed_crash_sweep"} == recomputed_model
    models = retained_model["models"]
    expected_sweep = {
        "0": (5, 4, 4),
        "1": (23, 24, 6),
        "2": (79, 91, 6),
        "3": (211, 260, 6),
        "4": (495, 633, 6),
    }
    for crashes, (states, edges, steps) in expected_sweep.items():
        observed = retained_model["receipt_closed_crash_sweep"][crashes]
        recomputed = explore_protocol("receipt_closed", max_crashes=int(crashes))
        assert observed["reachable_states"] == recomputed["reachable_states"] == states
        assert observed["explored_edges"] == recomputed["explored_edges"] == edges
        assert observed["max_no_further_crash_steps"] == steps
        assert observed["violation_kinds"] == []
        assert observed["all_states_complete_without_more_crashes"]
    correct_model = models["receipt_closed"]
    assert correct_model["reachable_states"] == 79
    assert correct_model["explored_edges"] == 91
    assert correct_model["violation_kinds"] == []
    assert correct_model["all_states_complete_without_more_crashes"]
    assert correct_model["max_no_further_crash_steps"] == 6
    expected_model_failures = {
        "send_before_reserve": {
            "effect_without_durable_reservation": 2,
            "duplicate_effect": 6,
        },
        "fresh_retry": {"duplicate_effect": 8},
        "query_without_fence": {"duplicate_effect": 8},
        "settle_before_charge": {"settled_before_recovery_charge": 6},
    }
    for variant, failures in expected_model_failures.items():
        for kind, steps in failures.items():
            assert len(models[variant]["shortest_counterexamples"][kind]["trace"]) == steps

    # Cross-trace workload-derived specifications and paired policy comparison.
    # The code trace freezes all thresholds; the conversation trace is evaluated
    # without refitting.  Every retained row is regenerated, not just recounted.
    source_paths = {
        "code": ROOT / "data" / "azure_llm_code_excerpt.csv",
        "conversation": ROOT / "data" / "azure_llm_conv_excerpt.csv",
    }
    source_rows = {name: load_rows(path) for name, path in source_paths.items()}
    assert {name: len(rows) for name, rows in source_rows.items()} == {
        "code": 128, "conversation": 128,
    }
    assert (source_rows["code"][0]["context"], source_rows["code"][0]["generated"]) == (4808, 10)
    assert (source_rows["code"][-1]["context"], source_rows["code"][-1]["generated"]) == (526, 7)
    assert (source_rows["conversation"][0]["context"], source_rows["conversation"][0]["generated"]) == (374, 44)
    assert (source_rows["conversation"][-1]["context"], source_rows["conversation"][-1]["generated"]) == (4107, 49)

    calibration = calibrate(source_rows["code"])
    assert calibration == {
        "schema": 2, "rows": 128,
        "context_q33": 583, "context_q67": 2647,
        "generated_q33": 9, "generated_q67": 18,
        "gap_q33_ms": 48, "gap_q67_ms": 169,
        "context_unit": 1650, "generated_unit": 13,
        "recovery_time": 2, "deadline": 4,
    }
    assert calibrate(source_rows["conversation"]) != calibration
    assert load(root / "trace" / "calibration.json") == calibration
    source_windows = {name: windows(path, 8, calibration) for name, path in source_paths.items()}
    assert {name: len(items) for name, items in source_windows.items()} == {
        "code": 16, "conversation": 16,
    }

    trace_rows = read_csv(root / "trace" / "trace_results.csv")
    method_names = [
        "adaptive", "always_accept", "threshold_1", "threshold_2",
        "always_fallback", "ignore_recovery",
    ]
    expected_trace_rows = 2 * 16 * 3 * 3 * 2 * len(method_names)
    assert len(trace_rows) == expected_trace_rows == 3456
    trace_index: dict[tuple[str, int, int, int, str, str], dict[str, str]] = {}
    for row in trace_rows:
        key = (row["source"], int(row["window"]), int(row["drift"]),
               int(row["crashes"]), row["budget"], row["method"])
        assert key not in trace_index
        trace_index[key] = row
    assert len(trace_index) == expected_trace_rows

    method_counts = {name: 0 for name in method_names}
    source_method_counts = {source: {name: 0 for name in method_names} for source in source_paths}
    paired: dict[tuple[str, int, int, int, str], dict[str, int]] = {}
    for source, trace_windows in source_windows.items():
        for window_index, jobs in enumerate(trace_windows):
            for drift, crashes, level in itertools.product(range(3), range(3), ("tight", "roomy")):
                spec = make_spec(jobs, drift, crashes, level, calibration)
                case_key = (source, window_index, drift, crashes, level)
                paired[case_key] = {}
                for method in method_names:
                    if method == "adaptive":
                        certificate = synthesize(spec)
                        value = certificate["worst_cost"]
                        feasible = certificate["feasible"]
                        assert (not feasible) or check(spec, certificate)
                    elif method == "ignore_recovery":
                        optimistic = copy.deepcopy(spec)
                        optimistic["recovery_time"] = 0
                        certificate = synthesize(optimistic)
                        value = evaluate_exported_policy(spec, certificate) if certificate["feasible"] else None
                        feasible = value is not None
                    else:
                        threshold = {
                            "always_accept": 0, "threshold_1": 1,
                            "threshold_2": 2, "always_fallback": 3,
                        }[method]
                        allowed = {
                            i: (("accept" if job["score"] >= threshold else "fallback"), "reject")
                            for i, job in enumerate(jobs)
                        }
                        certificate = synthesize(spec, allowed)
                        value = certificate["worst_cost"]
                        feasible = certificate["feasible"]
                    retained = trace_index[(source, window_index, drift, crashes, level, method)]
                    assert int(retained["feasible"]) == int(feasible)
                    assert retained["worst_cost"] == ("" if value is None else str(value))
                    assert int(retained["visited_states"]) == certificate["visited_states"]
                    assert math.isfinite(float(retained["planning_ms"])) and float(retained["planning_ms"]) >= 0
                    method_counts[method] += int(feasible)
                    source_method_counts[source][method] += int(feasible)
                    paired[case_key][method] = int(feasible)

    assert method_counts == {
        "adaptive": 345,
        "always_accept": 287,
        "always_fallback": 117,
        "ignore_recovery": 153,
        "threshold_1": 248,
        "threshold_2": 105,
    }
    assert source_method_counts == {
        "code": {
            "adaptive": 172, "always_accept": 144, "threshold_1": 125,
            "threshold_2": 54, "always_fallback": 57, "ignore_recovery": 80,
        },
        "conversation": {
            "adaptive": 173, "always_accept": 143, "threshold_1": 123,
            "threshold_2": 51, "always_fallback": 60, "ignore_recovery": 73,
        },
    }
    fixed_methods = ["always_accept", "threshold_1", "threshold_2", "always_fallback"]
    adaptive_only_any = {
        source: sum(values["adaptive"] and not any(values[m] for m in fixed_methods)
                    for key, values in paired.items() if source == "all" or key[0] == source)
        for source in ("all", *source_paths)
    }
    assert adaptive_only_any == {"all": 22, "code": 12, "conversation": 10}
    assert all(not values[m] or values["adaptive"] for values in paired.values() for m in method_names[1:])

    trace_summary = load(root / "trace" / "summary.json")
    assert trace_summary["calibration"] == calibration
    assert trace_summary["source_rows"] == {"code": 128, "conversation": 128}
    assert trace_summary["total_source_rows"] == 256
    assert trace_summary["windows_per_source"] == {"code": 16, "conversation": 16}
    assert trace_summary["cases_per_source_method"] == 288
    assert trace_summary["cases_per_method"] == 576
    assert {name: row["feasible"] for name, row in trace_summary["methods"].items()} == method_counts
    assert {source: trace_summary["methods"]["adaptive"]["per_source"][source]["feasible"]
            for source in source_paths} == {"code": 172, "conversation": 173}
    assert {source: trace_summary["adaptive_only_vs_any_fixed"][source]["adaptive_only"]
            for source in ("all", *source_paths)} == adaptive_only_any
    expected_score_counts = {
        name: {str(score): sum(to_job(row, calibration)["score"] == score for row in rows)
               for score in (0, 1, 2)}
        for name, rows in source_rows.items()
    }
    assert expected_score_counts == {
        "code": {"0": 20, "1": 63, "2": 45},
        "conversation": {"0": 16, "1": 58, "2": 54},
    }
    assert trace_summary["score_counts"] == expected_score_counts

    # Crash schedules and receiver controls.
    schedules = load(root / "faults" / "schedules.json")
    controls = load(root / "faults" / "controls.json")
    assert len(schedules) == 30 and all(row["valid"] for row in schedules)
    assert len(controls) == 5 and all(row["passes"] for row in controls)
    runtime_records = sorted((root / "faults" / "cases").glob("*/runtime.json"))
    assert len(runtime_records) == 30
    for runtime_path in runtime_records:
        case_root = runtime_path.parent
        case_spec = load(case_root / "spec.json")
        case_cert = load(case_root / "certificate.json")
        case_runtime = load(runtime_path)
        assert case_runtime["valid"] and case_runtime["snapshot"]["done"]
        assert case_runtime["snapshot"]["adapter"] == case_runtime["adapter"]
        assert check_runtime(case_spec, case_cert, case_runtime["snapshot"],
                             case_runtime["receiver_outcomes"], case_runtime["effects"])

    # Every root-to-terminal adversarial path of the pilot strategy, across
    # adapters and two recovery placements where a crash occurs.
    paths = load(root / "refinement" / "paths.json")
    refinement = read_csv(root / "refinement" / "executions.csv")
    assert len(paths) == 30 and len(refinement) == 171
    assert all(row["valid"] == "True" for row in refinement)
    assert sum(int(row["canceled_attempts"]) for row in refinement) == 162
    assert sum(int(row["post_effect_recoveries"]) for row in refinement) == 108

    # Bounded scale sweep.
    scaling = read_csv(root / "scaling" / "scaling.csv")
    assert len(scaling) == 100
    scale_states = {}
    for jobs in (4, 6, 8, 10, 12):
        subset = [row for row in scaling if int(row["jobs"]) == jobs]
        assert len(subset) == 20 and all(int(row["feasible"]) == 1 for row in subset)
        scale_states[str(jobs)] = max(int(row["visited_states"]) for row in subset)
    assert scale_states == {"4": 480, "6": 2112, "8": 4284, "10": 8326, "12": 11238}

    # Bounded concurrent receiver calls.
    races = read_csv(root / "concurrency" / "cases.csv")
    assert len(races) == 270 and all(row["passed"] == "True" for row in races)
    race_kinds = {kind: sum(row["kind"] == kind for row in races)
                  for kind in {row["kind"] for row in races}}
    assert race_kinds == {"duplicate_execute": 90, "execute_close": 90, "conflicting_reuse": 90}
    execute_close = [row for row in races if row["kind"] == "execute_close"]
    assert all((row["terminal"] == "done" and int(row["effects"]) == 1)
               or (row["terminal"] == "canceled" and int(row["effects"]) == 0)
               for row in execute_close)

    # Descriptive latency quantiles, recomputed from all retained samples.
    samples = read_csv(root / "latency" / "samples.csv")
    assert len(samples) == 4096
    timing = load(root / "latency" / "summary.json")
    for method in ("direct", "contract"):
        values = [float(row["microseconds"]) for row in samples if row["method"] == method]
        assert len(values) == 2048 and all(math.isfinite(v) and v >= 0 for v in values)
        observed = timing["methods"][method]
        assert close(percentile(values, 0.5), observed["median_us"])
        assert close(percentile(values, 0.95), observed["p95_us"])
        assert close(percentile(values, 0.99), observed["p99_us"])
        rep_medians = [statistics.median(float(row["microseconds"]) for row in samples
                                         if row["method"] == method and int(row["rep"]) == rep)
                       for rep in range(8)]
        assert close(min(rep_medians), observed["min_rep_median_us"])
        assert close(max(rep_medians), observed["max_rep_median_us"])
    assert close(timing["median_overhead_ratio"],
                 timing["methods"]["contract"]["median_us"] /
                 timing["methods"]["direct"]["median_us"])

    assert not list(root.rglob("*.db-wal"))
    assert not list(root.rglob("*.db-shm"))
    database_files = sorted(root.rglob("*.db"))
    assert database_files
    for database in database_files:
        uri = f"file:{database}?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "attempts" in tables:
                index_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name='one_pending_attempt'"
                ).fetchone()
                assert index_sql is not None and "WHERE state='pending'" in index_sql[0]
                assert connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE state='pending'"
                ).fetchone()[0] <= 1
            if "outcomes" in tables:
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='outcomes'"
                ).fetchone()[0]
                assert "state='done' AND adapter IS NOT NULL AND job IS NOT NULL" in table_sql
                assert connection.execute(
                    "SELECT COUNT(*) FROM outcomes WHERE "
                    "(state='done' AND (adapter IS NULL OR job IS NULL)) OR "
                    "(state='canceled' AND (adapter IS NOT NULL OR job IS NOT NULL OR cost<>0 OR unsafe<>0))"
                ).fetchone()[0] == 0

    return {
        "pilot_optimum": 10,
        "oracle_cases": 1200,
        "oracle_feasible": 911,
        "oracle_mismatches": 0,
        "micro_exhaustive_cases": 23040,
        "micro_exhaustive_feasible": 16864,
        "micro_exhaustive_mismatches": 0,
        "structural_value_mutants": 2072,
        "semantic_mutants": semantic_counts,
        "protocol_model_states": correct_model["reachable_states"],
        "protocol_model_edges": correct_model["explored_edges"],
        "protocol_crash_sweep": {key: {"states": values[0], "edges": values[1],
                                       "max_completion_steps": values[2]}
                                 for key, values in expected_sweep.items()},
        "protocol_mutant_counterexamples": expected_model_failures,
        "trace_feasible": method_counts,
        "trace_feasible_by_source": source_method_counts,
        "trace_adaptive_only_vs_any_fixed": adaptive_only_any,
        "fault_schedules": 30,
        "fault_runtime_replays": len(runtime_records),
        "fault_controls": 5,
        "refinement_paths": 30,
        "refinement_executions": 171,
        "scaling_max_states": scale_states,
        "concurrency_cases": 270,
        "latency_samples_per_method": 2048,
        "sqlite_databases_checked": len(database_files),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--results", type=Path, default=ROOT / "results")
    p.add_argument("--compare", type=Path)
    a = p.parse_args()
    checked = verify(a.results)
    if a.compare is not None:
        assert checked == verify(a.compare)
    print(json.dumps({"checked_claims": checked, "timing_equality_not_required": True},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
