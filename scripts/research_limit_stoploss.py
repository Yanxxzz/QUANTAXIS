"""Three user-specified exit rules on the existing fixed event filter.

No official account calls, new collection, descriptor search or old control
replay. Each exit definition is registered before any new account results.
"""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import os
import sys
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import limit_price
from scripts.research_io import read, binding, clean, write_clean_json
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_event_resume import extension_panel
from panda_alpha.limit_reversal import simulate
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key

OUT = ROOT / "research_runs/limit_stoploss_20261014"
PARENT = ROOT / "research_runs/limit_reversal_optimization_20261014"
POLICIES = ("stop_t1_limit_down", "stop_t2_limit_down", "stop_t2_close_loss15")


def save(name, value):
    write_clean_json(OUT / name, value)


def held_status_repairs(bars):
    """Reuse completed exact sources; no new dates, prices or entry eligibility."""
    dates = {"600234": ["2024-04-29", "2025-05-19"],
             "605178": ["2025-07-24", "2025-07-25", "2025-07-28", "2025-07-29"],
             "002693": ["2025-04-29", "2026-05-22"]}
    evidence = read(PARENT / "suspension_factor_equivalence.json")
    exact = {(x["code"], x["suspension_date"]): x["exact_current_asof_factor"]["adj"]
             for x in evidence["observations"] if x["equal"]}
    for date, name in [("2025-04-29", "002693_asof_source_check.json"),
                       ("2026-05-22", "002693_20260522_asof_check.json")]:
        item = read(PARENT / name)
        if not item["equal"]:
            raise ValueError("Prior exact suspension factor comparison is incomplete")
        factor = item.get("suspension_factor", item.get("suspension_exact_current_factor"))
        exact[("002693", date)] = factor["adj"]
    added = []
    for code, days in dates.items():
        for day in days:
            mask = bars.symbol.eq(code) & bars.date.eq(day)
            factor = exact[(code, day)]
            if mask.any():
                received = pd.to_numeric(bars.loc[mask, "adjustment_factor"], errors="coerce")
                if received.notna().any() and not np.allclose(received.dropna(), factor, rtol=0, atol=1e-12):
                    raise ValueError("Frozen suspension factor changed")
                bars.loc[mask, "trade_status"] = "suspended"
                bars.loc[mask, "source_status"] = "source_explicit_suspension"
                bars.loc[mask, "suspension_confirmed"] = True
                bars.loc[mask, "adjustment_factor"] = factor
            else:
                added.append({"symbol": code, "date": day, "trade_status": "suspended",
                              "source_status": "source_explicit_suspension",
                              "suspension_confirmed": True, "entry_eligible": False,
                              "adjustment_factor": factor})
    if added:
        bars = pd.concat([bars, pd.DataFrame(added)], ignore_index=True)
    for code, start, end, rate in [("600234", "2024-04-30", "2025-05-20", .05),
                                  ("002693", "2026-05-25", "2026-06-24", .10)]:
        mask = bars.symbol.eq(code) & bars.date.ge(start) & bars.date.lt(end)
        for side in ("up", "down"):
            bars.loc[mask, side + "_limit"] = [limit_price(v, rate, side)
                                                for v in bars.loc[mask, "prevclose"]]
    return bars, {"exact_suspension_dates": dates, "new_status_only_rows": len(added),
                  "exact_asof_factor_reused": True, "entry_eligibility_not_repaired": True,
                  "no_price_volume_or_fill_invention": True,
                  "execution_band_patches": ["600234:2024-04-30..2025-05-19 5%",
                                              "002693:2026-05-25..2026-06-23 10%"],
                  "outside_bound_patches_original_dynamic_rules": True}


