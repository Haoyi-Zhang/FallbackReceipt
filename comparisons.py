#!/usr/bin/env python3
"""Deterministic fixed-sequence, sensitivity, and progress evidence; standard library only.

This runner checks the strongest fixed-sequence
baseline, an independently enumerated envelope cross-check, and a finite productive-step
termination check.  It never represents workload-derived units as service data.
"""
from __future__ import annotations
import argparse
import collections
import copy
import itertools
import json
import random
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from game import synthesize
from open_loop import catalogue, solve, envelope, worst_counts
from workload import calibrate, load_rows, make_spec, windows
from protocol_model import initial, transitions


def trace_comparison():
    calibration = calibrate(load_rows(ROOT/'data/azure_llm_code_excerpt.csv'))
    rows=[]
    for source in ('code','conv'):
        for w,jobs in enumerate(windows(ROOT/f'data/azure_llm_{source}_excerpt.csv',8,calibration)):
            schedules=catalogue(jobs,calibration['deadline'],calibration['recovery_time'])
            for d,f,level in itertools.product(range(3),range(3),('tight','roomy')):
                spec=make_spec(jobs,d,f,level,calibration)
                adaptive=synthesize(spec)
                baseline=solve(spec,schedules)
                assert not baseline['feasible'] or adaptive['feasible']
                if baseline['feasible']:
                    assert adaptive['worst_cost'] <= baseline['worst_cost']
                rows.append(dict(source=source,window=w,drift=d,crashes=f,budget=level,
                                 adaptive_feasible=adaptive['feasible'],adaptive_cost=adaptive['worst_cost'],
                                 open_loop=baseline))
    both=[r for r in rows if r['adaptive_feasible'] and r['open_loop']['feasible']]
    return dict(contracts=len(rows),jobs_per_contract=8,sequences_per_contract=3**8,
                adaptive_feasible=sum(r['adaptive_feasible'] for r in rows),
                open_loop_feasible=sum(r['open_loop']['feasible'] for r in rows),
                adaptive_only=sum(r['adaptive_feasible'] and not r['open_loop']['feasible'] for r in rows),
                common_feasible=len(both),
                adaptive_lower_cost=sum(r['adaptive_cost']<r['open_loop']['worst_cost'] for r in both),
                rows=rows)


def baseline_crosscheck():
    """Check envelopes against direct Cartesian enumeration, no Bellman import."""
    rng=random.Random(20260923)
    instances=sequences=0
    for n in (1,2,3):
        for _ in range(80):
            jobs=[dict(score=rng.randrange(3),accept_cost=rng.randint(1,4),fallback_cost=rng.randint(1,6),
                       accept_time=rng.randint(1,6),fallback_time=rng.randint(1,6)) for _ in range(n)]
            d,f,rho,H=rng.randrange(4),rng.randrange(4),rng.randrange(4),rng.randint(1,6)
            for seq in itertools.product(('accept','fallback','reject'),repeat=n):
                domains=[]
                for j,a in zip(jobs,seq):
                    if a=='reject':domains.append([(0,0,0,1,0)]);continue
                    domains.append([(x,r,int(j['score']==0)^x if a=='accept' else 0,
                                     int(j[a+'_time']+r*rho>H),j[a+'_cost'])
                                    for x in ((0,1) if a=='accept' else (0,)) for r in range(f+1)])
                maxima=[0,0,0]
                for outcome in itertools.product(*domains):
                    if sum(t[0] for t in outcome)>d or sum(t[1] for t in outcome)>f:continue
                    for k,field in enumerate((4,2,3)):
                        maxima[k]=max(maxima[k],sum(t[field] for t in outcome))
                assert tuple(maxima)==worst_counts(envelope(jobs,seq,H,rho),d,f)
                sequences+=1
            instances+=1
    return dict(seed=20260923,instances=instances,fixed_sequences=sequences,mismatches=0,
                horizons=[1,2,3],drift_and_crash_budgets=[0,1,2,3],recovery_times=[0,1,2,3])


