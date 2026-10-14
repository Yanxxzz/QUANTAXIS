"""Continue only unfinished accounts using exact held-status source reviews."""
from pathlib import Path
import sys,json
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.backtest_limit_reversal import read,binding,limit_price,client
from scripts.research_limit_event_factor import clean
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_event_resume import extension_panel
from panda_alpha.limit_reversal import simulate
from panda_alpha.evaluation import performance
from panda_alpha.registry import ResearchRegistry
OUT=ROOT/"research_runs/limit_reversal_optimization_20261014"
def save(name,obj):(OUT/name).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2,allow_nan=False))
if (OUT/"resumed_summary.json").exists():raise RuntimeError("Existing continuation must be collected, never replayed")
old=read(OUT/"summary.json");selection=old["selection"]
if selection["feature"]!="prior_extension_20":raise RuntimeError("This bounded continuation binds only frozen20day extension")
calendar=read(ROOT/"research_runs/limit_reversal_20261014/source_metadata.json")["calendar"]
starts=[x["source_pending"][0]["date"] for x in old["paths"] if x["status"]=="SOURCE_PENDING"]
meta=read(ROOT/"research_runs/limit_reversal_20261014/source_metadata.json");meta["start"]=calendar[max(0,calendar.index(min(starts))-23)];meta["calendar"]=[d for d in calendar if d>=meta["start"]]
cfg=read(ROOT/"config/panda-alpha.local.json")
with client(cfg) as c:meta["source_rows"]=c[meta["source_profile"]["database"]].stock_day.count_documents({"source":"stockdb","code":{"$in":meta["codes"]},"date":{"$gte":meta["start"],"$lte":meta["end"]}})
protocol={"old_results":binding(OUT/"summary.json"),"selection":binding(OUT/"training_summary.json"),"source_reviews":[binding(OUT/(c+"_status_review.json")) for c in ["600234","605178"]],"reader":binding(__file__),"engine":binding(ROOT/"panda_alpha/limit_reversal.py"),"extension_helper":binding(ROOT/"panda_alpha/limit_event_resume.py"),"registration_delta":0,"only_future_unfinished_account_sessions":True,"no_training_labels_or_completed_factor_replay":True,"ref_indices_preserved":True,"new_quote_copy":False}
save("source_repair_resume_protocol.json",protocol)
panel,receipt,bars=load_retained_panel(cfg,meta,include_strategy=True)
score=extension_panel(panel["close"],panel["adjustment_factor"])
# Recreate ONLY remaining preopen masks from the already-frozen single formula.
valid=np.isfinite(score);up=((panel["close"]-panel["up_limit"]).abs()<=1e-7)&panel["high"].le(panel["up_limit"]+1e-7)&panel["low"].ge(panel["down_limit"]-1e-7)
prospective=up.shift(2,fill_value=False)&up.shift(1,fill_value=False)&panel["eligible"]&panel["eligible"].shift(1,fill_value=False)&panel["eligible"].shift(2,fill_value=False)
points=np.argwhere((valid&prospective).to_numpy());cm={};fm={}
for i,j in points:
 key=(str(score.index[i]),str(score.columns[j]));cm[key]=True;fm[key]=bool(score.iloc[i,j]<=selection["cutoff"])
# Status-only held accounting: no announcement flag is added to predictive input.
status={"600234":["2024-04-29","2025-05-19"],"605178":["2025-07-24","2025-07-25","2025-07-28","2025-07-29"]}
extra=[]
for code,dates in status.items():
 for date in dates:
  found=bars.symbol.eq(code)&bars.date.eq(date)
  if found.any():
   bars.loc[found,"trade_status"]="suspended";bars.loc[found,"source_status"]="source_explicit_suspension";bars.loc[found,"suspension_confirmed"]=True
  else:
   part=bars[bars.symbol.eq(code)&bars.date.lt(date)].sort_values("date")
   a=float(part.adjustment_factor.dropna().iloc[-1]) if part.adjustment_factor.notna().any() else np.nan
   extra.append({"symbol":code,"date":date,"trade_status":"suspended","source_status":"source_explicit_suspension","suspension_confirmed":True,"entry_eligible":False,"adjustment_factor":a})
