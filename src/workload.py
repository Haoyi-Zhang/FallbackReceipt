"""Deterministic workload-derived finite contracts from public Azure LLM traces.

The retained code trace is the calibration population.  Its empirical terciles
freeze an ordinal pressure score and bounded resource-envelope units.  The same
mapping is then applied without refitting to both the code trace and a
conversation-trace holdout.  This is a transparent workload specification, not
a trained model and not a measurement of Azure latency, price, safety, or model
quality.
"""
from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROWS_PER_EXCERPT = 128


def _nearest_rank(values: Iterable[int], numerator: int, denominator: int) -> int:
    ordered = sorted(values)
    if not ordered or numerator <= 0 or denominator <= 0 or numerator > denominator:
        raise ValueError("invalid nearest-rank request")
    index = max(0, math.ceil(numerator * len(ordered) / denominator) - 1)
    return ordered[index]


def load_rows(path: Path, *, expected_rows: int = ROWS_PER_EXCERPT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["TIMESTAMP", "ContextTokens", "GeneratedTokens"]:
            raise ValueError("unexpected Azure trace schema")
        for index, raw in enumerate(reader):
            stamp = datetime.fromisoformat(raw["TIMESTAMP"])
            context = int(raw["ContextTokens"])
            generated = int(raw["GeneratedTokens"])
            if context < 0 or generated < 0:
                raise ValueError("negative token count")
            gap_ms = 0 if not rows else int(round((stamp - rows[-1]["timestamp"]).total_seconds() * 1000))
            if gap_ms < 0:
                raise ValueError("timestamps are not ordered")
            rows.append({"index": index, "timestamp": stamp, "gap_ms": gap_ms,
                         "context": context, "generated": generated,
                         "tokens": context + generated})
    if expected_rows >= 0 and len(rows) != expected_rows:
        raise ValueError(f"the retained public excerpt must contain exactly {expected_rows} rows")
    return rows


def calibrate(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Freeze empirical cut points and integer units from one trace population."""
    if len(rows) != ROWS_PER_EXCERPT:
        raise ValueError("calibration requires the retained 128-row code excerpt")
    positive_gaps = [r["gap_ms"] for r in rows if r["gap_ms"] > 0]
    calibration = {
        "schema": 2,
        "rows": len(rows),
        "context_q33": _nearest_rank((r["context"] for r in rows), 1, 3),
        "context_q67": _nearest_rank((r["context"] for r in rows), 2, 3),
        "generated_q33": _nearest_rank((r["generated"] for r in rows), 1, 3),
        "generated_q67": _nearest_rank((r["generated"] for r in rows), 2, 3),
        "gap_q33_ms": _nearest_rank(positive_gaps, 1, 3),
        "gap_q67_ms": _nearest_rank(positive_gaps, 2, 3),
        "context_unit": max(1, _nearest_rank((r["context"] for r in rows), 1, 2)),
        "generated_unit": max(1, _nearest_rank((r["generated"] for r in rows), 1, 2)),
        "recovery_time": 2,
    }
    provisional = [to_job(r, calibration, include_provenance=False) for r in rows]
    calibration["deadline"] = max(
        3,
        _nearest_rank(
            [value for job in provisional for value in (job["accept_time"], job["fallback_time"])],
            3,
            4,
        ),
    )
    return calibration


def _band(value: int, low: int, high: int) -> int:
    return 0 if value <= low else (1 if value <= high else 2)


def to_job(row: dict[str, Any], calibration: dict[str, int], *,
           include_provenance: bool = False) -> dict[str, int]:
    required = {
        "context_q33", "context_q67", "generated_q33", "generated_q67",
        "gap_q33_ms", "gap_q67_ms", "context_unit", "generated_unit",
    }
    if not required.issubset(calibration):
        raise ValueError("incomplete workload calibration")

    context_band = _band(row["context"], calibration["context_q33"], calibration["context_q67"])
    generated_band = _band(row["generated"], calibration["generated_q33"], calibration["generated_q67"])
    # A smaller inter-arrival gap contributes more pressure.  The first row's
    # zero gap is conservatively treated as the burstiest band.
    if row["gap_ms"] <= calibration["gap_q33_ms"]:
        burst_band = 2
    elif row["gap_ms"] <= calibration["gap_q67_ms"]:
        burst_band = 1
    else:
        burst_band = 0
    pressure = context_band + generated_band + burst_band
    score = 2 if pressure <= 2 else (1 if pressure <= 4 else 0)

    # Map the frozen calibration bands to small ordinal resource envelopes.
    # Raw token counts characterize the workload, but they are not Azure cost or
    # latency measurements.  Bounding the derived units prevents a domain with
    # systematically longer generations from becoming infeasible solely because
    # its units differ from the calibration population.
    context_units = 1 + context_band
    generated_units = 1 + generated_band
    accept_cost = context_units + generated_units
    fallback_cost = accept_cost + generated_units
    accept_time = 1 + generated_band + int(burst_band == 2)
    fallback_time = accept_time + 1 + int(burst_band > 0)

    job: dict[str, int] = {
        "score": score,
        "accept_cost": accept_cost,
        "fallback_cost": fallback_cost,
        "accept_time": accept_time,
        "fallback_time": fallback_time,
    }
    if include_provenance:
        job.update({
            "pressure": pressure,
            "context_band": context_band,
            "generated_band": generated_band,
            "burst_band": burst_band,
            "context_units": context_units,
            "generated_units": generated_units,
        })
    return job


def windows(path: Path, size: int, calibration: dict[str, int]) -> list[list[dict[str, int]]]:
    rows = load_rows(path)
    if size <= 0 or len(rows) % size:
        raise ValueError("window size must divide the retained excerpt")
    jobs = [to_job(r, calibration) for r in rows]
    return [jobs[i:i + size] for i in range(0, len(jobs), size)]


def make_spec(jobs: list[dict[str, int]], drift: int, crashes: int, budget_level: str,
              calibration: dict[str, int]) -> dict[str, Any]:
    if budget_level not in {"tight", "roomy"}:
        raise ValueError("unknown budget level")
    if drift < 0 or crashes < 0:
        raise ValueError("negative adversary budget")
    accept_total = sum(j["accept_cost"] for j in jobs)
    fallback_delta = sum(j["fallback_cost"] - j["accept_cost"] for j in jobs)
    fraction = (1, 3) if budget_level == "tight" else (2, 3)
    extra = max(1, math.ceil(fallback_delta * fraction[0] / fraction[1]))
    high = sum(j["score"] == 0 for j in jobs)
    unsafe_fraction = (1, 3) if budget_level == "tight" else (2, 3)
    unsafe = max(1, math.ceil(high * unsafe_fraction[0] / unsafe_fraction[1]))
    return {
        "jobs": jobs,
        "drift": drift,
        "crashes": crashes,
        "unsafe": unsafe,
        "late": 1 if budget_level == "tight" else 3,
        "cost": accept_total + extra,
        "recovery_time": calibration["recovery_time"],
        "deadline": calibration["deadline"],
    }
