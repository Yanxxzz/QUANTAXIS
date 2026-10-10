import unittest
import numpy as np
import pandas as pd
from panda_alpha.risk_relation import relation_states


class RiskRelationTests(unittest.TestCase):
    def data(self):
        rng=np.random.default_rng(411)
        r=rng.normal(0,.02,(35,5));r[0]=0
        return pd.DataFrame(100*np.cumprod(1+r,axis=0),index=pd.date_range('2025-01-01',periods=35),columns=list('abcde'))
    def states(self,close):
        return relation_states(close,model=5,state=5,smooth=2,minimum_peers=3)
    def test_leave_own_out_market(self):
        s=self.states(self.data());day=s['returns'].index[8];r=s['returns'].loc[day]
        self.assertAlmostEqual(r.drop('a').mean(),s['market'].loc[day,'a'])
    def test_prior_model_error_uses_previous_coefficients(self):
        s=self.states(self.data());i=14
        expected=s['returns'].iloc[i]-s['alpha'].iloc[i-1]-s['beta'].iloc[i-1]*s['market'].iloc[i]
        np.testing.assert_allclose(s['prior_fit_error'].iloc[i],expected)
        self.assertFalse(np.allclose(s['prior_fit_error'].iloc[i],s['endpoint_residual'].iloc[i]))
    def test_future_changes_do_not_change_prior_values(self):
        close=self.data();s1=self.states(close);other=close.copy();other.iloc[24:]*=1.8;s2=self.states(other)
        for key in ['F141','RR01','RR02']:
            np.testing.assert_allclose(s1[key].iloc[:24],s2[key].iloc[:24],equal_nan=True)
    def test_missing_adjacent_quote_not_a_multiday_or_zero_return(self):
        close=self.data();close.iloc[9,0]=np.nan;s=self.states(close)
        self.assertTrue(np.isnan(s['returns'].iloc[9,0]));self.assertTrue(np.isnan(s['returns'].iloc[10,0]))
    def test_unknown_cross_section_not_a_market_mean(self):
        close=self.data();close.iloc[9,2:]=np.nan;s=self.states(close)
        self.assertTrue(s['market'].iloc[9].isna().all())
    def test_parent_bounds_do_not_clip_forecast_error_ratio(self):
        close=self.data();s=self.states(close)
        self.assertTrue(s['F141'].stack().between(0,1).all())
        self.assertTrue(s['RR01'].stack().ge(0).all())

if __name__=='__main__':unittest.main()