if extra:bars=pd.concat([bars,pd.DataFrame(extra)],ignore_index=True)
for start,end,rate in [("2024-04-30","2025-05-19",.05),("2025-05-20","2026-09-18",.10)]:
 mask=bars.symbol.eq("600234")&bars.date.ge(start)&bars.date.le(end)
 for side in ["up","down"]:bars.loc[mask,side+"_limit"]=[limit_price(v,rate,side) for v in bars.loc[mask,"prevclose"]]
save("resume_input_receipt.json",{**receipt,"status_only_rows":len(extra),"600234_execution_limit_transitions":True,"source_ST_entry_exclusion_unchanged":True,"mask_keys":len(cm),"missing_prices_or_trading_volume_filled":False})
save("resume_masks.json",{"common":[[d,c] for d,c in cm],"filtered":[[d,c] for (d,c),yes in fm.items() if yes],"cutoff":selection["cutoff"]})
curves=[];results=[];more=[];oldcurve=pd.read_csv(OUT/"daily_nav.csv",dtype={"date":str});oldtrades=pd.read_csv(OUT/"trades.csv",dtype={"symbol":str,"date":str})
for item in old["paths"]:
 key=item["path"]
 if item["status"]!="SOURCE_PENDING":results.append(item);curves.append(oldcurve.set_index("date")[[key+"_"+x for x in ["nav","cash","exposure"]]]);continue
 start=item["source_pending"][0]["date"];prefix=oldcurve[["date"]+[key+"_"+x for x in ["nav","cash","exposure"]]].dropna().rename(columns={key+"_"+x:x for x in ["nav","cash","exposure"]})
 tail=simulate(bars,calendar,policy="baseline",cost=item["cost_single_side"],evaluation_start=start,adjustment_factor_role="total_return_factor",preopen_signal_filter=fm if key.startswith("filtered") else cm,resume_state={"cash":item["terminal_cash"],"nav":item["last_completed_nav"],"holdings":item["terminal_holdings"]})
 combined=pd.concat([prefix,pd.DataFrame(tail["daily"])[["date","nav","cash","exposure"]]],ignore_index=True)
 if combined.date.duplicated().any():raise RuntimeError("Duplicate continuation date")
 result=tail["summary"];result.update(path=key,buy_fills=item["buy_fills"]+sum(t["side"]=="buy" for t in tail["trades"]),completed_round_trips=item["completed_round_trips"]+result["completed_round_trips"],total_fees=item["total_fees"]+result["total_fees"],completed_sessions=len(combined),continuation_only=True)
 result["metrics"]=performance(combined.nav/combined.nav.shift(1).fillna(100000)-1) if result["status"]=="POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" else None
 results.append(result);curves.append(combined.rename(columns={x:key+"_"+x for x in ["nav","cash","exposure"]}).set_index("date"));more.extend({"path":key,**t} for t in tail["trades"]);save(key+"_resumed_summary.json",result)
 print(json.dumps(clean({"path":key,"status":result["status"],"metrics":result["metrics"],"buys":result["buy_fills"],"pending":result["source_pending"]}),ensure_ascii=False),flush=True)
pd.concat(curves,axis=1).to_csv(OUT/"completed_daily_nav.csv",encoding="utf-8-sig");pd.concat([oldtrades,pd.DataFrame(more)],ignore_index=True).to_csv(OUT/"completed_trades.csv",index=False,encoding="utf-8-sig")
summary={**old,"paths":results,"status":"ALL_EVENT_FACTOR_AND_FOUR_STRATEGY_PATHS_COMPLETE" if all(x["metrics"] is not None for x in results) else "STRATEGY_SOURCE_PENDING","source_repair":protocol}
save("resumed_summary.json",summary)
r=ResearchRegistry(ROOT/"research_runs/research_trials.sqlite3");r.append_event("source_repair","LIMIT_EVENT_VALIDATION_HELD_STATES",protocol);r.close()
save("state.json",{"status":summary["status"],"process":None,"registry_total":summary["registry_total"],"official_compute_spent":0})
