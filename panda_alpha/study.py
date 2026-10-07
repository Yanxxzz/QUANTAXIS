"""Portable, hash-bound factor studies with potential daily-bar executions.

Source preparation, executable wealth accounting and research quality are
separate stages. Nothing here dispatches platforms, chooses parameter grids,
certifies auction fills or approves formal admission.
"""
from __future__ import annotations
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .attribution import Trade, account_day, summarize
from .evaluation import performance


class StudyInputError(ValueError):
    pass


class StudyExecutionError(ValueError):
    pass


def _clean(value):
    if isinstance(value, dict):return {str(k): _clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_clean(v) for v in value]
    if isinstance(value,(np.bool_,)):return bool(value)
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(float,np.floating)):return float(value) if math.isfinite(value) else None
    return value


def object_hash(value):
    return hashlib.sha256(json.dumps(_clean(value),ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def normalize_source_panel(panel):
    """Accept explicit long sources or FormulaEvaluator's date-by-symbol values.

    Finite formula outputs are usable within supplied inputs. This is not
    independent publication-time certification of a vendor financial snapshot.
    """
    if isinstance(panel,pd.Series):
        if isinstance(panel.index,pd.MultiIndex):
            if panel.index.nlevels!=2 or set(panel.index.names)!={"date","symbol"}:
                raise StudyInputError("FactorSeries requires named date/symbol index levels")
            frame=panel.rename("value").reset_index()[["date","symbol","value"]]
        else:raise StudyInputError("A factor series requires date/symbol MultiIndex")
    elif isinstance(panel,pd.DataFrame):
        frame=panel.copy()
        if not {"date","symbol","value"}.issubset(frame):
            if isinstance(frame.index,pd.MultiIndex) and "value" in frame:
                frame=frame.reset_index()
            else:
                frame=frame.rename_axis(index="date",columns="symbol").reset_index().melt(id_vars="date",var_name="symbol",value_name="value")
    else:raise StudyInputError("Factor panel must be a DataFrame or Series")
    if not {"date","symbol","value"}.issubset(frame):raise StudyInputError("Missing factor date/symbol/value")
    frame["date"]=pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
    frame["symbol"]=frame.symbol.astype(str)
    frame["value"]=pd.to_numeric(frame.value,errors="coerce")
    if frame.duplicated(["date","symbol"]).any():raise StudyInputError("Duplicate source stock-date")
    if "pit_usable" not in frame:frame["pit_usable"]=np.isfinite(frame.value)
    elif not frame.pit_usable.map(lambda v:isinstance(v,(bool,np.bool_))).all():raise StudyInputError("PIT flags must be explicit booleans")
    if "source_status" not in frame:frame["source_status"]=np.where(frame.pit_usable,"formula_inputs_finite","formula_inputs_pending")
    return frame.sort_values(["date","symbol"]).reset_index(drop=True)


def _quotes_frame(quotes):
    frame=quotes.copy()
    if "date" not in frame and "day" in frame:frame=frame.rename(columns={"day":"date"})
    if not {"date","symbol"}.issubset(frame):raise StudyInputError("Quotes require date/symbol")
    frame["date"]=pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
    frame["symbol"]=frame.symbol.astype(str)
    if frame.duplicated(["date","symbol"]).any():raise StudyInputError("Duplicate quote stock-date")
    return frame.sort_values(["date","symbol"]).reset_index(drop=True)


def frame_hash(frame):
    columns=sorted(frame.columns)
    return object_hash({"columns":columns,"rows":frame[columns].to_dict("records")})


def _protocol(protocol):
    p=dict(protocol);window=dict(p.get("window",{}))
    candidate=str(p.get("candidate_id",p.get("name","")))
    if not candidate:raise StudyInputError("Explicit candidate_id required")
    cycle=int(p.get("cycle",window.get("rebalance_days",5)))
    groups=int(p.get("groups",window.get("groups",10)))
    minimum=int(p.get("min_assets",p.get("minimum_assets",30)))
    direction=int(p.get("direction",1));costs=list(p.get("costs",[.003,.005]))
    if not 1<=cycle<=10 or not 2<=groups<=10 or minimum<groups or direction not in (0,1):raise StudyInputError("Invalid cycle/groups/min_assets/direction")
    if len(costs)!=2 or any(not math.isfinite(float(c)) or not 0<=float(c)<1 for c in costs) or costs[0]>=costs[1]:raise StudyInputError("Two ordered finite one-way costs required")
    warmup=int(p.get("warmup_sessions",0))
    if warmup<0:raise StudyInputError("Nonnegative warmup required")
    comparator_direction=int(p.get("comparator_direction",0))
    if comparator_direction not in (0,1):raise StudyInputError("Invalid comparator direction")
    return {**p,"candidate_id":candidate,"cycle":cycle,"groups":groups,"min_assets":minimum,"direction":direction,
            "costs":[float(c) for c in costs],"warmup_sessions":warmup,"window":window,
            "comparator_direction":comparator_direction,"comparator_id":str(p.get("comparator_id","COMPARATOR"))}


def quantile_groups(rows,groups,minimum):
    empty={str(g):[] for g in range(1,groups+1)}
    if len(rows)<minimum:return empty,"insufficient_common_support"
    bins=pd.qcut(rows.value,groups,labels=False,duplicates="drop")
    if int(bins.dropna().nunique())!=groups:return empty,"insufficient_distinct_quantile_groups"
    return {str(g+1):sorted(rows.loc[bins.eq(g),"symbol"].tolist()) for g in range(groups)},"groups_available"


def _inputs(protocol,candidate,quotes,comparator,receipt):
    return {"protocol":object_hash(protocol),"candidate_panel":frame_hash(candidate),"quotes":frame_hash(quotes),
            "comparator_panel":frame_hash(comparator) if comparator is not None else None,"source_receipt":object_hash(receipt) if receipt is not None else None,
            "study_engine":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),**_core_hashes()}


def _core_hashes():
    package=Path(__file__).parent
    return {"attribution_core":hashlib.sha256((package/"attribution.py").read_bytes()).hexdigest(),
            "performance_core":hashlib.sha256((package/"evaluation.py").read_bytes()).hexdigest()}


def _calendar(protocol,quotes,receipt):
    """Use an independently supplied session list, never certify observations."""
    receipt=receipt or {}
    values=protocol.get("calendar")
    verified=protocol.get("calendar_verified") is True
    origin="protocol"
    if values is None:
        values=receipt.get("calendar")
        verified=receipt.get("calendar_verified") is True
        origin="source_receipt"
    if values is None:
        return sorted(quotes.date.unique()),False,"observed_quotes_unverified"
    if not isinstance(values,(list,tuple)) or not values:
        raise StudyInputError("Independent calendar requires an ordered session list")
    sessions=pd.to_datetime(list(values)).strftime("%Y-%m-%d").tolist()
    if sessions!=sorted(set(sessions)):
        raise StudyInputError("Independent calendar sessions must be unique and ordered")
    return sessions,verified,origin


def prepare_study(protocol,candidate_panel,quotes,comparator_panel=None,*,source_receipt=None):
    p=_protocol(protocol);candidate=normalize_source_panel(candidate_panel);q=_quotes_frame(quotes)
    comparator=normalize_source_panel(comparator_panel) if comparator_panel is not None else None
    if comparator is not None and p["comparator_id"]==p["candidate_id"]:raise StudyInputError("Distinct candidate/comparator identities required")
    calendar,calendar_verified,calendar_origin=_calendar(p,q,source_receipt);window=p["window"]
    if len(calendar)<p["cycle"]+2:raise StudyInputError("Calendar lacks full next-open holding period")
    start=str(window.get("decisions_start",p.get("start",calendar[p["warmup_sessions"]] if p["warmup_sessions"]<len(calendar) else calendar[-1])))[:10]
    end=str(window.get("decisions_end",p.get("end",calendar[-1])))[:10]
    terminal=str(window.get("holding_end",p.get("holding_end",calendar[-1])))[:10]
    positions={d:i for i,d in enumerate(calendar)}
    requested=[d for d in calendar if d>=start and d<=end and positions[d]>=p["warmup_sessions"]]
    anchors=requested[::p["cycle"]]
    anchors=[d for d in anchors if positions[d]+p["cycle"]+1<len(calendar) and calendar[positions[d]+p["cycle"]+1]<=terminal]
    if not anchors:raise StudyInputError("No full formation/entry/holding anchor")
    missing_sessions=[d for d in calendar if start<=d<=terminal and d not in set(q.date)]
    targets=[];coverage=[];factor_dependence=[]
    for day in anchors:
        rows=candidate[candidate.date.eq(day)].copy()
        usable=rows.pit_usable & np.isfinite(rows.value)
        denominator=len(rows)
        if comparator is not None:
            other=comparator[comparator.date.eq(day)][["symbol","value","pit_usable"]].rename(columns={"value":"comparator_value","pit_usable":"comparator_usable"})
            rows=rows.merge(other,on="symbol",how="left",validate="one_to_one")
            usable=rows.pit_usable & np.isfinite(rows.value) & rows.comparator_usable.eq(True) & np.isfinite(rows.comparator_value)
        common=rows[usable].copy()
        candidate_groups,candidate_status=quantile_groups(common,p["groups"],p["min_assets"])
        if comparator is not None:
            benchmark_groups,benchmark_status=quantile_groups(common[["symbol","comparator_value"]].rename(columns={"comparator_value":"value"}),p["groups"],p["min_assets"])
            rho=(common.value*(1 if p["direction"] else -1)).corr(common.comparator_value*(1 if p["comparator_direction"] else -1),method="spearman") if len(common)>2 and common.value.nunique()>1 and common.comparator_value.nunique()>1 else np.nan
            factor_dependence.append({"date":day,"signed_spearman":rho,"absolute_spearman":abs(rho)})
        else:benchmark_groups,benchmark_status={},"comparator_not_supplied"
        at=positions[day]
        targets.append({"decision_date":day,"entry_date":calendar[at+1],"planned_exit_date":calendar[at+p["cycle"]+1],
                        "candidate_groups":candidate_groups,"comparator_groups":benchmark_groups,"candidate_group_status":candidate_status,"comparator_group_status":benchmark_status,"common_codes":sorted(common.symbol.tolist())})
        coverage.append({"decision_date":day,"source_denominator":denominator,"candidate_source_finite":int((candidate[candidate.date.eq(day)].pit_usable & np.isfinite(candidate[candidate.date.eq(day)].value)).sum()),"common_assets":len(common),"candidate_group_status":candidate_status,"comparator_group_status":benchmark_status})
    candidate_ready=sum(t["candidate_group_status"]=="groups_available" for t in targets)
    comparator_ready=sum(t["comparator_group_status"]=="groups_available" for t in targets) if comparator is not None else None
    all_anchors_ready=candidate_ready==len(targets) and (comparator is None or comparator_ready==len(targets))
    calendar_ready=calendar_verified and not missing_sessions
    status=("source_ready" if all_anchors_ready else "source_partial") if calendar_ready and candidate_ready else "source_pending"
    source_coverage={"all_anchors_ready":all_anchors_ready,"anchor_count":len(targets),"candidate_ready_anchors":candidate_ready,
                     "comparator_ready_anchors":comparator_ready,"calendar_verified":calendar_verified,
                     "calendar_origin":calendar_origin,"missing_whole_sessions":missing_sessions,
                     "research_source_complete":all_anchors_ready and calendar_ready}
    result={"schema_version":1,"status":status,
            "protocol":p,"input_hashes":_inputs(p,candidate,q,comparator,source_receipt),"calendar":calendar,"anchors":anchors,"holding_start":targets[0]["entry_date"],"holding_end":terminal,
            "targets":targets,"coverage":coverage,"source_coverage":source_coverage,"factor_dependence":_clean(factor_dependence),"comparator_present":comparator is not None,
            "no_forward_outcomes_used_to_select_targets":True,"ties_not_split_by_symbol":True,
            "pending_dates_retained_as_cash":True,"pair_rule":"parallel half initialcapital independently traded legs; no crossnetting or capital rebalance"}
    result=_clean(result);result["preparation_sha256"]=object_hash(result);return result


def validate_study(preparation,protocol,candidate_panel,quotes,comparator_panel=None,*,source_receipt=None):
    issues=[]
    own={k:v for k,v in preparation.items() if k!="preparation_sha256"}
    if object_hash(own)!=preparation.get("preparation_sha256"):issues.append("preparation_content_hash_mismatch")
    try:
        expected=prepare_study(protocol,candidate_panel,quotes,comparator_panel,source_receipt=source_receipt)
        if expected["preparation_sha256"]!=preparation.get("preparation_sha256"):issues.append("frozen_inputs_or_targets_changed")
    except (ValueError,TypeError,KeyError,IndexError) as error:issues.append(str(error))
    return {"verified":not issues,"status":"validated" if not issues else "invalid_frozen_preparation","issues":issues,"preparation_sha256":preparation.get("preparation_sha256")}


def _number(row,field):
    try:value=float(row[field])
    except (KeyError,TypeError,ValueError):return None
    return value if math.isfinite(value) else None


class PotentialQuoteBook:
    def __init__(self,frame):
        self.rows={(r["symbol"],r["date"]):r for r in frame.to_dict("records")};self.marks=defaultdict(list)
        self.risk_rows=defaultdict(list);self.risk_cache={}
        for (code,day),row in self.rows.items():
            close=_number(row,"close")
            if close is not None and close>0:self.marks[code].append((day,close))
            if "beta" in row or "sector" in row:self.risk_rows[code].append((day,row))
        for marks in self.marks.values():marks.sort()
        self.dates={c:[d for d,_ in marks] for c,marks in self.marks.items()}

    def suspended(self,row):
        return row is not None and (str(row.get("trade_status",row.get("tradestatus"))) in {"0","0.0"} or row.get("source_status")=="source_explicit_suspension")

    def price(self,code,day,field):
        row=self.rows.get((code,day));price=_number(row,field) if row is not None else None
        if price is not None and price>0:return price,False
        if not self.suspended(row):raise StudyExecutionError(f"unknown_quote:{code}:{day}:{field}")
        at=bisect_left(self.dates.get(code,[]),day)-1
        if at<0:raise StudyExecutionError(f"suspension_has_no_previous_mark:{code}:{day}")
        return self.marks[code][at][1],True

    def state(self,code,day,side):
        row=self.rows.get((code,day))
        if self.suspended(row):return "blocked_suspension"
        if row is None:return "unknown_quote"
        trade=row.get("trade_status",row.get("tradestatus"))
        if str(trade) not in {"1","1.0"}:return "unknown_trading_status"
        raw=[_number(row,"raw_"+x) for x in ["open","high","low","close"]]
        if any(v is None or v<=0 for v in raw):return "unknown_raw_ohlc"
        op,hi,lo,cl=raw
        if not lo<=min(op,cl)<=max(op,cl)<=hi:return "invalid_raw_geometry"
        volume,amount=_number(row,"volume"),_number(row,"amount")
        if volume is None or amount is None:return "unknown_traded_flow"
        if volume<=0 or amount<=0:return "blocked_zero_flow"
        if abs(hi-lo)<=1e-10:
            at=bisect_left(self.dates.get(code,[]),day)-1
            previous=self.marks[code][at][1] if at>=0 else None
            adjusted=_number(row,"close")
            if side=="sell" and previous is not None and adjusted is not None and adjusted>previous:return "potential_fill"
            return "blocked_one_price"
        return "potential_fill"

    def risk_fact(self,code,day,field):
        """Declared PIT metadata for close-wealth diagnostics, never trade choice."""
        key=(code,day,field)
        if key in self.risk_cache:return self.risk_cache[key]
        candidates=[]
        for observed,row in self.risk_rows.get(code,[]):
            if observed>day:continue
            pit=row.get(field+"_pit_usable",row.get("risk_metadata_pit_usable"))
            if not isinstance(pit,(bool,np.bool_)):continue
            asof=row.get(field+"_asof",row.get(field+"_asof_date"))
            if asof is None:continue
            try:asof=pd.Timestamp(asof).strftime("%Y-%m-%d")
            except (ValueError,TypeError):continue
            if asof>day:continue
            value=row.get(field)
            if field=="beta":value=_number(row,"beta")
            elif not isinstance(value,str) or not value.strip() or value.strip().upper()=="UNKNOWN":value=None
            else:value=value.strip()
            # An explicitly unknown newer snapshot supersedes an older value.
            # Ignoring it would certify stale sector/beta metadata as current.
            candidates.append((asof,observed,value if pit else None))
        if not candidates:result=(None,None)
        else:
            asof,_,value=max(candidates,key=lambda x:(x[0],x[1]));result=(value,asof)
        self.risk_cache[key]=result
        return result


def _exposure_day(holdings,closes,cash,nav,quotes,day):
    positions=[];sectors=defaultdict(float);known_beta=0.;unknown_beta=0.;unknown_sector=0.
    for code,quantity in sorted(holdings.items()):
        weight=quantity*closes[code]/nav
        sector,sector_asof=quotes.risk_fact(code,day,"sector")
        beta,beta_asof=quotes.risk_fact(code,day,"beta")
        if sector is None:unknown_sector+=weight
        else:sectors[sector]+=weight
        if beta is None:unknown_beta+=weight
        else:known_beta+=weight*beta
        positions.append({"symbol":code,"quantity":quantity,"close_mark":closes[code],"wealth_fraction":weight,
                          "preknown_sector":sector,"sector_asof":sector_asof,"preknown_beta":beta,"beta_asof":beta_asof})
    status="cash_only" if not holdings else ("risk_metadata_pending" if unknown_beta>1e-12 or unknown_sector>1e-12 else "supplied_risk_metadata_available")
    return {"date":day,"status":status,"positions":positions,"cash_fraction":cash/nav,
            "known_beta_component":known_beta,"beta_exposure":known_beta if unknown_beta<=1e-12 else None,
            "unknown_beta_wealth_fraction":unknown_beta,"unknown_sector_wealth_fraction":unknown_sector,
            "known_sector_wealth":dict(sectors),"maximum_industry_wealth_fraction":max(sectors.values(),default=0.) if unknown_sector<=1e-12 else None,
            "source_scope":"explicit asof and declared PIT metadata for close-wealth diagnostics; not selection, exchange fills or full-market certification"}


def replay_portfolio(name,schedule,days,quotes,cost):
    """Continuous quantities and cash, exact fee accounting, conservative fills."""
    cash,holdings,previous_nav,previous_marks=1.,{},1.,{}
    daily=[];orders=[];executions=[];turnover=[];blocked=Counter();exposure_days=[]
    for day in days:
        before=dict(holdings);cash0=cash
        opens={c:quotes.price(c,day,"open")[0] for c in holdings}
        equity_open=cash+sum(q*opens[c] for c,q in holdings.items());trades=[];traded=0.
        if day in schedule:
            event=schedule[day];selected=set(event["codes"]);goal=equity_open/len(selected) if selected else 0.
            for code in sorted(list(holdings)):
                excess=max(0.,holdings[code]*opens[code]-(goal if code in selected else 0.))
                if excess<=1e-12:continue
                state=quotes.state(code,day,"sell");orders.append({"date":day,"symbol":code,"side":"sell","state":state,"requested_value":excess})
                if state!="potential_fill":
                    if not state.startswith("blocked_"):raise StudyExecutionError(f"{state}:{code}:{day}:sell")
                    blocked["sell_"+state]+=1;continue
                qty=min(holdings[code],excess/opens[code]);value=qty*opens[code]
                holdings[code]-=qty;cash+=value*(1-cost);trades.append(Trade(code,-qty,opens[code],value*cost));traded+=value
                if holdings[code]<=1e-14:del holdings[code]
            deficits={}
            for code in sorted(selected):
                deficit=max(0.,goal-holdings.get(code,0.)*opens.get(code,0.))
                if deficit<=1e-12:continue
                state=quotes.state(code,day,"buy")
                if state!="potential_fill":
                    orders.append({"date":day,"symbol":code,"side":"buy","state":state,"requested_value":deficit})
                    if not state.startswith("blocked_"):raise StudyExecutionError(f"{state}:{code}:{day}:buy")
                    blocked["buy_"+state]+=1;continue
                opens[code]=quotes.price(code,day,"open")[0];deficits[code]=deficit
            demand=sum(deficits.values());scale=min(1.,max(0.,cash)/((1+cost)*demand)) if demand else 0.
            for code,deficit in deficits.items():
                value=deficit*scale
                if value<=1e-12:continue
                qty=value/opens[code];holdings[code]=holdings.get(code,0.)+qty;cash-=value*(1+cost)
                trades.append(Trade(code,qty,opens[code],value*cost));traded+=value
                orders.append({"date":day,"symbol":code,"side":"buy","state":"potential_fill","requested_value":deficit,"filled_value":value})
            turnover.append({"date":day,"two_side_traded_fraction":traded/equity_open,"one_side_turnover_proxy":traded/(2*equity_open)})
        if day==days[-1]:
            for code in sorted(list(holdings)):
                state=quotes.state(code,day,"sell");orders.append({"date":day,"symbol":code,"side":"sell","state":state,"final_liquidation":True})
                if state!="potential_fill":
                    if not state.startswith("blocked_"):raise StudyExecutionError(f"{state}:{code}:{day}:final_sell")
                    blocked["final_sell_"+state]+=1;continue
                qty=holdings.pop(code);value=qty*opens[code];cash+=value*(1-cost);trades.append(Trade(code,-qty,opens[code],value*cost))
        if cash<-1e-10:raise StudyExecutionError("negative_cash")
        cash=max(0.,cash);codes=set(before)|set(holdings)|{t.code for t in trades}
        for code in codes:opens.setdefault(code,quotes.price(code,day,"open")[0])
        closes={c:quotes.price(c,day,"close")[0] for c in codes}
        day_result=account_day(day=day,previous_nav=previous_nav,cash_before=cash0,quantities_before=before,previous_close=previous_marks,open_marks=opens,close_marks=closes,trades=trades,cash_after=cash)
        day_result["known_suspension_mark"]=any(quotes.price(c,day,"close")[1] for c in holdings)
        exposure_days.append(_exposure_day(holdings,closes,cash,day_result["nav"],quotes,day))
        daily.append(day_result);executions.extend({"date":day,**asdict(t)} for t in trades)
        previous_nav=day_result["nav"];previous_marks={c:closes[c] for c in holdings}
    summary=summarize(daily,folds=min(5,len(daily)))
    metrics=performance(pd.Series([d["net_return"] for d in daily]))
    risk_pending=any(d["status"]=="risk_metadata_pending" for d in exposure_days)
    exposure_summary={"mean_cash_fraction":float(np.mean([d["cash_fraction"] for d in exposure_days])),
                      "mean_unknown_beta_wealth_fraction":float(np.mean([d["unknown_beta_wealth_fraction"] for d in exposure_days])),
                      "mean_unknown_sector_wealth_fraction":float(np.mean([d["unknown_sector_wealth_fraction"] for d in exposure_days])),
                      "mean_beta_exposure":None if any(d["beta_exposure"] is None for d in exposure_days) else float(np.mean([d["beta_exposure"] for d in exposure_days])),
                      "maximum_industry_wealth_fraction":None if any(d["maximum_industry_wealth_fraction"] is None for d in exposure_days) else max(d["maximum_industry_wealth_fraction"] for d in exposure_days),
                      "total_fees_initial_equity_units":summary["fees"],"mean_one_side_turnover_proxy":float(np.mean([d["one_side_turnover_proxy"] for d in turnover])) if turnover else 0.}
    return _clean({"status":"potential_wealth_evaluated","name":name,"cost":cost,"metrics":metrics,"accounting":summary,
                   "daily":[{k:v for k,v in d.items() if k not in {"security_rows"}} for d in daily],
                   "orders":orders,"trades":executions,"turnover":turnover,"blocked_orders":dict(blocked),"final_open_positions":holdings,
                   "terminal_liquidation_pending":bool(holdings),
                   "exposures":{"status":"risk_metadata_pending" if risk_pending else "supplied_risk_metadata_available","summary":exposure_summary,"daily":exposure_days},
                   "auction_fills_or_capacity_certified":False,"wealth_unit_model_not_actual_account_nav":True})


def _series(run,key="net_return"):
    return pd.Series([d[key] for d in run["daily"]],index=[d["date"] for d in run["daily"]],dtype=float)


def evaluate_study(preparation,protocol,candidate_panel,quotes,comparator_panel=None,*,source_receipt=None,quality_callback=None):
    check=validate_study(preparation,protocol,candidate_panel,quotes,comparator_panel,source_receipt=source_receipt)
    if not check["verified"]:raise StudyInputError("Frozen validation failed: "+", ".join(check["issues"]))
    if preparation["status"]=="source_pending":return {"status":"source_pending","validation":check,"coverage":preparation["coverage"],"source_coverage":preparation["source_coverage"],"quality":{"research_state":"source_pending","factor_economically_rejected":False,"source_coverage":preparation["source_coverage"],"formal_admission":{"eligible":False,"status":"independent_admission_review_pending"}},"runs":[],"comparisons":[],"cost_reviews":[],"errors":[]}
    p=preparation["protocol"];q=PotentialQuoteBook(_quotes_frame(quotes));targets=preparation["targets"]
    days=[d for d in preparation["calendar"] if preparation["holding_start"]<=d<=preparation["holding_end"]]
    runs=[];lookup={};errors=[];reviews=[]
    names=[(p["candidate_id"],"candidate_groups",p["direction"])]
    if preparation["comparator_present"]:names.append((p["comparator_id"],"comparator_groups",p["comparator_direction"]))
    for cost in p["costs"]:
        for label,group_key,direction in names:
            for group in range(1,p["groups"]+1):
                name=f"{label}_G{group:02d}";schedule={t["entry_date"]:{"decision_date":t["decision_date"],"codes":t[group_key].get(str(group),[])} for t in targets}
                try:run=replay_portfolio(name,schedule,days,q,cost)
                except (StudyExecutionError,ValueError) as error:
                    run={"name":name,"cost":cost,"status":"execution_error","error":str(error)};errors.append(run)
                runs.append(run);lookup[(name,cost)]=run
        schedule={t["entry_date"]:{"decision_date":t["decision_date"],"codes":t["common_codes"] if t["candidate_group_status"]=="groups_available" else []} for t in targets}
        try:market=replay_portfolio("COMMON_MARKET",schedule,days,q,cost)
        except (StudyExecutionError,ValueError) as error:market={"name":"COMMON_MARKET","cost":cost,"status":"execution_error","error":str(error)};errors.append(market)
        runs.append(market);lookup[("COMMON_MARKET",cost)]=market
        held=p["groups"] if p["direction"] else 1;candidate=lookup[(f"{p['candidate_id']}_G{held:02d}",cost)]
        review={"cost":cost,"candidate_id":p["candidate_id"],"held_group":held,"candidate_metrics":candidate.get("metrics"),"same_support_market_metrics":market.get("metrics"),"pool_increment_status":"comparator_not_supplied","candidate_source_state":candidate["status"]}
        if candidate["status"]==market["status"]=="potential_wealth_evaluated":
            review.update(candidate_market_daily_return_correlation=_series(candidate).corr(_series(market)),candidate_relative_wealth_vs_market=candidate["daily"][-1]["nav"]/market["daily"][-1]["nav"]-1)
        if preparation["comparator_present"]:
            other_group=p["groups"] if p["comparator_direction"] else 1;other=lookup[(f"{p['comparator_id']}_G{other_group:02d}",cost)]
            review["comparator_metrics"]=other.get("metrics")
            if candidate["status"]==other["status"]=="potential_wealth_evaluated" and all(t["comparator_group_status"]=="groups_available" for t in targets):
                nav=(_series(candidate,"nav")+_series(other,"nav"))/2
                returns=nav.div(nav.shift(1).fillna(1.))-1
                review.update(pool_increment_status="paired_potential_wealth_evaluated",candidate_comparator_daily_return_correlation=_series(candidate).corr(_series(other)),pair_metrics=performance(returns),pair_rule=preparation["pair_rule"],pair_daily=[{"date":d,"nav":float(v),"net_return":float(returns[d])} for d,v in nav.items()])
            else:review["pool_increment_status"]="comparator_or_execution_pending"
        reviews.append(review)
    held_name=f"{p['candidate_id']}_G{p['groups'] if p['direction'] else 1:02d}"
    held_errors=any(lookup[(held_name,c)]["status"]!="potential_wealth_evaluated" or lookup[("COMMON_MARKET",c)]["status"]!="potential_wealth_evaluated" for c in p["costs"])
    status="execution_error" if held_errors else "evaluated_research"
    if preparation["status"]=="source_partial":status="source_pending"
    if not held_errors and preparation["source_coverage"]["research_source_complete"]:
        high=reviews[-1];held_run=lookup[(held_name,p["costs"][-1])]
        positive=sum(f["net_return"]>0 for f in held_run["accounting"]["folds"])
        if (high["candidate_metrics"]["compounded_return"]<=0 and high.get("candidate_relative_wealth_vs_market",0)<=0
                and positive<len(held_run["accounting"]["folds"])/2):status="economic_weak"
    quality={"research_state":status,"factor_economically_rejected":False,"dimensions":reviews,"source_coverage":preparation["source_coverage"],
             "formal_admission":{"eligible":False,"status":"independent_admission_review_pending","official_points":None},
             "no_sign_window_weight_search":True,"research_quality_is_not_formal_sharpe_threshold":True}
    all_groups={str(cost):{label:[{"group":g,"status":lookup[(f"{label}_G{g:02d}",cost)]["status"],"metrics":lookup[(f"{label}_G{g:02d}",cost)].get("metrics")} for g in range(1,p["groups"]+1)] for label,_,_ in names} for cost in p["costs"]}
    folds={f"{r['name']}:{r['cost']}":r["accounting"]["folds"] for r in runs if r["status"]=="potential_wealth_evaluated"}
    concentration={f"{r['name']}:{r['cost']}":r["accounting"]["concentration"] for r in runs if r["status"]=="potential_wealth_evaluated"}
    result=_clean({"status":status,"validation":check,"preparation_sha256":preparation["preparation_sha256"],"candidate_id":p["candidate_id"],"runs":runs,"comparisons":reviews,"cost_reviews":reviews,"all_groups":all_groups,"folds":folds,"concentration":concentration,"coverage":preparation["coverage"],"source_coverage":preparation["source_coverage"],"diagnostic_only_partial_source":preparation["status"]=="source_partial","factor_dependence":preparation["factor_dependence"],"quality":quality,"errors":errors,"official_runs":0})
    if quality_callback is not None:result["quality_callback_result"]=_clean(quality_callback(result))
    return result
