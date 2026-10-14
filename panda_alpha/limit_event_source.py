"""Read retained raw main-board contract once, with explicit reference gaps."""
from bisect import bisect_left
import hashlib,json
import numpy as np
import pandas as pd
from pymongo import MongoClient
from scripts.backtest_limit_reversal import limit_price

def load_retained_panel(cfg,meta,*,include_strategy=False):
    profile=meta["source_profile"];codes=meta["codes"];window={"$gte":meta["start"],"$lte":meta["end"]}
    fields={k:1 for k in ["code","date","open","high","low","close","preclose","amount","volume_shares","is_st","trade_status","adjustment","source_row_sha256"]};fields["_id"]=0
    h=hashlib.sha256();chunks=[];buf=[]
    with MongoClient(cfg["data"]["mongo_uri"],serverSelectionTimeoutMS=10000,socketTimeoutMS=180000) as c:
        db=c[profile["database"]]
        for row in db.stock_day.find({"source":"stockdb","code":{"$in":codes},"date":window},fields).sort([("code",1),("date",1)]).batch_size(5000):
            h.update((row["code"]+"|"+row["date"]+"|"+row.get("source_row_sha256","")+"\n").encode());buf.append(row)
            if len(buf)==100000:chunks.append(pd.DataFrame(buf));buf.clear()
        if buf:chunks.append(pd.DataFrame(buf));buf.clear()
        sidecars=list(c[profile["reference_database"]].stock_day.find({"source":"baostock","trade_status":"0","code":{"$in":codes},"date":window},{"_id":0,"code":1,"date":1}))
        factors=pd.DataFrame(list(db.stock_adj.find({"source":"stockdb","code":{"$in":codes},"date":{"$lte":meta["end"]}},{"_id":0,"code":1,"date":1,"adj":1})))
    frame=pd.concat(chunks,ignore_index=True);chunks.clear()
    if len(frame)!=meta["source_rows"] or frame.duplicated(["code","date"]).any() or not frame.adjustment.eq("none").all():raise ValueError("Frozen raw identities or raw adjustment contract changed")
    all_days=meta["all_reference_days_through_end"];eligible={};delisted={}
    for life in meta["lifecycle"]:
        if not life.get("ipo_date") or life.get("lifecycle_issues"):continue
        n=bisect_left(all_days,life["ipo_date"])+20
        eligible[life["code"]]=all_days[n] if n<len(all_days) else "9999-12-31"
        if life.get("delisted_date"):delisted[life["code"]]=life["delisted_date"]
    frame["eligible"]=frame.date.ge(frame.code.map(eligible).fillna("9999-12-31"))&frame.date.lt(frame.code.map(delisted).fillna("9999-12-31"))&frame.is_st.eq(False)&frame.trade_status.eq("1")
    stknown=frame.is_st.isin([True,False]);rates=np.where(frame.is_st.eq(True)&frame.date.lt("2026-07-06"),.05,.10)
    frame["up_limit"]=[limit_price(v,r,"up") for v,r in zip(frame.preclose,rates)]
    frame["down_limit"]=[limit_price(v,r,"down") for v,r in zip(frame.preclose,rates)]
    frame.loc[~stknown,["up_limit","down_limit"]]=np.nan
    corrected=frame.code.eq("000518")&frame.date.ge("2026-05-20")
    frame.loc[corrected,"up_limit"]=[limit_price(v,.10,"up") for v in frame.loc[corrected,"preclose"]]
    frame.loc[corrected,"down_limit"]=[limit_price(v,.10,"down") for v in frame.loc[corrected,"preclose"]]
    frame["adjustment_factor"]=np.nan;fg={code:x.sort_values("date") for code,x in factors.groupby("code",sort=False)};records=[]
    for code,idx in frame.groupby("code",sort=False).groups.items():
        part=fg.get(code)
        if part is None:continue
        if part.date.duplicated().any() or not (np.isfinite(part.adj)&part.adj.gt(0)).all():raise ValueError("Invalid cumulative factors")
        ix=np.searchsorted(part.date.to_numpy(),frame.loc[idx,"date"].to_numpy(),side="right")-1
        valid=ix>=0;v=np.full(len(ix),np.nan);v[valid]=part.adj.to_numpy()[ix[valid]];frame.loc[idx,"adjustment_factor"]=v
        records.extend(part[["code","date","adj"]].itertuples(index=False,name=None))
    fh=hashlib.sha256(json.dumps(sorted(records),separators=(",",":"),allow_nan=False).encode()).hexdigest()
    names=["open","high","low","close","preclose","amount","volume_shares","up_limit","down_limit","adjustment_factor","eligible"]
    panel={k:frame.pivot(index="date",columns="code",values=k).reindex(index=meta["calendar"],columns=codes) for k in names}
    panel["eligible"]=panel["eligible"].fillna(False).astype(bool)
    receipt={"raw_rows":len(frame),"raw_identity_row_hash":h.hexdigest(),"factor_record_hash":fh,"factor_rows":len(factors),"calendar_sessions":len(meta["calendar"]),"codes":len(codes),"missing_raw_days_not_compressed":True,"amount_role":"canonical same-source CNY turnover cash, not share turnover ratio","ST_lifecycle_PIT_complete":False,"000518_execution_limit_override":"publication-bound 2026-05-20 10%; original ST entry exclusion unchanged","raw_panel_saved":False}
    if include_strategy:
        observed=set(zip(frame.code,frame.date));extra=[]
        for x in sidecars:
            if (x["code"],x["date"]) in observed:continue
            part=fg.get(x["code"]);prior=part[part.date.le(x["date"])] if part is not None else None
            factor=float(prior.adj.iloc[-1]) if prior is not None and not prior.empty else np.nan
            extra.append({"code":x["code"],"date":x["date"],"trade_status":"0","source_status":"source_explicit_suspension","suspension_confirmed":True,"eligible":False,"adjustment_factor":factor})
        for code,d in delisted.items():
            if meta["start"]<=d<=meta["end"] and (code,d) not in observed:extra.append({"code":code,"date":d,"trade_status":"delisted","eligible":False})
        if extra:frame=pd.concat([frame,pd.DataFrame(extra)],ignore_index=True)
        frame=frame.rename(columns={"code":"symbol","preclose":"prevclose","eligible":"entry_eligible",**{k:"raw_"+k for k in ["open","high","low","close"]}})
        frame["entry_eligible"]=frame.entry_eligible.fillna(False).astype(bool)
        keep=["date","symbol","raw_open","raw_high","raw_low","raw_close","prevclose","amount","volume_shares","up_limit","down_limit","adjustment_factor","trade_status","entry_eligible","source_status","suspension_confirmed"]
        for k in keep:
            if k not in frame:frame[k]=None
        return panel,receipt,frame[keep]
    return panel,receipt
