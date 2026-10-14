"""New past-episode measurement on a fixed retained universe, not old PnL replay.

Freeze/register before the one real source consumption. The supplied legal-board
and factor states are local observed roles, not certified Panda deployment/PIT.
No current future-return labels or account paths are calculated or retained.
"""
from pathlib import Path
from bisect import bisect_left
from datetime import datetime, timezone
import hashlib
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding, client, limit_price
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key

OUT = ROOT / "research_runs/night_limit_gate_20261015/historical_episode_support"
NIGHT = OUT.parent
META = ROOT / "research_runs/limit_reversal_20261014/source_metadata.json"
SOURCE_ID = "retained_stockdb_raw_and_stock_adj_asof"
DEADLINE = datetime(2026, 10, 14, 23, tzinfo=timezone.utc)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def assert_open():
    if datetime.now(timezone.utc) >= DEADLINE:
        raise RuntimeError("Authorized07:00 cutoff: do not start new source/research")


def prepare():
    assert_open()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "protocol.json").exists():
        raise RuntimeError("This frozen unit exists: collect the original state, never repeat")
    schema = NIGHT / "episode_history_software/protocol.json"
    qa = NIGHT / "episode_history_software/completion_decimal_receipt.json"
    independent = NIGHT / "episode_history_software/episode_runtime_independent_review.json"
    # A completed actual test/review receipt is mandatory; do not fabricate passed.
    for path in (schema, qa, independent):
        if not path.is_file():
            raise ValueError("Collect actual software/clock review first: " + str(path))
    actual_qa=read(qa)
    if actual_qa["final_runtime"]["sha256"] != binding(ROOT/"panda_alpha/limit_episode_history.py")["sha256"]:
        raise ValueError("Actual tested runtime hash differs before real freeze")
    if actual_qa["final_tests"]["sha256"] != binding(ROOT/"tests/test_limit_episode_history.py")["sha256"] or actual_qa["actual_test_execution"]["exit_code"] != 0:
        raise ValueError("Actual test receipt/hash differs before real freeze")
    if read(independent)["blockers"]:
        raise ValueError("Unresolved independent software blockers")
    meta = read(META)
    protocol = {"authority": binding(NIGHT / "authorization.json"),
        "source_metadata": binding(META), "mechanism_review": binding(NIGHT / "new_supported_gate_factor_paths_review.json"),
        "source_schema_time": binding(schema), "actual_synthetic_test_receipt": binding(qa),
        "independent_runtime_review": binding(independent),
        "reader": binding(__file__), "runtime": binding(ROOT / "panda_alpha/limit_episode_history.py"),
        "scope": {"source_start": meta["start"], "source_end": meta["end"],
                  "source_codes": meta["codes"], "evaluation_start": "2024-01-02", "evaluation_end": meta["end"],
                  "independent_reference_sessions": len(meta["calendar"]), "future_current_labels": 0},
        "hypothesis": "Issuer-specific completed positive price-limit rallies may persistently give back; lower past full-giveback frequency may identify a more reliable rally. Persistence is unproved, not identified manipulation or investor flow.",
        "measurement": {"episode": "first pair only: e-2 known not upper, e-1/e legally upper, all three normal known non-ST/lifecycle-domain/trading/positive actual shares",
            "anchor": "same-source asof total-return economic close(e-2)",
            "maturity": "five independent reference sessions e+1..e+5 closed by formation close c; unknown/halt/role gap stays pending",
            "failure": "any complete economic close(e+1..e+5) strictly below economic pre-rally anchor;0 only if all five known and none below",
            "candidate": "sum failures / N fully supported mature episodes since fixed source start; direction0",
            "control": "N fully supported mature episodes, lower direction0 attention/frequency descriptor on the identical finite risk support",
            "unknown": "N_supported>0 AND no identified mature unknown outcome required for both risk/control; otherwise NaN. No past history is never0 risk; incomplete/immature episode counts independently reported.",
            "formation": "close c for daily factor/next open only; old lower-price intraday bid can read only c=t-1",
            "dependence": "first pair deduplicates a streak but separate episodes/overlapping five-session windows remain dependent"},
        "old_scope_exposed_not_new_OOS": True, "no_new_return_labels_or_PnL_or_threshold": True,
        "formal_native_input_PIT_or_pool_eligibility_certified": False, "official_runs_or_fees": 0}
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    before = registry.total
    if registry.historical != 415:
        raise ValueError("Historical baseline mismatch: inspect registry")
    registrations = []
    for suffix, measurement in (("GIVEBACK_RISK", protocol["measurement"]["candidate"]), ("EPISODE_COUNT_CONTROL", protocol["measurement"]["control"])):
        candidate = {"candidate_id": "LIMIT_EPISODE_" + suffix + "_20261015", "direction": 0,
            "strategy_spec": {"measurement": measurement, "joint_contract": protocol["measurement"],
                "source": protocol["source_metadata"]["sha256"], "runtime": protocol["runtime"]["sha256"],
                "purpose": "complete source-bound past-state/value diagnostic, no current future outcome or account"}}
        window = {"start": "2024-01-02", "end": meta["end"]}
        definition = strategy_definition(candidate, window, 1, 2)
        registry.record(candidate, window, 1, 2)
        registrations.append({"candidate": candidate, "key": trial_key(definition)})
    protocol.update(registrations=registrations, registry_before=before, registry_total=registry.total)
    registry.close()
    save("protocol.json", protocol)
    save("state.json", {"status": "FROZEN_REGISTERED_NO_REAL_EPISODE_VALUES_YET", "process": None,
                         "registry_total": protocol["registry_total"], "official_spent": 0})
    return protocol, meta


