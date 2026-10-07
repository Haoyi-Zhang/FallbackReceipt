"""Exact compilation comparison; source excerpts and envelopes remain fixed."""
from __future__ import annotations
import argparse
import ctypes
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from frontier import solve_frontier
from open_loop import solve, catalogue, envelope, worst_counts
from workload import load_rows, calibrate, windows, make_spec


def check(spec, result):
    if result['feasible']:
        cost, unsafe, late = worst_counts(envelope(spec['jobs'], result['actions'], spec['deadline'], spec['recovery_time']), spec['drift'], spec['crashes'])
        assert cost == result['worst_cost'] and cost <= spec['cost'] and unsafe <= spec['unsafe'] and late <= spec['late']


def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    if sys.platform == 'win32':
        ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.windll.kernel32.GetCurrentProcess(), 1)
    rng = random.Random(390071)
    for case in range(256):
        n = rng.randrange(1, 7)
        jobs = [dict(score=rng.randrange(3), accept_cost=rng.randrange(1, 7), fallback_cost=rng.randrange(1, 7),
                     accept_time=rng.randrange(1, 7), fallback_time=rng.randrange(1, 7)) for _ in range(n)]
        spec = dict(jobs=jobs, drift=rng.randrange(4), crashes=rng.randrange(4), unsafe=rng.randrange(n + 1),
                    late=rng.randrange(n + 1), cost=rng.randrange(6 * n + 1), recovery_time=rng.randrange(4), deadline=rng.randrange(1, 8))
        a, b = solve(spec), solve_frontier(spec)
        assert (a['feasible'], a['worst_cost']) == (b['feasible'], b['worst_cost']), (case, spec, a, b)
        check(spec, b)
    root = Path(__file__).resolve().parent
    calibration = calibrate(load_rows(root / 'data/azure_llm_code_excerpt.csv'))
    grid = []
    for source in ('code', 'conv'):
        for i, jobs in enumerate(windows(root / f'data/azure_llm_{source}_excerpt.csv', 8, calibration)):
            cached = catalogue(jobs, calibration['deadline'], calibration['recovery_time'])
            for drift in range(3):
                for crashes in range(3):
                    for level in ('tight', 'roomy'):
                        spec = make_spec(jobs, drift, crashes, level, calibration)
                        a, b = solve(spec, cached), solve_frontier(spec)
                        assert (a['feasible'], a['worst_cost']) == (b['feasible'], b['worst_cost'])
                        check(spec, b)
                        grid.append({'source': source, 'window': i, 'drift': drift, 'crashes': crashes, 'level': level,
                                     'feasible': b['feasible'], 'worst_cost': b['worst_cost'], 'expanded_labels': b['expanded_labels'],
                                     'maximum_frontier': b['maximum_frontier']})
    samples = []
    # Four preselected windows, covering both source populations. Both timers
    # include full setup. Warm cache reuse is not given to just one comparator.
    for source in ('code', 'conv'):
        all_jobs = windows(root / f'data/azure_llm_{source}_excerpt.csv', 8, calibration)
        for window in (0, 8):
            spec = make_spec(all_jobs[window], 1, 1, 'roomy', calibration)
            for pair in range(7):
                for mode in (('enumerated', 'frontier') if pair % 2 == 0 else ('frontier', 'enumerated')):
                    start = time.perf_counter_ns()
                    result = solve(spec) if mode == 'enumerated' else solve_frontier(spec)
                    elapsed = time.perf_counter_ns() - start
                    check(spec, result)
                    samples.append({'source': source, 'window': window, 'pair': pair, 'mode': mode,
                                    'elapsed_ns': elapsed, 'feasible': result['feasible'], 'worst_cost': result['worst_cost']})
    scale = []
    for source in ('code', 'conv'):
        for n in (16, 32):
            for window, jobs in enumerate(windows(root / f'data/azure_llm_{source}_excerpt.csv', n, calibration)):
                spec = make_spec(jobs, 1, 1, 'roomy', calibration)
                spec['late'] = max(3, 3 * n // 8)
                start = time.perf_counter_ns()
                result = solve_frontier(spec)
                elapsed = time.perf_counter_ns() - start
                check(spec, result)
                scale.append({'source': source, 'jobs': n, 'window': window, 'elapsed_ns': elapsed, 'specification': spec, **result})
    ratios = []
    for source in ('code', 'conv'):
        for window in (0, 8):
            v = [row for row in samples if row['source'] == source and row['window'] == window]
            pairs = [{r['mode']: r['elapsed_ns'] for r in v if r['pair'] == i} for i in range(7)]
            ratios.append({'source': source, 'window': window,
                           'median_paired_enumerated_over_frontier': statistics.median(p['enumerated'] / max(1, p['frontier']) for p in pairs)})
    (output / 'grid.json').write_text(json.dumps(grid, indent=2) + '\n')
    (output / 'samples.json').write_text(json.dumps(samples, indent=2) + '\n')
    (output / 'scale.json').write_text(json.dumps(scale, indent=2) + '\n')
    summary = {'random_agreement_cases': 256, 'grid_agreement_cases': len(grid), 'scale_cases': len(scale),
               'scale_feasible': sum(r['feasible'] for r in scale), 'ratios': ratios,
               'environment': {'python': sys.version, 'platform': platform.platform(), 'processor': platform.processor(),
                               'timer': 'perf_counter_ns', 'affinity_mask': 1, 'seed': 390071}}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
