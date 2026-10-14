"""Continue only the two unfinished close-stop accounts after exact status repair."""
from pathlib import Path
from collections import Counter
import os
import sys
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding, client
from scripts.research_limit_event_factor import clean
from scripts.research_limit_stoploss import held_status_repairs
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_reversal import simulate
from panda_alpha.evaluation import performance
from panda_alpha.registry import ResearchRegistry

OUT = ROOT / "research_runs/limit_stoploss_20261014"
PARENT = ROOT / "research_runs/limit_reversal_optimization_20261014"


def save(name, value):
    (OUT / name).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2,
                                     allow_nan=False), encoding="utf-8")


def run():
    if (OUT / "resume_protocol.json").exists():
        raise RuntimeError("An existing continuation must be collected, never replayed")
    old = read(OUT / "summary.json")
    pending = [x for x in old["paths"] if x["status"] == "SOURCE_PENDING"]
    if len(pending) != 2 or any(x["policy"]["name"] != "stop_t2_close_loss15" for x in pending):
        raise ValueError("This exact continuation binds only the two close-stop accounts")
    if any(x["source_pending"] != [{"date": "2025-04-22", "reason": "held_unknown_status:002868:2025-04-22"}] for x in pending):
        raise ValueError("Inspect a new source problem rather than guessing status")
    checks = read(OUT / "002868_20250422_asof_check.json")
    pairs = checks["exact_suspension_factor_pairs"]
    if not all(x["equal"] for x in pairs):
        raise ValueError("Suspension corporate-action factor role incomplete")
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    meta["start"] = calendar[calendar.index("2025-04-22") - 2]
    meta["calendar"] = [x for x in calendar if x >= meta["start"]]
    cfg = read(ROOT / "config/panda-alpha.local.json")
    with client(cfg) as c:
        meta["source_rows"] = c[meta["source_profile"]["database"]].stock_day.count_documents({"source": "stockdb", "code": {"$in": meta["codes"]}, "date": {"$gte": meta["start"], "$lte": meta["end"]}})
    protocol = {"prior_results": binding(OUT / "summary.json"),
                "new_exact_review": binding(OUT / "002868_20250422_status_review.json"),
                "factor_role_check": binding(OUT / "002868_20250422_asof_check.json"),
                "frozen_preopen_mask": binding(PARENT / "resume_masks.json"),
                "prior_new_unit": binding(OUT / "protocol.json"),
                "engine": binding(ROOT / "panda_alpha/limit_reversal.py"), "runner": binding(__file__),
                "quote_read_start": meta["start"], "remaining_start": "2025-04-22", "end": meta["end"],
                "bounded_raw_rows": meta["source_rows"], "no_completed_path_training_factor_or_label_replay": True,
                "reference_indices_preserved": True, "no_formula_or_exit_change": True,
                "new_contexts": 0, "official_fees": 0}
    if protocol["engine"]["sha256"] != read(OUT / "protocol.json")["engine"]["sha256"]:
        raise ValueError("Source repair cannot silently change the frozen economic runtime")
    save("resume_protocol.json", protocol)
    save("state.json", {"status": "RESUMING_ONLY_TWO_CLOSE_STOP_ACCOUNTS", "process": os.getpid(), "argv": str(Path(__file__).resolve()), "registry_total": old["registry_total"], "official_compute_spent": 0})
    panel, receipt, bars = load_retained_panel(cfg, meta, include_strategy=True)
    if receipt["factor_record_hash"] != read(OUT / "source_receipt.json")["factor_record_hash"]:
        raise ValueError("Exact retained factor records changed")
    del panel
    bars, previous_repairs = held_status_repairs(bars)
    bars = bars[bars.date.ge(meta["start"])].copy()
    extra = []
    for item in pairs:
        day, factor = item["date"], item["current_asof_factor"]["adj"]
        mask = bars.symbol.eq("002868") & bars.date.eq(day)
        if mask.any():
            bars.loc[mask, "trade_status"] = "suspended"
            bars.loc[mask, "source_status"] = "source_explicit_suspension"
            bars.loc[mask, "suspension_confirmed"] = True
            bars.loc[mask, "adjustment_factor"] = factor
        else:
            extra.append({"date": day, "symbol": "002868", "trade_status": "suspended", "source_status": "source_explicit_suspension", "suspension_confirmed": True, "entry_eligible": False, "adjustment_factor": factor})
    if extra:
        bars = pd.concat([bars, pd.DataFrame(extra)], ignore_index=True)
    # Source ST flag is already true from effective2025-04-30; no broad override.
    masks = read(PARENT / "resume_masks.json")
    selected = {tuple(x): True for x in masks["filtered"] if x[0] >= "2025-04-22"}
    save("resume_input_receipt.json", {**receipt, "exact002868_status_dates": [x["date"] for x in pairs], "new_status_only_rows": len(extra), "old_bounded_repairs_reused": previous_repairs, "no_missing_price_volume_or_fill_invention": True, "frozen_mask_reused_no_feature_recalculation": True, "remaining_filter_keys": len(selected)})
    paths = []
    for item in old["paths"]:
        if item["status"] != "SOURCE_PENDING":
            paths.append(item)
            continue
        key = item["path"]
        prefix = pd.read_csv(OUT / (key + "_nav.csv"), dtype={"date": str})
        trades = pd.read_csv(OUT / (key + "_trades.csv"), dtype={"date": str, "symbol": str})
        tail = simulate(bars, calendar, policy="stop_t2_close_loss15", cost=item["cost_single_side"], evaluation_start="2025-04-22", adjustment_factor_role="total_return_factor", preopen_signal_filter=selected,
                        resume_state={"cash": item["terminal_cash"], "nav": item["last_completed_nav"], "holdings": item["terminal_holdings"]})
        nav = pd.concat([prefix, pd.DataFrame(tail["daily"]).drop(columns=["positions"], errors="ignore")], ignore_index=True)
        if nav.date.duplicated().any():
            raise ValueError("Duplicate continued date")
        full_trades = pd.concat([trades, pd.DataFrame(tail["trades"])], ignore_index=True)
        events = read(OUT / (key + "_stop_events.json")) + tail["stop_events"]
        orders = read(OUT / (key + "_stop_orders.json")) + [x for x in tail["orders"] if x["side"] == "sell"]
        blocked = Counter(item["blocked_orders"]) + Counter(tail["summary"]["blocked_orders"])
        result = tail["summary"]
        artifact = key + "_completed"
        result.update(path=key, artifact_prefix=artifact, completed_sessions=len(nav), requested_sessions=659,
                      buys=int(full_trades.side.eq("buy").sum()), completed_round_trips=int(full_trades.side.eq("sell").sum()),
                      total_fees=item["total_fees"] + result["total_fees"], blocked_orders=dict(blocked),
                      sale_reasons=dict(Counter(full_trades.loc[full_trades.side.eq("sell"), "reason"])), stop_events_count=len(events))
        result["metrics"] = performance(nav.nav / nav.nav.shift(1).fillna(100000) - 1) if result["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" else None
        save(artifact + "_summary.json", result)
        save(artifact + "_stop_events.json", events)
        save(artifact + "_stop_orders.json", orders)
        nav.to_csv(OUT / (artifact + "_nav.csv"), index=False, encoding="utf-8-sig")
        full_trades.to_csv(OUT / (artifact + "_trades.csv"), index=False, encoding="utf-8-sig")
        paths.append(result)
        print(json.dumps(clean({"path": key, "status": result["status"], "metrics": result["metrics"], "buys": result["buys"], "sale_reasons": result["sale_reasons"], "pending": result["source_pending"]}), ensure_ascii=False), flush=True)
    summary = {**old, "paths": paths, "resume_source_repair": binding(OUT / "resume_protocol.json"), "status": "ALL_SIX_FIXED_STOP_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in paths) else "NEW_STOP_SOURCE_PENDING"}
    save("summary_complete.json", summary)
    r = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    r.append_event("source_repair", "LIMIT_STOP_CLOSE_002868_20250422_HELD_STATE", protocol)
    r.close()
    save("state.json", {"status": summary["status"], "process": None, "registry_total": summary["registry_total"], "official_compute_spent": 0})


if __name__ == "__main__":
    run()
