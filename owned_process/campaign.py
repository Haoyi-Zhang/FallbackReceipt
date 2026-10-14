"""Fixed owned process/recovery cohort; no original results are overwritten.

The common-information comparison changes ONLY the receiver implementation;
both use the same checked controller, actions, budgets, request/receipt fields,
SQLite modes, terminal audit and retained effect-shape tables. No production,
RIFL/Beldi runtime, global footprint, power-loss or novelty claim follows.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
A = HERE.parent
sys.path.insert(0,str(A/'src'))
from checker import check
from evidence_check import verify_pair
from game import synthesize

SCHEDULES = {'none':[], 'pre_effect':['after_reserve'], 'post_effect':['after_execute'],
             'close_interrupted':['after_reserve','after_close'],
             'recover_interrupted':['after_reserve','after_recover'],
             'two_pre_effect':['after_reserve','after_reserve'],
             'receiver_uncommitted':['receiver_uncommitted']}
SOURCES = ['src/game.py','src/checker.py','src/runtime.py','src/trace_check.py',
           'src/evidence_check.py','owned_process/ordinary.py','owned_process/worker.py',
           'owned_process/campaign.py']


def require(value,message):
    if not value:
        raise RuntimeError(message)


def write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')


def spec(charge):
    return dict(jobs=[dict(score=0,accept_cost=charge,fallback_cost=charge,
                           accept_time=1,fallback_time=1) for _ in range(3)],
                drift=0,crashes=2,unsafe=0,late=0,cost=3*charge,recovery_time=0,deadline=1)


def disk(root):
    return {p.name:p.stat().st_size for p in root.iterdir()
            if p.is_file() and (p.name.endswith('.db') or p.name.endswith(('-wal','-shm')))}


def state_after_crash(root):
    result = {}
    # Read-only, live-WAL snapshots are per-file, not an atomic cross-file read.
    for role in ('controller','receiver'):
        path = root/f'{role}.db'
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('BEGIN')
            if role=='controller':
                result['state'] = json.loads(db.execute("SELECT value FROM meta WHERE key='state'").fetchone()[0])
                result['attempts'] = [list(row) for row in db.execute(
                    'SELECT id,state,recoveries FROM attempts ORDER BY id')]
            else:
                result['outcomes'] = [list(row) for row in db.execute(
                    'SELECT id,state,cost,unsafe FROM outcomes ORDER BY id')]
                result['effects'] = [list(row) for row in db.execute('SELECT id,cost,unsafe FROM effects ORDER BY id')]
            db.execute('COMMIT')
    return result


def normalize(value):
    """Compare semantics after renaming each fresh UUID ticket by first use.

    Only ticket strings are renamed; states, IDs, effects and other fields are
    preserved. Raw records retain actual tickets for latest-ticket replay.
    """
    names = {}
    def walk(v,key=''):
        if key=='ticket' and isinstance(v,str):
            return names.setdefault(v,f'ticket-{len(names)}')
        if isinstance(v,dict):
            return {k:walk(x,k) for k,x in v.items()}
        if isinstance(v,list):
            return [walk(x) for x in v]
        return v
    return walk(value)


def one(root,adapter,charge,schedule,receiver,optimized):
    root.mkdir(parents=True,exist_ok=False)
    inputs = spec(charge)
    certificate = synthesize(inputs)
    require(certificate['feasible'] and check(inputs,certificate),'native input certificate rejected')
    require(all(row['action']=='fallback' for row in certificate['policy'].values()),
            'owned cohort must test actual fallback')
    write(root/'spec.json',inputs)
    write(root/'certificate.json',certificate)
    launches, crash_snapshots = [], []
    last = None
    case_started = time.perf_counter()
    cuts = SCHEDULES[schedule]
    for number in range(len(cuts)+4):
        cut = cuts[number] if number<len(cuts) else 'none'
        command = [sys.executable,'-B','-S','-W','error::ResourceWarning']
        if optimized:
            command.append('-O')
        command += [str(HERE/'worker.py'),'--root',str(root),'--adapter',adapter,
                    '--receiver',receiver,'--cut',cut]
        started = time.perf_counter()
        process = subprocess.run(command,capture_output=True,text=True,timeout=15)
        parent_seconds = time.perf_counter()-started
        (root/f'launch-{number:02d}.stdout.jsonl').write_text(process.stdout,encoding='utf-8')
        (root/f'launch-{number:02d}.stderr.txt').write_text(process.stderr,encoding='utf-8')
        expected = 73 if cut!='none' else 0
        require(process.returncode==expected,f'owned process failed: {command}: {process.returncode}: {process.stderr}')
        require(not process.stderr,'unexpected worker stderr/resource warning')
        output = [json.loads(line) for line in process.stdout.splitlines()]
        require(output,'worker emitted no receipt')
        if cut!='none':
            require(output[-1].get('cut')==cut and output[-1].get('exit')==73,'cut not reached')
            observed = state_after_crash(root)
            if cut=='receiver_uncommitted':
                require(not observed['outcomes'] and not observed['effects'],
                        'uncommitted receiver effect escaped its transaction')
            crash_snapshots.append(dict(cut=cut,observed=observed,disk_sample=disk(root)))
        else:
            last = output[-1]
            require(last['optimize']==int(optimized),'Python mode does not match request')
        launches.append(dict(command=command,cut=cut,exit_code=process.returncode,
                             parent_wall_seconds=parent_seconds,output=output))
        if cut=='none' and last['complete']:
            break
    require(last is not None and last['complete'],'bounded worker plan did not complete')
    case_seconds = time.perf_counter()-case_started
    record = dict(adapter=adapter,cuts=cuts,launches=launches,snapshot=last['snapshot'],
                  receiver_outcomes=last['receiver_outcomes'],effects=last['effects'],valid=True,
                  receiver_implementation=receiver,optimized=optimized,charge=charge,
                  crash_snapshots=crash_snapshots)
    write(root/'runtime.json',record)
    verify_started = time.perf_counter()
    verify_pair(root,adapter,cuts)
    verification_seconds = time.perf_counter()-verify_started
    require(len(record['effects'])==3,'owned logical jobs did not have three effects')
    require(all(out['unsafe']==0 for out in record['receiver_outcomes'].values()),'unsafe fallback')
    clean = [item['output'][-1] for item in launches if item['exit_code']==0]
    returned = [item for launch in launches for item in launch['output']
                if item.get('kind')=='receiver_return']
    files = [p for p in root.iterdir() if p.is_file()]
    return dict(adapter=adapter,charge=charge,schedule=schedule,receiver=receiver,optimized=optimized,
                case=str(root.name),launches=len(launches),exit_counts=dict(Counter(x['exit_code'] for x in launches)),
                case_process_phase_seconds=case_seconds,verification_seconds=verification_seconds,
                parent_launch_wall_seconds=sum(x['parent_wall_seconds'] for x in launches),
                successful_worker_cpu_seconds=sum(x['worker_cpu_seconds'] for x in clean),
                successful_worker_wall_seconds=sum(x['worker_wall_seconds'] for x in clean),
                checkpoint_seconds=sum(x['checkpoint_seconds'] for x in clean),
                returned_receipts=len(returned),effects=len(record['effects']),
                attempts=len(record['snapshot']['attempts']),
                terminal_semantics=normalize({k:record[k] for k in
                    ('snapshot','receiver_outcomes','effects')}),
                db_file_bytes=sum(p.stat().st_size for p in files if p.name.endswith('.db')),
                complete_case_file_bytes=sum(p.stat().st_size for p in files),
                file_count=len(files),last_disk_sample=disk(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--small',action='store_true')
    args = parser.parse_args()
    require(not args.out.exists(),'refusing existing activity path')
    args.out.mkdir(parents=True)
    versions = {name:hashlib.sha256((A/name).read_bytes()).hexdigest() for name in SOURCES}
    write(args.out/'source-versions.json',versions)
    activities = []
    started = time.perf_counter()
    modes = [False] if args.small else [False,True]
    adapters = ['admission'] if args.small else ['admission','cache','tier']
    charges = [2] if args.small else [2,(1<<63)-1]
    schedules = ['none','receiver_uncommitted'] if args.small else list(SCHEDULES)
    for optimized in modes:
        for adapter in adapters:
            for charge in charges:
                for schedule in schedules:
                    pair = []
                    for receiver in ('receipt','ordinary'):
                        name = f'{adapter}-{charge}-{schedule}-{receiver}-O{int(optimized)}'
                        row = one(args.out/'cases'/name,adapter,charge,schedule,receiver,optimized)
                        activities.append(row)
                        pair.append(row)
                        write(args.out/'rows.json',activities)
                    require(pair[0]['terminal_semantics']==pair[1]['terminal_semantics'],
                            'same-information receiver implementations disagree')
                    print(f'owned process cases complete {len(activities)}',flush=True)
    expected_cases = 4 if args.small else 168
    require(len(activities)==expected_cases,'cohort cardinality differs')
    current = {name:hashlib.sha256((A/name).read_bytes()).hexdigest() for name in SOURCES}
    require(current==versions,'execution source changed during activity')
    summary = dict(scope='Owned local Python processes, not RIFL/Beldi executions, production or power-loss tests',
                   small=args.small,cases=len(activities),paired_cases=len(activities)//2,
                   launches=sum(r['launches'] for r in activities),
                   effects=sum(r['effects'] for r in activities),
                   attempts=sum(r['attempts'] for r in activities),
                   returned_receipts=sum(r['returned_receipts'] for r in activities),
                   same_information_disagreements=0,
                   activity_wall_seconds=time.perf_counter()-started,
                   source_versions=versions,
                   environment=dict(python=sys.version,executable=sys.executable,platform=platform.platform(),
                                    sqlite=sqlite3.sqlite_version,
                                    clocks={k:vars(time.get_clock_info(k)) for k in ('perf_counter','process_time','time')}),
                   cost_scope='Parent launch wall includes startup/exit; successful worker CPU excludes killed-worker CPU and final output encoding. Case process phase excludes input synthesis and final verification; checkpoint costs are separately recorded and included in successful worker time. Disk samples are logical file lengths, not physical allocation, continuous peaks or device write traffic.')
    write(args.out/'summary.json',summary)
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