def load_states(cfg, meta):
    fields = {k:1 for k in ("code", "date", "open", "high", "low", "close", "preclose", "volume_shares", "is_st", "trade_status", "adjustment", "source_row_sha256")}
    fields["_id"] = 0
    identity = hashlib.sha256(); chunks = []; buffer = []
    with client(cfg) as conn:
        db = conn[meta["source_profile"]["database"]]
        query = {"source":"stockdb", "code":{"$in":meta["codes"]}, "date":{"$gte":meta["start"], "$lte":meta["end"]}}
        for row in db.stock_day.find(query, fields).sort([("code",1),("date",1)]).batch_size(5000):
            identity.update((row["code"]+"|"+row["date"]+"|"+row.get("source_row_sha256","")+"\n").encode())
            buffer.append(row)
            if len(buffer) == 100000:
                chunks.append(pd.DataFrame(buffer)); buffer.clear()
        if buffer: chunks.append(pd.DataFrame(buffer))
        factors = pd.DataFrame(list(db.stock_adj.find({"source":"stockdb", "code":{"$in":meta["codes"]}, "date":{"$lte":meta["end"]}}, {"_id":0,"code":1,"date":1,"adj":1})))
    frame = pd.concat(chunks, ignore_index=True); del chunks, buffer
    if len(frame) != meta["source_rows"] or frame.duplicated(["code","date"]).any() or not frame.adjustment.eq("none").all():
        raise ValueError("Frozen raw source identities/adjustment changed")
    groups = {code:x.sort_values("date") for code,x in factors.groupby("code",sort=False)}
    frame["adjustment_factor"] = np.nan
    frame["factor_asof_date"] = pd.Series(None, index=frame.index, dtype=object)
    records=[]
    for code, positions in frame.groupby("code",sort=False).groups.items():
        part = groups.get(code)
        if part is None: continue
        if part.date.duplicated().any() or not (np.isfinite(part.adj)&part.adj.gt(0)).all():
            raise ValueError("Invalid duplicate/nonpositive source factor")
        ix = np.searchsorted(part.date.to_numpy(), frame.loc[positions,"date"].to_numpy(), side="right")-1
        valid=ix>=0; values=np.full(len(ix),np.nan); stamps=np.full(len(ix),None,dtype=object)
        values[valid]=part.adj.to_numpy()[ix[valid]]
        stamps[valid]=part.date.to_numpy()[ix[valid]]
        frame.loc[positions,"adjustment_factor"]=values
        frame.loc[positions,"factor_asof_date"]=stamps
        records.extend(part[["code","date","adj"]].itertuples(index=False,name=None))
    frame["raw_close"] = frame.close
    frame["trading"] = pd.Series(np.nan, index=frame.index, dtype=object)
    frame.loc[frame.trade_status.eq("1"),"trading"]=True
    frame.loc[frame.trade_status.eq("0"),"trading"]=False
    known_st=frame.is_st.isin([True,False])
    rates=np.where(frame.is_st.eq(True)&frame.date.lt("2026-07-06"),.05,.10)
    frame["upper_price"]=[limit_price(p,r,"up") for p,r in zip(frame.preclose,rates)]
    frame["lower_price"]=[limit_price(p,r,"down") for p,r in zip(frame.preclose,rates)]
    normal=frame.is_st.eq(False)&known_st
    actual_legal=known_st.copy()
    for name in ("open","high","low","close","upper_price","lower_price"):
        actual_legal &= np.isfinite(frame[name]) & frame[name].gt(0)
    actual_legal &= (frame.low.le(frame.open+1e-7)&frame.open.le(frame.high+1e-7)&frame.low.le(frame.close+1e-7)&frame.close.le(frame.high+1e-7)&frame.low.ge(frame.lower_price-1e-7)&frame.high.le(frame.upper_price+1e-7)&frame.lower_price.le(frame.upper_price))
    starts={}; ends={}; all_days=meta["all_reference_days_through_end"]
    for life in meta["lifecycle"]:
        if not life.get("ipo_date") or life.get("lifecycle_issues"):continue
        n=bisect_left(all_days,life["ipo_date"])+20
        starts[life["code"]]=all_days[n] if n<len(all_days) else "9999-12-31"
        if life.get("delisted_date"):ends[life["code"]]=life["delisted_date"]
    lifecycle=frame.code.isin(starts)&frame.date.ge(frame.code.map(starts).fillna("9999-12-31"))&frame.date.lt(frame.code.map(ends).fillna("9999-12-31"))
    known=actual_legal&lifecycle&frame.trade_status.eq("1")&np.isfinite(frame.volume_shares)&frame.volume_shares.gt(0)
    frame["legal_price"] = known
    frame["upper_close"] = pd.Series(np.nan,index=frame.index,dtype=object)
    frame.loc[known&normal,"upper_close"]=np.isclose(frame.loc[known&normal,"close"],frame.loc[known&normal,"upper_price"],atol=1e-7,rtol=0)
    frame["factor_source_id"] = SOURCE_ID
    panel={name:frame.pivot(index="date",columns="code",values=name).reindex(index=meta["calendar"],columns=meta["codes"]) for name in ("raw_close","upper_close","trading","is_st","legal_price","adjustment_factor","factor_asof_date","factor_source_id")}
    known_active=frame[known&normal].pivot(index="date",columns="code",values="legal_price").reindex(index=meta["calendar"],columns=meta["codes"]).fillna(False).astype(bool)
    receipt={"raw_rows":len(frame),"raw_identity_sha256":identity.hexdigest(),"factor_rows":len(factors),
        "factor_record_sha256":hashlib.sha256(json.dumps(sorted(records),separators=(",",":"),allow_nan=False).encode()).hexdigest(),
        "source_codes":len(meta["codes"]),"independent_reference_days":len(meta["calendar"]),
        "is_st_lifecycle_PIT_certified":False,"official_native_value_or_calendar_or_factor_parity":False,
        "original_raw_quotes_or_market_panel_saved":False,"new_external_requests":0,
        "measurement_only_new_past_state_not_old_factor_labels_or_cost_replay":True}
    return panel,known_active,receipt


