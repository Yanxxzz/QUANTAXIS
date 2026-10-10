import unittest
import numpy as np
import pandas as pd
from panda_alpha.vwap_dislocation import slow_vwap_dislocation


class DislocationTests(unittest.TestCase):
    def panels(self):
        close=pd.DataFrame([[10.,20.,30.],[11.,19.,31.],[10.,21.,29.],[12.,20.,28.]],
                           index=pd.date_range('2024-01-02',periods=4))
        volume=pd.DataFrame(1000.,index=close.index,columns=close.columns)
        mean=close+pd.Series([.2,-.2,.1],index=close.columns)
        return close,mean*volume,volume,close+1,close-1

    def test_original_rank_ratio_and_rolling_mean(self):
        c,a,v,h,l=self.panels();got=slow_vwap_dislocation(c,a,v,h,l,window=2,minimum_assets=2)
        expected=((a/v-c).rank(axis=1,pct=True)/(a/v+c).rank(axis=1,pct=True)).rolling(2,min_periods=2).mean()
        pd.testing.assert_frame_equal(got,expected)

    def test_hands_mistaken_for_shares_fail_source_bounds(self):
        c,a,v,h,l=self.panels()
        self.assertTrue(slow_vwap_dislocation(c,a,v/100,h,l,window=1,minimum_assets=2).isna().all().all())

    def test_missing_or_zero_volume_is_not_a_zero_signal(self):
        c,a,v,h,l=self.panels();v.iloc[1,0]=0
        out=slow_vwap_dislocation(c,a,v,h,l,window=2,minimum_assets=2)
        self.assertTrue(out.iloc[1:3,0].isna().all())
        self.assertTrue(np.isfinite(out.iloc[3,0]))

    def test_future_changes_preserve_prior_values(self):
        c,a,v,h,l=self.panels();before=slow_vwap_dislocation(c,a,v,h,l,window=1,minimum_assets=2)
        a.iloc[3]=c.iloc[3]*v.iloc[3]
        after=slow_vwap_dislocation(c,a,v,h,l,window=1,minimum_assets=2)
        pd.testing.assert_frame_equal(before.iloc[:3],after.iloc[:3])

    def test_currency_scale_must_be_common_across_assets(self):
        c,a,v,h,l=self.panels();before=slow_vwap_dislocation(c,a,v,h,l,window=1,minimum_assets=2)
        after=slow_vwap_dislocation(c*10,a*10,v,h*10,l*10,window=1,minimum_assets=2,quote_tolerance=.11)
        pd.testing.assert_frame_equal(before,after)


if __name__=='__main__':unittest.main()
