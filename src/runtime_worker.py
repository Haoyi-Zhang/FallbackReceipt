#!/usr/bin/env python3
"""One restartable worker step with owned crash injection."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from checker import check
from runtime import ControllerStore, ReceiverStore

CUTS = {
    "none", "after_close", "after_recover", "before_reserve", "after_reserve",
    "before_execute", "after_execute", "after_settle",
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("certificate", type=Path)
    p.add_argument("--adapter", choices=["admission", "cache", "tier"], default="admission")
    p.add_argument("--cut", choices=sorted(CUTS), default="none")
    a = p.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    spec = json.loads(a.spec.read_text())
    cert = json.loads(a.certificate.read_text())
    if not cert.get("feasible") or not check(spec, cert):
        raise SystemExit("certificate rejected")

    controller = ControllerStore(a.root / "controller.db", spec, cert, controller_id="artifact-run", adapter=a.adapter)
    receiver = ReceiverStore(a.root / "receiver.db")

    def crash(name: str) -> None:
        if a.cut == name:
            os._exit(73)

    # Reconcile exactly one pending attempt at a time so after_close is a real cut.
    pending = controller.pending()
    if pending:
        ticket = controller.record_recovery(pending[0])
        # This cut is deliberately before receiver closure.  A following
        # restart therefore sees the same pending attempt and consumes another
        # declared recovery fault, matching the finite game rather than hiding
        # a crash in a post-settlement gap.
        crash("after_recover")
        receipt = receiver.close_attempt(pending[0], ticket)
        crash("after_close")
        controller.settle(receipt)
        # A canceled attempt leaves the same logical action selected; continue below.
        if controller.done():
            print(json.dumps(controller.snapshot(), sort_keys=True))
            return

    action = controller.ensure_selected()
    if action == "done":
        print(json.dumps(controller.snapshot(), sort_keys=True))
        return
    if action == "reject":
        crash("before_reserve")
        controller.apply_reject()
        crash("after_settle")
        print(json.dumps(controller.snapshot(), sort_keys=True))
        return

    crash("before_reserve")
    key, action, cost, _ = controller.reserve()
    crash("after_reserve")
    crash("before_execute")
    unsafe = int(spec["jobs"][controller.state()[0]]["score"] == 0) if action == "accept" else 0
    reserved_adapter, target = controller.attempt_descriptor(key)
    receipt = receiver.execute_once(key, reserved_adapter, target, cost, unsafe)
    crash("after_execute")
    controller.settle(receipt)
    crash("after_settle")
    print(json.dumps(controller.snapshot(), sort_keys=True))


if __name__ == "__main__":
    main()
