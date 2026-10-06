"""Exact fixed-sequence baseline using budget envelopes, not the Bellman solver.

An open-loop controller commits one action per job before any effect or crash
outcome.  The worst unsafe and late counts can be maximised separately, since a
robust contract must satisfy each coordinate for every legal environment path.
This module does not import the synthesizer, its transitions, or its checker.
"""
from __future__ import annotations
from itertools import product
from dataclasses import dataclass
from typing import Any, Iterable

ACTIONS = ("accept", "fallback", "reject")
MAX_SCHEDULES = 100_000
JOB_FIELDS = ("score", "accept_cost", "fallback_cost", "accept_time", "fallback_time")


def validate_spec(spec: object) -> None:
    names = {"jobs", "drift", "crashes", "unsafe", "late", "cost", "recovery_time", "deadline"}
    if not isinstance(spec, dict) or set(spec) != names:
        raise ValueError("invalid open-loop specification fields")
    if any(type(spec[k]) is not int or spec[k] < 0 for k in names - {"jobs"}):
        raise ValueError("contract quantities must be nonnegative integers")
    if spec["deadline"] == 0 or not isinstance(spec["jobs"], list) or not spec["jobs"]:
        raise ValueError("positive deadline and nonempty job list required")
    for job in spec["jobs"]:
        if not isinstance(job, dict) or set(job) != set(JOB_FIELDS):
            raise ValueError("invalid job fields")
        if type(job["score"]) is not int or job["score"] not in (0, 1, 2):
            raise ValueError("score must be an integer class")
        if any(type(job[k]) is not int or job[k] <= 0 for k in JOB_FIELDS[1:]):
            raise ValueError("positive integer envelopes required")


def _binding(jobs, deadline, recovery_time):
    return (tuple(tuple(j[k] for k in JOB_FIELDS) for j in jobs), deadline, recovery_time)


@dataclass(frozen=True)
class Catalogue:
    """Internal enumeration cache; binds immutable job, time and deadline inputs."""
    binding: tuple
    rows: tuple



def envelope(jobs: list[dict[str, int]], actions: Iterable[str], deadline: int,
             recovery_time: int) -> tuple[int, int, int, int, tuple[int, ...]]:
    """Return cost, nominal-bad accepts, flippable accepts, base late, crash needs."""
    actions = tuple(actions)
    if len(actions) != len(jobs) or any(a not in ACTIONS for a in actions):
        raise ValueError("one legal action per job is required")
    cost = nominal_bad = flippable = late = 0
    needs = []
    for j, a in zip(jobs, actions):
        if a == "reject":
            late += 1
            continue
        cost += j[a + "_cost"]
        if a == "accept":
            nominal_bad += int(j["score"] == 0)
            flippable += int(j["score"] != 0)
        h = j[a + "_time"]
        if h > deadline:
            late += 1
        elif recovery_time > 0:
            needs.append((deadline - h) // recovery_time + 1)
    return cost, nominal_bad, flippable, late, tuple(sorted(needs))


def worst_counts(signature: tuple[int, int, int, int, tuple[int, ...]],
                 drift: int, crashes: int) -> tuple[int, int, int]:
    cost, nominal_bad, flippable, late, needs = signature
    remaining = crashes
    for needed in needs:
        if needed > remaining:
            break
        remaining -= needed
        late += 1
    return cost, nominal_bad + min(drift, flippable), late


def catalogue(jobs: list[dict[str, int]], deadline: int,
              recovery_time: int) -> Catalogue:
    if 3 ** len(jobs) > MAX_SCHEDULES:
        raise RuntimeError("open-loop enumeration limit; not an infeasibility result")
    return Catalogue(_binding(jobs, deadline, recovery_time),
                     tuple((a, envelope(jobs, a, deadline, recovery_time))
                           for a in product(ACTIONS, repeat=len(jobs))))


def solve(spec: dict[str, Any], schedules: Catalogue | None = None) -> dict:
    """Return exact minimum certified charge among robust feasible fixed sequences.

    Actual receiver costs are only upper-bounded by the declared charges; this
    comparator neither observes them nor assumes that their bounds are attained.
    """
    validate_spec(spec)
    if schedules is None:
        schedules = catalogue(spec["jobs"], spec["deadline"], spec["recovery_time"])
    if (not isinstance(schedules, Catalogue) or
            schedules.binding != _binding(spec["jobs"], spec["deadline"], spec["recovery_time"])):
        raise ValueError("enumeration cache belongs to different job/time inputs")
    best = None
    feasible = 0
    for actions, signature in schedules.rows:
        cost, unsafe, late = worst_counts(signature, spec["drift"], spec["crashes"])
        if cost <= spec["cost"] and unsafe <= spec["unsafe"] and late <= spec["late"]:
            feasible += 1
            # Catalogue uses the same documented action order; do not optimise
            # the baseline after observing an adversarial outcome.
            if best is None or cost < best[0]:
                best = (cost, actions)
    return {"feasible": best is not None,
            "worst_cost": None if best is None else best[0],
            "actions": None if best is None else list(best[1]),
            "feasible_sequences": feasible, "sequences_checked": len(schedules.rows)}