def run():
    from panda_alpha.limit_episode_history import build_episode_history
    protocol, meta = prepare()
    assert_open()
    save("state.json", {"status":"RUNNING_NEW_REGISTERED_HISTORICAL_EPISODE_SUPPORT", "process":os.getpid(),
        "argv":str(Path(__file__).resolve()),"registry_total":protocol["registry_total"],"official_spent":0})
    panel, active, source = load_states(read(ROOT/"config/panda-alpha.local.json"),meta)
    save("source_receipt.json", source)
    print(json.dumps({"stage":"NEW_EPISODE_SOURCE_RECEIVED","raw_rows":source["raw_rows"],"registry":protocol["registry_total"]}),flush=True)
    result = build_episode_history(panel,meta["calendar"],formation_end=meta["end"],factor_source_id=SOURCE_ID,factor_role="total_return_factor",mode="values")
    # Output keys are bound to the completed runtime before freezing this script.
    summarize(result,active,protocol,source)


def summarize(result, active, protocol, source):
    daily = result["daily"]
    all_risk = daily["risk"]
    risk = all_risk.loc["2024-01-02":]
    control = daily["episode_frequency_count"].loc[risk.index]
    if not np.array_equal(np.isfinite(risk),np.isfinite(control)):
        raise ValueError("Candidate/control fixed joint support differs")
    finite = risk.notna()
    active = active.loc[risk.index,risk.columns]
    # Both full supplied roster and known-active source denominator are shown;
    # the latter is a description, not a lowered formal/native quality gate.
    finite_active = finite & active
    rank_groups = np.ceil(risk.rank(axis=1,method="average",pct=True)*10).clip(1,10)
    per_day=[]
    rhos=[]
    for date, row in risk.iterrows():
        known=row.dropna(); count_row=control.loc[date,known.index]
        rho=known.corr(count_row,method="spearman") if len(known)>2 and known.nunique()>1 and count_row.nunique()>1 else np.nan
        if np.isfinite(rho):rhos.append(float(rho))
        per_day.append({"date":date,"finite":int(known.size),"distinct_risk":int(known.nunique()),
            "honest10_average_tie_groups_nonempty":int(rank_groups.loc[date].dropna().nunique()),
            "known_active_source_keys":int(active.loc[date].sum()),
            "finite_active_source_keys":int(finite_active.loc[date].sum()),
            "no_complete_history":int(daily["count"].loc[date].eq(0).sum()),
            "has_mature_unknown_episode":int(daily["unknown"].loc[date].gt(0).sum())})
    save("daily_support_summary.json",{"rows":per_day,"not_full_stock_daily_values":True})
    old_entries=read(ROOT/"research_runs/limit_gate_diagnosis_20261014/protocol.json")["fixed_entry_identities"]
    entry_descriptors=[]
    calendar=list(all_risk.index)
    tail_codes={"605178","000820","600239","000151","600234"}
    for entry in old_entries:
        c=calendar[calendar.index(entry["date"])-1]
        symbol=entry["symbol"]
        value=all_risk.loc[c,symbol]
        entry_descriptors.append({"old_entry_date":entry["date"],"symbol":symbol,"formation_close":c,
            "past_complete_count":int(daily["count"].loc[c,symbol]),
            "past_mature_unknown_count":int(daily["unknown"].loc[c,symbol]),
            "current_immature_known_episodes":int(daily["pending"].loc[c,symbol]),
            "historical_giveback_risk":float(value) if np.isfinite(value) else None,
            "previously_exposed_maxDD_tail_identity":symbol in tail_codes and entry["date"]<="2024-03-25"})
    save("old39_entry_past_state_descriptors.json",{"rows":entry_descriptors,"posthoc_endogenous_old_entry_cohort":True,"new_future_label_or_account_path":False})
    episodes=result["episodes"]
    # Event identities/classification flags are compact valid measurement inputs,
    # not six-price sequences or a duplicated market/PDF database.
    state_bytes=json.dumps({"episodes":episodes,"historical_five_day_states_known_only_at_maturity":True},ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
    (OUT/"identified_episode_states.json.gz").write_bytes(gzip.compress(state_bytes,mtime=0))
    episode_state_binding=binding(OUT/"identified_episode_states.json.gz")
    finite_total=int(finite.to_numpy().sum()); full_keys=int(risk.size)
    known_active_total=int(active.to_numpy().sum())
    finite_active_total=int(finite_active.to_numpy().sum())
    n=control.to_numpy(); supported_n=n[np.isfinite(n)]
    day_counts=pd.DataFrame(per_day)
    summary={"status":"FIXED_PAST_EPISODE_VALUE_AND_SUPPORT_DIAGNOSTIC_COMPLETE",
        "registry_total":protocol["registry_total"],"new_contexts":2,"official_spent":0,
        "source":source,"episode_receipt":result["receipt"],"compact_event_state_input":episode_state_binding,
        "compact_event_state_bytes":(OUT/"identified_episode_states.json.gz").stat().st_size,
        "evaluation":{"start":str(risk.index[0]),"end":str(risk.index[-1]),"reference_days":len(risk),
            "fixed_codes":len(risk.columns),"full_fixed_roster_keys":full_keys,"finite_history_risk_keys":finite_total,
            "full_fixed_roster_output_density":finite_total/full_keys,
            "known_active_received_source_keys":known_active_total,"finite_active_received_source_keys":finite_active_total,
            "known_active_received_source_density":finite_active_total/known_active_total if known_active_total else None,
            "denominator_source_PIT_or_formal_coverage_certified":False,
            "finite_daily_min":int(day_counts.finite.min()),"finite_daily_max":int(day_counts.finite.max()),
            "distinct_risk_daily_min":int(day_counts.distinct_risk.min()),"distinct_risk_daily_max":int(day_counts.distinct_risk.max()),
            "honest10_tie_groups_all_days":bool(day_counts.honest10_average_tie_groups_nonempty.eq(10).all()),
            "honest10_tie_groups_min":int(day_counts.honest10_average_tie_groups_nonempty.min()),
            "median_supported_prior_episode_N":float(np.median(supported_n)) if len(supported_n) else None,
            "historical_risk_count_signal_rho_median":float(np.median(rhos)) if rhos else None,
            "risk_is_not_current_future_Return_or_RankIC":True},
        "old_actual_entry_diagnostic":{"n":len(entry_descriptors),"finite_past_risk":sum(x["historical_giveback_risk"] is not None for x in entry_descriptors),
            "posthoc_not_signal_accuracy_or_strategy_comparison":True,
            "maxDD5_tail_states":[x for x in entry_descriptors if x["previously_exposed_maxDD_tail_identity"]]},
        "no_current_future_labels_PnL_or_fitted_gate_threshold":True,"formal_eligible":False,
        "decision":"Review honestly supported episode counts/ties/clock first; no fee merely for a new ratio or low correlation. Future strategy/factor return comparisons need a new frozen scope/common control and actual usable source contract."}
    save("summary.json",summary)
    save("state.json",{"status":summary["status"],"process":None,"registry_total":protocol["registry_total"],"official_spent":0,
        "collect_only":True,"recovery":"Keep completed episode inputs/values/support. No source or past-state recomputation, no arbitrary horizon/min-count/sign rescue. Any new returns/unit requires separate freeze and common-control registration."})
    registry=ResearchRegistry(ROOT/"research_runs/research_trials.sqlite3")
    artifact=binding(OUT/"summary.json")
    for record in protocol["registrations"]:
        registry.append_event("evaluation",record["key"],{"summary":artifact,"status":summary["status"],"formal_eligible":False,"new_current_future_labels_or_account_paths":0,"official_spent":0},event_id="historical_episode_support:"+record["key"])
    registry.close()
    print(json.dumps({"status":summary["status"],"registry":protocol["registry_total"],"evaluation":summary["evaluation"],"past_episode_receipt":result["receipt"]},ensure_ascii=False),flush=True)


def main():
    try:
        run()
    except Exception as error:
        state_path = OUT / "state.json"
        current = read(state_path) if state_path.is_file() else {}
        # Refusing a completed/frozen invocation must not downgrade its state.
        # Only this process's actual new running unit owns pending bookkeeping.
        if (not (OUT / "summary.json").exists()
                and current.get("status") == "RUNNING_NEW_REGISTERED_HISTORICAL_EPISODE_SUPPORT"
                and current.get("process") == os.getpid()):
            save("state.json",{"status":"SOURCE_OR_EXECUTION_PENDING_NOT_ECONOMIC_FAILURE","process":None,
                "error_type":type(error).__name__,"error":str(error),"official_spent":0,
                "recovery":"Investigate exact original stage; no automatic source/label restart. Preserve frozen protocol and any complete receipt."})
        raise


if __name__ == "__main__":
    main()
