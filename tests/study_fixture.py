"""Deterministic nonfinancial study fixture; never loads real market files."""
import pandas as pd


def synthetic_study(names=36,sessions=72,candidate_id="ORBIT07"):
    dates=pd.bdate_range("2024-01-02",periods=sessions).strftime("%Y-%m-%d").tolist()
    symbols=[f"S{i:02d}" for i in range(names)];sources=[];quotes=[]
    for n,day in enumerate(dates):
        for i,symbol in enumerate(symbols):
            opening=10*(1+.0004+.00006*i)**n;closing=opening*(1+.0002)
            sources.append({"date":day,"symbol":symbol,"value":float(i),"pit_usable":True,"source_status":"synthetic_source_known"})
            quotes.append({"date":day,"symbol":symbol,"open":opening,"close":closing,"high":closing*1.002,"low":opening*.998,
                           "raw_open":opening,"raw_close":closing,"raw_high":closing*1.002,"raw_low":opening*.998,
                           "volume":1000000.,"amount":50000000.,"trade_status":1,"adjustment":"hfq"})
    source=pd.DataFrame(sources);quote=pd.DataFrame(quotes);comparator=source.copy()
    protocol={"candidate_id":candidate_id,"direction":1,"groups":10 if names>=30 else 2,"cycle":5 if sessions>=60 else 1,
              "min_assets":30 if names>=30 else 3,"costs":[.003,.005],"comparator_id":"REFERENCE11","comparator_direction":0,
              "calendar":dates,"calendar_verified":True,"calendar_evidence_scope":"declared_synthetic_fixture_sessions",
              "window":{"decisions_start":dates[0],"decisions_end":dates[-1],"holding_end":dates[-1]}}
    return protocol,source,quote,comparator