def run():
    if (OUT / "protocol.json").exists():
        raise RuntimeError("A frozen unit already exists; collect or resume it, never recreate")
    parent = read(PARENT / "resumed_summary3.json")
    if parent["status"] != "ALL_EVENT_FACTOR_AND_FOUR_STRATEGY_PATHS_COMPLETE":
        raise ValueError("Existing no-stop controls must be complete")
    cutoff = parent["selection"]["cutoff"]
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    source_names = ["600234_status_review.json", "605178_status_review.json", "002693_status_review.json",
                    "002693_20260522_status_review.json", "suspension_factor_equivalence.json",
                    "002693_asof_source_check.json", "002693_20260522_asof_check.json"]
    protocol = {"user_authorization": binding(OUT / "authorization.json"),
                "start": "2024-01-02", "end": meta["end"], "reference_sessions": 659,
                "source_metadata": binding(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json"),
                "existing_no_stop_result": binding(PARENT / "resumed_summary3.json"),
                "existing_no_stop_curve": binding(PARENT / "completed_daily_nav3.csv"),
                "source_reviews_reused": [binding(PARENT / name) for name in source_names],
                "fixed_filter": {"feature": "prior_extension_20", "cutoff": cutoff,
                                 "selection": binding(PARENT / "training_summary.json")},
                "engine": binding(ROOT / "panda_alpha/limit_reversal.py"),
                "reader": binding(ROOT / "panda_alpha/limit_event_source.py"),
                "extension": binding(ROOT / "panda_alpha/limit_event_resume.py"),
                "runner": binding(__file__), "policies": list(POLICIES),
                "take_profit": "strict economic price gain>10%, T+1, unchanged",
                "t_role": "actual buy reference day; offsets1/2 not counting buy as1",
                "intraday_stops": "specified day touches actual lower limit; sell at lower limit if potential execution; simultaneous profit/stop uses stop first",
                "close_stop": "only still-held t+2 day-end economic mark loss strictly>15%; earliest t+3 open; fee excluded from trigger",
                "suspension_close_role": "explicit legal held mark proxy separately logged, not observed traded close",
                "triggered_exit": "persistent pending; blocked one-price lower limits, suspension or daily capacity retry at later open",
                "no_stop_if_no_designated_day_trigger": True,
                "initial_cash": 100000, "max_slots": 5, "slot_fraction": .20,
                "code_order": "ascending preopen", "cost_single_side": [.003, .005],
                "max_daily_participation": .01, "same_day_sale_cash_recycled": False,
                "official_compute_spent": 0, "old_source_training_IC_and_control_replay": False,
                "new_economic_contexts": 3, "previously_exposed_window_not_new_OOS": True}
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    if registry.total != 565:
        raise ValueError("Registry moved; inspect current authority instead of resetting")
    registrations = []
    for name in POLICIES:
        candidate = {"candidate_id": "LIMIT_FILTER_" + name.upper() + "_20261014", "direction": 1,
                     "strategy_spec": {"base_filter": protocol["fixed_filter"], "engine_sha": protocol["engine"]["sha256"],
                                       "exit": name, "execution_contract": {k: protocol[k] for k in ["take_profit", "t_role", "intraday_stops", "close_stop", "triggered_exit"]},
                                       "source": protocol["source_metadata"]["sha256"],
                                       "common_window_start": protocol["start"], "initial_cash": 100000}}
        window = {"start": protocol["start"], "end": protocol["end"]}
        definition = strategy_definition(candidate, window, 5, 2)
        registry.record(candidate, window, 5, 2)
        registrations.append({"candidate": candidate, "key": trial_key(definition), "definition": definition})
    protocol["registrations"] = registrations
    protocol["registry_total"] = registry.total
    registry.close()
    save("protocol.json", protocol)
    save("state.json", {"status": "RUNNING_THREE_FIXED_NEW_EXIT_POLICIES", "process": os.getpid(),
                        "argv": str(Path(__file__).resolve()), "registry_total": protocol["registry_total"],
                        "official_compute_spent": 0, "observed_at": datetime.now(timezone.utc).isoformat()})
    print("Three new exit contexts frozen and registered before new account results", flush=True)
    panel, receipt, bars = load_retained_panel(read(ROOT / "config/panda-alpha.local.json"), meta, include_strategy=True)
    original = read(PARENT / "source_receipt.json")
    for key in ("raw_identity_row_hash", "factor_record_hash", "raw_rows"):
        if receipt[key] != original[key]:
            raise ValueError("Retained source changed; do not quietly change scope")
    score = extension_panel(panel["close"], panel["adjustment_factor"])
    up = ((panel["close"] - panel["up_limit"]).abs() <= 1e-7) & panel["high"].le(panel["up_limit"] + 1e-7) & panel["low"].ge(panel["down_limit"] - 1e-7)
    valid = np.isfinite(score) & up.shift(2, fill_value=False) & up.shift(1, fill_value=False)
    valid &= panel["eligible"] & panel["eligible"].shift(1, fill_value=False) & panel["eligible"].shift(2, fill_value=False)
    selected = valid & score.le(cutoff)
    filter_mask = {(str(score.index[i]), str(score.columns[j])): True for i, j in np.argwhere(selected.to_numpy())}
    old_mask = read(PARENT / "resume_masks.json")
    old_filtered = {tuple(k) for k in old_mask["filtered"]}
    if any((tuple(key) in filter_mask) != (tuple(key) in old_filtered) for key in old_mask["common"]):
        raise ValueError("Previously frozen selection mask differs")
    bars, repairs = held_status_repairs(bars)
    save("source_receipt.json", {**receipt, "repairs": repairs, "preopen_selected_mask_keys": len(filter_mask),
                                "previous_frozen_mask_compared": True,
                                "no_new_training_labels_factor_IC_or_old_cost_replay": True})
    del panel, score, selected, valid, up
    paths = []
    for policy in POLICIES:
        for cost in (.003, .005):
            key = policy + "_" + str(int(cost * 10000)) + "bp"
            result = simulate(bars, calendar, policy=policy, cost=cost, evaluation_start=protocol["start"],
                              adjustment_factor_role="total_return_factor", preopen_signal_filter=filter_mask)
            item = result["summary"]
            item.update(path=key, buys=sum(t["side"] == "buy" for t in result["trades"]),
                        sale_reasons=dict(Counter(t.get("reason") for t in result["trades"] if t["side"] == "sell")),
                        stop_events_count=len(result.get("stop_events", [])))
            save(key + "_summary.json", item)
            save(key + "_stop_events.json", result.get("stop_events", []))
            pd.DataFrame(result["daily"]).drop(columns=["positions"], errors="ignore").to_csv(OUT / (key + "_nav.csv"), index=False, encoding="utf-8-sig")
            pd.DataFrame(result["trades"]).to_csv(OUT / (key + "_trades.csv"), index=False, encoding="utf-8-sig")
            save(key + "_stop_orders.json", [x for x in result["orders"] if x["side"] == "sell"])
            paths.append(item)
            print(json.dumps(clean({"path": key, "status": item["status"], "metrics": item["metrics"],
                                    "buys": item["buys"], "sale_reasons": item["sale_reasons"], "pending": item["source_pending"]}), ensure_ascii=False), flush=True)
            save("checkpoint.json", {"completed_or_source_pending_paths": paths, "process": os.getpid()})
    summary = {"status": "ALL_SIX_FIXED_STOP_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in paths) else "NEW_STOP_SOURCE_PENDING",
               "paths": paths, "existing_no_stop": [x for x in parent["paths"] if x["path"].startswith("filtered")],
               "protocol": binding(OUT / "protocol.json"), "registry_total": protocol["registry_total"],
               "official_compute_spent": 0, "new_contexts": 3, "not_untouched_OOS": True}
    save("summary.json", summary)
    save("state.json", {"status": summary["status"], "process": None, "registry_total": protocol["registry_total"], "official_compute_spent": 0})


if __name__ == "__main__":
    run()
