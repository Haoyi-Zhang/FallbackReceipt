#!/usr/bin/env python3
"""Finite robust selective-fallback game and strategy-certificate exporter.

The model is deliberately finite.  A receiver-closed retry can be canceled before
its effect or recovered after its effect, so crashes add bounded recovery delay
but do not multiply physical cost or unsafe effects.  The runtime implementation
in runtime.py is checked against this action/outcome contract separately.
"""
from __future__ import annotations

import argparse
import functools
import json
from pathlib import Path
from typing import Any, Iterable

ACTIONS = ("accept", "fallback", "reject")
MAX_STATES = 2_000_000
State = tuple[int, int, int, int, int, int]


def _is_nat(value: object) -> bool:
    return type(value) is int and value >= 0


def validate_spec(spec: dict[str, Any]) -> None:
    required = {"jobs", "drift", "crashes", "unsafe", "late", "cost", "recovery_time", "deadline"}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError("invalid specification fields")
    if not isinstance(spec["jobs"], list) or not spec["jobs"]:
        raise ValueError("jobs must be a nonempty list")
    for name in required - {"jobs"}:
        if not _is_nat(spec[name]):
            raise ValueError(f"{name} must be a nonnegative integer")
    if spec["deadline"] == 0:
        raise ValueError("deadline must be positive")
    job_fields = {"score", "accept_cost", "fallback_cost", "accept_time", "fallback_time"}
    for job in spec["jobs"]:
        if not isinstance(job, dict) or set(job) != job_fields:
            raise ValueError("invalid job fields")
        if type(job["score"]) is not int or job["score"] not in (0, 1, 2):
            raise ValueError("score must be one of 0, 1, 2")
        for name in job_fields - {"score"}:
            if not _is_nat(job[name]) or job[name] == 0:
                raise ValueError(f"{name} must be a positive integer")


def initial(spec: dict[str, Any]) -> State:
    validate_spec(spec)
    return (0, spec["drift"], spec["crashes"], spec["unsafe"], spec["late"], spec["cost"])


def encode(state: State) -> str:
    return ",".join(map(str, state))


def decode(text: str) -> State:
    parts = text.split(",")
    if len(parts) != 6:
        raise ValueError("state must have six coordinates")
    values = tuple(int(x) for x in parts)
    if any(v < 0 for v in values):
        raise ValueError("certificate states must be nonnegative")
    return values  # type: ignore[return-value]


def nominal_unsafe(score: int) -> int:
    """Score class zero is the nominal unsafe-accept class."""
    return int(score == 0)


def successors(spec: dict[str, Any], state: State, action: str) -> list[tuple[State, int, dict[str, int]]]:
    """Return all declared adversarial outcomes for one controller action.

    Each tuple is (next_state, immediate_certified_charge, outcome_record).  A crash
    is a receiver-closed canceled attempt or a lost reply to a completed attempt;
    either way the final logical action has at most one physical effect.
    """
    i, drift, crashes, unsafe, late, cost = state
    if min(state) < 0 or i >= len(spec["jobs"]):
        return []
    if action not in ACTIONS:
        raise ValueError("unknown action")
    if action == "reject":
        return [((i + 1, drift, crashes, unsafe, late - 1, cost), 0,
                 {"flip": 0, "crashes": 0, "unsafe": 0, "late": 1, "cost": 0})]

    job = spec["jobs"][i]
    flips: Iterable[int] = (0, 1) if action == "accept" and drift > 0 else (0,)
    rows: list[tuple[State, int, dict[str, int]]] = []
    for flip in flips:
        realized_unsafe = nominal_unsafe(job["score"]) ^ flip if action == "accept" else 0
        for retry_crashes in range(crashes + 1):
            physical_cost = job[f"{action}_cost"]
            duration = job[f"{action}_time"] + retry_crashes * spec["recovery_time"]
            is_late = int(duration > spec["deadline"])
            nxt: State = (
                i + 1,
                drift - flip,
                crashes - retry_crashes,
                unsafe - realized_unsafe,
                late - is_late,
                cost - physical_cost,
            )
            rows.append((nxt, physical_cost, {
                "flip": flip,
                "crashes": retry_crashes,
                "unsafe": realized_unsafe,
                "late": is_late,
                "cost": physical_cost,
            }))
    return rows


def synthesize(spec: dict[str, Any], allowed_actions: dict[int, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Synthesize a minimax strategy and export its reachable strategy closure."""
    root = initial(spec)
    seen_count = 0
    decision: dict[State, tuple[str, int]] = {}

    @functools.lru_cache(maxsize=None)
    def value(state: State) -> int | None:
        nonlocal seen_count
        seen_count += 1
        if seen_count > MAX_STATES:
            raise RuntimeError("state limit reached; this is not an infeasibility certificate")
        i = state[0]
        if min(state[1:]) < 0:
            return None
        if i == len(spec["jobs"]):
            return 0
        actions = allowed_actions.get(i, ACTIONS) if allowed_actions else ACTIONS
        candidates: list[tuple[int, int, str]] = []
        for order, action in enumerate(ACTIONS):
            if action not in actions:
                continue
            outcomes = successors(spec, state, action)
            vals: list[int] = []
            losing = False
            for nxt, immediate, _ in outcomes:
                sub = value(nxt)
                if sub is None:
                    losing = True
                    break
                vals.append(immediate + sub)
            if not losing and vals:
                candidates.append((max(vals), order, action))
        if not candidates:
            return None
        best_value, _, best_action = min(candidates)
        decision[state] = (best_action, best_value)
        return best_value

    root_value = value(root)
    if root_value is None:
        return {"schema": 2, "feasible": False, "worst_cost": None,
                "visited_states": seen_count, "policy": {}}

    # Export exactly the nonterminal strategy closure under all declared outcomes.
    policy: dict[str, dict[str, Any]] = {}
    stack = [root]
    closure: set[State] = set()
    while stack:
        state = stack.pop()
        if state in closure or state[0] == len(spec["jobs"]):
            continue
        closure.add(state)
        action, state_value = decision[state]
        outcomes = successors(spec, state, action)
        row_outcomes = []
        for nxt, immediate, meta in outcomes:
            sub = 0 if nxt[0] == len(spec["jobs"]) and min(nxt[1:]) >= 0 else value(nxt)
            if sub is None:
                raise AssertionError("selected action has a losing successor")
            row_outcomes.append({"next": encode(nxt), "charge": immediate, "meta": meta,
                                 "continuation": sub})
            if nxt[0] < len(spec["jobs"]):
                stack.append(nxt)
        policy[encode(state)] = {"action": action, "worst_cost": state_value,
                                 "outcomes": row_outcomes}
    return {"schema": 2, "feasible": True, "worst_cost": root_value,
            "visited_states": seen_count, "policy": policy}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    cert = synthesize(spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cert, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"feasible": cert["feasible"], "worst_cost": cert["worst_cost"],
                      "visited_states": cert["visited_states"], "policy_rows": len(cert["policy"])},
                     sort_keys=True))


if __name__ == "__main__":
    main()
