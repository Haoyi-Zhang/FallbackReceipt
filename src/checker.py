#!/usr/bin/env python3
"""Independent checker for reachable selective-fallback strategy certificates.

This module deliberately does not import game.py.  It reconstructs the finite
transition relation from the JSON specification and checks exact strategy value,
coverage, and absence of extra policy rows.
"""
from __future__ import annotations

import argparse
import functools
import json
from pathlib import Path
from typing import Any

ACTIONS = ("accept", "fallback", "reject")
MAX_CHECK_STATES = 2_000_000
State = tuple[int, int, int, int, int, int]


def _nat(x: object) -> bool:
    return type(x) is int and x >= 0


def _parse_state(text: object) -> State | None:
    if not isinstance(text, str):
        return None
    parts = text.split(",")
    if len(parts) != 6:
        return None
    try:
        values = tuple(int(x) for x in parts)
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    return values  # type: ignore[return-value]


def _encode(s: State) -> str:
    return ",".join(map(str, s))


def _valid_spec(s: object) -> bool:
    if not isinstance(s, dict):
        return False
    names = {"jobs", "drift", "crashes", "unsafe", "late", "cost", "recovery_time", "deadline"}
    if set(s) != names or not isinstance(s["jobs"], list) or not s["jobs"]:
        return False
    if any(not _nat(s[k]) for k in names - {"jobs"}) or s["deadline"] == 0:
        return False
    fields = {"score", "accept_cost", "fallback_cost", "accept_time", "fallback_time"}
    for j in s["jobs"]:
        if not isinstance(j, dict) or set(j) != fields or type(j["score"]) is not int or j["score"] not in (0, 1, 2):
            return False
        if any(not _nat(j[k]) or j[k] == 0 for k in fields - {"score"}):
            return False
    return True


def _succ(spec: dict[str, Any], state: State, action: str) -> list[tuple[State, int, dict[str, int]]]:
    i, d, f, u, t, c = state
    if i >= len(spec["jobs"]):
        return []
    if action == "reject":
        return [((i + 1, d, f, u, t - 1, c), 0,
                 {"flip": 0, "crashes": 0, "unsafe": 0, "late": 1, "cost": 0})]
    if action not in ("accept", "fallback"):
        return []
    job = spec["jobs"][i]
    flips = (0, 1) if action == "accept" and d > 0 else (0,)
    rows = []
    for flip in flips:
        bad = (int(job["score"] == 0) ^ flip) if action == "accept" else 0
        for crashes in range(f + 1):
            charge = job[action + "_cost"]
            is_late = int(job[action + "_time"] + crashes * spec["recovery_time"] > spec["deadline"])
            nxt: State = (i + 1, d - flip, f - crashes, u - bad, t - is_late, c - charge)
            rows.append((nxt, charge, {"flip": flip, "crashes": crashes, "unsafe": bad,
                                      "late": is_late, "cost": charge}))
    return rows


def check(spec: object, cert: object) -> bool:
    if not _valid_spec(spec) or not isinstance(cert, dict):
        return False
    spec = spec  # type: ignore[assignment]
    if set(cert) != {"schema", "feasible", "worst_cost", "visited_states", "policy"}:
        return False
    if type(cert["schema"]) is not int or cert["schema"] != 2 or type(cert["feasible"]) is not bool or not _nat(cert["visited_states"]):
        return False
    root: State = (0, spec["drift"], spec["crashes"], spec["unsafe"], spec["late"], spec["cost"])

    # Recompute the minimax value independently instead of trusting either the
    # producer's selected action or its claimed infeasibility.  The serialized
    # strategy remains a compact witness for the chosen closure, while this
    # bounded Bellman pass establishes optimality/completeness for the declared
    # finite instance.
    visited_count = 0
    @functools.lru_cache(maxsize=None)
    def optimal(state: State) -> int | None:
        nonlocal visited_count
        visited_count += 1
        if visited_count > MAX_CHECK_STATES:
            raise RuntimeError("checker state limit; no feasibility decision")
        if min(state[1:]) < 0:
            return None
        if state[0] == len(spec["jobs"]):
            return 0
        candidates: list[int] = []
        for action in ACTIONS:
            totals: list[int] = []
            losing = False
            for nxt, charge, _ in _succ(spec, state, action):
                sub = optimal(nxt)
                if sub is None:
                    losing = True
                    break
                totals.append(charge + sub)
            if not losing and totals:
                candidates.append(max(totals))
        return min(candidates) if candidates else None

    optimum = optimal(root)
    recomputed_visited = optimal.cache_info().currsize
    if cert["visited_states"] != recomputed_visited:
        return False
    if not cert["feasible"]:
        return cert["worst_cost"] is None and cert["policy"] == {} and optimum is None
    if not _nat(cert["worst_cost"]) or not isinstance(cert["policy"], dict):
        return False
    if optimum is None or cert["worst_cost"] != optimum:
        return False

    visiting: set[State] = set()
    checked: set[State] = set()

    def verify(state: State) -> int | None:
        if min(state[1:]) < 0:
            return None
        if state[0] == len(spec["jobs"]):
            return 0
        if state in visiting:
            return None
        if state in checked:
            row = cert["policy"].get(_encode(state))
            return row["worst_cost"] if isinstance(row, dict) else None
        key = _encode(state)
        row = cert["policy"].get(key)
        if not isinstance(row, dict) or set(row) != {"action", "worst_cost", "outcomes"}:
            return None
        if row["action"] not in ACTIONS or not _nat(row["worst_cost"]) or not isinstance(row["outcomes"], list):
            return None
        expected = _succ(spec, state, row["action"])
        if len(expected) != len(row["outcomes"]):
            return None
        visiting.add(state)
        totals = []
        for exported, (nxt, charge, meta) in zip(row["outcomes"], expected):
            if not isinstance(exported, dict) or set(exported) != {"next", "charge", "meta", "continuation"}:
                visiting.remove(state)
                return None
            if (not _nat(exported["charge"]) or not _nat(exported["continuation"])
                    or not isinstance(exported["meta"], dict)
                    or any(not _nat(v) for v in exported["meta"].values())):
                visiting.remove(state)
                return None
            if exported["next"] != _encode(nxt) or exported["charge"] != charge or exported["meta"] != meta:
                visiting.remove(state)
                return None
            sub = verify(nxt)
            if sub is None or exported["continuation"] != sub:
                visiting.remove(state)
                return None
            totals.append(charge + sub)
        visiting.remove(state)
        if not totals or max(totals) != row["worst_cost"] or row["worst_cost"] != optimal(state):
            return None
        checked.add(state)
        return row["worst_cost"]

    value = verify(root)
    if value is None or value != cert["worst_cost"]:
        return False
    # Every exported row must be reachable under the selected strategy.
    if set(cert["policy"]) != {_encode(s) for s in checked}:
        return False
    return True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("spec", type=Path)
    p.add_argument("certificate", type=Path)
    a = p.parse_args()
    ok = check(json.loads(a.spec.read_text()), json.loads(a.certificate.read_text()))
    print(json.dumps({"valid": ok}))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
