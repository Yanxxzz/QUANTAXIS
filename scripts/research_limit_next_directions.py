"""Six preregistered local accounts: four hypotheses and two clock controls.

Reuse the frozen original source grid and completed baseline results. No new
quote collection, training, event labels, IC, official calls or parameter scan.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.research_io import read, binding, clean, write_clean_json
from scripts.research_limit_stoploss import held_status_repairs
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_event_resume import extension_panel
from panda_alpha.limit_entry_signals import build_limit_entry_signals
from panda_alpha.limit_reversal import simulate
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key

OUT = ROOT / "research_runs/limit_next_directions_20261014"
PARENT = ROOT / "research_runs/limit_reversal_optimization_20261014"
STOP = ROOT / "research_runs/limit_stoploss_20261014"
UNITS = (
    ("recovery_parent_next_open", "recovery_parent_mask", "next_open", "baseline"),
    ("recovery_next_open", "recovery_mask", "next_open", "baseline"),
    ("time_after_5", "base_mask", "lower_limit", "time_after_5"),
    ("market_gate", "market_mask", "lower_limit", "baseline"),
    ("firstboard_parent_next_open", "first_board_parent_mask", "next_open", "baseline"),
    ("firstboard_repair_next_open", "first_board_mask", "next_open", "baseline"),
)


def save(name, value):
    write_clean_json(OUT / name, value)


def reuse_002868_status(bars):
    checks = read(STOP / "002868_20250422_asof_check.json")
    pairs = checks["exact_suspension_factor_pairs"]
    if len(pairs) != 5 or not all(x["equal"] for x in pairs):
        raise ValueError("Existing exact held-only suspension/factor evidence incomplete")
    added = []
    for item in pairs:
        day, factor = item["date"], item["current_asof_factor"]["adj"]
        mask = bars.symbol.eq("002868") & bars.date.eq(day)
        if mask.any():
            received = pd.to_numeric(bars.loc[mask, "adjustment_factor"], errors="coerce")
            if received.notna().any() and not np.allclose(received.dropna(), factor, rtol=0, atol=1e-12):
                raise ValueError("Frozen 002868 factor changed")
            bars.loc[mask, "trade_status"] = "suspended"
            bars.loc[mask, "source_status"] = "source_explicit_suspension"
            bars.loc[mask, "suspension_confirmed"] = True
            bars.loc[mask, "adjustment_factor"] = factor
        else:
            added.append({"date": day, "symbol": "002868", "trade_status": "suspended",
                          "source_status": "source_explicit_suspension", "suspension_confirmed": True,
                          "entry_eligible": False, "adjustment_factor": factor})
    if added:
        bars = pd.concat([bars, pd.DataFrame(added)], ignore_index=True)
    return bars, {"exact_dates": [x["date"] for x in pairs], "new_status_only_rows": len(added),
                  "predictive_input_repaired": False, "raw_quote_or_volume_filled": False}


def run():
    continuation = (OUT / "protocol.json").exists()
    if continuation and (OUT / "clock_repair_protocol.json").exists():
        raise RuntimeError("Existing continuation exists: collect or exact-checkpoint resume, never recreate")
    if continuation and any((OUT / name).exists() for name in ("source_receipt.json", "formation_receipt.json", "frozen_masks.json", "checkpoint.json", "summary.json")):
        raise RuntimeError("Source or account artifact already exists; this pre-formation repair cannot replay it")
    parent = read(PARENT / "resumed_summary3.json")
    if parent["status"] != "ALL_EVENT_FACTOR_AND_FOUR_STRATEGY_PATHS_COMPLETE":
        raise ValueError("Read-only old baseline is not complete")
    cutoff = parent["selection"]["cutoff"]
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    expected = [x for x in calendar if x >= "2024-01-02"]
    if len(expected) != 659 or meta["end"] != "2026-09-18":
        raise ValueError("Fixed comparison calendar changed")
    common = {"start": "2024-01-02", "end": meta["end"], "initial_cash": 100000,
              "max_slots": 5, "slot_fraction": .20, "lot_size": 100,
              "single_side_cost": [.003, .005], "daily_participation_bound": .01,
              "take_profit": "strict economic price gain>10%; T+1; unchanged",
              "other_stop": None, "terminal_liquidation_forced": False,
              "code_order": "ascending before open; slots/cash reserved before same-day exits",
              "sale_cash_recycling_same_day": False,
              "quantity_model": "same-source total-return-equivalent units; not actual CA shares/cash",
              "opening_limit_up": "next-open buy blocked even if later board opens",
              "fill_certified": False, "same_source_identities": len(meta["codes"]),
              "previously_exposed_window_not_untouched_OOS": True}
    formulas = {
        "recovery_parent_next_open": "old fixed two-upper-close/extension<=cutoff event touches lower with legal observed bar; next reference open",
        "recovery_next_open": "same recovery parent, additionally event close>lower; next reference open",
        "time_after_5": "old fixed screened lower-limit bid; allow t+1..t+5; attempt exit t+6 open, persistent if blocked; no percentage stop",
        "market_gate": "old fixed screened lower-limit bid; previous original-source known eligible/trading cohort lower-close count<=upper-close count; unknown cohort blocks",
        "firstboard_parent_next_open": "b upper-close after20complete trading reference bars with no upper-close; b+1 normal eligible not upper-close; b+2 open; no old extension filter",
        "firstboard_repair_next_open": "same firstboard parent plus b+1 economic close>b-1 economic close and raw low>lower; b+2 open; no old extension filter",
    }
    protocol = {"authorization": binding(OUT / "authorization.json"), "common": common,
                "source_metadata": binding(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json"),
                "old_source": binding(PARENT / "source_receipt.json"),
                "old_filtered_result": binding(PARENT / "resumed_summary3.json"),
                "old_filtered_nav": binding(PARENT / "completed_daily_nav3.csv"),
                "selection": binding(PARENT / "training_summary.json"), "cutoff": cutoff,
                "reader": binding(ROOT / "panda_alpha/limit_event_source.py"),
                "engine": binding(ROOT / "panda_alpha/limit_reversal.py"),
                "signals": binding(ROOT / "panda_alpha/limit_entry_signals.py"),
                "extension": binding(ROOT / "panda_alpha/limit_event_resume.py"),
                "runner": binding(__file__), "synthetic_QA": binding(OUT / "synthetic_QA.json"),
                "held_only_repair_runtime": binding(ROOT / "scripts/research_limit_stoploss.py"),
                "002868_status_review": binding(STOP / "002868_20250422_status_review.json"),
                "002868_factor_review": binding(STOP / "002868_20250422_asof_check.json"),
                "formulas": formulas,
                "paired_comparison": {"pairs": {"recovery_next_open": "recovery_parent_next_open",
                                                  "time_after_5": "existing_filtered",
                                                  "market_gate": "existing_filtered",
                                                  "firstboard_repair_next_open": "firstboard_parent_next_open"},
                                      "statistic": "252*mean(paired complete daily net-return difference)",
                                      "bootstrap": "whole calendar-month blocks; weighted by actual repeated days",
                                      "seed": 20261014, "replications": 2000, "interval": [.025, .975],
                                      "full_economic_upgrade": "both costs higher compounded return and Sharpe, lower maximum drawdown",
                                      "limits": "descriptive; previously exposed; multiple comparisons unadjusted; not independent events or new OOS"},
                "new_contexts": 6, "official_compute_spent": 0,
                "old_cost_training_IC_labels_replayed": False}
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    if continuation:
        old = read(OUT / "protocol.json")
        if registry.total != old["registry_total"] or registry.total != 574:
            raise ValueError("Repair must retain the six existing economic registrations")
        for field in ("common", "formulas", "cutoff", "paired_comparison", "source_metadata", "engine", "reader", "extension"):
            if protocol[field] != old[field]:
                raise ValueError("Clock/source precondition repair changed a frozen economic contract")
        protocol["original_frozen_protocol"] = binding(OUT / "protocol.json")
        protocol["original_signal_runtime"] = binding(OUT / "frozen_runtime/limit_entry_signals.py")
        protocol["original_runner_runtime"] = binding(OUT / "frozen_runtime/research_limit_next_directions.py")
        protocol["repair_synthetic_QA"] = binding(OUT / "clock_repair_QA.json")
        protocol["repair"] = {"interrupted_pid": 33372, "phase": "incomplete source read before formation or PnL artifacts",
                              "entry_clock": "Remove next-execution-day eligibility from yesterday's close-formed masks; engine checks it after slot/cash reservations",
                              "complete_firstboard_history": "Require positive received shares for prior20, board and pullback trading bars",
                              "common_firstboard_support": "Parent and target both require known b-1/b+1 total-return factor pair",
                              "market_gate": "Trust frozen original base; only additional gate reads previous-reference known-cohort breadth",
                              "economic_definition_or_params_changed": False, "new_contexts": 0,
                              "new_labels_or_account_results_before_repair": False}
        protocol["registrations"] = old["registrations"]
        protocol["registry_total"] = old["registry_total"]
        registry.append_event("execution_repair", "LIMIT_NEXT_CLOSE_FORMED_CLOCK_20261014", protocol)
        save("clock_repair_protocol.json", protocol)
    else:
        if registry.total != 568:
            raise ValueError("Registry moved: inspect current ledger rather than resetting")
        registrations = []
        for name, mask, entry, policy in UNITS:
            candidate = {"candidate_id": "LIMIT_NEXT_" + name.upper() + "_20261014", "direction": 1,
                         "strategy_spec": {"formula": formulas[name], "common": common,
                                           "cutoff_first3_only": cutoff if not name.startswith("firstboard") else None,
                                           "source_sha": protocol["source_metadata"]["sha256"],
                                           "engine_sha": protocol["engine"]["sha256"],
                                           "signal_sha": protocol["signals"]["sha256"],
                                           "mask": mask, "entry": entry, "exit_policy": policy}}
            window = {"start": common["start"], "end": common["end"]}
            definition = strategy_definition(candidate, window, 1, 2)
            registry.record(candidate, window, 1, 2)
            registrations.append({"name": name, "candidate": candidate, "key": trial_key(definition), "definition": definition})
        protocol["registrations"] = registrations
        protocol["registry_total"] = registry.total
        save("protocol.json", protocol)
    registry.close()
    save("state.json", {"status": "RUNNING_SIX_FIXED_LOCAL_CONTEXTS", "process": os.getpid(),
                        "argv": str(Path(__file__).resolve()), "registry_total": protocol["registry_total"],
                        "official_compute_spent": 0, "observed_at": datetime.now(timezone.utc).isoformat()})
    print("Frozen definitions retained; incomplete pre-formation source read continues" if continuation else "Six definitions frozen and registered before new results; source read begins", flush=True)
    panel, receipt, bars = load_retained_panel(read(ROOT / "config/panda-alpha.local.json"), meta, include_strategy=True)
    old_source = read(PARENT / "source_receipt.json")
    for key in ("raw_identity_row_hash", "factor_record_hash", "raw_rows"):
        if receipt[key] != old_source[key]:
            raise ValueError("Original retained source identity changed")
    panel["trade_status"] = bars.pivot(index="date", columns="symbol", values="trade_status").reindex(index=panel["close"].index, columns=panel["close"].columns)
    score = extension_panel(panel["close"], panel["adjustment_factor"])
    up = ((panel["close"] - panel["up_limit"]).abs() <= 1e-7) & panel["high"].le(panel["up_limit"] + 1e-7) & panel["low"].ge(panel["down_limit"] - 1e-7)
    selected = np.isfinite(score) & up.shift(2, fill_value=False) & up.shift(1, fill_value=False)
    selected &= panel["eligible"] & panel["eligible"].shift(1, fill_value=False) & panel["eligible"].shift(2, fill_value=False) & score.le(cutoff)
    base = {(str(score.index[i]), str(score.columns[j])): True for i, j in np.argwhere(selected.to_numpy())}
    old_mask = read(PARENT / "resume_masks.json")
    old_filtered = {tuple(k) for k in old_mask["filtered"]}
    if any((tuple(k) in base) != (tuple(k) in old_filtered) for k in old_mask["common"]):
        raise ValueError("Previously frozen base selection changed")
    signals = build_limit_entry_signals(panel, calendar, base, adjustment_factor_role="total_return_factor")
    signals["base_mask"] = base
    save("frozen_masks.json", {name: [list(k) for k, v in signals[name].items() if v]
                               for name in ["base_mask"] + sorted({x[1] for x in UNITS})})
    save("formation_receipt.json", {"coverage": signals["coverage"], "contracts": signals["contracts"],
                                    "masks": binding(OUT / "frozen_masks.json"),
                                    "future_price_or_labels_read": False})
    pd.DataFrame(signals["market_coverage"]).to_csv(OUT / "market_coverage.csv", index=False, encoding="utf-8-sig")
    save("firstboard_formation_roles.json", signals["first_board_parent_rows"])
    bars, old_repairs = held_status_repairs(bars)
    bars, extra_repairs = reuse_002868_status(bars)
    save("source_receipt.json", {**receipt, "base_mask_keys": len(base), "old_base_mask_compared": True,
                                "held_execution_only_repairs": old_repairs, "extra_held_only_repairs": extra_repairs,
                                "prediction_panel_was_original_before_held_repairs": True,
                                "no_training_IC_labels_or_old_cost_replay": True})
    del panel, score, selected, up, base
    print("Frozen source and close-formed masks verified; sequential accounts begin", flush=True)
    paths = []
    for name, mask, mode, policy in UNITS:
        for cost in (.003, .005):
            key = name + "_" + str(int(cost * 10000)) + "bp"
            args = {"preopen_signal_candidates": signals[mask]} if mode == "next_open" else {"preopen_signal_filter": signals[mask]}
            result = simulate(bars, calendar, policy=policy, cost=cost, evaluation_start=common["start"],
                              entry_mode=mode, adjustment_factor_role="total_return_factor", **args)
            item = result["summary"]
            item.update(path=key, unit=name, signal_mask=mask, entry_mode=mode,
                        buys=sum(t["side"] == "buy" for t in result["trades"]),
                        sale_reasons=dict(Counter(t.get("reason") for t in result["trades"] if t["side"] == "sell")))
            save(key + "_summary.json", item)
            pd.DataFrame(result["daily"]).drop(columns=["positions"], errors="ignore").to_csv(OUT / (key + "_nav.csv"), index=False, encoding="utf-8-sig")
            pd.DataFrame(result["trades"]).to_csv(OUT / (key + "_trades.csv"), index=False, encoding="utf-8-sig")
            save(key + "_exit_orders.json", [x for x in result["orders"] if x["side"] == "sell"])
            paths.append(item)
            save("checkpoint.json", {"completed_or_source_pending_paths": paths, "process": os.getpid()})
            print(json.dumps({"path": key, "status": item["status"], "sessions": item["completed_sessions"],
                              "pending": item["source_pending"]}, ensure_ascii=False), flush=True)
    summary = {"status": "ALL_TWELVE_FIXED_NEXT_DIRECTION_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in paths) else "NEXT_DIRECTION_SOURCE_PENDING",
               "paths": paths, "existing_filtered": [x for x in parent["paths"] if x["path"].startswith("filtered")],
               "protocol": binding(OUT / "protocol.json"), "clock_repair": binding(OUT / "clock_repair_protocol.json") if continuation else None, "registry_total": protocol["registry_total"],
               "official_compute_spent": 0, "new_contexts": 6, "not_untouched_OOS": True}
    save("summary.json", summary)
    save("state.json", {"status": summary["status"], "process": None, "registry_total": protocol["registry_total"], "official_compute_spent": 0})


if __name__ == "__main__":
    run()
