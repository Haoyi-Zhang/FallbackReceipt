#!/usr/bin/env python3
"""Executable local admission example with one suppressed receipt.

This demonstrates the API; fault-injection experiments are in reproduce.py.
It executes no model and contacts no external receiver.
"""
from __future__ import annotations
import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from game import synthesize
from checker import check
from runtime import ControllerStore,ReceiverStore,recover_pending
from trace_check import check_runtime


def run_example(directory: Path) -> dict:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('example output directory must be empty')
    directory.mkdir(parents=True,exist_ok=True)
    spec=json.loads((ROOT/'results/pilot/spec.json').read_text())
    cert=synthesize(spec)
    if not check(spec,cert):raise RuntimeError('certificate rejected')
    controller=ControllerStore(directory/'controller.db',spec,cert,controller_id='example',adapter='admission')
    receiver=ReceiverStore(directory/'receiver.db')
    try:
        attempt,_,cost,_=controller.reserve()
        adapter,target=controller.attempt_descriptor(attempt)
        receiver.execute_once(attempt,adapter,target,cost,0)
        # Deliberately do not settle the receipt: reopening sees a pending attempt.
        controller.close()
        controller=ControllerStore(directory/'controller.db',spec,cert,controller_id='example',adapter='admission')
        receipts=recover_pending(controller,receiver)
        if len(receipts)!=1 or receipts[0]['status']!='done':raise RuntimeError('recovery failed')
        while not controller.done():
            action=controller.ensure_selected()
            if action=='reject':controller.apply_reject();continue
            key,action,cost,_=controller.reserve()
            unsafe=int(spec['jobs'][controller.state()[0]]['score']==0) if action=='accept' else 0
            adapter,target=controller.attempt_descriptor(key)
            controller.settle(receiver.execute_once(key,adapter,target,cost,unsafe))
        snapshot,outcomes,effects=controller.snapshot(),receiver.outcomes(),receiver.effects()
        valid=check_runtime(spec,cert,snapshot,outcomes,effects)
        damaged=copy.deepcopy(snapshot);damaged['events'].pop(0)
        incomplete_rejected=not check_runtime(spec,cert,damaged,outcomes,effects)
        if not valid or not incomplete_rejected:raise RuntimeError('unexpected replay verdict')
        record=dict(spec=spec,certificate=cert,snapshot=snapshot,receiver_outcomes=outcomes,
                    effects=effects,valid=valid,incomplete_rejected=incomplete_rejected)
        (directory/'execution.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
        return dict(valid=valid,incomplete_rejected=incomplete_rejected,
                    certified_worst_cost=cert['worst_cost'],
                    actual_effects=len(effects),actual_cost=sum(e[0] for e in effects.values()),
                    recovered_attempt=attempt,final_state=snapshot['state'])
    finally:
        controller.close();receiver.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    if args.output is None:
        with tempfile.TemporaryDirectory() as td:result=run_example(Path(td))
    else:result=run_example(args.output)
    print(json.dumps(result,indent=2,sort_keys=True))
if __name__=='__main__':main()
