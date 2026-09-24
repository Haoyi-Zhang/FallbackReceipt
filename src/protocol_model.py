#!/usr/bin/env python3
"""Independent finite model for the receipt-closure boundary.

The executable controller uses SQLite.  This module does not import that
implementation.  It defines a small labelled transition system for one
non-idempotent logical action and explores every interleaving up to a declared
crash bound.  The model is intentionally narrower than a distributed-system
proof: receiver execute and close are atomic, and a fair, available receiver is
assumed once faults cease.

The correct protocol is compared with four deliberately broken variants:

* send_before_reserve: dispatch can precede the durable controller reservation;
* fresh_retry: restart abandons the old identifier and immediately retries;
* query_without_fence: a negative status query is treated as cancellation;
* settle_before_charge: a recovered DONE result can settle before the durable
  recovery charge.

The model checker reports shortest counterexample traces for safety properties
and computes no-further-crash completion reachability for the correct model.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
from pathlib import Path
from typing import Any, Iterable

NONE = "none"
DONE = "done"
CANCELED = "canceled"
VARIANTS = (
    "receipt_closed",
    "send_before_reserve",
    "fresh_retry",
    "query_without_fence",
    "settle_before_charge",
)


@dataclasses.dataclass(frozen=True)
class State:
    """A bounded abstract protocol state.

    ``statuses`` is the receiver's durable record per attempt identifier.
    ``reserved`` records whether the controller durably reserved the identifier
    before it could reach the receiver.  ``dispatched`` means an execute request
    may still arrive; a receiver-side CANCELED state fences that delayed request.
    ``active`` is the identifier currently tracked by the controller, or -1.
    """

    phase: str
    active: int
    statuses: tuple[str, ...]
    reserved: tuple[bool, ...]
    dispatched: tuple[bool, ...]
    crashes: int
    restarts: int
    charges: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "active": self.active,
            "statuses": list(self.statuses),
            "reserved": list(self.reserved),
            "dispatched": list(self.dispatched),
            "crashes": self.crashes,
            "restarts": self.restarts,
            "charges": self.charges,
        }


@dataclasses.dataclass(frozen=True)
class Edge:
    label: str
    state: State


def _replace(values: tuple[Any, ...], index: int, value: Any) -> tuple[Any, ...]:
    out = list(values)
    out[index] = value
    return tuple(out)


def _new_attempt(state: State, *, reserved: bool, dispatched: bool = False) -> State:
    index = len(state.statuses)
    return dataclasses.replace(
        state,
        phase="live",
        active=index,
        statuses=state.statuses + (NONE,),
        reserved=state.reserved + (reserved,),
        dispatched=state.dispatched + (dispatched,),
    )


def initial() -> State:
    return State("boot", -1, (), (), (), 0, 0, 0)


def _receiver_steps(state: State) -> Iterable[Edge]:
    # Any dispatched request may arrive while the controller is live, crashed,
    # recovering, or even after it believes the logical operation is complete.
    # DONE is the atomic effect+receipt record.  CANCELED absorbs late execute.
    for i, (status, dispatched) in enumerate(zip(state.statuses, state.dispatched)):
        if dispatched and status == NONE:
            yield Edge(
                f"receiver execute a{i}",
                dataclasses.replace(state, statuses=_replace(state.statuses, i, DONE)),
            )


def _crash(state: State, max_crashes: int, *, lose_unreserved: bool = False) -> Iterable[Edge]:
    if state.crashes >= max_crashes:
        return
    active = state.active
    if lose_unreserved and active >= 0 and not state.reserved[active]:
        active = -1
    yield Edge(
        "controller crash",
        dataclasses.replace(state, phase="crashed", active=active, crashes=state.crashes + 1),
    )


def transitions(state: State, variant: str, max_crashes: int, max_attempts: int) -> tuple[Edge, ...]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    edges: list[Edge] = list(_receiver_steps(state))

    if state.phase == "boot":
        if variant == "send_before_reserve":
            edges.append(Edge("dispatch a0 before durable reserve", _new_attempt(state, reserved=False, dispatched=True)))
        else:
            edges.append(Edge("durably reserve a0", _new_attempt(state, reserved=True)))

    elif state.phase == "live":
        if state.active < 0:
            if len(state.statuses) < max_attempts:
                edges.append(Edge("reserve replacement after lost local record", _new_attempt(state, reserved=True)))
        else:
            i = state.active
            if not state.dispatched[i]:
                edges.append(Edge(
                    f"dispatch a{i}",
                    dataclasses.replace(state, dispatched=_replace(state.dispatched, i, True)),
                ))
            if state.statuses[i] == DONE:
                edges.append(Edge(f"settle DONE a{i}", dataclasses.replace(state, phase="done", active=-1)))
            edges.extend(_crash(state, max_crashes, lose_unreserved=(variant == "send_before_reserve")))

    elif state.phase == "crashed":
        # A restart with a tracked reservation must account for recovery before
        # closing or retrying.  The settle-before-charge mutant splits this step.
        if variant == "settle_before_charge" and state.active >= 0:
            edges.append(Edge(
                "restart with pending attempt (charge not yet durable)",
                dataclasses.replace(state, phase="unpaid_recovery", restarts=state.restarts + 1),
            ))
        elif state.active >= 0:
            edges.append(Edge(
                "restart and durably charge recovery",
                dataclasses.replace(
                    state, phase="recovery", restarts=state.restarts + 1, charges=state.charges + 1
                ),
            ))
        elif len(state.statuses) < max_attempts:
            # The send-before-reserve mutant has no durable pending identifier.
            # It can still account for the process failure, but it cannot close
            # the request that may already be in flight.
            charged = dataclasses.replace(
                state, phase="recovery", restarts=state.restarts + 1, charges=state.charges + 1
            )
            edges.append(Edge("restart finds no durable attempt; reserve replacement", _new_attempt(charged, reserved=True)))

    elif state.phase == "unpaid_recovery":
        if state.active >= 0 and state.statuses[state.active] == DONE:
            edges.append(Edge(
                f"settle recovered DONE a{state.active} before charging",
                dataclasses.replace(state, phase="done", active=-1),
            ))
        edges.append(Edge(
            "durably charge recovery",
            dataclasses.replace(state, phase="recovery", charges=state.charges + 1),
        ))
        edges.extend(_crash(state, max_crashes))

    elif state.phase == "recovery":
        if state.active < 0:
            if len(state.statuses) < max_attempts:
                edges.append(Edge("reserve replacement", _new_attempt(state, reserved=True)))
        elif variant == "fresh_retry":
            if len(state.statuses) < max_attempts:
                edges.append(Edge(
                    f"abandon a{state.active}; reserve fresh identifier",
                    _new_attempt(dataclasses.replace(state, active=-1), reserved=True),
                ))
        elif variant == "query_without_fence":
            i = state.active
            if state.statuses[i] == DONE:
                edges.append(Edge(f"query observes DONE a{i}", dataclasses.replace(state, phase="closed")))
            elif len(state.statuses) < max_attempts:
                edges.append(Edge(
                    f"query observes no result for a{i}; retry without fence",
                    _new_attempt(dataclasses.replace(state, active=-1), reserved=True),
                ))
        else:
            i = state.active
            closed_status = DONE if state.statuses[i] == DONE else CANCELED
            edges.append(Edge(
                f"atomically close a{i} as {closed_status.upper()}",
                dataclasses.replace(
                    state, phase="closed", statuses=_replace(state.statuses, i, closed_status)
                ),
            ))
        edges.extend(_crash(state, max_crashes))

    elif state.phase == "closed":
        if state.active >= 0:
            i = state.active
            if state.statuses[i] == DONE:
                edges.append(Edge(f"settle closed DONE a{i}", dataclasses.replace(state, phase="done", active=-1)))
            elif state.statuses[i] == CANCELED and len(state.statuses) < max_attempts:
                edges.append(Edge(f"reserve successor after CANCELED a{i}", _new_attempt(state, reserved=True)))
        edges.extend(_crash(state, max_crashes))

    elif state.phase == "done":
        # Only delayed receiver steps remain; they expose duplicate effects in
        # broken variants that abandoned an unfenced identifier.
        pass
    else:
        raise AssertionError(f"invalid phase: {state.phase}")

    # Avoid duplicate labelled edges and no-op transitions.  Self-loops such as
    # an execute arriving after CANCELED are semantically fenced and omitted.
    unique: dict[tuple[str, State], Edge] = {}
    for edge in edges:
        if edge.state != state:
            unique[(edge.label, edge.state)] = edge
    return tuple(unique.values())


def violations(state: State) -> tuple[str, ...]:
    problems: list[str] = []
    effects = sum(status == DONE for status in state.statuses)
    if effects > 1:
        problems.append("duplicate_effect")
    if any(status == DONE and not state.reserved[i] for i, status in enumerate(state.statuses)):
        problems.append("effect_without_durable_reservation")
    if state.phase == "done" and state.charges < state.restarts:
        problems.append("settled_before_recovery_charge")
    if state.phase == "done" and effects == 0:
        problems.append("settled_without_effect")
    # Once the controller stops tracking an attempt, a dispatched request must
    # already have a stable receiver terminal state.  Otherwise a delayed
    # execute can still race a replacement attempt.
    for i, (status, dispatched) in enumerate(zip(state.statuses, state.dispatched)):
        if i != state.active and dispatched and status == NONE:
            problems.append("abandoned_attempt_not_receiver_closed")
            break
    return tuple(problems)


def _trace(state: State, predecessor: dict[State, tuple[State, str] | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = state
    while predecessor[cursor] is not None:
        prev, label = predecessor[cursor]  # type: ignore[misc]
        rows.append({"action": label, "state": cursor.as_dict()})
        cursor = prev
    rows.reverse()
    return rows


def explore(variant: str, max_crashes: int = 2) -> dict[str, Any]:
    if type(max_crashes) is not int or max_crashes < 0 or max_crashes > 4:
        raise ValueError("max_crashes must be an integer in 0..4")
    max_attempts = max_crashes + 2
    root = initial()
    queue: collections.deque[State] = collections.deque([root])
    predecessor: dict[State, tuple[State, str] | None] = {root: None}
    shortest: dict[str, dict[str, Any]] = {}
    edge_count = 0

    while queue:
        state = queue.popleft()
        for problem in violations(state):
            if problem not in shortest:
                shortest[problem] = {"state": state.as_dict(), "trace": _trace(state, predecessor)}
        for edge in transitions(state, variant, max_crashes, max_attempts):
            edge_count += 1
            if edge.state not in predecessor:
                predecessor[edge.state] = (state, edge.label)
                queue.append(edge.state)

    states = tuple(predecessor)
    terminal = [s for s in states if s.phase == "done"]
    quiescent = [s for s in terminal if not tuple(_receiver_steps(s))]

    # With no further crash edges, every correct reachable state must retain a
    # finite path to a quiescent DONE state.  This is completion reachability
    # under receiver availability/fair scheduling, not wait-free liveness.
    reverse: dict[State, list[State]] = collections.defaultdict(list)
    for state in states:
        for edge in transitions(state, variant, max_crashes, max_attempts):
            if edge.label == "controller crash":
                continue
            reverse[edge.state].append(state)
    distance: dict[State, int] = {s: 0 for s in quiescent}
    work: collections.deque[State] = collections.deque(quiescent)
    while work:
        state = work.popleft()
        for prev in reverse[state]:
            if prev not in distance:
                distance[prev] = distance[state] + 1
                work.append(prev)

    return {
        "variant": variant,
        "max_crashes": max_crashes,
        "max_attempts": max_attempts,
        "reachable_states": len(states),
        "explored_edges": edge_count,
        "terminal_states": len(terminal),
        "quiescent_terminal_states": len(quiescent),
        "violation_kinds": sorted(shortest),
        "shortest_counterexamples": shortest,
        "all_states_complete_without_more_crashes": len(distance) == len(states),
        "states_without_completion_path": len(states) - len(distance),
        "max_no_further_crash_steps": max(distance.values(), default=None),
    }


def ambiguity_witness() -> dict[str, Any]:
    """Two indistinguishable post-crash worlds for a non-idempotent effect."""
    observation = {
        "controller_log": "attempt a0 is durably pending",
        "reply": "none",
        "receiver_query_or_fence": "unavailable",
    }
    return {
        "assumptions": [
            "the effect is non-idempotent",
            "the controller crashed after dispatch and before receiving a reply",
            "no stable result query or atomic cancellation fence is available",
        ],
        "shared_controller_observation": observation,
        "worlds": {
            "effect_not_executed": {"receiver_effects": 0, "controller_observation": observation},
            "effect_executed_reply_lost": {"receiver_effects": 1, "controller_observation": observation},
        },
        "deterministic_choices": {
            "retry_with_fresh_identifier": {
                "effect_not_executed": "progress with one effect",
                "effect_executed_reply_lost": "two effects; at-most-once violated",
            },
            "do_not_retry": {
                "effect_not_executed": "no effect; conditional progress violated",
                "effect_executed_reply_lost": "one effect but controller cannot establish completion",
            },
        },
        "conclusion": (
            "Local post-crash state alone cannot guarantee both at-most-one non-idempotent effect "
            "and conditional completion; a stable result or receiver-side closing operation is necessary."
        ),
    }


def run_all(max_crashes: int = 2) -> dict[str, Any]:
    models = {variant: explore(variant, max_crashes) for variant in VARIANTS}
    correct = models["receipt_closed"]
    if correct["violation_kinds"]:
        raise AssertionError("receipt-closed model reached a safety violation")
    if not correct["all_states_complete_without_more_crashes"]:
        raise AssertionError("receipt-closed model has a bounded no-further-crash dead end")
    expected = {
        "send_before_reserve": "effect_without_durable_reservation",
        "fresh_retry": "duplicate_effect",
        "query_without_fence": "duplicate_effect",
        "settle_before_charge": "settled_before_recovery_charge",
    }
    for variant, problem in expected.items():
        if problem not in models[variant]["shortest_counterexamples"]:
            raise AssertionError(f"{variant} did not expose {problem}")
    return {
        "schema": 1,
        "max_crashes": max_crashes,
        "ambiguity_witness": ambiguity_witness(),
        "models": models,
        "expected_mutant_failures": expected,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-crashes", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_all(args.max_crashes)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
