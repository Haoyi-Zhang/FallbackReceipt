"""Independent replay checker for complete controller/receiver histories.

This module deliberately imports neither the runtime implementation nor the
strategy producer.  It first invokes the independently implemented certificate
checker, then reconstructs the finite event semantics from plain JSON records,
validates receiver closure, checks every reservation and recovery, and requires
a fully completed controller history with no orphan outcome or effect.
"""
from __future__ import annotations

from typing import Any

from checker import check as check_certificate

ACTIONS = {"accept", "fallback", "reject"}
ADAPTERS = {"admission", "cache", "tier"}


def _nat(x: object) -> bool:
    return type(x) is int and x >= 0


def _key(state: tuple[int, ...]) -> str:
    return ",".join(map(str, state))


def _valid_ticket(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 32
            and all(ch in "0123456789abcdef" for ch in value))


def _valid_controller_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 48
        and all(
            ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for ch in value
        )
    )


def _valid_spec(spec: object) -> bool:
    if not isinstance(spec, dict):
        return False
    names = {
        "jobs",
        "drift",
        "crashes",
        "unsafe",
        "late",
        "cost",
        "recovery_time",
        "deadline",
    }
    if set(spec) != names or not isinstance(spec["jobs"], list) or not spec["jobs"]:
        return False
    if any(not _nat(spec[name]) for name in names - {"jobs"}) or spec["deadline"] == 0:
        return False
    fields = {"score", "accept_cost", "fallback_cost", "accept_time", "fallback_time"}
    for job in spec["jobs"]:
        if not isinstance(job, dict) or set(job) != fields or type(job["score"]) is not int or job["score"] not in (0, 1, 2):
            return False
        if any(not _nat(job[name]) or job[name] == 0 for name in fields - {"score"}):
            return False
    return True


def _record_types(value: object, field: str = "") -> bool:
    """Our event format has integer quantities and a single boolean done flag.

    Reject numeric aliases such as 1.0 == 1 or False == 0 before comparisons.
    Tuples are permitted for direct SQLite effect exports; JSON uses lists.
    """
    if type(value) is bool:
        return field == "done"
    if value is None or type(value) in (str, int):
        return True
    if isinstance(value, (list, tuple)):
        return all(_record_types(v) for v in value)
    if isinstance(value, dict):
        return all(type(k) is str and _record_types(v, k) for k, v in value.items())
    return False


