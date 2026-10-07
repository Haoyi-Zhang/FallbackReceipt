"""Unfiltered retained-cohort comparison, 20 predetermined sixty-ID shards.

Only finite mathematical modules are imported by workers. No runtime, database,
native, provider or external-target workload is run. A failed/limited shard is
incomplete and the full 1,200-case denominator is retained. No timing metric.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
INPUT_SHA256 = '8913b13a4b95b9c602b92383a051933178f96778b2e22aee377b069a71d866d4'
SHARDS = 20
SHARD_SIZE = 60
TIMEOUT = 120


def validate_cases(cases):
    if type(cases) is not list or len(cases) != 1200:
        raise ValueError('exact 1200 retained inputs required')
    from literal_fixed import validate
    for expected, row in enumerate(cases):
        if type(row) is not dict or type(row.get('case')) is not int or row['case'] != expected:
            raise ValueError('exact ordered IDs 0..1199 required')
        validate(row['spec'])
        if type(row.get('feasible')) is not bool:
            raise ValueError('retained feasibility must be Boolean')
        value = row.get('oracle_cost')
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('retained oracle cost')
        if row['feasible'] != (value is not None):
            raise ValueError('retained verdict/cost inconsistency')


def load_cases():
    data = (ROOT / 'results/oracle/cases.json').read_bytes()
    if hashlib.sha256(data).hexdigest() != INPUT_SHA256:
        raise ValueError('retained cohort byte binding changed; no substitute cohort')
    cases = json.loads(data)
    validate_cases(cases)
    return cases


def evaluate(row):
    import game
    import checker
    import oracle
    import open_loop
    import literal_fixed
    result = {'case': row['case'], 'status': 'incomplete', 'spec': row['spec']}
    snapshot = json.dumps(row['spec'], sort_keys=True)
    try:
        cert = game.synthesize(row['spec'])
        accepted = checker.check(row['spec'], cert)  # Includes infeasibility certificates.
        exact = oracle.exact(row['spec'])
        fixed = open_loop.solve(row['spec'])
        literal = literal_fixed.solve(row['spec'])
        errors = []
        if not accepted:
            errors.append('fresh certificate rejected')
        if (cert['worst_cost'], cert['feasible']) != (exact, exact is not None):
            errors.append('adaptive/oracle mismatch')
        if (cert['worst_cost'], cert['feasible']) != (row['oracle_cost'], row['feasible']):
            errors.append('fresh/retained adaptive mismatch')
        if {k: literal[k] for k in fixed} != fixed:
            errors.append('fixed/literal mismatch')
        if fixed['feasible'] and (not cert['feasible'] or cert['worst_cost'] > fixed['worst_cost']):
            errors.append('fixed better than claimed adaptive optimum')
        if snapshot != json.dumps(row['spec'], sort_keys=True):
            errors.append('input mutation')
        result.update(status='mismatch' if errors else 'complete', errors=errors,
                      adaptive=cert, checker_accepted=accepted, oracle_cost=exact,
                      retained_oracle_cost=row['oracle_cost'], fixed=fixed, literal=literal)
    except (RuntimeError, MemoryError) as exc:
        result['reason'] = type(exc).__name__ + ': ' + str(exc)
    return result


def summarise(rows):
    if [r['case'] for r in rows] != list(range(1200)):
        raise ValueError('comparison must retain every ID exactly once in order')
    complete = [r for r in rows if r['status'] == 'complete']
    common = [r for r in complete if r['adaptive']['feasible'] and r['fixed']['feasible']]
    return {'schema': 1, 'input_sha256': INPUT_SHA256, 'cases': 1200,
            'complete_cases': len(complete),
            'incomplete_cases': sum(r['status'] == 'incomplete' for r in rows),
            'mismatches': sum(r['status'] == 'mismatch' for r in rows),
            'adaptive_feasible': sum(r['adaptive']['feasible'] for r in complete),
            'fixed_feasible': sum(r['fixed']['feasible'] for r in complete),
            'both_infeasible': sum(not r['adaptive']['feasible'] and not r['fixed']['feasible'] for r in complete),
            'adaptive_only': sum(r['adaptive']['feasible'] and not r['fixed']['feasible'] for r in complete),
            'common_feasible': len(common),
            'equal_cost': sum(r['adaptive']['worst_cost'] == r['fixed']['worst_cost'] for r in common),
            'adaptive_lower_cost': sum(r['adaptive']['worst_cost'] < r['fixed']['worst_cost'] for r in common),
            'fixed_sequences': sum(r['literal']['sequences_checked'] for r in complete),
            'literal_worlds': sum(r['literal']['worlds_checked'] for r in complete),
            'checker_acceptances': sum(r['checker_accepted'] for r in complete),
            'scope': 'Retained bounded mathematical contracts; not runtime, deployment, or universal equivalence evidence.'}


def main():
    sys.path.insert(0, str(ROOT / 'src'))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker', type=int)
    p.add_argument('--output', type=Path)
    p.add_argument('--verify', action='store_true')
    args = p.parse_args()
    cases = load_cases()
    if args.worker is not None:
        if not 0 <= args.worker < SHARDS or args.output is not None or args.verify:
            p.error('worker must be a shard ID 0..19 only')
        begin = args.worker * SHARD_SIZE
        print(json.dumps([evaluate(r) for r in cases[begin:begin + SHARD_SIZE]], sort_keys=True))
        return 0
    if (args.output is None) == (not args.verify):
        p.error('choose exactly one of --output NEW_DIRECTORY or --verify')
    if args.output is not None:
        if args.output.exists():
            p.error('output already exists; no replacement or deletion')
        args.output.mkdir(parents=True)
    rows, receipts = [], []
    for shard in range(SHARDS):
        start = shard * SHARD_SIZE
        try:
            child = subprocess.run([sys.executable, '-B', '-S', str(Path(__file__).resolve()),
                                    '--worker', str(shard)], capture_output=True, text=True,
                                   timeout=TIMEOUT, check=False)
            receipts.append({'shard': shard, 'first_id': start, 'last_id': start + 59,
                             'timeout_seconds': TIMEOUT, 'exit': child.returncode,
                             'stderr': child.stderr})
            if child.returncode != 0:
                raise RuntimeError('worker exit ' + str(child.returncode))
            part = json.loads(child.stdout)
            if [r['case'] for r in part] != list(range(start, start + 60)):
                raise ValueError('worker ID coverage mismatch')
            rows.extend(part)
        except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                receipts.append({'shard': shard, 'first_id': start, 'last_id': start + 59,
                                 'timeout_seconds': TIMEOUT, 'exit': None, 'reason': 'timeout'})
            rows.extend({'case': i, 'status': 'incomplete', 'reason': str(exc)}
                        for i in range(start, start + 60))
    summary = summarise(rows)
    bindings = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ('src/game.py', 'src/checker.py', 'src/oracle.py', 'src/open_loop.py',
                             'src/literal_fixed.py', 'compare_oracle_fixed.py')}
    receipt = {'input_sha256': INPUT_SHA256, 'source_sha256': bindings, 'shards': receipts,
               'process_bound_seconds': TIMEOUT, 'timing_measured': False}
    if args.output is not None:
        for name, data in (('rows.json', rows), ('summary.json', summary), ('receipt.json', receipt)):
            with (args.output / name).open('x', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write('\n')
    else:
        expected = ROOT / 'results/oracle-fixed'
        if rows != json.loads((expected / 'rows.json').read_text(encoding='utf-8')):
            raise ValueError('fresh/retained complete comparison rows differ')
        if summary != json.loads((expected / 'summary.json').read_text(encoding='utf-8')):
            raise ValueError('fresh/retained comparison summary differs')
    print(json.dumps(summary, sort_keys=True))
    return int(summary['complete_cases'] != 1200 or summary['mismatches'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
