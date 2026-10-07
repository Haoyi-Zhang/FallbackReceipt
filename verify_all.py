#!/usr/bin/env python3
"""Check the artifact; optionally replay experiments and compile the paper.

A successful exit certifies only these executed checks, not publication
readiness, general implementation correctness, or author declarations.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def run(arguments: list[str], timeout: int = 600) -> None:
    print('+ ' + ' '.join(arguments), flush=True)
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONWARNINGS'] = 'error::ResourceWarning'
    completed = subprocess.run(arguments, cwd=ROOT, env=env, check=False, timeout=timeout,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(completed.stdout, end='', flush=True)
    completed.check_returncode()
    if 'ResourceWarning:' in completed.stdout:
        raise RuntimeError('resource warning in subprocess; verification not clean')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reproduce', action='store_true',
                        help='also execute all ten experiment phases in a temporary directory')
    parser.add_argument('--paper', type=Path, help='optional paper directory containing build.sh')
    args = parser.parse_args()
    py = [sys.executable, '-S']
    run(py + ['-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    run(py + ['-m', 'unittest', 'discover', '-s', 'regressions',
              '-p', 'test_frontier_limits.py', '-v'])
    run(py + ['verify_results.py', '--results', 'results'])
    run(py + ['comparisons.py', '--verify'])
    run(py + ['compare_oracle_fixed.py', '--verify'], timeout=120)
    run(py + ['retention_checks.py', '--verify'])
    run(py + ['examples/run_contract.py'])
    if args.reproduce:
        with tempfile.TemporaryDirectory(prefix='receipt-closed-replay-') as tmp:
            out = Path(tmp) / 'results'
            run(py + ['reproduce.py', '--phase', 'all', '--output', str(out)])
            run(py + ['verify_results.py', '--results', str(out), '--compare', 'results'])
    if args.paper is not None:
        paper = args.paper.resolve()
        if not (paper / 'build.sh').is_file():
            parser.error('--paper must identify a directory containing build.sh')
        run(['sh', str(paper / 'build.sh')])
    print('All requested computational checks passed. Author and submission declarations are not checked.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f'CHECK FAILED: {exc}', file=sys.stderr)
        raise SystemExit(1)