def check_runtime(
    spec: object,
    cert: object,
    snapshot: object,
    receiver_outcomes: object,
    effects: object,
) -> bool:
    """Return whether exported records form one complete certified execution."""
    try:
        if not all(_record_types(v) for v in (snapshot, receiver_outcomes, effects)):
            return False
        if (
            not _valid_spec(spec)
            or not isinstance(cert, dict)
            or not check_certificate(spec, cert)
            or not cert.get("feasible")
            or not isinstance(snapshot, dict)
        ):
            return False
        if set(snapshot) != {
            "controller_id",
            "adapter",
            "state",
            "done",
            "attempts",
            "events",
        }:
            return False
        controller_id = snapshot["controller_id"]
        adapter = snapshot["adapter"]
        if not _valid_controller_id(controller_id) or adapter not in ADAPTERS:
            return False
        if not isinstance(receiver_outcomes, dict) or not isinstance(effects, dict):
            return False
        if not isinstance(snapshot["events"], list) or not isinstance(snapshot["attempts"], dict):
            return False

        spec = spec  # type: ignore[assignment]
        jobs = spec["jobs"]
        state = (
            0,
            spec["drift"],
            spec["crashes"],
            spec["unsafe"],
            spec["late"],
            spec["cost"],
        )

        # Validate receiver terminal records independently of the controller log.
        for attempt_id, outcome in receiver_outcomes.items():
            if not isinstance(attempt_id, str) or not attempt_id or not isinstance(outcome, dict):
                return False
            if set(outcome) != {"status", "adapter", "job", "cost", "unsafe"}:
                return False
            if outcome["status"] == "done":
                if (
                    outcome["adapter"] not in ADAPTERS
                    or not isinstance(outcome["job"], str)
                    or not outcome["job"]
                ):
                    return False
                if not _nat(outcome["cost"]) or outcome["unsafe"] not in (0, 1):
                    return False
                if attempt_id not in effects or list(effects[attempt_id]) != [
                    outcome["cost"],
                    outcome["unsafe"],
                ]:
                    return False
            elif outcome["status"] == "canceled":
                if outcome["adapter"] is not None or outcome["job"] is not None:
                    return False
                if outcome["cost"] != 0 or outcome["unsafe"] != 0 or attempt_id in effects:
                    return False
            else:
                return False
        for attempt_id, effect in effects.items():
            if (
                not isinstance(attempt_id, str)
                or not isinstance(effect, (list, tuple))
                or len(effect) != 2
            ):
                return False
            if not _nat(effect[0]) or effect[1] not in (0, 1):
                return False
        if set(effects) - set(receiver_outcomes):
            return False

        selected: dict[int, str] = {}
        next_attempt: dict[int, int] = {}
        job_recoveries: dict[int, int] = {}
        pending: dict[str, dict[str, Any]] = {}
        expected_attempts: dict[str, dict[str, Any]] = {}
        attempt_recoveries: dict[str, int] = {}
        terminal: set[str] = set()
        seen_ids: set[str] = set()
        current_liability = [0, 0]
        completed_actual = [0, 0]
        seen_tickets: set[str] = set()

        for event in snapshot["events"]:
            if not isinstance(event, dict) or "kind" not in event:
                return False
            kind = event["kind"]

            if kind == "select":
                if set(event) != {"kind", "job", "action", "state"}:
                    return False
                i = event["job"]
                if (
                    not _nat(i)
                    or i != state[0]
                    or event["state"] != list(state)
                    or i in selected
                ):
                    return False
                row = cert.get("policy", {}).get(_key(state))
                if not isinstance(row, dict) or row.get("action") != event["action"]:
                    return False
                if event["action"] not in ACTIONS:
                    return False
                selected[i] = event["action"]
                next_attempt[i] = 0
                job_recoveries[i] = 0

            elif kind == "reserve":
                if set(event) != {
                    "kind",
                    "id",
                    "job",
                    "action",
                    "adapter",
                    "target",
                    "upper",
                }:
                    return False
                attempt_id, i, action = event["id"], event["job"], event["action"]
                if not isinstance(attempt_id, str) or not attempt_id or attempt_id in seen_ids:
                    return False
                if i != state[0] or selected.get(i) != action or action not in {"accept", "fallback"}:
                    return False
                expected_id = f"{controller_id}-j{i}-a{next_attempt.get(i, -1)}"
                expected_target = f"job-{i}"
                if (
                    attempt_id != expected_id
                    or event["adapter"] != adapter
                    or event["target"] != expected_target
                ):
                    return False
                next_attempt[i] += 1
                upper = event["upper"]
                if not isinstance(upper, list) or len(upper) != 2 or any(not _nat(x) for x in upper):
                    return False
                job = jobs[i]
                if action == "accept":
                    nominal = int(job["score"] == 0)
                    upper_unsafe = max(nominal, nominal ^ 1) if state[1] > 0 else nominal
                else:
                    upper_unsafe = 0
                expected_upper = [job[action + "_cost"], upper_unsafe]
                if upper != expected_upper or pending:
                    return False
                if upper[0] > state[5] or upper[1] > state[3]:
                    return False
                pending[attempt_id] = {
                    "job": i,
                    "action": action,
                    "adapter": adapter,
                    "target": expected_target,
                    "upper": list(upper),
                }
                expected_attempts[attempt_id] = {
                    "job": i,
                    "action": action,
                    "adapter": adapter,
                    "target": expected_target,
                    "status": "pending",
                    "upper": list(upper),
                    "recoveries": 0,
                    "ticket": None,
                    "actual": None,
                }
                attempt_recoveries[attempt_id] = 0
                seen_ids.add(attempt_id)
                current_liability[0] += upper[0]
                current_liability[1] += upper[1]
                if completed_actual[0] + current_liability[0] > spec["cost"]:
                    return False
                if completed_actual[1] + current_liability[1] > spec["unsafe"]:
                    return False

            elif kind == "recover":
                if set(event) != {"kind", "id", "job", "recoveries", "ticket"}:
                    return False
                attempt_id = event["id"]
                if attempt_id not in pending or event["job"] != pending[attempt_id]["job"]:
                    return False
                i = event["job"]
                ticket = event["ticket"]
                if not _valid_ticket(ticket) or ticket in seen_tickets:
                    return False
                seen_tickets.add(ticket)
                job_recoveries[i] = job_recoveries.get(i, 0) + 1
                attempt_recoveries[attempt_id] += 1
                expected_attempts[attempt_id]["recoveries"] = attempt_recoveries[attempt_id]
                expected_attempts[attempt_id]["ticket"] = ticket
                if (
                    event["recoveries"] != attempt_recoveries[attempt_id]
                    or job_recoveries[i] > state[2]
                ):
                    return False

            elif kind == "settle":
                attempt_id = event.get("id")
                if attempt_id not in pending or event.get("status") not in {"done", "canceled"}:
                    return False
                attempt = pending.pop(attempt_id)
                i = attempt["job"]
                current_liability[0] -= attempt["upper"][0]
                current_liability[1] -= attempt["upper"][1]

                if event["status"] == "canceled":
                    if set(event) != {
                        "kind",
                        "id",
                        "status",
                        "via",
                        "ticket",
                        "adapter",
                        "target",
                        "actual",
                    }:
                        return False
                    if (
                        event["via"] != "close"
                        or event["ticket"] != expected_attempts[attempt_id]["ticket"]
                        or event["adapter"] != attempt["adapter"]
                        or event["target"] != attempt["target"]
                        or event["actual"] != [0, 0]
                        or attempt_recoveries[attempt_id] == 0
                    ):
                        return False
                    outcome = receiver_outcomes.get(attempt_id)
                    if not isinstance(outcome, dict) or outcome.get("status") != "canceled":
                        return False
                    expected_attempts[attempt_id]["status"] = "canceled"
                    expected_attempts[attempt_id]["actual"] = [0, 0]
                    terminal.add(attempt_id)
                    continue

                if set(event) != {
                    "kind",
                    "id",
                    "status",
                    "via",
                    "ticket",
                    "adapter",
                    "target",
                    "actual",
                    "charge",
                    "crashes",
                    "late",
                    "next",
                }:
                    return False
                if (
                    event["adapter"] != attempt["adapter"]
                    or event["target"] != attempt["target"]
                    or event["via"] not in {"execute", "close"}
                ):
                    return False
                recoveries = job_recoveries.get(i, 0)
                current_attempt_recoveries = attempt_recoveries[attempt_id]
                expected_ticket = expected_attempts[attempt_id]["ticket"]
                if current_attempt_recoveries == 0:
                    if event["via"] != "execute" or event["ticket"] is not None:
                        return False
                elif event["via"] != "close" or event["ticket"] != expected_ticket:
                    return False
                actual = event["actual"]
                if not isinstance(actual, list) or len(actual) != 2 or any(not _nat(x) for x in actual):
                    return False
                charge = event["charge"]
                if charge != attempt["upper"][0] or actual[0] > charge or actual[1] > attempt["upper"][1]:
                    return False
                outcome = receiver_outcomes.get(attempt_id)
                if not isinstance(outcome, dict) or outcome.get("status") != "done":
                    return False
                if (
                    outcome.get("adapter") != attempt["adapter"]
                    or outcome.get("job") != attempt["target"]
                    or list(effects.get(attempt_id, ())) != actual
                    or [outcome.get("cost"), outcome.get("unsafe")] != actual
                ):
                    return False
                action = attempt["action"]
                job = jobs[i]
                if action == "fallback" and actual[1] != 0:
                    return False
                flip = (int(job["score"] == 0) ^ actual[1]) if action == "accept" else 0
                late = int(
                    job[action + "_time"] + recoveries * spec["recovery_time"] > spec["deadline"]
                )
                # Cost state is charged conservatively by the declared envelope;
                # actual physical cost is separately checked not to exceed it.
                nxt = (
                    i + 1,
                    state[1] - flip,
                    state[2] - recoveries,
                    state[3] - actual[1],
                    state[4] - late,
                    state[5] - charge,
                )
                if min(nxt[1:]) < 0:
                    return False
                if (
                    event["crashes"] != recoveries
                    or event["late"] != late
                    or event["next"] != list(nxt)
                ):
                    return False
                policy_row = cert["policy"].get(_key(state))
                expected_meta = {
                    "flip": flip,
                    "crashes": recoveries,
                    "unsafe": actual[1],
                    "late": late,
                    "cost": charge,
                }
                if not isinstance(policy_row, dict) or policy_row.get("action") != action:
                    return False
                if not any(
                    isinstance(outcome_row, dict)
                    and outcome_row.get("next") == _key(nxt)
                    and outcome_row.get("charge") == charge
                    and outcome_row.get("meta") == expected_meta
                    for outcome_row in policy_row.get("outcomes", [])
                ):
                    return False
                completed_actual[0] += actual[0]
                completed_actual[1] += actual[1]
                expected_attempts[attempt_id]["status"] = "done"
                expected_attempts[attempt_id]["actual"] = list(actual)
                state = nxt
                terminal.add(attempt_id)

            elif kind == "reject":
                if set(event) != {"kind", "job", "next"}:
                    return False
                i = event["job"]
                if i != state[0] or selected.get(i) != "reject":
                    return False
                nxt = (i + 1, state[1], state[2], state[3], state[4] - 1, state[5])
                if min(nxt[1:]) < 0 or event["next"] != list(nxt):
                    return False
                policy_row = cert["policy"].get(_key(state))
                expected_meta = {"flip": 0, "crashes": 0, "unsafe": 0, "late": 1, "cost": 0}
                if not isinstance(policy_row, dict) or policy_row.get("action") != "reject":
                    return False
                if not any(
                    isinstance(outcome_row, dict)
                    and outcome_row.get("next") == _key(nxt)
                    and outcome_row.get("charge") == 0
                    and outcome_row.get("meta") == expected_meta
                    for outcome_row in policy_row.get("outcomes", [])
                ):
                    return False
                state = nxt
            else:
                return False

        # The checker is for a complete export, not a safe-but-stalled prefix.
        if pending or state[0] != len(jobs) or snapshot["done"] is not True:
            return False
        if snapshot["state"] != list(state):
            return False
        if terminal != seen_ids or set(snapshot["attempts"]) != seen_ids:
            return False
        if set(receiver_outcomes) != seen_ids:
            return False
        expected_effect_ids = {
            attempt_id
            for attempt_id, outcome in receiver_outcomes.items()
            if outcome["status"] == "done"
        }
        if set(effects) != expected_effect_ids:
            return False

        for attempt_id, recorded in snapshot["attempts"].items():
            if not isinstance(recorded, dict) or set(recorded) != {
                "job",
                "action",
                "adapter",
                "target",
                "status",
                "upper",
                "recoveries",
                "ticket",
                "actual",
            }:
                return False
            if recorded != expected_attempts[attempt_id]:
                return False
            outcome = receiver_outcomes[attempt_id]
            if recorded["status"] != outcome["status"]:
                return False
            if recorded["actual"] != [outcome["cost"], outcome["unsafe"]]:
                return False
            if recorded["status"] == "done" and (
                recorded["adapter"] != outcome["adapter"] or recorded["target"] != outcome["job"]
            ):
                return False
        return True
    except (KeyError, TypeError, ValueError, IndexError):
        return False
