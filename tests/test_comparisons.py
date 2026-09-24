"""Exact-comparison and input-boundary regressions."""
import copy
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import game
import checker
import open_loop
from comparisons import baseline_crosscheck, progress
from test_contracts import one_job, three_jobs


class ComparisonTests(unittest.TestCase):
    def test_noninteger_score_is_rejected_by_both_implementations(self):
        cert = game.synthesize(one_job())
        for score in (True, False, 0.0, 1.0, 2.0, '2', None):
            with self.subTest(score=score):
                spec = one_job(); spec['jobs'][0]['score'] = score
                with self.assertRaises(ValueError): game.synthesize(spec)
                self.assertFalse(checker.check(spec, cert))
                with self.assertRaises(ValueError): open_loop.solve(spec)

    def test_certificate_schema_must_be_integer(self):
        spec=one_job();cert=game.synthesize(spec)
        for schema in (2.0, '2', True):
            bad=copy.deepcopy(cert);bad['schema']=schema
            self.assertFalse(checker.check(spec,bad))

    def test_certificate_edge_quantities_must_be_integers(self):
        spec=three_jobs();cert=game.synthesize(spec)
        for row_key, row in cert['policy'].items():
            for index, outcome in enumerate(row['outcomes']):
                for field in ('charge', 'continuation'):
                    bad=copy.deepcopy(cert)
                    bad['policy'][row_key]['outcomes'][index][field]=float(outcome[field])
                    self.assertFalse(checker.check(spec,bad))
                for field, value in outcome['meta'].items():
                    bad=copy.deepcopy(cert)
                    bad['policy'][row_key]['outcomes'][index]['meta'][field]=float(value)
                    self.assertFalse(checker.check(spec,bad))

    def test_checker_limit_is_not_infeasibility(self):
        spec=three_jobs();cert=game.synthesize(spec)
        with patch.object(checker,'MAX_CHECK_STATES',1):
            with self.assertRaisesRegex(RuntimeError,'no feasibility decision'):
                checker.check(spec,cert)

    def test_fixed_sequence_baseline_matches_direct_enumeration(self):
        result=baseline_crosscheck()
        self.assertEqual(result['instances'],240)
        self.assertEqual(result['fixed_sequences'],3120)
        self.assertEqual(result['mismatches'],0)

    def test_fixed_sequence_pilot_has_equal_optimal_cost(self):
        spec=three_jobs();a=game.synthesize(spec);b=open_loop.solve(spec)
        self.assertEqual(a['worst_cost'],10)
        self.assertEqual(b['worst_cost'],10)
        self.assertEqual(b['sequences_checked'],27)

    def test_fixed_sequence_limit_is_not_infeasibility(self):
        with patch.object(open_loop,'MAX_SCHEDULES',2):
            with self.assertRaisesRegex(RuntimeError,'not an infeasibility result'):
                open_loop.solve(one_job())

    def test_enumeration_cache_cannot_rebind_time_or_job(self):
        spec=three_jobs();cache=open_loop.catalogue(spec['jobs'],spec['deadline'],spec['recovery_time'])
        for field in ('deadline','recovery_time'):
            changed=copy.deepcopy(spec);changed[field]+=1
            with self.assertRaisesRegex(ValueError,'different job/time'):
                open_loop.solve(changed,cache)
        changed=copy.deepcopy(spec);changed['jobs'][0]['accept_cost']+=1
        with self.assertRaisesRegex(ValueError,'different job/time'):
            open_loop.solve(changed,cache)
        changed=copy.deepcopy(spec);changed['unsafe']+=1
        self.assertEqual(open_loop.solve(changed),open_loop.solve(changed,cache))

    def test_productive_progress_has_no_cycles_or_nonterminal_deadends(self):
        for bound in range(5):
            result=progress(bound)
            self.assertEqual(result['nonterminal_dead_ends'],0)
            self.assertEqual(result['productive_cycles'],0)
            self.assertLessEqual(result['longest_no_further_crash_path'],6)

    def test_zero_recovery_time_cannot_add_late_outcomes(self):
        jobs=three_jobs()['jobs'];actions=('accept','fallback','accept')
        env=open_loop.envelope(jobs,actions,10,0)
        for f in range(5):
            self.assertEqual(open_loop.worst_counts(env,2,f),(10,2,0))

    def test_runtime_freezes_input_objects_before_execution(self):
        from runtime import ControllerStore
        spec=one_job();cert=game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            co=ControllerStore(Path(td)/'c.db',spec,cert)
            try:
                spec['jobs'][0]['accept_cost']=999
                cert['policy'].clear()
                self.assertEqual(co.reserve()[2],2)
                with self.assertRaises(TypeError):co.spec['jobs'][0]['accept_cost']=3
                with self.assertRaises(AttributeError):co.spec={}
                with self.assertRaises(TypeError):co.cert['policy']['new']={}
            finally:co.close()

    def test_runtime_rejects_nonobjects_before_creating_database(self):
        from runtime import ControllerStore
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'c.db'
            for spec,cert in ((None,{}),({},[]),(True,False)):
                with self.assertRaises(ValueError):ControllerStore(path,spec,cert)
                self.assertFalse(path.exists())

    def test_replay_rejects_float_and_boolean_numeric_aliases(self):
        from runtime import ControllerStore,ReceiverStore
        from trace_check import check_runtime
        spec=one_job();cert=game.synthesize(spec)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);co=ControllerStore(path/'c.db',spec,cert);rx=ReceiverStore(path/'r.db')
            try:
                key,_,cost,_=co.reserve()
                co.settle(rx.execute_once(key,*co.attempt_descriptor(key),cost,0))
                snap,out,eff=co.snapshot(),rx.outcomes(),rx.effects()
                self.assertTrue(check_runtime(spec,cert,snap,out,eff))
                bad=copy.deepcopy(snap);bad['state'][0]=float(bad['state'][0])
                self.assertFalse(check_runtime(spec,cert,bad,out,eff))
                bad=copy.deepcopy(out);bad[key]['unsafe']=False
                self.assertFalse(check_runtime(spec,cert,snap,bad,eff))
                bad=copy.deepcopy(snap);bad['attempts'][key]['recoveries']=0.0
                self.assertFalse(check_runtime(spec,cert,bad,out,eff))
            finally:co.close();rx.close()

    def test_executable_example_recovers_without_duplicate_effect(self):
        from examples.run_contract import run_example
        with tempfile.TemporaryDirectory() as td:
            result=run_example(Path(td))
            self.assertTrue(result['valid'])
            self.assertTrue(result['incomplete_rejected'])
            self.assertEqual(result['actual_effects'],3)
            self.assertEqual(result['certified_worst_cost'],10)
            self.assertEqual(result['actual_cost'],9)

    def test_crash_driver_reader_closes_sqlite_connections(self):
        import sqlite3
        import reproduce
        from contextlib import closing
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with closing(sqlite3.connect(root/'controller.db')) as db:
                db.execute('CREATE TABLE meta(key TEXT, value TEXT)')
                db.execute("INSERT INTO meta VALUES('state', '[1,0,0,0,0,0]')")
                db.commit()
            original=sqlite3.connect
            opened=[]
            def connect(*args, **kwargs):
                db=original(*args, **kwargs);opened.append(db);return db
            with patch.object(reproduce.sqlite3, 'connect', side_effect=connect):
                self.assertEqual(reproduce._current_job(root, {}), 1)
            for db in opened:
                with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')

if __name__=='__main__':unittest.main()
