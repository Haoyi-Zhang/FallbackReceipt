"""Exact fixed-sequence compilation by compositional envelope dominance.

This solves the open-loop problem, not the adaptive Bellman game. It imports
neither transition code nor the Cartesian fixed-sequence comparator.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    charge: int
    unsafe: tuple[int, ...]
    late: tuple[int, ...]
    actions: tuple[str, ...]


def _extend(label: Label, job: dict, action: str, rho: int, deadline: int) -> Label:
    unsafe = []
    late = []
    for d in range(len(label.unsafe)):
        if action != 'accept':
            unsafe.append(label.unsafe[d])
        else:
            bit = int(job['score'] == 0)
            best = label.unsafe[d] + bit
            if d:
                best = max(best, label.unsafe[d - 1] + (1 - bit))
            unsafe.append(best)
    for f in range(len(label.late)):
        if action == 'reject':
            late.append(label.late[f] + 1)
        else:
            late.append(max(label.late[f - r] + int(job[action + '_time'] + r * rho > deadline)
                            for r in range(f + 1)))
    return Label(label.charge + (0 if action == 'reject' else job[action + '_cost']),
                 tuple(unsafe), tuple(late), label.actions + (action,))


def _dominates(a: Label, b: Label) -> bool:
    return (a.charge <= b.charge and all(x <= y for x, y in zip(a.unsafe, b.unsafe))
            and all(x <= y for x, y in zip(a.late, b.late)))


def solve_frontier(spec: dict, *, limit: int = 100000) -> dict:
    names = {'jobs', 'drift', 'crashes', 'unsafe', 'late', 'cost', 'recovery_time', 'deadline'}
    if not isinstance(spec, dict) or set(spec) != names:
        raise ValueError('invalid finite contract')
    if any(type(spec[k]) is not int or spec[k] < 0 for k in names - {'jobs'}):
        raise ValueError('nonnegative integer budgets required')
    if spec['deadline'] == 0 or not isinstance(spec['jobs'], list) or not spec['jobs']:
        raise ValueError('positive deadline and nonempty job list required')
    if type(limit) is not int or limit < 1:
        raise ValueError('positive frontier limit required')
    fields = {'score', 'accept_cost', 'fallback_cost', 'accept_time', 'fallback_time'}
    for job in spec['jobs']:
        if not isinstance(job, dict) or set(job) != fields or type(job['score']) is not int or job['score'] not in (0, 1, 2):
            raise ValueError('invalid job')
        if any(type(job[k]) is not int or job[k] < 1 for k in fields - {'score'}):
            raise ValueError('positive integer envelopes required')
    if spec['drift'] > 1024 or spec['crashes'] > 1024:
        raise RuntimeError('budget-vector resource limit, not infeasibility')
    frontier = [Label(0, (0,) * (spec['drift'] + 1), (0,) * (spec['crashes'] + 1), ())]
    expanded = dominated = 0
    maximum = 1
    widths = []
    for job in spec['jobs']:
        labels = []
        for parent in frontier:
            for action in ('accept', 'fallback', 'reject'):
                label = _extend(parent, job, action, spec['recovery_time'], spec['deadline'])
                expanded += 1
                if label.charge <= spec['cost'] and label.unsafe[-1] <= spec['unsafe'] and label.late[-1] <= spec['late']:
                    labels.append(label)
        # Sorting by charge makes any possible dominator precede a strict
        # higher-charge label. Equal-charge envelope ties keep a fixed witness.
        labels.sort(key=lambda v: (v.charge, v.unsafe, v.late, v.actions))
        frontier = []
        for label in labels:
            if any(_dominates(other, label) for other in frontier):
                dominated += 1
                continue
            frontier.append(label)
            if len(frontier) > limit:
                raise RuntimeError('frontier resource limit, not infeasibility')
        widths.append(len(frontier))
        maximum = max(maximum, len(frontier))
        if not frontier:
            break
    best = min(frontier, key=lambda v: (v.charge, v.actions)) if frontier else None
    return {'feasible': best is not None, 'worst_cost': None if best is None else best.charge,
            'actions': None if best is None else list(best.actions),
            'expanded_labels': expanded, 'dominated_labels': dominated,
            'maximum_frontier': maximum, 'frontier_widths': widths}


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('specification', type=Path)
    args = parser.parse_args()
    print(json.dumps(solve_frontier(json.loads(args.specification.read_text(encoding='utf-8'))), indent=2))
