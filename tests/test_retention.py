"""Per-contract identity lifetime regressions; all mutation is local and disposable."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from retention_checks import check_counts, summarize_history, deletion_control, evaluate

class RetentionTests(unittest.TestCase):
    def test_exact_bound_and_rejected_jobs(self):
        self.assertTrue(check_counts(3,2,5,3,2,2))
        self.assertTrue(check_counts(3,2,2,1,1,2))
        self.assertTrue(check_counts(3,2,0,0,0,0))

    def test_invalid_counts_fail(self):
        for values in ((3,2,6,3,3,3),(3,2,4,4,0,0),(3,2,4,3,1,0),
                       (3,2,4,3,0,1),(3,2,5,3,2,3)):
            with self.subTest(values=values):self.assertFalse(check_counts(*values))

    def test_integer_aliases_fail(self):
        values=[3,2,5,3,2,2]
        for pos in range(6):
            for v in (float(values[pos]),True,None,-1):
                changed=values.copy();changed[pos]=v
                self.assertFalse(check_counts(*changed))

    def _pilot(self):
        p=ROOT/'results/pilot'
        return [json.loads((p/name).read_text()) for name in ('spec.json','certificate.json','runtime.json')]

    def test_pilot_replay_and_counts(self):
        row=summarize_history(*self._pilot())
        self.assertEqual(row['effects'],3)
        self.assertEqual(row['canceled'],0)

    def test_incomplete_history_is_not_a_count_certificate(self):
        s,c,r=self._pilot();r=copy.deepcopy(r)
        r['snapshot']['events'].pop()
        with self.assertRaisesRegex(ValueError,'replay-valid'):summarize_history(s,c,r)

    def test_tombstone_survives_reopen_and_deletion_control_breaks_fence(self):
        with tempfile.TemporaryDirectory() as td:
            for a in ('admission','cache','tier'):
                row=deletion_control(a,Path(td))
                self.assertEqual(row['effects_with_tombstone'],0)
                self.assertEqual(row['effects_after_deletion'],1)

    def test_frozen_count_domains_and_tight_cases(self):
        r=evaluate()
        self.assertEqual(r['complete_histories_replayed'],31)
        self.assertEqual(r['refinement_count_rows_checked'],171)
        self.assertGreater(r['bound_attained_histories'],0)

if __name__=='__main__':unittest.main()
