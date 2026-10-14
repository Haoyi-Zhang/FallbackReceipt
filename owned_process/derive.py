"""Re-derive the fixed native cohorts from raw process streams and closed DBs.

No process workload is executed and no input is changed. Explicit exceptions
remain enabled under -O. Generated tables use this activity only, never the
historical timing cohort. File bytes mean logical lengths at sampled endpoints.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import sys

A = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(A/'src'))
from checker import check
from evidence_check import verify_pair

CUTS = {'none': [], 'pre_effect': ['after_reserve'], 'post_effect': ['after_execute'],
        'close_interrupted': ['after_reserve','after_close'],
        'recover_interrupted': ['after_reserve','after_recover'],
        'two_pre_effect': ['after_reserve','after_reserve'],
        'receiver_uncommitted': ['receiver_uncommitted']}


def need(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def normalize(value):
    tickets = {}
    def walk(v, key=''):
        if key == 'ticket' and isinstance(v,str):
            return tickets.setdefault(v, len(tickets))
        if isinstance(v,dict):
            return {k:walk(x,k) for k,x in v.items()}
        if isinstance(v,list):
            return [walk(x) for x in v]
        return v
    return walk(value)


def cohort(root, kind):
    versions = read(root/'source-versions.json')
    need(versions == read(root/'summary.json')['source_versions'], 'source receipt mismatch')
    for name, digest in versions.items():
        need(hashlib.sha256((A/name).read_bytes()).hexdigest()==digest, 'source changed: '+name)
    rows = read(root/'rows.json')
    keys = {(r['adapter'],r['optimized'],r.get('repeat',r['charge']),
             r['schedule'],r['receiver']) for r in rows}
    modes = [False] if kind=='small' else [False,True]
    adapters = ['admission'] if kind=='small' else ['admission','cache','tier']
    third = range(7) if kind=='cost' else ([2] if kind=='small' else [2,2**63-1])
    schedules = ['none'] if kind=='cost' else (['none','receiver_uncommitted'] if kind=='small' else CUTS)
    expected = set(itertools.product(adapters,modes,third,schedules,['receipt','ordinary']))
    need(keys==expected and len(rows)==len(keys), 'cohort omission/duplication')
    case_names = {r['case'] for r in rows}
    need(case_names=={p.name for p in (root/'cases').iterdir()}, 'extra/missing case directory')
    pairs = defaultdict(dict)
    totals = Counter()
    measured = []
    for row in rows:
        case = root/'cases'/row['case']
        spec, cert, raw = [read(case/n) for n in ('spec.json','certificate.json','runtime.json')]
        need(check(spec,cert) and cert['feasible'], 'certificate fails')
        need(spec['crashes']==2 and len(spec['jobs'])==3 and spec['cost']==3*row['charge'], 'input scope differs')
        need(all(j['score']==0 and j['fallback_cost']==row['charge'] for j in spec['jobs']), 'fallback inputs differ')
        need(all(p['action']=='fallback' for p in cert['policy'].values()), 'not actual fallback')
        need(raw['cuts']==CUTS[row['schedule']], 'cut schedule differs')
        verify_pair(case,row['adapter'],raw['cuts'])
        returns = 0
        samples = []
        clean = []
        launch_seconds = 0.0
        launches = raw['launches']
        need(len(launches)==row['launches'], 'launch count mismatch')
        for i, launch in enumerate(launches):
            stream = (case/f'launch-{i:02d}.stdout.jsonl').read_text(encoding='utf-8')
            parsed = [json.loads(line) for line in stream.splitlines()]
            need(parsed==launch['output'], 'raw stream differs')
            need(not (case/f'launch-{i:02d}.stderr.txt').read_bytes(), 'stderr not empty')
            need(('-O' in launch['command'])==row['optimized'], 'mode differs')
            need(launch['exit_code']==(73 if launch['cut']!='none' else 0), 'exit differs')
            launch_seconds += launch['parent_wall_seconds']
            totals['exit_'+str(launch['exit_code'])] += 1
            for out in parsed:
                if out.get('kind')=='receiver_return':
                    r, obs = out['receipt'], out['committed']
                    need(obs['id']==r['id'] and obs['status']==r['status'], 'observation ID/status differs')
                    need(obs['row']==[r[k] for k in ('status','adapter','job','cost','unsafe')], 'commit row differs')
                    need(obs['effects']==int(r['status']=='done'), 'commit effect differs')
                    returns += 1
                if 'disk_sample' in out:
                    samples.append(out['disk_sample'])
            if launch['exit_code']==0:
                out = parsed[-1]
                need(out['optimize']==int(row['optimized']) and out['sqlite_version']=='3.53.1', 'worker environment differs')
                need(out['checkpoints']==[{'role':'controller','result':[0,0,0]},
                                          {'role':'receiver','result':[0,0,0]}], 'checkpoint incomplete')
                need(out['receipts']==[x['receipt'] for x in parsed if x.get('kind')=='receiver_return'], 'receipt omission')
                need(out['commit_observations']==[x['committed'] for x in parsed if x.get('kind')=='receiver_return'], 'observation omission')
                samples += [out[k] for k in ('before_checkpoint','after_checkpoint','after_close')]
                clean.append(out)
        need(clean and clean[-1]['complete'], 'no complete final process')
        for crash in raw['crash_snapshots']:
            samples.append(crash['disk_sample'])
            if crash['cut']=='receiver_uncommitted':
                need(not crash['observed']['outcomes'] and not crash['observed']['effects'], 'uncommitted effect retained')
        attempts, effects = len(raw['snapshot']['attempts']), len(raw['effects'])
        need(3<=attempts<=5 and effects==3 and returns==row['returned_receipts'], 'contract counts differ')
        need(attempts==row['attempts'] and effects==row['effects'], 'row counts differ')
        semantics = normalize({k:raw[k] for k in ('snapshot','receiver_outcomes','effects')})
        key = (row['adapter'],row['optimized'],row.get('repeat',row['charge']),row['schedule'])
        pairs[key][row['receiver']] = semantics
        files = [p for p in case.iterdir() if p.is_file()]
        need(len(files)==5+2*len(launches), 'raw input/output file inventory differs')
        dbbytes = sum(p.stat().st_size for p in files if p.suffix=='.db')
        casebytes = sum(p.stat().st_size for p in files)
        need(dbbytes==row['db_file_bytes'] and casebytes==row['complete_case_file_bytes'], 'retained file lengths changed')
        totals.update(cases=1,launches=len(launches),effects=effects,attempts=attempts,
                      returned_receipts=returns,raw_inputs=2,raw_runtime_json=1,
                      databases=2,stdout_files=len(launches),stderr_files=len(launches),
                      case_files=len(files),case_file_bytes=casebytes,db_file_bytes=dbbytes)
        totals['tight_n_plus_F_cases'] += int(attempts==5)
        measured.append(dict(adapter=row['adapter'],optimized=row['optimized'],receiver=row['receiver'],
                             repeat=row.get('repeat'),parent_ms=1000*launch_seconds,
                             successful_cpu_ms=1000*sum(x['worker_cpu_seconds'] for x in clean),
                             checkpoint_ms=1000*sum(x['checkpoint_seconds'] for x in clean),
                             max_sample_db_wal_shm_bytes=max(sum(x.values()) for x in samples),
                             db_file_bytes=dbbytes,case_file_bytes=casebytes))
    need(all(p['receipt']==p['ordinary'] for p in pairs.values()), 'same-information disagreement')
    need(totals['cases']==read(root/'summary.json')['cases'], 'summary cardinality differs')
    allfiles = [p for p in root.rglob('*') if p.is_file()]
    totals['all_cohort_files'] = len(allfiles)
    totals['all_cohort_file_bytes'] = sum(p.stat().st_size for p in allfiles)
    return dict(kind=kind,totals=dict(totals),pairs=len(pairs),disagreements=0,
                measurement_rows=measured,source_versions=versions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    need(not args.out.exists(), 'refusing existing derivation')
    records = {k:cohort(A/'owned-process-results'/n,k) for k,n in [
        ('small','small-20261014-001'),('full','full-20261014-001'),('cost','cost-pairs-20261014-001')]}
    strata = []
    table = []
    for optimized, adapter in itertools.product([False,True],['admission','cache','tier']):
        rows = [r for r in records['cost']['measurement_rows'] if r['optimized']==optimized and r['adapter']==adapter]
        data = {}
        for receiver in ['receipt','ordinary']:
            selected = [r for r in rows if r['receiver']==receiver]
            data[receiver] = {metric:statistics.median(r[metric] for r in selected)
                              for metric in ('parent_ms','successful_cpu_ms','checkpoint_ms','db_file_bytes','case_file_bytes')}
        by_pair = defaultdict(dict)
        for r in rows:
            by_pair[r['repeat']][r['receiver']] = r['parent_ms']
        ratio = statistics.median(p['receipt']/p['ordinary'] for p in by_pair.values())
        strata.append(dict(adapter=adapter,optimized=optimized,medians=data,median_paired_parent_ratio=ratio))
        x,y = data['receipt'],data['ordinary']
        table.append(f"{adapter.capitalize()} & {'-O' if optimized else 'normal'} & {x['parent_ms']:.1f}/{y['parent_ms']:.1f} & {x['checkpoint_ms']:.2f}/{y['checkpoint_ms']:.2f} & {ratio:.2f} " + chr(92)*2)
    records['cost_strata'] = strata
    records['deriver_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.out.mkdir(parents=True)
    (args.out/'derived.json').write_text(json.dumps(records,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    (args.out/'cost_rows.tex').write_text('\n'.join(table)+'\n'+chr(92)+'bottomrule\n',encoding='utf-8')
    print(json.dumps({k:{'totals':v['totals'],'pairs':v['pairs'],'disagreements':v['disagreements']}
                      for k,v in records.items() if k in ('small','full','cost')},indent=2))
    print(json.dumps(strata,indent=2))


if __name__=='__main__':
    main()
