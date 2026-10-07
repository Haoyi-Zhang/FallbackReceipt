"""Small fixed-policy reference: literal legal worlds, no production helpers.

This deliberately enumerates score flips and recovery allocations. Coordinate
maxima certify separate universal bounds; they need not occur in the same world.
It is bounded reference code, not a replacement for either compiler.
"""
from itertools import product

ACTIONS = ('accept', 'fallback', 'reject')
MAX_WORLDS = 20_000


def validate(spec):
    fields = {'jobs', 'drift', 'crashes', 'unsafe', 'late', 'cost', 'recovery_time', 'deadline'}
    if type(spec) is not dict or set(spec) != fields:
        raise ValueError('literal specification fields')
    if any(type(spec[k]) is not int or spec[k] < 0 for k in fields - {'jobs'}):
        raise ValueError('literal nonnegative integer quantities required')
    if type(spec['jobs']) is not list or not spec['jobs'] or spec['deadline'] == 0:
        raise ValueError('literal nonempty jobs and positive deadline required')
    job_fields = {'score', 'accept_cost', 'fallback_cost', 'accept_time', 'fallback_time'}
    for job in spec['jobs']:
        if type(job) is not dict or set(job) != job_fields:
            raise ValueError('literal job fields')
        if type(job['score']) is not int or job['score'] not in (0, 1, 2):
            raise ValueError('literal score class')
        if any(type(job[k]) is not int or job[k] <= 0 for k in job_fields - {'score'}):
            raise ValueError('literal positive integer envelopes required')
    if len(spec['jobs']) > 4 or spec['drift'] > 2 or spec['crashes'] > 2:
        raise RuntimeError('literal domain refusal: incomplete, not infeasible')


def bounds(spec, actions, counter=None):
    validate(spec)
    actions = tuple(actions)
    if len(actions) != len(spec['jobs']) or any(a not in ACTIONS for a in actions):
        raise ValueError('one literal legal action per job required')
    if counter is None:
        counter = [0]
    charge = sum(j[a + '_cost'] for j, a in zip(spec['jobs'], actions) if a != 'reject')
    worst_unsafe = worst_late = 0
    flip_choices = [(0, 1) if a == 'accept' else (0,) for a in actions]
    crash_choices = [range(spec['crashes'] + 1) if a != 'reject' else (0,) for a in actions]
    flips = [f for f in product(*flip_choices) if sum(f) <= spec['drift']]
    crashes = [f for f in product(*crash_choices) if sum(f) <= spec['crashes']]
    for flip in flips:
        for recovery in crashes:
            counter[0] += 1
            if counter[0] > MAX_WORLDS:
                raise RuntimeError('literal world limit: incomplete, not infeasible')
            unsafe = late = 0
            for j, a, changed, retried in zip(spec['jobs'], actions, flip, recovery):
                if a == 'reject':
                    late += 1
                else:
                    if a == 'accept':
                        nominal_bad = 1 if j['score'] == 0 else 0
                        unsafe += 1 - nominal_bad if changed else nominal_bad
                    late += int(j[a + '_time'] + retried * spec['recovery_time'] > spec['deadline'])
            worst_unsafe = max(worst_unsafe, unsafe)
            worst_late = max(worst_late, late)
    return [charge, worst_unsafe, worst_late]


def solve(spec):
    validate(spec)
    best = None
    feasible = sequences = 0
    counter = [0]
    for actions in product(ACTIONS, repeat=len(spec['jobs'])):
        sequences += 1
        charge, unsafe, late = bounds(spec, actions, counter)
        if charge <= spec['cost'] and unsafe <= spec['unsafe'] and late <= spec['late']:
            feasible += 1
            if best is None or charge < best[0]:
                best = (charge, list(actions), [charge, unsafe, late])
    return {'feasible': best is not None, 'worst_cost': None if best is None else best[0],
            'actions': None if best is None else best[1], 'feasible_sequences': feasible,
            'sequences_checked': sequences, 'worlds_checked': counter[0],
            'witness_bounds': None if best is None else best[2]}
