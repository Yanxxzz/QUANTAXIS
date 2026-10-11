"""Deferred EF30/50bps paths; reuse original GF/F/market controls.

Root freezes this execution protocol first. Never register a new candidate,
load native/Mongo prices, rerun controls, or persist per-stock daily holdings.
Completed paths are checkpointed; resume only after their owner has stopped.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
sys.path.insert(0,str(ROOT))
from panda_alpha import study


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(4*1024*1024),b""): h.update(chunk)
    return h.hexdigest()


def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def artifact(path): return {"path":str(Path(path).resolve()),"sha256":sha(path)}


def bound(item):
    if artifact(item["path"])!=item: raise ValueError("Frozen input/runtime changed: "+item["path"])
    return Path(item["path"])


def write(path,value):
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(study._clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    temporary.replace(path)


def emit(stage,**facts): print(json.dumps({"stage":stage,**facts},ensure_ascii=False),flush=True)


def helpers(protocol):
    path=bound(protocol["bindings"]["original_cost_driver"])
    spec=importlib.util.spec_from_file_location("bound_original_cost_helpers_for_efficiency_pair",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # Separate imported module, original source and saved controls are untouched.
    # The old scheduler unconditionally constructs common-market support; keep
    # that target key, while only EF is executed below.
    module.MODES=("EF",);module.NAMES=("EF","COMMON_MARKET")
    return module


def target_inputs(protocol,engine):
    mid=pd.read_parquet(bound(protocol["bindings"]["EF_signals"]),columns=["date","symbol","EF"])
    old=pd.read_parquet(bound(protocol["bindings"]["original_signals"]),columns=["date","symbol"])
    if mid.duplicated(["date","symbol"]).any() or not np.isfinite(mid.EF).all():
        raise ValueError("Invalid fixed EF signal support")
    if len(mid)!=32905 or len(old)!=32905 or not mid[["date","symbol"]].equals(old[["date","symbol"]]):
        raise ValueError("EF and original controls must have exact same 32905orderedformation keys")
    prep=read(bound(protocol["bindings"]["EF_preparation"]))
    proof=read(bound(protocol["bindings"]["EF_preflight"]))
    oldreceipt=read(bound(protocol["bindings"]["original_input_receipt"]))
    required = ("controls_reuse_allowed", "shared_finite_mask_exact", "source_qualification_exact",
                "original_f_formation_keys_exact", "all_251_decisions_group_ready", "original_SOURCE_and_all_peer_F_method_preserved", "holding_tail_true_NaN",
                "immutable_E_SOURCE_and_all_old_gates_preserved", "only_native_class_and_component_AST_changed", "no_final_Z_or_signal_return_blend")
    if any(prep.get(key) is not True for key in required):
        raise ValueError("EF/support mismatch or missing proof requires root new common F context; controls cannot be reused or rerun here")
    reference_error=prep.get("max_absolute_saved_E_minus_F_control_reference_error")
    if (isinstance(reference_error,bool) or not isinstance(reference_error,(int,float))
            or not np.isfinite(reference_error) or not 0<=reference_error<=2e-12):
        raise ValueError("Actual native EF must match the fixed saved E/F control reference")
    candidate=read(bound(protocol["bindings"]["EF_candidate"]))
    e_protocol=read(bound(protocol["bindings"]["EF_protocol"]))
    old_protocol=read(bound(protocol["bindings"]["original_cost_protocol"]))
    if (candidate["candidate_id"]!=protocol["economic_context"] or candidate["direction"]!=1 or candidate.get("dispatch_ready") is not True
            or candidate["code_sha256"]!=protocol["bindings"]["EF_code"]["sha256"]
            or prep.get("status")!="EF_FULL_SCOPE_NATIVE_READY_REUSE_GF_F_MARKET"
            or prep["protocol_sha256"]!=protocol["bindings"]["EF_protocol"]["sha256"]
            or proof.get("research_replay_passed") is not True or proof["code_sha256"]!=protocol["bindings"]["EF_code"]["sha256"]
            or proof["proof"]["input_frame_sha256"]!=prep["old_input_frame_sha256"]
            or proof["proof"]["input_frame_sha256"]!=oldreceipt["native_outputs"]["GF"]["input_frame_sha256"]
            or proof["proof"]["output_sha256"]!=prep["output_sha256"]
            or (prep["formation_dates"],prep["common_anchor_rows"],prep["holding_days"],prep["financial_report_identities"],prep["financial_source_rows"])!=(51,32905,256,1736,161366)
            or old_protocol["anchors"]!=protocol["anchors"] or old_protocol["window"]!=protocol["window"]
            or old_protocol["groups"]!=10 or old_protocol["cycle"]!=5 or old_protocol["costs"]!=protocol["costs"]
            or old_protocol["held_groups"]["F141_CONTROL"]!="1"
            or e_protocol["candidate_draft"]["strategy_spec"]["formation_dates"]!=protocol["anchors"]
            or e_protocol["candidate_draft"]["candidate_id"]!=candidate["candidate_id"]
            or e_protocol["candidate_draft"]["direction"]!=1):
        raise ValueError("Fixed EF/source/native/control-definition contract mismatch")
    days,schedules=engine.make_schedules(protocol,mid)
    if (len(days)!=256 or days[0]!="2025-08-15" or days[-1]!="2026-09-03"
            or len(schedules["EF"])!=51):
        raise ValueError("Fixed EF wealth calendar or formation phase changed")
    targets=study.object_hash({"days":days,"schedules":schedules})
    target_receipt={"protocol":artifact(OUT/"protocol.json"),"EF_signals":protocol["bindings"]["EF_signals"],
                    "original_signals":protocol["bindings"]["original_signals"],"common_anchor_rows":len(mid),
                    "formation_dates":51,"holding_days":len(days),"first_day":days[0],"last_day":days[-1],
                    "target_sha256":targets,"all_targets_frozen_before_price_and_result_reads":True,
                    "controls_recomputed":0,"new_contexts":0,"official_spent":0}
    oldtarget=OUT/"target_receipt.json"
    if oldtarget.exists() and read(oldtarget)!=target_receipt:
        raise ValueError("Previously frozen target receipt changed")
    if not oldtarget.exists():write(oldtarget,target_receipt)
    return mid,days,schedules,artifact(oldtarget),targets


def finalize(protocol,checkpoint,days,target_binding):
    expected={"EF:"+format(cost,".3f") for cost in protocol["costs"]}
    if set(checkpoint["completed"])!=expected:
        return {"status":"EXECUTION_PENDING","completed_paths":sorted(checkpoint["completed"]),
                "errors":checkpoint["errors"],"economic_failure_claimed":False,"partial_metrics_withheld":True}
    records=[checkpoint["completed"]["EF:"+format(cost,".3f")]["record"] for cost in protocol["costs"]]
    daily=pd.DataFrame([row for cost in protocol["costs"] for row in checkpoint["completed"]["EF:"+format(cost,".3f")]["daily"]])
    # Economic results are opened only now, after both fixed new paths complete.
    old=read(bound(protocol["bindings"]["original_control_summary"]))
    control_daily=pd.read_parquet(bound(protocol["bindings"]["original_control_ledger"]))
    if (old.get("status")!="ALL_SIX_FIXED_PATHS_COST_WEALTH_EVALUATED" or old.get("errors")
            or old.get("ledger")!=protocol["bindings"]["original_control_ledger"]):
        raise ValueError("Original six controls are incomplete; do not rerun them here")
    e_control=read(bound(protocol["bindings"]["E_control_summary"]))
    e_daily=pd.read_parquet(bound(protocol["bindings"]["E_control_ledger"]))
    if (e_control.get("status")!="BOTH_FIXED_E_COST_PATHS_EVALUATED" or e_control.get("errors")
            or e_control.get("ledger")!=protocol["bindings"]["E_control_ledger"]):
        raise ValueError("Completed standalone E controls are incomplete; do not rerun them here")
    comparisons=[]
    for cost,record in zip(protocol["costs"],records):
        candidate=daily[daily.cost.eq(cost)].set_index("date")
        if candidate.index.tolist()!=days:raise ValueError("EF holding dates changed")
        for name in ("GF","F141_CONTROL","COMMON_MARKET","E"):
            parent_daily=e_daily if name=="E" else control_daily
            parent_summary=e_control if name=="E" else old
            control=parent_daily[parent_daily.name.eq(name)&parent_daily.cost.eq(cost)].set_index("date")
            if not candidate.index.equals(control.index):raise ValueError("Fixed control dates diverged")
            matches=[r for r in parent_summary["results"] if r["name"]==name and r["cost"]==cost]
            if len(matches)!=1:raise ValueError("Unique original control path missing")
            cm,bm=record["metrics"],matches[0]["metrics"]
            comparisons.append({"candidate":"EF","control":name,"cost":cost,
                "net_return_difference":cm["compounded_return"]-bm["compounded_return"],
                "relative_terminal_wealth":candidate.nav.iloc[-1]/control.nav.iloc[-1]-1,
                "sharpe_difference":cm["sharpe"]-bm["sharpe"] if cm["sharpe"] is not None and bm["sharpe"] is not None else None,
                "max_drawdown_difference":cm["max_drawdown"]-bm["max_drawdown"],
                "daily_net_return_correlation":candidate.net_return.corr(control.net_return)})
    ledger=OUT/"EF_daily_net_ledger.parquet";engine=helpers(protocol);engine.parquet(ledger,daily)
    return {"status":"BOTH_FIXED_EF_COST_PATHS_EVALUATED","protocol":artifact(OUT/"protocol.json"),
        "target_receipt":target_binding,"original_controls":protocol["bindings"]["original_control_summary"],
        "original_control_ledger":protocol["bindings"]["original_control_ledger"],"controls_recomputed":0,
        "standalone_E_controls":protocol["bindings"]["E_control_summary"],
        "standalone_E_control_ledger":protocol["bindings"]["E_control_ledger"],
        "official_references_only":protocol["official_references_only"],
        "window":protocol["window"],"anchors":51,"holding_days":len(days),"results":records,
        "comparisons":comparisons,"errors":[],"primary_cost":0.005,"sensitivity_cost":0.003,
        "ledger":artifact(ledger),"runtime":artifact(__file__),"new_contexts":0,"official_spent":0,
        "full_PIT":False,"official_NAV":False,"formal_admission":False,"fresh_blind_OOS":False,
        "source_limited_local_daily_bar_execution_proxy":True,"not_actual_auction_or_capacity":True,
        "risk_metadata_unknowns_remain_pending":True,"no_partial_results_used_for_tuning":True,
        "runtime_reporting_change_only":protocol["runtime_reporting_note"],
        "no_new_price_snapshot_or_source_changes":True, "same_support_control_reuse":True}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--protocol-sha");parser.add_argument("--resume",action="store_true")
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args();proto=OUT/"protocol.json"
    if not args.execute:
        emit("deferred_no_execution",required="root-frozen protocol SHA and explicit --execute");return
    if not args.protocol_sha:parser.error("--execute requires --protocol-sha")
    if sha(proto)!=args.protocol_sha:raise ValueError("Root-frozen execution protocol changed")
    p=read(proto)
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("User research dispatch deadline reached")
    if sha(__file__)!=p["driver_sha256"]:raise ValueError("Exact frozen cost runtime changed")
    if p.get("status")!="FROZEN_EF_COST_RECOMPUTATION_AWAIT_ROOT_EXECUTION" or p.get("direction")!=1:
        raise ValueError("Deferred EF cost protocol not root-frozen")
    if p["costs"]!=[0.003,0.005] or p["held_groups"]!={"EF":"10"} or p["cycle"]!=5 or p["groups"]!=10:
        raise ValueError("Fixed EF/cost/group contract changed")
    for item in p["bindings"].values():bound(item)
    summary_path,cp_path=OUT/"wealth_summary.json",OUT/"checkpoint.json"
    if summary_path.exists() and read(summary_path).get("status")=="BOTH_FIXED_EF_COST_PATHS_EVALUATED":
        summary=read(summary_path);bound(summary["ledger"]);bound(summary["target_receipt"]);emit("already_completed_no_replay");return
    if cp_path.exists() and not args.resume:raise ValueError("Checkpoint exists; check real owner before explicit--resume")
    cp=read(cp_path) if cp_path.exists() else {"protocol_sha256":args.protocol_sha,"completed":{},"errors":[],"error_history":[]}
    if cp["protocol_sha256"]!=args.protocol_sha:raise ValueError("Checkpoint protocol mismatch")
    active=None;phase="frozen_targets"
    try:
        engine=helpers(p);signals,days,schedules,target_binding,targets=target_inputs(p,engine)
        if cp.get("target_sha256",targets)!=targets:raise ValueError("Completed targets changed")
        expected={"EF:"+format(cost,".3f") for cost in p["costs"]}
        for key,item in cp["completed"].items():
            if key not in expected or [r["date"] for r in item["daily"]]!=days or item["record"]["accounting_max_error"]>1e-8:
                raise ValueError("Invalid completed EF path")
        cp.update(status="RUNNING",pid=os.getpid(),target_sha256=targets,target_receipt=target_binding,active_path=None,errors=[],updated_at=datetime.now(timezone.utc).isoformat())
        write(cp_path,cp)
        if set(cp["completed"])!=expected:
            phase="bound_quote_book";book=engine.quote_book(p,set(signals.symbol))
            for cost in p["costs"]:
                active="EF:"+format(cost,".3f")
                if active in cp["completed"]:emit("completed_path_reused",path=active);continue
                phase="EF_path_execution";cp.update(active_path=active,updated_at=datetime.now(timezone.utc).isoformat());write(cp_path,cp)
                tick=time.monotonic()
                run=study.replay_portfolio("EF",schedules["EF"],days,book,cost)
                record,daily=engine.compact_run(run,days);del run
                cp["completed"][active]={"record":record,"daily":daily,"runtime_sha256":sha(__file__)}
                cp.update(active_path=None,updated_at=datetime.now(timezone.utc).isoformat());write(cp_path,cp)
                emit("EF_path_checkpointed",path=active,completed=len(cp["completed"]),seconds=round(time.monotonic()-tick,2))
            del book
        active=None;phase="paired_consolidation";summary=finalize(p,cp,days,target_binding);write(summary_path,summary)
        if summary["status"]=="BOTH_FIXED_EF_COST_PATHS_EVALUATED":
            cp.update(status="COMPLETED",completed_paths=sorted(cp["completed"]),completed={},errors=[],wealth_summary=artifact(summary_path),ledger=summary["ledger"])
            write(cp_path,cp)
        emit("EF_cost_replay_finished",status=summary["status"],controls_recomputed=0,official_spent=0)
    except Exception as exc:
        error={"phase":phase,"path":active,"error":type(exc).__name__+": "+str(exc),"economic_failure_claimed":False,"at":datetime.now(timezone.utc).isoformat()}
        cp["error_history"].append(error);cp.update(status="EXECUTION_PENDING",pid=os.getpid(),active_path=active,latest_error=error,errors=[error],updated_at=error["at"])
        write(cp_path,cp);emit("execution_pending",phase=phase,path=active,error=error["error"]);raise


if __name__=="__main__":main()
