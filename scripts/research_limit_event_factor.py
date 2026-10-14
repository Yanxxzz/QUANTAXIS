"""Preregistered event-feature discovery, temporal validation and local factor."""
from pathlib import Path
import sys,json,hashlib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr,pearsonr
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_event_features import build_event_features,future_labels,FEATURES
from panda_alpha.limit_reversal import simulate
from panda_alpha.registry import ResearchRegistry,strategy_definition,trial_key
from scripts.backtest_limit_reversal import read,binding
OUT=ROOT/"research_runs/limit_reversal_optimization_20261014"
ROLE="total_return_factor"

def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(float,np.floating)):return float(value) if np.isfinite(value) else None
    if isinstance(value,np.bool_):return bool(value)
    return value

def save(name,value):
    (OUT/name).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")

def register(name,spec,start,end,cycle=5,groups=2,direction=0):
    registry=ResearchRegistry(ROOT/"research_runs/research_trials.sqlite3")
    cand={"candidate_id":name,"direction":direction,"strategy_spec":spec}
    definition=strategy_definition(cand,{"start":start,"end":end},cycle,groups)
    registry.record(cand,{"start":start,"end":end},cycle,groups)
    item={"key":trial_key(definition),"definition":definition,"candidate":cand,"registry_total":registry.total};registry.close();return item

def describe(data,feature,cut):
    valid=data[np.isfinite(data[feature])&data.label_complete].copy();low=valid[valid[feature].le(cut)];high=valid[valid[feature].gt(cut)]
    rho=float(spearmanr(valid[feature],valid.future_return_5).statistic) if len(valid)>2 and valid[feature].nunique()>1 else None
    def part(x):return {"n":len(x),"mean_return5":float(x.future_return_5.mean()) if len(x) else None,"median_return5":float(x.future_return_5.median()) if len(x) else None,"crash_mae10_fraction":float(x.future_mae_5.le(-.10).mean()) if len(x) else None,"two_down_closes_fraction":float(x.future_two_down_closes.dropna().mean()) if x.future_two_down_closes.notna().any() else None}
    low_summary,high_summary=part(low),part(high)
    delta=(low_summary["mean_return5"]-high_summary["mean_return5"]) if len(low) and len(high) else None
    blocks=valid.assign(month=valid.date.str[:7]);months=sorted(blocks.month.unique());rng=np.random.default_rng(20261014);boot=[]
    if len(months)>2 and len(low) and len(high):
        by={m:blocks[blocks.month.eq(m)] for m in months}
        for _ in range(200):
            sample=pd.concat([by[m] for m in rng.choice(months,size=len(months),replace=True)],ignore_index=True)
            a=sample[sample[feature].le(cut)];b=sample[sample[feature].gt(cut)]
            if len(a) and len(b):boot.append(float(a.future_return_5.mean()-b.future_return_5.mean()))
    return {"feature":feature,"cutoff":cut,"known_events":len(valid),"raw_pooled_spearman":rho,"direction_aligned_pooled_spearman":-rho if rho is not None else None,"low_half":low_summary,"high_half":high_summary,"low_minus_high_mean_return5":delta,"month_block_95_interval":np.quantile(boot,[.025,.975]).tolist() if boot else None,"bootstrap_caveat":"single month block, not independent stock/event samples; selection and3comparisons unadjusted","not_standard_daily_RankIC":True,"year_strata":[{"year":year,"low_n":len(group[group[feature].le(cut)]),"high_n":len(group[group[feature].gt(cut)]),"low_mean5":float(group.loc[group[feature].le(cut),"future_return_5"].mean()) if group[feature].le(cut).any() else None,"high_mean5":float(group.loc[group[feature].gt(cut),"future_return_5"].mean()) if group[feature].gt(cut).any() else None} for year,group in valid.groupby(valid.date.str[:4])]}

