"""Uncached alternating-tree oracle for small specifications.

The implementation intentionally shares no transition helpers with game.py.
"""
from __future__ import annotations


def exact(spec):
    if len(spec["jobs"]) > 4 or spec["drift"] > 2 or spec["crashes"] > 2:
        raise ValueError("oracle domain exceeded")

    def visit(i, d, f, u, t, c):
        if min(d, f, u, t, c) < 0:
            return None
        if i == len(spec["jobs"]):
            return 0
        job = spec["jobs"][i]
        choices = []

        rejected = visit(i + 1, d, f, u, t - 1, c)
        if rejected is not None:
            choices.append(rejected)

        for action in ("accept", "fallback"):
            all_values = []
            losing = False
            flip_values = (0, 1) if action == "accept" and d else (0,)
            for flip in flip_values:
                bad = (int(job["score"] == 0) ^ flip) if action == "accept" else 0
                for crashed in range(f + 1):
                    cost = job[action + "_cost"]
                    late = int(job[action + "_time"] + crashed * spec["recovery_time"] > spec["deadline"])
                    tail = visit(i + 1, d - flip, f - crashed, u - bad, t - late, c - cost)
                    if tail is None:
                        losing = True
                    else:
                        all_values.append(cost + tail)
            if not losing and all_values:
                choices.append(max(all_values))
        return min(choices) if choices else None

    return visit(0, spec["drift"], spec["crashes"], spec["unsafe"], spec["late"], spec["cost"])
