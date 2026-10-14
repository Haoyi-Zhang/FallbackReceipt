"""Seven alternating common-contract process-cost pairs per adapter/mode.

Separate from the 168-case correctness activity. No crashes are injected here;
cost includes three process starts and the same observer/checkpoint obligations.
This avoids using that activity's fixed receiver order as a speed comparison.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from campaign import A, SOURCES, normalize, one, require, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    require(not args.out.exists(),'refusing existing paired-cost path')
    args.out.mkdir(parents=True)
    paths = SOURCES + ['owned_process/cost_pairs.py']
    versions = {name:hashlib.sha256((A/name).read_bytes()).hexdigest() for name in paths}
    write(args.out/'source-versions.json',versions)
    rows = []
    started = time.perf_counter()
    for optimized in (False,True):
        for adapter in ('admission','cache','tier'):
            for repeat in range(7):
                order = ('receipt','ordinary') if repeat%2==0 else ('ordinary','receipt')
                pair = []
                for position, receiver in enumerate(order):
                    name = f'{adapter}-O{int(optimized)}-pair{repeat}-{receiver}'
                    row = one(args.out/'cases'/name,adapter,2,'none',receiver,optimized)
                    row.update(repeat=repeat,position=position)
                    rows.append(row)
                    pair.append(row)
                    write(args.out/'rows.json',rows)
                require(pair[0]['terminal_semantics']==pair[1]['terminal_semantics'],
                        'paired-cost implementations differ in semantics')
            print(f'alternating pairs complete {adapter} O{int(optimized)}',flush=True)
    require(len(rows)==84,'paired cost cardinality differs')
    require(versions=={n:hashlib.sha256((A/n).read_bytes()).hexdigest() for n in paths},
            'source changed during paired activity')
    write(args.out/'summary.json',dict(cases=84,paired_cases=42,launches=sum(r['launches'] for r in rows),
                                      returned_receipts=sum(r['returned_receipts'] for r in rows),
                                      effects=sum(r['effects'] for r in rows),
                                      same_information_disagreements=0,source_versions=versions,
                                      activity_wall_seconds=time.perf_counter()-started,
                                      scope='Alternating order, seven pairs per adapter/mode; declared small cost units; shared host and explicit observation/checkpoint overhead. Not production steady-state latency or primitive novelty.'))
    print(json.dumps(json.loads((args.out/'summary.json').read_text()),indent=2),flush=True)


if __name__=='__main__':
    main()
