import unittest
import pandas as pd
from panda_alpha.earnings_signal import sparse_asof_values


class SparseNewsSignalTests(unittest.TestCase):
    def test_news_not_visible_before_publication_decision(self):
        days=["2025-01-01","2025-01-02","2025-01-03","2025-01-06"]
        row={"code":"000001","pub_date":"2025-01-02","decision_date":"2025-01-03","score":.4}
        s=sparse_asof_values([row],days,["000001"],lifetime=1)
        self.assertEqual("value",s.name)
        self.assertEqual(["symbol","date"],s.index.names)
        self.assertEqual([0.,0.,.4,0.],s.tolist())

    def test_unknown_recent_event_blocks_old_signal(self):
        days=["2025-01-01","2025-01-02","2025-01-03","2025-01-06"]
        row={"code":"000001","pub_date":"2025-01-01","decision_date":"2025-01-02","score":.4}
        pending={"code":"000001","decision_date":"2025-01-03"}
        s=sparse_asof_values([row],days,["000001"],lifetime=2,pending_events=[pending])
        self.assertEqual(.4,s.iloc[1]);self.assertTrue(pd.isna(s.iloc[2]));self.assertTrue(pd.isna(s.iloc[3]))

    def test_same_day_conflicting_numeric_news_not_silently_chosen(self):
        rows=[{"code":"000001","pub_date":"2025-01-01","decision_date":"2025-01-02","score":x} for x in [1.,2.]]
        with self.assertRaises(ValueError):sparse_asof_values(rows,["2025-01-01","2025-01-02"],["000001"])

    def test_unobserved_code_not_assumed_zero(self):
        self.assertTrue(sparse_asof_values([],['2025-01-01'],[]).empty)

if __name__=='__main__':unittest.main()
