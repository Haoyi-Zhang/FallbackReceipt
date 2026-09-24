#!/usr/bin/env python3
"""Check per-contract retained-identity counts and a local deletion counterexample.

The deletion experiment operates only on disposable, owned SQLite files. It is a
negative control, not a supported garbage-collection operation. Counts concern
one completed contract; they do not bound storage bytes or an ongoing service.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from runtime import ReceiverStore
from trace_check import check_runtime


def check_counts(jobs: int, crash_bound: int, attempts: int, effects: int,
                 canceled: int, recovery_events: int) -> bool:
    values = (jobs, crash_bound, attempts, effects, canceled, recovery_events)
    return (all(type(v) is int and v >= 0 for v in values)
            and effects <= jobs
            and canceled <= recovery_events <= crash_bound
            and attempts == effects + canceled
            and attempts <= jobs + crash_bound)


def summarize_history(spec: dict, cert: dict, record: dict) -> dict[str, int]:
    if not check_runtime(spec, cert, record['snapshot'], record['receiver_outcomes'], record['effects']):
        raise ValueError('count bound requires a complete replay-valid history')
    snap = record['snapshot']
    result = {
        'jobs': len(spec['jobs']), 'crash_bound': spec['crashes'],
        'attempts': len(snap['attempts']), 'effects': len(record['effects']),
        'canceled': sum(a['status'] == 'canceled' for a in snap['attempts'].values()),
        'recovery_events': sum(e['kind'] == 'recover' for e in snap['events']),
    }
    if not check_counts(**result):
        raise AssertionError('retained identity bound violated: ' + str(result))
    return result


def deletion_control(adapter: str, root: Path) -> dict[str, Any]:
    """Demonstrate that erasing a tombstone permits an already-canceled request."""
    path = root / (adapter + '.db')
    key = 'owned-delayed-attempt'
    receiver = ReceiverStore(path)
    try:
        first = receiver.close_attempt(key, '0' * 32)
        if first['status'] != 'canceled': raise AssertionError('fresh id not canceled')
    finally:
        receiver.close()
    # A restart must not erase the fence.
    receiver = ReceiverStore(path)
    try:
        delayed = receiver.execute_once(key, adapter, 'owned-job', 1, 0)
        before = len(receiver.effects())
        if delayed['status'] != 'canceled' or before != 0:
            raise AssertionError('retained tombstone did not fence a delayed request')
        # Deliberately violate the retention assumption in an owned temporary DB.
        # No production API performs this deletion; keep the audit rows unchanged.
        receiver.db.execute('DELETE FROM outcomes WHERE id=?', (key,))
        replayed = receiver.execute_once(key, adapter, 'owned-job', 1, 0)
        after = len(receiver.effects())
        if replayed['status'] != 'done' or after != 1:
            raise AssertionError('deletion control did not expose the expected effect')
        return {'adapter': adapter, 'after_restart_with_tombstone': delayed['status'],
                'effects_with_tombstone': before, 'after_illegal_deletion': replayed['status'],
                'effects_after_deletion': after, 'expected_retention_violation_observed': True}
    finally:
        receiver.close()


def evaluate(results: Path = ROOT / 'results') -> dict:
    directories = [results/'pilot'] + sorted((results/'faults/cases').iterdir())
    if len(directories) != 31: raise ValueError('expected pilot plus thirty frozen fault cases')
    rows = []
    for d in directories:
        row = summarize_history(json.loads((d/'spec.json').read_text()),
                                json.loads((d/'certificate.json').read_text()),
                                json.loads((d/'runtime.json').read_text()))
        rows.append({'case': str(d.relative_to(results)), **row})
    # These are scalar cross-checks against the independently reconstructed
    # refinement experiment; not 171 additional exported event histories.
    spec = json.loads((results/'pilot/spec.json').read_text())
    with (results/'refinement/executions.csv').open(newline='') as f:
        executions = list(csv.DictReader(f))
    for r in executions:
        counts = dict(jobs=len(spec['jobs']), crash_bound=spec['crashes'],
                      attempts=int(r['attempts']), effects=int(r['effects']),
                      canceled=int(r['canceled_attempts']), recovery_events=int(r['declared_crashes']))
        if r['valid'] != 'True' or not check_counts(**counts):
            raise AssertionError('invalid refinement count: ' + str(r))
    with tempfile.TemporaryDirectory(prefix='receipt-retention-') as td:
        controls = [deletion_control(a, Path(td)) for a in ('admission','cache','tier')]
    return {
        'scope': 'One completed finite contract; no byte-size or global service storage bound.',
        'complete_histories_replayed': len(rows), 'refinement_count_rows_checked': len(executions),
        'histories': rows, 'max_attempts_observed': max(r['attempts'] for r in rows),
        'bound_attained_histories': sum(r['attempts'] == r['jobs'] + r['crash_bound'] for r in rows),
        'tombstone_controls': controls,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    value = evaluate()
    frozen = ROOT/'results/retention/summary.json'
    if args.verify and json.loads(frozen.read_text()) != value:
        raise SystemExit('retention results differ from recomputation')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in value.items() if k != 'histories'}, sort_keys=True))


if __name__ == '__main__': main()