def progress(bound):
    """Explore reachable states, then remove future-crash edges and check a DAG.

Terminal states have no obligations. Idle/stuttering steps are not represented;
this proves termination of productive paths, conditional on progress, not a
wall-clock guarantee or a scheduler-fairness proof.
"""
    start=initial(); todo=collections.deque([start]); seen={start}; graph={}
    while todo:
        s=todo.popleft()
        edges=transitions(s,'receipt_closed',bound,bound+2)
        graph[s]=edges
        for e in edges:
            if e.state not in seen:
                seen.add(e.state);todo.append(e.state)
    visiting=set(); lengths={}; dead=[]
    def longest(s):
        if s in visiting:
            raise AssertionError('productive no-further-crash cycle')
        if s in lengths:return lengths[s]
        if s.phase=='done': lengths[s]=0;return 0
        nxt=[e.state for e in graph[s] if e.state.crashes==s.crashes]
        if not nxt:
            dead.append(s.as_dict()); lengths[s]=0; return 0
        visiting.add(s);v=1+max(longest(t) for t in nxt);visiting.remove(s)
        lengths[s]=v;return v
    for s in seen:longest(s)
    assert not dead
    return dict(crash_bound=bound,states=len(seen),edges=sum(len(e) for e in graph.values()),
                nonterminal_dead_ends=len(dead),productive_cycles=0,
                longest_no_further_crash_path=max(lengths.values()))


def parameter_sensitivity():
    """Exploratory fixed-budget perturbations; no parameters selected by outcome.

    This is an exploratory robustness probe selected after examining the base grid, not
    a preregistered holdout. All five variants and all source windows are kept.
    """
    calibration=calibrate(load_rows(ROOT/'data/azure_llm_code_excerpt.csv'))
    variants=('base','recovery_plus_one','time_plus_one','cost_plus_one','score_rotation')
    rows=[]
    for variant in variants:
        for source in ('code','conv'):
            for window,jobs in enumerate(windows(ROOT/f'data/azure_llm_{source}_excerpt.csv',8,calibration)):
                schedules=None
                for level in ('tight','roomy'):
                    spec=copy.deepcopy(make_spec(jobs,1,1,level,calibration))
                    # Freeze the budgets from the unperturbed input. Do not
                    # compensate for perturbation by raising resource limits.
                    if variant=='recovery_plus_one':spec['recovery_time']+=1
                    elif variant=='time_plus_one':
                        for job in spec['jobs']:
                            job['accept_time']+=1;job['fallback_time']+=1
                    elif variant=='cost_plus_one':
                        for job in spec['jobs']:
                            job['accept_cost']+=1;job['fallback_cost']+=1
                    elif variant=='score_rotation':
                        for job in spec['jobs']:job['score']=(job['score']+1)%3
                    if schedules is None:
                        schedules=catalogue(spec['jobs'],spec['deadline'],spec['recovery_time'])
                    adaptive=synthesize(spec);fixed=solve(spec,schedules)
                    assert not fixed['feasible'] or adaptive['feasible']
                    rows.append(dict(variant=variant,source=source,window=window,budget=level,
                                     adaptive_feasible=adaptive['feasible'],adaptive_cost=adaptive['worst_cost'],
                                     open_loop_feasible=fixed['feasible'],open_loop_cost=fixed['worst_cost']))
    summary={}
    for variant in variants:
        subset=[r for r in rows if r['variant']==variant]
        summary[variant]=dict(contracts=len(subset),
            adaptive_feasible=sum(r['adaptive_feasible'] for r in subset),
            open_loop_feasible=sum(r['open_loop_feasible'] for r in subset),
            adaptive_only=sum(r['adaptive_feasible'] and not r['open_loop_feasible'] for r in subset),
            adaptive_lower_cost=sum(r['adaptive_feasible'] and r['open_loop_feasible']
                                    and r['adaptive_cost']<r['open_loop_cost'] for r in subset))
    return dict(contracts=len(rows),drift=1,crashes=1,exploratory=True,summary=summary,rows=rows)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=ROOT/'results/comparisons')
    ap.add_argument('--verify',action='store_true');args=ap.parse_args()
    result={'open_loop':trace_comparison(),'baseline_crosscheck':baseline_crosscheck(),
            'progress':[progress(b) for b in range(5)],'parameter_sensitivity':parameter_sensitivity()}
    if args.verify:
        for key,value in result.items():
            old=json.loads((args.output/(key+'.json')).read_text())
            if old!=value:raise SystemExit(f'{key}: deterministic evidence mismatch')
    else:
        args.output.mkdir(parents=True,exist_ok=True)
        for key,value in result.items():
            (args.output/(key+'.json')).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'open_loop':{k:v for k,v in result['open_loop'].items() if k!='rows'},
                      'baseline_crosscheck':result['baseline_crosscheck'],
                      'progress':result['progress'],'parameter_sensitivity':result['parameter_sensitivity']['summary'],'verified':args.verify},indent=2))
if __name__=='__main__':main()
