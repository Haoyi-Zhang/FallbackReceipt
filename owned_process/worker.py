"""One owned local process step; bounded crash cuts use intentional exit 73.

No external payload, service or object is accessed. The ordinary and reference
receivers share the checked controller, contract, inputs and recorded output
obligations. Only the receiver implementation differs.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'src'))
from runtime import ControllerStore, ReceiverStore
from trace_check import check_runtime
from ordinary import OrdinaryReceiver


def require(value, message):
    if not value:
        raise RuntimeError(message)


def disk(root):
    return {p.name:p.stat().st_size for p in root.iterdir()
            if p.is_file() and (p.name.endswith('.db') or p.name.endswith(('-wal','-shm')))}


def committed_receipt(path, receipt):
    # Live WAL must be read with mode=ro, not immutable=1. The two observations
    # use one read transaction; they do not attest cross-database real-time order.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        row = db.execute('SELECT state,adapter,job,cost,unsafe FROM outcomes WHERE id=?',
                         (receipt['id'],)).fetchone()
        effects = db.execute('SELECT COUNT(*) FROM effects WHERE id=?',(receipt['id'],)).fetchone()[0]
        db.execute('COMMIT')
    require(row == tuple(receipt[k] for k in ('status','adapter','job','cost','unsafe')),
            'returned receipt is not independently visible after commit')
    require(effects == int(receipt['status']=='done'), 'receipt/effect snapshot differs')
    return dict(id=receipt['id'],status=receipt['status'],row=list(row),effects=effects)


def main():
    started, cpu = time.perf_counter(), time.process_time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--adapter',choices=['admission','cache','tier'],required=True)
    parser.add_argument('--receiver',choices=['receipt','ordinary'],required=True)
    parser.add_argument('--cut',choices=['none','after_reserve','after_execute','after_recover',
                                       'after_close','receiver_uncommitted'],default='none')
    args = parser.parse_args()
    root = args.root
    spec = json.loads((root/'spec.json').read_text())
    cert = json.loads((root/'certificate.json').read_text())
    controller = ControllerStore(root/'controller.db',spec,cert,controller_id='owned-process',adapter=args.adapter)

    def crash(name):
        if args.cut == name:
            # Make the chosen cut observable in stdout before terminating only
            # this owned worker. No returned receiver receipt is fabricated.
            print(json.dumps({'cut':name,'exit':73,'disk_sample':disk(root)}),flush=True)
            os._exit(73)

    class CutReceiver(ReceiverStore):
        def _event(self,event):
            super()._event(event)
            crash('receiver_uncommitted')

    receiver = (CutReceiver(root/'receiver.db') if args.receiver=='receipt'
                else OrdinaryReceiver(root/'receiver.db',lambda:crash('receiver_uncommitted')))
    receipts, commit_observations = [], []
    try:
        pending = controller.pending()
        if pending:
            key = pending[0]
            ticket = controller.record_recovery(key)
            crash('after_recover')
            receipt = receiver.close_attempt(key,ticket)
            receipts.append(receipt)
            commit_observations.append(committed_receipt(root/'receiver.db',receipt))
            print(json.dumps(dict(kind='receiver_return',receipt=receipt,
                                  committed=commit_observations[-1])),flush=True)
            crash('after_close')
            controller.settle(receipt)
        if not controller.done():
            action = controller.ensure_selected()
            if action=='reject':
                controller.apply_reject()
            else:
                key, action, charge, _ = controller.reserve()
                crash('after_reserve')
                adapter,target = controller.attempt_descriptor(key)
                job = spec['jobs'][controller.state()[0]]
                unsafe = int(job['score']==0) if action=='accept' else 0
                receipt = receiver.execute_once(key,adapter,target,charge,unsafe)
                receipts.append(receipt)
                commit_observations.append(committed_receipt(root/'receiver.db',receipt))
                print(json.dumps(dict(kind='receiver_return',receipt=receipt,
                                      committed=commit_observations[-1])),flush=True)
                crash('after_execute')
                controller.settle(receipt)
        snapshot,outcomes,effects = controller.snapshot(),receiver.outcomes(),receiver.effects()
        complete = controller.done()
        if complete:
            require(check_runtime(spec,cert,snapshot,outcomes,effects),'complete owned history rejected')
        before = disk(root)
        checkpoints = []
        checkpoint_started = time.perf_counter()
        for name, store in [('controller',controller),('receiver',receiver)]:
            result = store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            require(result[0]==0,'checkpoint is busy')
            checkpoints.append(dict(role=name,result=list(result)))
        checkpoint_seconds = time.perf_counter()-checkpoint_started
        after = disk(root)
        controller.close()
        receiver.close()
        controller = receiver = None
        elapsed, used_cpu = time.perf_counter()-started,time.process_time()-cpu
        print(json.dumps(dict(snapshot=snapshot,receiver_outcomes=outcomes,effects=effects,
                             complete=complete,receipts=receipts,commit_observations=commit_observations,
                             before_checkpoint=before,after_checkpoint=after,after_close=disk(root),
                             checkpoints=checkpoints,checkpoint_seconds=checkpoint_seconds,
                             worker_wall_seconds=elapsed,worker_cpu_seconds=used_cpu,
                             optimize=sys.flags.optimize,sqlite_version=sqlite3.sqlite_version)),flush=True)
    finally:
        if receiver is not None:
            receiver.close()
        if controller is not None:
            controller.close()


if __name__=='__main__':
    main()