def add_down_labels(data,panel,calendar):
    positions={d:i for i,d in enumerate(calendar)};labels=[]
    for row in data.itertuples(index=False):
        t=positions[row.date]
        if t+5>=len(calendar):labels.append(np.nan);continue
        close=panel["close"][row.symbol].iloc[t+1:t+6].to_numpy();lower=panel["down_limit"][row.symbol].iloc[t+1:t+6].to_numpy()
        if not (np.isfinite(close)&np.isfinite(lower)&(close>0)&(lower>0)).all():labels.append(np.nan);continue
        down=np.abs(close-lower)<=1e-7;labels.append(bool((down[:-1]&down[1:]).any()))
    data["future_two_down_closes"]=labels;return data

def run():
    if (OUT/"summary.json").exists():raise RuntimeError("Complete study exists; no duplicate labels or paths")
    meta=read(ROOT/"research_runs/limit_reversal_20261014/source_metadata.json");calendar=meta["calendar"]
    bindings={k:binding(p) for k,p in {"source":ROOT/"research_runs/limit_reversal_20261014/source_metadata.json","mechanism":OUT/"mechanism_review.json","features":ROOT/"panda_alpha/limit_event_features.py","source_reader":ROOT/"panda_alpha/limit_event_source.py","execution":ROOT/"panda_alpha/limit_reversal.py","factor":ROOT/"panda_alpha/limit_event_factor.py","feature_tests":ROOT/"tests/test_limit_event_features.py","factor_tests":ROOT/"tests/test_limit_event_factor.py","runner":__file__}.items()}
    proto={"bindings":bindings,"discovery_window":{"start":meta["start"],"end":"2023-12-29"},"validation_window":{"start":"2024-01-02","end":meta["end"]},"future_horizon_reference_sessions":5,"features":list(FEATURES),"all_fixed_directions":0,"split":"temporal validation in previously exposed historical window; not untouched OOS","cutoff":"single training median per feature, no threshold search","selection":"greatest positive train lower-half minus upper-half5day return, raw rho<0, min60events and each side>=10; no supportive direction uses primary extension hypothesis with no-support label","label_purge":"training event+5 must end within2023; validation labels unavailable until selection protocol registered","registration_before_labels":True,"official_compute_spent":0}
    regs=[]
    for feature in FEATURES:regs.append(register("LIMIT_EVENT_DISCOVERY_"+feature.upper()+"_20261014",{"feature":feature,"source_hash":bindings["source"]["sha256"],"feature_runtime":bindings["features"]["sha256"],"event":"previous2close at actual upperlimit then potential lowerlimit touch","outcomes":"bidcost t to close t+5; future1..5MAE and two consecutive lowerlimit closes","candidate_direction":0},meta["start"],"2023-12-29"))
    proto["registrations"]=regs;save("protocol.json",proto);print("Three descriptors frozen and registered before labels",flush=True)
    cfg=read(ROOT/"config/panda-alpha.local.json");panel,receipt,bars=load_retained_panel(cfg,meta,include_strategy=True)
    old=read(ROOT/"research_runs/limit_reversal_20261014/actual_input_receipt.json")
    if any(receipt[k]!=old[k] for k in ["raw_identity_row_hash","factor_record_hash"]):raise RuntimeError("Original raw/factor source bindings changed")
    save("source_receipt.json",receipt);print("Source contract verified; building preopen descriptors",flush=True)
    events=build_event_features(bars,calendar,adjustment_factor_role=ROLE)
    touch=events[events.touch_event.eq(True)].copy();positions={d:i for i,d in enumerate(calendar)}
    train=touch[touch.date.le("2023-12-29")&touch.date.map(lambda d:positions[d]+5<len(calendar) and calendar[positions[d]+5]<= "2023-12-29")].copy()
    train=future_labels(bars,calendar,train,horizon=5,adjustment_factor_role=ROLE);train=add_down_labels(train,panel,calendar)
    descriptions=[];cutoffs={}
    for feature in FEATURES:
        valid=train[np.isfinite(train[feature])];cut=float(valid[feature].median()) if len(valid) else np.nan;cutoffs[feature]=cut;descriptions.append(describe(train,feature,cut))
    good=[x for x in descriptions if x["known_events"]>=60 and x["low_half"]["n"]>=10 and x["high_half"]["n"]>=10 and x["low_minus_high_mean_return5"] is not None and x["low_minus_high_mean_return5"]>0 and x["raw_pooled_spearman"] is not None and x["raw_pooled_spearman"]<0]
    chosen=max(good,key=lambda x:x["low_minus_high_mean_return5"])["feature"] if good else "prior_extension_20"
    cut=cutoffs[chosen]
    if not np.isfinite(cut):raise RuntimeError("Chosen source missing; no substitute thresholds")
    selection={"feature":chosen,"cutoff":cut,"trained_only_until":"2023-12-29","training_support":bool(good),"all_three_training_results":descriptions,"validation_prices_not_yet_evaluated":True,"no_sign_flip":True,"max_new_filter":1}
    save("training_summary.json",selection);print(json.dumps(clean({"selected_feature":chosen,"cutoff":cut,"supported":bool(good),"train":descriptions}),ensure_ascii=False),flush=True)
    window_start="2024-01-02";window_end=meta["end"]
    common={"feature":chosen,"cutoff":cut,"training_summary_sha":binding(OUT/"training_summary.json")["sha256"],"source":bindings["source"]["sha256"],"known_feature_common_support":True,"entry":"preopen two boards then lowerlimit bid; actualtouch only execution","exit":"strict10%profit only; same T+1/max5slots/20%/1%volume guard/cost30&50","initial_cash":100000}
    control=register("LIMIT_EVENT_VALIDATION_COMMON_CONTROL_20261014",{**common,"filter":"featurefinite only"},window_start,window_end,direction=1)
    target=register("LIMIT_EVENT_VALIDATION_LOW_RISK_FILTER_20261014",{**common,"filter":"featurefinite and feature<=trainmedian"},window_start,window_end,direction=1)
    factor=register("LIMIT_EVENT_FACTOR_CLOSE_FORMED_20261014",{"source":bindings["source"]["sha256"],"feature":chosen,"value":"-preopen feature only where today lowerlimit touch confirmed; NaN elsewhere","formation":"t close, tradable t+1 open","label":"t+1open to t+5close fixed5 sessions; require raw/factor allfuturebars","groups":2,"minimum_cohort_per_date":4,"missing_not_zero":True},window_start,window_end,direction=1)
    save("validation_protocol.json",{"selection":selection,"registrations":[control,target,factor],"fixed_cutoff_before_validation_labels":True,"factor_clock_differs_from_original_bid":True})
    test=touch[touch.date.ge(window_start)].copy();test=future_labels(bars,calendar,test,horizon=5,adjustment_factor_role=ROLE);test=add_down_labels(test,panel,calendar);diag=describe(test,chosen,cut);save("validation_event_summary.json",diag)
    common_mask={(r.date,r.symbol):bool(np.isfinite(getattr(r,chosen))) for r in events.itertuples(index=False)}
    selected_mask={(r.date,r.symbol):bool(np.isfinite(getattr(r,chosen)) and getattr(r,chosen)<=cut) for r in events.itertuples(index=False)}
    paths=[];curves=[];trades=[]
    for policy,mask in [("common_control",common_mask),("filtered",selected_mask)]:
        for cost in [.003,.005]:
            key=policy+"_"+str(int(cost*10000))+"bp"
            result=simulate(bars,calendar,policy="baseline",cost=cost,evaluation_start=window_start,adjustment_factor_role=ROLE,preopen_signal_filter=mask)
            item=result["summary"];item.update(path=key,buy_fills=sum(t["side"]=="buy" for t in result["trades"]));paths.append(item);save(key+"_summary.json",item)
            curves.append(pd.DataFrame(result["daily"])[["date","nav","cash","exposure"]].rename(columns={x:key+"_"+x for x in ["nav","cash","exposure"]}).set_index("date"));trades.extend({"path":key,**t} for t in result["trades"])
            print(json.dumps(clean({"path":key,"status":item["status"],"metrics":item["metrics"],"buys":item["buy_fills"],"pending":item["source_pending"]}),ensure_ascii=False),flush=True)
    pd.concat(curves,axis=1).to_csv(OUT/"daily_nav.csv",encoding="utf-8-sig");pd.DataFrame(trades).to_csv(OUT/"trades.csv",index=False,encoding="utf-8-sig")
    from panda_alpha.limit_event_factor import LimitEventFactor
    indexed=bars.set_index(["date","symbol"]);inputs={k:indexed[k] for k in ["raw_close","raw_high","raw_low","up_limit","down_limit","amount","adjustment_factor","entry_eligible"]};inputs["eligible"]=inputs.pop("entry_eligible")
    model=LimitEventFactor(chosen);values=model.calculate(inputs,calendar,adjustment_factor_role=ROLE);coverage=model.coverage_counts()
    factortest=test[np.isfinite(test[chosen])].copy();labels=[]
    for row in factortest.itertuples(index=False):
        t=positions[row.date];vals=[]
        if t+5>=len(calendar):labels.append(np.nan);continue
        for name,ix in [("open",t+1),("adjustment_factor",t+1),("close",t+5),("adjustment_factor",t+5)]:vals.append(panel[name][row.symbol].iloc[ix])
        labels.append(float(vals[2]*vals[3]/(vals[0]*vals[1])-1) if all(np.isfinite(v) and v>0 for v in vals) and row.label_complete else np.nan)
    factortest["next_open_return5"]=labels;factortest["value"]=-factortest[chosen]
    ic=[];rank=[];sample=[];aligned=set(calendar[calendar.index(window_start)::5])
    for date,cohort in factortest.groupby("date"):
        data=cohort[np.isfinite(cohort.next_open_return5)]
        if date in aligned and len(data)>=4 and data.value.nunique()>1 and data.next_open_return5.nunique()>1:
            rank.append(float(spearmanr(data.value,data.next_open_return5).statistic));ic.append(float(pearsonr(data.value,data.next_open_return5).statistic));sample.append({"date":date,"n":len(data)})
    valid=factortest[np.isfinite(factortest.next_open_return5)];pooled=float(spearmanr(valid.value,valid.next_open_return5).statistic) if len(valid)>2 and valid.value.nunique()>1 else None
    factor_summary={"feature":chosen,"direction":1,"cycle":5,"groups":2,"formation":"tclose->t+1open","valid_event_labels":len(valid),"mean_next_open_return5":float(valid.next_open_return5.mean()) if len(valid) else None,"coverage":coverage,"aligned_valid_IC_periods":len(ic),"RankIC":float(np.mean(rank)) if rank else None,"IC_mean":float(np.mean(ic)) if ic else None,"ICIR":float(np.mean(ic)/np.std(ic,ddof=1)) if len(ic)>1 and np.std(ic,ddof=1)>0 else None,"pooled_event_Spearman_not_dailyRankIC":pooled,"period_counts":sample,"native_default_price_basis_or_inputs_verified":False,"formal_10_groups_or_pool_eligible":False,"unconditional_factor_values_not_zero_filled":True}
    save("factor_summary.json",factor_summary)
    registry=ResearchRegistry(ROOT/"research_runs/research_trials.sqlite3");total=registry.total;registry.close()
    summary={"status":"COMPLETED_LOCAL_EVENT_RESEARCH","raw_preopen_candidates":len(events),"potential_touch_events":len(touch),"training_events":len(train),"validation_events":len(test),"selection":selection,"validation_event_diagnostic":diag,"paths":paths,"factor":factor_summary,"registry_total":total,"official_compute_spent":0,"formal_candidate":False,"not_untouched_OOS":True}
    save("summary.json",summary);save("state.json",{"status":summary["status"],"process":None,"registry_total":total,"official_compute_spent":0});print(json.dumps(clean({"factor":factor_summary,"total":total}),ensure_ascii=False),flush=True)

if __name__=="__main__":run()
