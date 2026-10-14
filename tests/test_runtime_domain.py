"""Owned executable-boundary tests; model infeasibility is a different result."""
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from checker import check
from game import synthesize
from runtime import (ControllerStore, ReceiverStore, RuntimeDomainError,
                     SQLITE_INTEGER_MAX, _check_storage_domain)
from trace_check import check_runtime


def contract(charge=2, count=1, fallback_only=False):
    return {'jobs':[{'score':0 if fallback_only else 2, 'accept_cost':charge,
                     'fallback_cost':charge, 'accept_time':1, 'fallback_time':1}
                    for _ in range(count)], 'drift':0, 'crashes':2, 'unsafe':0,
            'late':0, 'cost':charge*count, 'recovery_time':0, 'deadline':1}


class RuntimeDomainTests(unittest.TestCase):
    def test_feasible_but_unsupported_fallback_is_refused_before_files(self):
        spec = contract(SQLITE_INTEGER_MAX+1, fallback_only=True)
        cert = synthesize(spec)
        self.assertTrue(check(spec, cert))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'uncreated'/'controller.db'
            with self.assertRaisesRegex(RuntimeDomainError, 'selected cost'):
                ControllerStore(path, spec, cert)
            self.assertFalse(path.parent.exists())
        self.assertTrue(cert['feasible'])  # No fabricated mathematical infeasibility.

    def test_large_unselected_envelope_does_not_refuse_safe_policy(self):
        spec = contract(1)
        spec['jobs'][0]['fallback_cost'] = SQLITE_INTEGER_MAX+1
        cert = synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            store = ControllerStore(Path(td)/'controller.db', spec, cert)
            try:
                self.assertEqual(store.ensure_selected(), 'accept')
                self.assertEqual(store.reserve()[2], 1)
            finally:
                store.close()

    def test_maximum_effect_cost_and_larger_json_aggregate_complete_exactly(self):
        spec = contract(SQLITE_INTEGER_MAX, count=2, fallback_only=True)
        cert = synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            controller = ControllerStore(Path(td)/'controller.db', spec, cert,
                                         controller_id='max-domain')
            receiver = ReceiverStore(Path(td)/'receiver.db')
            try:
                self.assertGreater(controller.state()[5], SQLITE_INTEGER_MAX)
                for _ in range(2):
                    key, action, cost, _ = controller.reserve()
                    self.assertEqual(action, 'fallback')
                    adapter, target = controller.attempt_descriptor(key)
                    controller.settle(receiver.execute_once(key, adapter, target, cost, 0))
                self.assertTrue(check_runtime(spec, cert, controller.snapshot(),
                                              receiver.outcomes(), receiver.effects()))
                self.assertEqual(controller.state()[5], 0)
                self.assertEqual(receiver.db.execute(
                    'SELECT cost,typeof(cost) FROM effects ORDER BY id').fetchall(),
                    [(SQLITE_INTEGER_MAX, 'integer')]*2)
            finally:
                receiver.close()
                controller.close()

    def test_direct_overrange_effect_is_typed_and_has_no_partial_write(self):
        with tempfile.TemporaryDirectory() as td:
            receiver = ReceiverStore(Path(td)/'receiver.db')
            try:
                for cost in (SQLITE_INTEGER_MAX+1, True, -1, 1.0):
                    with self.subTest(cost=cost), self.assertRaises(ValueError):
                        receiver.execute_once('owned-id', 'admission', 'job', cost, 0)
                self.assertFalse(receiver.db.in_transaction)
                self.assertEqual(receiver.outcomes(), {})
                self.assertEqual(receiver.effects(), {})
                self.assertEqual(receiver.events(), [])
                self.assertEqual(receiver.db.execute('SELECT COUNT(*) FROM queue_effects').fetchone()[0], 0)
            finally:
                receiver.close()

    def test_counter_guard_requires_room_for_final_attempt_count(self):
        # Unit-level admission predicate boundary, not an accepted huge policy
        # or completed huge game search. Certificate soundness is checked first
        # by the actual constructor; this directly exercises only its guard.
        spec = contract()
        def rows(f):
            return {'policy':{f'0,0,{f},0,0,2':{'action':'fallback'}}}
        _check_storage_domain(spec, rows(SQLITE_INTEGER_MAX-1))
        with self.assertRaisesRegex(RuntimeDomainError, 'recovery count'):
            _check_storage_domain(spec, rows(SQLITE_INTEGER_MAX))

    def test_duplicate_cancellation_keeps_canonical_shape_and_noop(self):
        spec = contract()
        cert = synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            controller = ControllerStore(Path(td)/'controller.db', spec, cert)
            receiver = ReceiverStore(Path(td)/'receiver.db')
            try:
                key, _, _, _ = controller.reserve()
                receipt = receiver.close_attempt(key, controller.record_recovery(key))
                controller.settle(receipt)
                original = controller.snapshot()
                for changes in ({'adapter':'cache'}, {'job':'wrong-local-job'},
                                {'adapter':'cache','job':'wrong-local-job'}):
                    wrong = dict(receipt, **changes)
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        controller.settle(wrong)
                    self.assertEqual(controller.snapshot(), original)
                controller.settle(copy.deepcopy(receipt))
                self.assertEqual(controller.snapshot(), original)
            finally:
                receiver.close()
                controller.close()

    def test_returned_receiver_receipt_is_visible_on_other_connection(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'receiver.db'
            receiver = ReceiverStore(path)
            try:
                for key, operation in [('done-id','execute'), ('cancel-id','close')]:
                    receipt = (receiver.execute_once(key,'cache','job',2,0) if operation=='execute'
                               else receiver.close_attempt(key,'0'*32))
                    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as read:
                        observed = read.execute('SELECT state,cost,unsafe FROM outcomes WHERE id=?',
                                                (key,)).fetchone()
                        self.assertEqual(observed, (receipt['status'],receipt['cost'],receipt['unsafe']))
                        self.assertEqual(read.execute('SELECT COUNT(*) FROM effects WHERE id=?',
                                                     (key,)).fetchone()[0], operation=='execute')
                    read.close()  # sqlite context manager does not close the handle.
            finally:
                receiver.close()


if __name__ == '__main__':
    unittest.main()
