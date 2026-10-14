import numpy as np
import pandas as pd
from panda_alpha.limit_event_resume import extension_panel
from panda_alpha.limit_event_features import build_event_features

def test_vector_extension_matches_frozen_event_feature_at_candidate():
    days=pd.bdate_range("2024-01-02",periods=35).strftime("%Y-%m-%d").tolist()
    close=np.arange(35)*.1+10;close[24]=20;close[25]=22
    bars=pd.DataFrame({"date":days,"symbol":"A","raw_close":close,"raw_high":close,"raw_low":close,"up_limit":100.,"down_limit":1.,"amount":100.,"adjustment_factor":1.,"eligible":True})
    bars.loc[24,"up_limit"]=20;bars.loc[25,"up_limit"]=22
    events=build_event_features(bars,days,adjustment_factor_role="total_return_factor")
    q=pd.DataFrame({"A":close},index=days);a=pd.DataFrame(1.,index=days,columns=["A"])
    score=extension_panel(q,a)
    for row in events.itertuples():assert score.loc[row.date,row.symbol] == row.prior_extension_20
    future=q.copy();future.iloc[27:]*=100
    pd.testing.assert_frame_equal(score.iloc[:27],extension_panel(future,a).iloc[:27])
    q.iloc[8]=np.nan
    assert np.isnan(extension_panel(q,a).iloc[26,0])
