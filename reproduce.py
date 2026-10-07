#!/usr/bin/env python3
"""Bounded offline reproduction for receipt-closed selective fallback."""
from __future__ import annotations

import argparse
from contextlib import closing
import concurrent.futures
import copy
import csv
import itertools
import json
import math
import os
import random
try:
    import resource
except ModuleNotFoundError:
    resource = None
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from checker import check
from game import ACTIONS, encode, initial, successors, synthesize
from oracle import exact
from protocol_model import run_all as run_protocol_models
from runtime import ControllerStore, ReceiverStore
from trace_check import check_runtime
from workload import calibrate, load_rows, make_spec, to_job, windows


def bounded_environment() -> None:
    # Serial Python plus one synchronous child.  No stress test or external work.
    if resource is None:
        raise RuntimeError('Full reproduction requires Unix resource limits; the finite model phase is portable.')
    resource.setrlimit(resource.RLIMIT_AS, (3_250 * 1024**2, 3_250 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    if hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, set(available[: min(4, len(available))]))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("cannot write empty CSV")
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("empty sample")
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def peak_rss_kib() -> int:
    if resource is not None:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # The small portable model phase records a real Windows peak working set.
    # Full reproduction still requires Unix CPU/address-space limits.
    import ctypes
    from ctypes import wintypes
    class MemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
             'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = wintypes.HANDLE
    info = ctypes.windll.psapi.GetProcessMemoryInfo
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    info.restype = wintypes.BOOL
    if not info(current(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return int(counters.PeakWorkingSetSize // 1024)


def phase_record(start_wall: float, start_cpu: float) -> dict[str, float | int]:
    return {
        "wall_seconds": time.perf_counter() - start_wall,
        "cpu_seconds": time.process_time() - start_cpu,
        "max_rss_kib": peak_rss_kib(),
    }


def pilot_spec() -> dict[str, Any]:
    return {
        "jobs": [
            {"score": 2, "accept_cost": 2, "fallback_cost": 4, "accept_time": 2, "fallback_time": 4},
            {"score": 0, "accept_cost": 2, "fallback_cost": 5, "accept_time": 2, "fallback_time": 4},
            {"score": 1, "accept_cost": 3, "fallback_cost": 5, "accept_time": 3, "fallback_time": 5},
        ],
        "drift": 1,
        "crashes": 2,
        "unsafe": 1,
        "late": 0,
        "cost": 16,
        "recovery_time": 2,
        "deadline": 10,
    }


def _current_job(root: Path, spec: dict[str, Any]) -> int:
    db_path = root / "controller.db"
    if not db_path.exists():
        return 0
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute("SELECT value FROM meta WHERE key='state'").fetchone()
    return int(json.loads(row[0])[0]) if row else 0


def _run_worker(root: Path, spec_path: Path, cert_path: Path, adapter: str, cut: str,
                spec: dict[str, Any], expect_crash: bool) -> dict[str, Any]:
    cmd = [sys.executable, "-S", str(ROOT / "src" / "runtime_worker.py"), str(root),
           str(spec_path), str(cert_path), "--adapter", adapter, "--cut", cut]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    expected = 73 if expect_crash else 0
    if proc.returncode != expected:
        raise RuntimeError({"cmd": cmd, "expected": expected, "actual": proc.returncode,
                            "stdout": proc.stdout, "stderr": proc.stderr})
    return {"cut": cut, "exit": proc.returncode, "wall_seconds": time.perf_counter() - t0,
            "stdout": proc.stdout, "stderr": proc.stderr}


def run_runtime_schedule(base: Path, spec: dict[str, Any], cert: dict[str, Any], adapter: str,
                         cuts: list[str]) -> dict[str, Any]:
    base.mkdir(parents=True, exist_ok=True)
    spec_path = base / "spec.json"
    cert_path = base / "certificate.json"
    save_json(spec_path, spec)
    save_json(cert_path, cert)
    launches = []
    for cut in cuts:
        launches.append(_run_worker(base, spec_path, cert_path, adapter, cut, spec, True))
    for _ in range(12):
        controller = ControllerStore(base / "controller.db", spec, cert, controller_id="artifact-run", adapter=adapter)
        done = controller.done()
        controller.close()
        if done:
            break
        launches.append(_run_worker(base, spec_path, cert_path, adapter, "none", spec, False))
    controller = ControllerStore(base / "controller.db", spec, cert, controller_id="artifact-run", adapter=adapter)
    receiver = ReceiverStore(base / "receiver.db")
    snapshot = controller.snapshot()
    outcomes = receiver.outcomes()
    effects = receiver.effects()
    valid = check_runtime(spec, cert, snapshot, outcomes, effects)
    controller.close()
    receiver.close()
    if not snapshot["done"] or not valid:
        raise AssertionError("runtime schedule did not close cleanly")
    record = {"adapter": adapter, "cuts": cuts, "launches": launches, "snapshot": snapshot,
              "receiver_outcomes": outcomes, "effects": effects, "valid": valid}
    save_json(base / "runtime.json", record)
    return record


def pilot(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    spec = pilot_spec()
    cert = synthesize(spec)
    oracle_value = exact(spec)
    if not cert["feasible"] or cert["worst_cost"] != oracle_value or not check(spec, cert):
        raise AssertionError("pilot certificate/oracle mismatch")
    runtime = run_runtime_schedule(out / "runtime", spec, cert, "admission", ["after_execute"])
    summary = {
        "oracle_cost": oracle_value,
        "certificate_valid": True,
        "visited_states": cert["visited_states"],
        "policy_rows": len(cert["policy"]),
        "runtime_valid": runtime["valid"],
        "runtime_attempts": len(runtime["snapshot"]["attempts"]),
        "runtime_effects": len(runtime["effects"]),
        **phase_record(start_wall, start_cpu),
    }
    save_json(out / "spec.json", spec)
    save_json(out / "certificate.json", cert)
    save_json(out / "runtime.json", runtime)
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def oracle_phase(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    rng = random.Random(902_771)
    rows = []
    feasible = 0
    max_states = 0
    max_seconds = 0.0
    for case in range(1200):
        n = 1 + case % 4
        jobs = [{
            "score": rng.randrange(3),
            "accept_cost": rng.randrange(1, 6),
            "fallback_cost": rng.randrange(2, 8),
            "accept_time": rng.randrange(1, 6),
            "fallback_time": rng.randrange(2, 8),
        } for _ in range(n)]
        spec = {
            "jobs": jobs,
            "drift": (case // 3) % 3,
            "crashes": (case // 7) % 3,
            "unsafe": case % (n + 1),
            "late": (case // 5) % (n + 1),
            "cost": 2 + case % 28,
            "recovery_time": 1 + (case % 3),
            "deadline": 3 + (case % 7),
        }
        t0 = time.perf_counter()
        cert = synthesize(spec)
        elapsed = time.perf_counter() - t0
        expected = exact(spec)
        if cert["feasible"] != (expected is not None) or cert["worst_cost"] != expected:
            raise AssertionError((case, cert, expected))
        if cert["feasible"] and not check(spec, cert):
            raise AssertionError("checker rejected synthesized feasible certificate")
        feasible += int(cert["feasible"])
        max_states = max(max_states, cert["visited_states"])
        max_seconds = max(max_seconds, elapsed)
        rows.append({"case": case, "spec": spec, "oracle_cost": expected,
                     "feasible": cert["feasible"], "visited_states": cert["visited_states"],
                     "policy_rows": len(cert["policy"]), "synthesis_seconds": elapsed})
    # Exhaust the complete one-job micro-domain stated here rather than relying
    # only on pseudo-random bounded cases.  Every combination uses score in
    # {0,1,2}; costs/times in {1,2}; drift/crash/unsafe/late budgets in {0,1};
    # total cost in [0,4]; recovery time in {1,2}; and deadline in {1,2,3}.
    micro_rows: list[dict[str, Any]] = []
    micro_feasible = 0
    micro_max_states = 0
    for micro_case, values in enumerate(itertools.product(
        range(3), range(1, 3), range(1, 3), range(1, 3), range(1, 3),
        range(2), range(2), range(2), range(2), range(5), range(1, 3), range(1, 4),
    )):
        (score, accept_cost, fallback_cost, accept_time, fallback_time, drift, crashes,
         unsafe, late, cost, recovery_time, deadline) = values
        micro_spec = {
            "jobs": [{"score": score, "accept_cost": accept_cost,
                      "fallback_cost": fallback_cost, "accept_time": accept_time,
                      "fallback_time": fallback_time}],
            "drift": drift, "crashes": crashes, "unsafe": unsafe, "late": late,
            "cost": cost, "recovery_time": recovery_time, "deadline": deadline,
        }
        micro_cert = synthesize(micro_spec)
        micro_expected = exact(micro_spec)
        if (micro_cert["feasible"] != (micro_expected is not None)
                or micro_cert["worst_cost"] != micro_expected
                or (micro_cert["feasible"] and not check(micro_spec, micro_cert))):
            raise AssertionError(("micro-domain mismatch", micro_case, micro_cert, micro_expected))
        micro_feasible += int(micro_cert["feasible"])
        micro_max_states = max(micro_max_states, micro_cert["visited_states"])
        micro_rows.append({
            "case": micro_case, "score": score, "accept_cost": accept_cost,
            "fallback_cost": fallback_cost, "accept_time": accept_time,
            "fallback_time": fallback_time, "drift": drift, "crashes": crashes,
            "unsafe": unsafe, "late": late, "cost": cost,
            "recovery_time": recovery_time, "deadline": deadline,
            "oracle_cost": "" if micro_expected is None else micro_expected,
            "feasible": int(micro_cert["feasible"]),
            "visited_states": micro_cert["visited_states"],
            "policy_rows": len(micro_cert["policy"]),
        })

    summary = {"cases": len(rows), "feasible": feasible, "infeasible": len(rows) - feasible,
               "mismatches": 0, "max_visited_states": max_states,
               "max_synthesis_seconds": max_seconds,
               "micro_exhaustive_cases": len(micro_rows),
               "micro_exhaustive_feasible": micro_feasible,
               "micro_exhaustive_infeasible": len(micro_rows) - micro_feasible,
               "micro_exhaustive_mismatches": 0,
               "micro_exhaustive_max_visited_states": micro_max_states,
               **phase_record(start_wall, start_cpu)}
    save_json(out / "cases.json", rows)
    write_csv(out / "micro_exhaustive.csv", micro_rows)
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def mutants(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    rng = random.Random(440_119)
    mutation_rows = []
    semantic_rows = []
    case = 0
    while case < 300:
        n = 1 + case % 4
        jobs = [{"score": rng.randrange(3), "accept_cost": rng.randrange(1, 5),
                 "fallback_cost": rng.randrange(3, 8), "accept_time": rng.randrange(1, 5),
                 "fallback_time": rng.randrange(2, 8)} for _ in range(n)]
        spec = {"jobs": jobs, "drift": case % 3, "crashes": (case // 2) % 3,
                "unsafe": max(1, case % (n + 1)), "late": max(1, (case // 4) % (n + 1)),
                "cost": 8 + case % 25, "recovery_time": 1 + case % 3, "deadline": 5 + case % 5}
        cert = synthesize(spec)
        if not cert["feasible"]:
            case += 1
            continue
        if not check(spec, cert):
            raise AssertionError("valid certificate rejected before mutation")
        root = encode(initial(spec))
        variants: list[tuple[str, dict[str, Any]]] = []
        bad = copy.deepcopy(cert); bad["worst_cost"] += 1; variants.append(("root_value", bad))
        bad = copy.deepcopy(cert); del bad["policy"][root]; variants.append(("missing_root", bad))
        bad = copy.deepcopy(cert); bad["policy"][root]["action"] = "skip"; variants.append(("unknown_action", bad))
        bad = copy.deepcopy(cert); bad["policy"]["9,0,0,0,0,0"] = {"action": "accept", "worst_cost": 0, "outcomes": []}; variants.append(("extra_row", bad))
        bad = copy.deepcopy(cert); bad["policy"][root]["outcomes"][0]["charge"] += 1; variants.append(("charge", bad))
        bad = copy.deepcopy(cert); bad["policy"][root]["outcomes"][0]["meta"]["late"] ^= 1; variants.append(("outcome_meta", bad))
        bad = copy.deepcopy(cert); bad["visited_states"] += 1; variants.append(("visited_states", bad))
        for kind, altered in variants:
            mutation_rows.append({"case": case, "kind": kind, "rejected": not check(spec, altered)})

        optimistic_spec = copy.deepcopy(spec)
        optimistic_spec["recovery_time"] = 0
        optimistic = synthesize(optimistic_spec)
        optimistic_for_check = copy.deepcopy(optimistic)
        # Isolate model semantics from the producer-diagnostic visited-state
        # field, which is separately mutated in the structural campaign.
        optimistic_for_check["visited_states"] = cert["visited_states"]
        semantic_rows.append({"case": case, "kind": "ignore_recovery_time",
                              "producer_feasible": optimistic["feasible"],
                              "rejected": (not optimistic["feasible"])
                                          or (not check(spec, optimistic_for_check))})
        inverted_spec = copy.deepcopy(spec)
        for job in inverted_spec["jobs"]:
            job["score"] = 2 - job["score"]
        inverted = synthesize(inverted_spec)
        inverted_for_check = copy.deepcopy(inverted)
        inverted_for_check["visited_states"] = cert["visited_states"]
        semantic_rows.append({"case": case, "kind": "invert_score_classes",
                              "producer_feasible": inverted["feasible"],
                              "rejected": (not inverted["feasible"])
                                          or (not check(spec, inverted_for_check))})
        case += 1

    if not all(r["rejected"] for r in mutation_rows):
        raise AssertionError("a structural/value mutation escaped")
    summary = {
        "candidate_cases": 300,
        "feasible_source_cases": len(semantic_rows) // 2,
        "structural_value_mutants": len(mutation_rows),
        "structural_value_rejected": sum(r["rejected"] for r in mutation_rows),
        "semantic_mutants": len(semantic_rows),
        "semantic_rejected": sum(r["rejected"] for r in semantic_rows),
        "semantic_by_kind": {
            kind: {"cases": sum(r["kind"] == kind for r in semantic_rows),
                   "rejected": sum(r["kind"] == kind and r["rejected"] for r in semantic_rows)}
            for kind in sorted({r["kind"] for r in semantic_rows})
        },
        **phase_record(start_wall, start_cpu),
    }
    save_json(out / "structural_value.json", mutation_rows)
    save_json(out / "semantic.json", semantic_rows)
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def evaluate_exported_policy(spec: dict[str, Any], cert: dict[str, Any]) -> int | None:
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
        vals = []
        for nxt, charge, _ in successors(spec, state, row["action"]):
            sub = visit(nxt)
            if sub is None:
                memo[state] = None
                return None
            vals.append(charge + sub)
        memo[state] = max(vals) if vals else None
        return memo[state]

    return visit(initial(spec))


def trace_phase(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    source_paths = {
        "code": ROOT / "data" / "azure_llm_code_excerpt.csv",
        "conversation": ROOT / "data" / "azure_llm_conv_excerpt.csv",
    }
    calibration = calibrate(load_rows(source_paths["code"]))
    save_json(out / "calibration.json", calibration)

    source_windows = {name: windows(path, 8, calibration) for name, path in source_paths.items()}
    source_jobs = {
        name: [to_job(row, calibration, include_provenance=True) for row in load_rows(path)]
        for name, path in source_paths.items()
    }
    rows: list[dict[str, Any]] = []
    method_names = ["adaptive", "always_accept", "threshold_1", "threshold_2", "always_fallback", "ignore_recovery"]
    for source, trace_windows in source_windows.items():
        for window_index, jobs in enumerate(trace_windows):
            for drift, crashes, level in itertools.product(range(3), range(3), ("tight", "roomy")):
                spec = make_spec(jobs, drift, crashes, level, calibration)
                for method in method_names:
                    t0 = time.perf_counter()
                    if method == "adaptive":
                        cert = synthesize(spec)
                        value = cert["worst_cost"]
                        feasible = cert["feasible"]
                        states = cert["visited_states"]
                        if feasible and not check(spec, cert):
                            raise AssertionError("adaptive certificate rejected")
                    elif method == "ignore_recovery":
                        optimistic_spec = copy.deepcopy(spec)
                        optimistic_spec["recovery_time"] = 0
                        cert = synthesize(optimistic_spec)
                        value = evaluate_exported_policy(spec, cert) if cert["feasible"] else None
                        feasible = value is not None
                        states = cert["visited_states"]
                    else:
                        threshold = {"always_accept": 0, "threshold_1": 1,
                                     "threshold_2": 2, "always_fallback": 3}[method]
                        allowed = {}
                        for i, job in enumerate(jobs):
                            action = "accept" if job["score"] >= threshold else "fallback"
                            allowed[i] = (action, "reject")
                        cert = synthesize(spec, allowed)
                        value = cert["worst_cost"]
                        feasible = cert["feasible"]
                        states = cert["visited_states"]
                    rows.append({"source": source, "window": window_index, "drift": drift,
                                 "crashes": crashes, "budget": level, "method": method,
                                 "feasible": int(feasible),
                                 "worst_cost": "" if value is None else value,
                                 "visited_states": states,
                                 "planning_ms": (time.perf_counter() - t0) * 1000})
    write_csv(out / "trace_results.csv", rows)
    windows_per_source = {name: len(items) for name, items in source_windows.items()}
    cases_per_source_method = 16 * 3 * 3 * 2
    summary: dict[str, Any] = {
        "cases_per_method": len(source_paths) * cases_per_source_method,
        "cases_per_source_method": cases_per_source_method,
        "source_rows": {name: len(load_rows(path)) for name, path in source_paths.items()},
        "total_source_rows": sum(len(load_rows(path)) for path in source_paths.values()),
        "window_size": 8,
        "windows_per_source": windows_per_source,
        "calibration_source": "code",
        "holdout_source": "conversation",
        "calibration": calibration,
        "score_counts": {
            name: {str(score): sum(job["score"] == score for job in jobs) for score in (0, 1, 2)}
            for name, jobs in source_jobs.items()
        },
        "pressure_counts": {
            name: {str(pressure): sum(job["pressure"] == pressure for job in jobs)
                   for pressure in range(7)}
            for name, jobs in source_jobs.items()
        },
        "methods": {},
    }
    for method in method_names:
        subset = [r for r in rows if r["method"] == method]
        costs = [int(r["worst_cost"]) for r in subset if r["feasible"]]
        per_source = {}
        for source in source_paths:
            source_subset = [r for r in subset if r["source"] == source]
            per_source[source] = {
                "feasible": sum(r["feasible"] for r in source_subset),
                "infeasible": len(source_subset) - sum(r["feasible"] for r in source_subset),
            }
        summary["methods"][method] = {
            "feasible": sum(r["feasible"] for r in subset),
            "infeasible": len(subset) - sum(r["feasible"] for r in subset),
            "per_source": per_source,
            "median_worst_cost_feasible": percentile(costs, 0.5) if costs else None,
            "p95_planning_ms": percentile([r["planning_ms"] for r in subset], 0.95),
            "max_visited_states": max(r["visited_states"] for r in subset),
        }

    case_fields = ("source", "window", "drift", "crashes", "budget")
    paired: dict[tuple[Any, ...], dict[str, int]] = {}
    for row in rows:
        key = tuple(row[field] for field in case_fields)
        paired.setdefault(key, {})[row["method"]] = row["feasible"]
    if len(paired) != summary["cases_per_method"] or any(set(v) != set(method_names) for v in paired.values()):
        raise AssertionError("trace grid is not a complete paired policy comparison")

    fixed_methods = ["always_accept", "threshold_1", "threshold_2", "always_fallback"]
    summary["paired_comparison"] = {}
    for baseline in [*fixed_methods, "ignore_recovery"]:
        baseline_result: dict[str, Any] = {}
        for source in ("all", *source_paths):
            records = [values for key, values in paired.items() if source == "all" or key[0] == source]
            baseline_result[source] = {
                "adaptive_only": sum(v["adaptive"] and not v[baseline] for v in records),
                "baseline_only": sum(v[baseline] and not v["adaptive"] for v in records),
                "both": sum(v[baseline] and v["adaptive"] for v in records),
                "neither": sum(not v[baseline] and not v["adaptive"] for v in records),
            }
        summary["paired_comparison"][baseline] = baseline_result

    summary["adaptive_only_vs_any_fixed"] = {}
    for source in ("all", *source_paths):
        records = [values for key, values in paired.items() if source == "all" or key[0] == source]
        summary["adaptive_only_vs_any_fixed"][source] = {
            "adaptive_only": sum(v["adaptive"] and not any(v[m] for m in fixed_methods) for v in records),
            "fixed_only": sum(not v["adaptive"] and any(v[m] for m in fixed_methods) for v in records),
            "both": sum(v["adaptive"] and any(v[m] for m in fixed_methods) for v in records),
            "neither": sum(not v["adaptive"] and not any(v[m] for m in fixed_methods) for v in records),
        }

    sensitivity = []
    for method in method_names:
        for source in source_paths:
            for drift, crashes, level in itertools.product(range(3), range(3), ("tight", "roomy")):
                subset = [r for r in rows if r["method"] == method and r["source"] == source
                          and r["drift"] == drift and r["crashes"] == crashes
                          and r["budget"] == level]
                sensitivity.append({"method": method, "source": source, "drift": drift,
                                    "crashes": crashes, "budget": level,
                                    "feasible_windows": sum(r["feasible"] for r in subset),
                                    "windows": len(subset)})
    write_csv(out / "sensitivity.csv", sensitivity)
    summary.update(phase_record(start_wall, start_cpu))
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def faults(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    spec = pilot_spec()
    cert = synthesize(spec)
    if not check(spec, cert):
        raise AssertionError("fault campaign certificate invalid")
    schedules = {
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
    records = []
    cases_dir = out / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    for adapter in ("admission", "cache", "tier"):
        for name, cuts in schedules.items():
            case_dir = cases_dir / f"{adapter}-{name}"
            record = run_runtime_schedule(case_dir, spec, cert, adapter, cuts)
            records.append({"adapter": adapter, "schedule": name, "cuts": cuts,
                            "attempts": len(record["snapshot"]["attempts"]),
                            "effects": len(record["effects"]),
                            "launches": len(record["launches"]),
                            "final_state": record["snapshot"]["state"], "valid": record["valid"]})
    if not all(r["valid"] for r in records):
        raise AssertionError("fault schedule failed independent replay")

    controls = []
    with tempfile.TemporaryDirectory(prefix="closure-controls-", dir=out) as td:
        root = Path(td)
        # Close-before-execute: the late execute is fenced and has no effect.
        receiver = ReceiverStore(root / "close-first.db")
        canceled = receiver.close_attempt("old", "0" * 32)
        late = receiver.execute_once("old", "admission", "job", 3, 1)
        controls.append({"name": "close_before_execute", "canceled": canceled,
                         "late_execute": late, "effects": len(receiver.effects()),
                         "passes": late["status"] == "canceled" and not receiver.effects()})
        receiver.close()
        # Execute-before-close: close recovers the one durable effect.
        receiver = ReceiverStore(root / "execute-first.db")
        done = receiver.execute_once("old", "admission", "job", 3, 1)
        recovered = receiver.close_attempt("old", "1" * 32)
        controls.append({"name": "execute_before_close", "done": done, "recovered": recovered,
                         "effects": len(receiver.effects()),
                         "passes": recovered["status"] == "done" and len(receiver.effects()) == 1})
        receiver.close()
        # Fresh identifiers repeat the physical effect.
        receiver = ReceiverStore(root / "fresh.db")
        receiver.execute_once("a0", "admission", "job", 3, 1)
        receiver.execute_once("a1", "admission", "job", 3, 1)
        controls.append({"name": "fresh_identifiers", "effects": len(receiver.effects()),
                         "passes": len(receiver.effects()) == 2})
        receiver.close()
        # Releasing locally without receiver closure lets a delayed old attempt race a new one.
        receiver = ReceiverStore(root / "unfenced.db")
        receiver.execute_once("new", "admission", "job", 3, 1)
        receiver.execute_once("old", "admission", "job", 3, 1)
        actual = [sum(v[i] for v in receiver.effects().values()) for i in (0, 1)]
        controls.append({"name": "release_without_fence", "effects": len(receiver.effects()),
                         "actual": actual, "declared_single_attempt_budget": [4, 1],
                         "passes": actual[0] > 4 or actual[1] > 1})
        receiver.close()
        # Reservation-only preserves safety but cannot complete after the sender loses the reply.
        receiver = ReceiverStore(root / "reservation-only.db")
        receiver.execute_once("stuck", "admission", "job", 3, 1)
        controls.append({"name": "reservation_only", "effects": len(receiver.effects()),
                         "pending_liability": [4, 1], "logical_completion": False, "passes": True})
        receiver.close()
    if not all(c["passes"] for c in controls):
        raise AssertionError("fault control did not show its intended boundary")
    summary = {"schedules": len(records), "valid_schedules": sum(r["valid"] for r in records),
               "adapters": 3, "controls": len(controls), "controls_passed": sum(c["passes"] for c in controls),
               **phase_record(start_wall, start_cpu)}
    save_json(out / "schedules.json", records)
    save_json(out / "controls.json", controls)
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def _strategy_paths(spec: dict[str, Any], cert: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Enumerate every adversarial root-to-terminal path of one fixed strategy."""
    terminal = len(spec["jobs"])

    def walk(state: tuple[int, int, int, int, int, int]) -> list[list[dict[str, Any]]]:
        if state[0] == terminal:
            return [[]]
        row = cert["policy"][encode(state)]
        paths: list[list[dict[str, Any]]] = []
        for outcome in row["outcomes"]:
            nxt = tuple(int(x) for x in outcome["next"].split(","))
            step = {"state": list(state), "action": row["action"],
                    "meta": outcome["meta"], "next": list(nxt)}
            for tail in walk(nxt):
                paths.append([step, *tail])
        return paths

    return walk(initial(spec))


def _realize_strategy_path(root: Path, spec: dict[str, Any], cert: dict[str, Any],
                           adapter: str, path: list[dict[str, Any]], placement: str) -> dict[str, Any]:
    """Realize one certified path with all-pre-effect or final-post-effect recovery.

    ``all_pre`` closes every crashed attempt as CANCELED before a final effect.
    ``final_post`` moves the last declared crash (when one exists) after the final
    effect but before its reply is settled.  Both physical layouts project to the
    same finite-game outcome and therefore exercise the composition boundary.
    """
    if placement not in {"all_pre", "final_post"}:
        raise ValueError("unknown recovery placement")
    controller = ControllerStore(root / "controller.db", spec, cert, controller_id="artifact-run", adapter=adapter)
    receiver = ReceiverStore(root / "receiver.db")
    canceled = 0
    done_effects = 0
    post_effect_recoveries = 0
    try:
        for step in path:
            if list(controller.state()) != step["state"]:
                raise AssertionError("runtime prefix diverged from certified state")
            action = controller.ensure_selected()
            if action != step["action"]:
                raise AssertionError("runtime selected a different certified action")
            if action == "reject":
                controller.apply_reject()
                if list(controller.state()) != step["next"]:
                    raise AssertionError("reject projection mismatch")
                continue

            crashes = step["meta"]["crashes"]
            pre_crashes = crashes if placement == "all_pre" else max(0, crashes - 1)
            for _ in range(pre_crashes):
                key, selected, _, _ = controller.reserve()
                if selected != action:
                    raise AssertionError("reservation changed selected action")
                ticket = controller.record_recovery(key)
                receipt = receiver.close_attempt(key, ticket)
                if receipt["status"] != "canceled":
                    raise AssertionError("pre-effect recovery was not canceled")
                controller.settle(receipt)
                canceled += 1

            key, selected, cost, _ = controller.reserve()
            if selected != action or cost != step["meta"]["cost"]:
                raise AssertionError("runtime envelope differs from finite outcome")
            reserved_adapter, target = controller.attempt_descriptor(key)
            receipt = receiver.execute_once(
                key, reserved_adapter, target, cost, step["meta"]["unsafe"]
            )
            done_effects += 1
            if placement == "final_post" and crashes > 0:
                ticket = controller.record_recovery(key)
                receipt = receiver.close_attempt(key, ticket)
                if receipt["status"] != "done":
                    raise AssertionError("post-effect recovery did not recover DONE")
                post_effect_recoveries += 1
            controller.settle(receipt)
            if list(controller.state()) != step["next"]:
                raise AssertionError("physical execution did not refine finite transition")

        snapshot = controller.snapshot()
        outcomes = receiver.outcomes()
        effects = receiver.effects()
        valid = check_runtime(spec, cert, snapshot, outcomes, effects)
        if not snapshot["done"] or not valid:
            raise AssertionError("completed path failed independent replay")
        return {"valid": valid, "final_state": snapshot["state"],
                "attempts": len(snapshot["attempts"]), "effects": len(effects),
                "canceled_attempts": canceled, "done_effects": done_effects,
                "post_effect_recoveries": post_effect_recoveries,
                "events": len(snapshot["events"])}
    finally:
        controller.close()
        receiver.close()


def refinement(out: Path) -> None:
    """Exhaustively realize every path of the pilot strategy in the local runtime."""
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    spec = pilot_spec()
    cert = synthesize(spec)
    if not cert["feasible"] or not check(spec, cert):
        raise AssertionError("refinement campaign certificate invalid")
    paths = _strategy_paths(spec, cert)
    path_records = []
    executions = []
    out.mkdir(parents=True, exist_ok=True)
    for path_id, path in enumerate(paths):
        total_crashes = sum(step["meta"]["crashes"] for step in path)
        flips = sum(step["meta"]["flip"] for step in path)
        path_records.append({"path": path_id, "steps": path, "declared_crashes": total_crashes,
                             "declared_flips": flips})
        placements = ("all_pre", "final_post") if total_crashes else ("all_pre",)
        for adapter in ("admission", "cache", "tier"):
            for placement in placements:
                with tempfile.TemporaryDirectory(prefix="refine-", dir=out) as td:
                    record = _realize_strategy_path(Path(td), spec, cert, adapter, path, placement)
                executions.append({"path": path_id, "adapter": adapter, "placement": placement,
                                   "declared_crashes": total_crashes, "declared_flips": flips,
                                   **record})
    if not executions or not all(r["valid"] for r in executions):
        raise AssertionError("refinement execution failed")
    save_json(out / "paths.json", path_records)
    write_csv(out / "executions.csv", executions)
    summary = {
        "strategy_paths": len(paths),
        "executions": len(executions),
        "adapters": 3,
        "paths_with_recovery": sum(r["declared_crashes"] > 0 for r in path_records),
        "paths_with_drift_flip": sum(r["declared_flips"] > 0 for r in path_records),
        "all_valid": True,
        "total_canceled_attempts": sum(r["canceled_attempts"] for r in executions),
        "total_post_effect_recoveries": sum(r["post_effect_recoveries"] for r in executions),
        **phase_record(start_wall, start_cpu),
    }
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def scaling(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    source_rows = load_rows(ROOT / "data" / "azure_llm_code_excerpt.csv")
    calibration = calibrate(source_rows)
    source = [to_job(row, calibration) for row in source_rows]
    rows = []
    for size in (4, 6, 8, 10, 12):
        for sample in range(20):
            start = (sample * 5) % (len(source) - size + 1)
            jobs = source[start:start + size]
            spec = make_spec(jobs, drift=1, crashes=1, budget_level="roomy",
                             calibration=calibration)
            t0 = time.perf_counter()
            cert = synthesize(spec)
            elapsed = time.perf_counter() - t0
            encoded = json.dumps(cert, sort_keys=True, separators=(",", ":")).encode()
            rows.append({"jobs": size, "sample": sample, "feasible": int(cert["feasible"]),
                         "visited_states": cert["visited_states"], "policy_rows": len(cert["policy"]),
                         "certificate_bytes": len(encoded), "planning_ms": elapsed * 1000})
    write_csv(out / "scaling.csv", rows)
    summary = {str(size): {
        "cases": sum(r["jobs"] == size for r in rows),
        "feasible": sum(r["jobs"] == size and r["feasible"] for r in rows),
        "median_planning_ms": percentile([r["planning_ms"] for r in rows if r["jobs"] == size], 0.5),
        "p95_planning_ms": percentile([r["planning_ms"] for r in rows if r["jobs"] == size], 0.95),
        "median_certificate_bytes": percentile([r["certificate_bytes"] for r in rows if r["jobs"] == size], 0.5),
        "max_visited_states": max(r["visited_states"] for r in rows if r["jobs"] == size),
    } for size in (4, 6, 8, 10, 12)}
    summary.update(phase_record(start_wall, start_cpu))
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def concurrency(out: Path) -> None:
    """Exercise receiver closure under bounded concurrent SQLite callers."""
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    rows: list[dict[str, Any]] = []

    def call(path: Path, barrier: threading.Barrier, operation: str, adapter: str,
             key: str, job: str) -> dict[str, Any]:
        receiver = ReceiverStore(path)
        try:
            barrier.wait(timeout=5)
            if operation == "execute":
                result = receiver.execute_once(key, adapter, job, 3, 1)
            else:
                result = receiver.close_attempt(key, "c" * 32)
            return {"ok": True, "result": result}
        except BaseException as exc:
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        finally:
            receiver.close()

    out.mkdir(parents=True, exist_ok=True)
    for adapter in ("admission", "cache", "tier"):
        for rep in range(30):
            with tempfile.TemporaryDirectory(prefix="dup-race-", dir=out) as td:
                path = Path(td) / "receiver.db"
                ReceiverStore(path).close()
                barrier = threading.Barrier(4)
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(
                        lambda _: call(path, barrier, "execute", adapter, "same", "job"), range(4)
                    ))
                receiver = ReceiverStore(path)
                effects = receiver.effects(); outcomes = receiver.outcomes(); receiver.close()
                passed = all(r["ok"] and r["result"]["status"] == "done" for r in results)
                passed = passed and len(effects) == 1 and outcomes["same"]["status"] == "done"
                rows.append({"adapter": adapter, "kind": "duplicate_execute", "rep": rep,
                             "passed": passed, "effects": len(effects),
                             "errors": sum(not r["ok"] for r in results),
                             "terminal": outcomes["same"]["status"]})

            with tempfile.TemporaryDirectory(prefix="close-race-", dir=out) as td:
                path = Path(td) / "receiver.db"
                ReceiverStore(path).close()
                barrier = threading.Barrier(2)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    execute_future = pool.submit(call, path, barrier, "execute", adapter, "race", "job")
                    close_future = pool.submit(call, path, barrier, "close", adapter, "race", "job")
                    results = [execute_future.result(), close_future.result()]
                receiver = ReceiverStore(path)
                effects = receiver.effects(); outcomes = receiver.outcomes(); receiver.close()
                terminal = outcomes["race"]["status"]
                statuses = [r.get("result", {}).get("status") for r in results if r["ok"]]
                passed = all(r["ok"] for r in results) and statuses == [terminal, terminal]
                passed = passed and ((terminal == "done" and len(effects) == 1)
                                     or (terminal == "canceled" and len(effects) == 0))
                rows.append({"adapter": adapter, "kind": "execute_close", "rep": rep,
                             "passed": passed, "effects": len(effects),
                             "errors": sum(not r["ok"] for r in results), "terminal": terminal})

            with tempfile.TemporaryDirectory(prefix="conflict-race-", dir=out) as td:
                path = Path(td) / "receiver.db"
                ReceiverStore(path).close()
                barrier = threading.Barrier(2)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    a = pool.submit(call, path, barrier, "execute", adapter, "conflict", "job-a")
                    b = pool.submit(call, path, barrier, "execute", adapter, "conflict", "job-b")
                    results = [a.result(), b.result()]
                receiver = ReceiverStore(path)
                effects = receiver.effects(); outcomes = receiver.outcomes(); receiver.close()
                passed = sum(r["ok"] for r in results) == 1 and len(effects) == 1
                passed = passed and sum((not r["ok"] and r["error"] == "ValueError") for r in results) == 1
                rows.append({"adapter": adapter, "kind": "conflicting_reuse", "rep": rep,
                             "passed": passed, "effects": len(effects),
                             "errors": sum(not r["ok"] for r in results),
                             "terminal": outcomes["conflict"]["status"]})

    if not all(r["passed"] for r in rows):
        raise AssertionError("receiver concurrency campaign failed")
    write_csv(out / "cases.csv", rows)
    summary = {
        "cases": len(rows), "passed": sum(r["passed"] for r in rows), "adapters": 3,
        "repetitions_per_kind": 30,
        "done_execute_close_races": sum(r["kind"] == "execute_close" and r["terminal"] == "done" for r in rows),
        "canceled_execute_close_races": sum(r["kind"] == "execute_close" and r["terminal"] == "canceled" for r in rows),
        **phase_record(start_wall, start_cpu),
    }
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def latency(out: Path) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    count = 256
    jobs = [{"score": 2, "accept_cost": 1, "fallback_cost": 2,
             "accept_time": 1, "fallback_time": 2} for _ in range(count)]
    spec = {"jobs": jobs, "drift": 0, "crashes": 0, "unsafe": 0, "late": 0,
            "cost": count, "recovery_time": 1, "deadline": 2}
    cert = synthesize(spec)
    if not cert["feasible"] or not check(spec, cert):
        raise AssertionError("latency certificate invalid")
    rows = []
    for rep in range(8):
        order = ("direct", "contract") if rep % 2 == 0 else ("contract", "direct")
        for method in order:
            with tempfile.TemporaryDirectory(prefix=f"lat-{method}-", dir=out) as td:
                root = Path(td)
                receiver = ReceiverStore(root / "receiver.db")
                controller = ControllerStore(root / "controller.db", spec, cert, controller_id="artifact-run", adapter="admission") if method == "contract" else None
                for i in range(count):
                    t0 = time.perf_counter_ns()
                    if method == "direct":
                        receiver.execute_once(f"r{rep}-{i}", "admission", f"job-{i}", 1, 0)
                    else:
                        key, action, cost, _ = controller.reserve()  # type: ignore[union-attr]
                        reserved_adapter, target = controller.attempt_descriptor(key)  # type: ignore[union-attr]
                        receipt = receiver.execute_once(key, reserved_adapter, target, cost, 0)
                        controller.settle(receipt)  # type: ignore[union-attr]
                    rows.append({"rep": rep, "position": i, "method": method,
                                 "microseconds": (time.perf_counter_ns() - t0) / 1000.0})
                if controller:
                    if not controller.done():
                        raise AssertionError("contract benchmark did not finish")
                    controller.close()
                receiver.close()
    write_csv(out / "samples.csv", rows)
    summary = {"operations_per_method": 8 * count, "repetitions": 8, "ordered_alternating": True,
               "methods": {}}
    for method in ("direct", "contract"):
        values = [r["microseconds"] for r in rows if r["method"] == method]
        per_rep_medians = [statistics.median(r["microseconds"] for r in rows
                                            if r["method"] == method and r["rep"] == rep)
                           for rep in range(8)]
        summary["methods"][method] = {
            "median_us": percentile(values, 0.5), "p95_us": percentile(values, 0.95),
            "p99_us": percentile(values, 0.99),
            "min_rep_median_us": min(per_rep_medians), "max_rep_median_us": max(per_rep_medians),
        }
    summary["median_overhead_ratio"] = (
        summary["methods"]["contract"]["median_us"] / summary["methods"]["direct"]["median_us"]
    )
    summary.update(phase_record(start_wall, start_cpu))
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def protocol_model_phase(out: Path) -> None:
    """Exhaustively explore the independent one-action closure model."""
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    result = run_protocol_models(max_crashes=2)
    models = result["models"]
    # Re-run the correct protocol across a wider bounded crash range.  Mutant
    # expectations are kept at two crashes because some faults are unreachable
    # in the zero-crash model by construction.
    from protocol_model import explore as explore_protocol
    result["receipt_closed_crash_sweep"] = {
        str(crashes): {
            "reachable_states": model["reachable_states"],
            "explored_edges": model["explored_edges"],
            "violation_kinds": model["violation_kinds"],
            "all_states_complete_without_more_crashes":
                model["all_states_complete_without_more_crashes"],
            "max_no_further_crash_steps": model["max_no_further_crash_steps"],
        }
        for crashes in range(5)
        for model in [explore_protocol("receipt_closed", max_crashes=crashes)]
    }
    summary = {
        "max_crashes": result["max_crashes"],
        "correct_reachable_states": models["receipt_closed"]["reachable_states"],
        "correct_explored_edges": models["receipt_closed"]["explored_edges"],
        "correct_violation_kinds": models["receipt_closed"]["violation_kinds"],
        "correct_all_states_complete_without_more_crashes":
            models["receipt_closed"]["all_states_complete_without_more_crashes"],
        "correct_max_no_further_crash_steps":
            models["receipt_closed"]["max_no_further_crash_steps"],
        "receipt_closed_crash_sweep": result["receipt_closed_crash_sweep"],
        "mutant_shortest_counterexample_steps": {
            variant: {kind: len(record["trace"])
                      for kind, record in model["shortest_counterexamples"].items()}
            for variant, model in models.items() if variant != "receipt_closed"
        },
        **phase_record(start_wall, start_cpu),
    }
    save_json(out / "model.json", result)
    save_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def checkpoint_databases(root: Path) -> None:
    """Checkpoint every closed SQLite database and remove disposable WAL state."""
    for database in sorted(root.rglob("*.db")):
        connection = sqlite3.connect(database, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise RuntimeError(f"could not checkpoint {database}: {checkpoint}")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"integrity check failed for {database}")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"foreign-key check failed for {database}")
        finally:
            connection.close()
    for pattern in ("*.db-wal", "*.db-shm"):
        for sidecar in root.rglob(pattern):
            sidecar.unlink()


PHASES: dict[str, Callable[[Path], None]] = {
    "pilot": pilot,
    "oracle": oracle_phase,
    "mutants": mutants,
    "model": protocol_model_phase,
    "trace": trace_phase,
    "faults": faults,
    "refinement": refinement,
    "scaling": scaling,
    "concurrency": concurrency,
    "latency": latency,
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=[*PHASES, "all"], required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.is_symlink():
        p.error('output must not be a symbolic link')
    a.output.mkdir(parents=True, exist_ok=True)
    if a.phase == 'all' and any(a.output.iterdir()):
        p.error('complete reproduction requires an empty output directory')
    if resource is not None or a.phase != 'model':
        bounded_environment()
    if a.phase == "all":
        for name, fn in PHASES.items():
            target = a.output / name
            target.mkdir()
            fn(target)
    else:
        PHASES[a.phase](a.output)
    checkpoint_databases(a.output)


if __name__ == "__main__":
    main()
