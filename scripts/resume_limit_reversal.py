"""Resume only unfinished baseline accounts after a source-bound limit-rule repair."""
import sys,json,hashlib
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.backtest_limit_reversal import read,save,binding,load_bars,client
from panda_alpha.limit_reversal import simulate
from panda_alpha.evaluation import performance
from panda_alpha.registry import ResearchRegistry
out=ROOT/"research_runs/limit_reversal_20261014"
if (out/"resumed_summary.json").exists(): raise RuntimeError("Completed resume cannot be repeated")
cfg=read(ROOT/"config/panda-alpha.local.json");original=read(out/"source_metadata.json");meta=dict(original)
meta["start"]="2026-05-18";meta["calendar"]=[d for d in original["calendar"] if d>=meta["start"]]
with client(cfg) as c:
 meta["source_rows"]=c[meta["source_profile"]["database"]].stock_day.count_documents({"source":"stockdb","code":{"$in":meta["codes"]},"date":{"$gte":meta["start"],"$lte":meta["end"]}})
repair={"source_url":"https://static.cninfo.com.cn/finalpage/2026-05-19/1225313933.PDF","announcement":"临-2026-23","public_date":"2026-05-19","effective_date":"2026-05-20","code":"000518","verified_limit":.10,"role":"existing held claim execution only; keep conservative original source ST entry exclusion; no full ST census certification","old_outputs":binding(out/"summary.json"),"runtime":binding(ROOT/"panda_alpha/limit_reversal.py"),"resume_reader":binding(__file__),"tests":"20 passed, including exact checkpoint/full-path synthetic equality","registration_delta":0}
save(out/"source_repair_resume_protocol.json",repair)
frame,receipt=load_bars(cfg,meta)
from scripts.backtest_limit_reversal import limit_price
mask=frame.symbol.eq("000518") & frame.date.ge("2026-05-20")
frame.loc[mask,"up_limit"]=[limit_price(x,.10,"up") for x in frame.loc[mask,"prevclose"]]
frame.loc[mask,"down_limit"]=[limit_price(x,.10,"down") for x in frame.loc[mask,"prevclose"]]
save(out/"resume_input_receipt.json",{**receipt,"repair":repair,"warmup":"2026-05-18..19; preserve original full reference calendar entry indices","no_price_fill":True})
prior=read(out/"summary.json");daily=pd.read_csv(out/"daily_nav.csv",dtype={"date":str});trades=pd.read_csv(out/"trades_30bp.csv",dtype={"symbol":str,"date":str})
results=[];curves=[];extra=[]
for cost in [.003,.005]:
 key="baseline_"+str(int(cost*10000))+"bp";old=next(x for x in prior["paths"] if x["path"]==key)
 if old["status"]!="SOURCE_PENDING" or old["source_pending"]!=[{"date":"2026-05-20","reason":"held_price_unknown:000518:2026-05-20"}]: raise RuntimeError("Unexpected checkpoint")
 tail=simulate(frame,original["calendar"],policy="baseline",cost=cost,evaluation_start="2026-05-20",adjustment_factor_role="total_return_factor",resume_state={"cash":old["terminal_cash"],"nav":old["last_completed_nav"],"holdings":old["terminal_holdings"]})
 prefix=daily[["date"]+[key+"_"+x for x in ["nav","cash","exposure"]]].dropna().rename(columns={key+"_"+x:x for x in ["nav","cash","exposure"]})
 combined=pd.concat([prefix,pd.DataFrame(tail["daily"])[["date","nav","cash","exposure"]]],ignore_index=True)
 if combined.date.duplicated().any(): raise RuntimeError("Duplicate account date")
 summary=tail["summary"];summary.update(path=key,completed_sessions=len(combined),total_fees=old["total_fees"]+summary["total_fees"],completed_round_trips=old["completed_round_trips"]+summary["completed_round_trips"],buy_fills=old["buy_fills"]+sum(t["side"]=="buy" for t in tail["trades"]),continuation_only=True,original_pending_summary_preserved=True)
 returns=combined.nav/combined.nav.shift(1).fillna(100000)-1
 summary["metrics"]=performance(returns) if summary["status"]=="POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" else None
 results.append(summary);save(out/(key+"_resumed_summary.json"),summary)
 curves.append(combined.rename(columns={x:key+"_"+x for x in ["nav","cash","exposure"]}).set_index("date"))
 if cost==.003:extra.extend({"policy":"baseline",**t} for t in tail["trades"])
 print(json.dumps({"path":key,"status":summary["status"],"metrics":summary["metrics"],"pending":summary["source_pending"]}),flush=True)
for key in ["risk_guard_30bp","risk_guard_50bp"]:
 results.append(next(x for x in prior["paths"] if x["path"]==key));curves.append(daily.set_index("date")[[key+"_"+x for x in ["nav","cash","exposure"]]])
pd.concat(curves,axis=1).to_csv(out/"completed_daily_nav.csv",encoding="utf-8-sig")
pd.concat([trades,pd.DataFrame(extra)],ignore_index=True).to_csv(out/"completed_trades_30bp.csv",index=False,encoding="utf-8-sig")
save(out/"resumed_summary.json",{"status":"ALL_FOUR_COMPLETE" if all(x["metrics"] is not None for x in results) else "SOURCE_PENDING","paths":results,"repair":repair,"official_compute_spent":0,"original_source_input_and_completed_risk_paths_not_replayed":True})
r=ResearchRegistry(ROOT/"research_runs/research_trials.sqlite3");r.append_event("source_repair","LIMIT_REVERSAL_000518",repair);r.close()
