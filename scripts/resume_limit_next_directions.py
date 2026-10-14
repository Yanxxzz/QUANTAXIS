"""Continue eight bound unfinished accounts; four complete paths stay intact."""
from collections import Counter
from pathlib import Path
import json
import os
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding, limit_price
from scripts.research_limit_event_factor import clean
from scripts.research_limit_next_directions import reuse_002868_status
from scripts.research_limit_stoploss import held_status_repairs
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_reversal import simulate
from panda_alpha.evaluation import performance
from panda_alpha.registry import ResearchRegistry

OUT = ROOT / "research_runs/limit_next_directions_20261014"


def save(name, value):
    (OUT / name).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2,
                                     allow_nan=False), encoding="utf-8")


def run():
    if (OUT / "resume_protocol.json").exists():
        raise RuntimeError("Existing continuation must be collected; do not replay")
    old = read(OUT / "summary.json")
    pending = [x for x in old["paths"] if x["status"] == "SOURCE_PENDING"]
    expected = {"recovery_parent_next_open": ("600370", "2026-04-29"),
                "market_gate": ("002686", "2026-03-31"),
                "firstboard_parent_next_open": ("600537", "2026-04-28"),
                "firstboard_repair_next_open": ("600537", "2026-04-28")}
    if len(pending) != 8:
        raise ValueError("This exact source continuation expects eight unfinished paths")
    for item in pending:
        code, day = expected[item["unit"]]
        if item["source_pending"] != [{"date": day, "reason": "held_unknown_status:" + code + ":" + day}]:
            raise ValueError("Unexpected source state: investigate instead of guessing")
    frozen = read(OUT / "clock_repair_protocol.json")
    if binding(ROOT / "panda_alpha/limit_reversal.py") != frozen["engine"]:
        raise ValueError("Runtime changed after the original accounts")
    checks = read(OUT / "three_status_source_asof_complete.json")
    if not checks["all_factor_pairs_equal"]:
        raise ValueError("Suspension factor/economic mark binding incomplete")
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    first = min(expected[x["unit"]][1] for x in pending)
    meta["start"] = calendar[calendar.index(first) - 2]
    meta["calendar"] = [d for d in calendar if d >= meta["start"]]
    meta["source_rows"] = checks["bounded_raw_rows"]
    if meta["start"] != checks["bounded_quote_start"]:
        raise ValueError("Bounded quote read identity changed")
    reviews = ["600370_20260429_status_review.json", "002686_20260331_status_review.json", "600537_20260428_status_review.json"]
    protocol = {"original": binding(OUT / "protocol.json"), "clock_repair": binding(OUT / "clock_repair_protocol.json"),
                "old_results": binding(OUT / "summary.json"), "source_reviews": [binding(OUT / x) for x in reviews],
                "asof_checks": binding(OUT / "three_status_source_asof_complete.json"),
                "frozen_masks": binding(OUT / "frozen_masks.json"),
                "engine": binding(ROOT / "panda_alpha/limit_reversal.py"), "runner": binding(__file__),
                "bounded_quote_start": meta["start"], "end": meta["end"],
                "pending_paths_only": [x["path"] for x in pending], "reference_indices_preserved": True,
                "no_signal_feature_IC_label_or_complete_account_replay": True,
                "new_contexts": 0, "official_compute_spent": 0}
    save("resume_protocol.json", protocol)
    save("state.json", {"status": "RESUMING_EIGHT_BOUND_SOURCE_ACCOUNTS", "process": os.getpid(),
                        "argv": str(Path(__file__).resolve()), "registry_total": old["registry_total"],
                        "official_compute_spent": 0})
    panel, receipt, bars = load_retained_panel(read(ROOT / "config/panda-alpha.local.json"), meta, include_strategy=True)
    if receipt["factor_record_hash"] != read(OUT / "source_receipt.json")["factor_record_hash"]:
        raise ValueError("Original retained cumulative factors changed")
    del panel
    bars, old_repair = held_status_repairs(bars)
    bars, prior_extra = reuse_002868_status(bars)
    bars = bars[bars.date.ge(meta["start"])].copy()
    extra = []
    for item in checks["factor_pairs"]:
        code, day, factor = item["code"], item["suspension_date"], item["current_asof_factor"]["adj"]
        mask = bars.symbol.eq(code) & bars.date.eq(day)
        if mask.any():
            bars.loc[mask, "trade_status"] = "suspended"
            bars.loc[mask, "source_status"] = "source_explicit_suspension"
            bars.loc[mask, "suspension_confirmed"] = True
            bars.loc[mask, "adjustment_factor"] = factor
        else:
            extra.append({"date": day, "symbol": code, "trade_status": "suspended",
                          "source_status": "source_explicit_suspension", "suspension_confirmed": True,
                          "entry_eligible": False, "adjustment_factor": factor})
    if extra:
        bars = pd.concat([bars, pd.DataFrame(extra)], ignore_index=True)
    for patch in checks["bounded_execution_band_patches"]:
        mask = bars.symbol.eq(patch["code"]) & bars.date.ge(patch["effective_start"]) & bars.date.lt(patch["exclusive_end"])
        for side in ("up", "down"):
            bars.loc[mask, side + "_limit"] = [limit_price(v, patch["rate"], side) for v in bars.loc[mask, "prevclose"]]
    save("resume_input_receipt.json", {**receipt, "old_held_repairs": old_repair,
                                       "new_status_only_rows": len(extra),
                                       "new_status_dates": [{"code": x["code"], "date": x["suspension_date"]} for x in checks["factor_pairs"]],
                                       "band_patches": checks["bounded_execution_band_patches"],
                                       "no_raw_quote_volume_or_prediction_mask_changes": True})
    masks = {k: {tuple(x): True for x in vals} for k, vals in read(OUT / "frozen_masks.json").items()}
    paths = []
    for item in old["paths"]:
        if item["status"] != "SOURCE_PENDING":
            paths.append(item)
            continue
        key = item["path"]
        start = expected[item["unit"]][1]
        prefix = pd.read_csv(OUT / (key + "_nav.csv"), dtype={"date": str})
        trades = pd.read_csv(OUT / (key + "_trades.csv"), dtype={"date": str, "symbol": str})
        signal = {k: v for k, v in masks[item["signal_mask"]].items() if k[0] >= start}
        args = {"preopen_signal_candidates": signal} if item["entry_mode"] == "next_open" else {"preopen_signal_filter": signal}
        tail = simulate(bars, calendar, policy=item["policy"]["name"], cost=item["cost_single_side"],
                        evaluation_start=start, adjustment_factor_role="total_return_factor", entry_mode=item["entry_mode"],
                        resume_state={"cash": item["terminal_cash"], "nav": item["last_completed_nav"], "holdings": item["terminal_holdings"]}, **args)
        nav = pd.concat([prefix, pd.DataFrame(tail["daily"]).drop(columns=["positions"], errors="ignore")], ignore_index=True)
        complete_trades = pd.concat([trades, pd.DataFrame(tail["trades"])], ignore_index=True)
        if nav.date.duplicated().any():
            raise ValueError("Continuation repeated account dates")
        result = tail["summary"]
        result.update(path=key, unit=item["unit"], signal_mask=item["signal_mask"], entry_mode=item["entry_mode"],
                      artifact_prefix=key + "_completed", completed_sessions=len(nav), requested_sessions=659,
                      total_fees=item["total_fees"] + result["total_fees"],
                      blocked_orders=dict(Counter(item["blocked_orders"]) + Counter(result["blocked_orders"])),
                      buys=int(complete_trades.side.eq("buy").sum()), completed_round_trips=int(complete_trades.side.eq("sell").sum()),
                      sale_reasons=dict(Counter(complete_trades.loc[complete_trades.side.eq("sell"), "reason"])))
        result["metrics"] = performance(nav.nav / nav.nav.shift(1).fillna(100000) - 1) if result["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" else None
        artifact = result["artifact_prefix"]
        save(artifact + "_summary.json", result)
        nav.to_csv(OUT / (artifact + "_nav.csv"), index=False, encoding="utf-8-sig")
        complete_trades.to_csv(OUT / (artifact + "_trades.csv"), index=False, encoding="utf-8-sig")
        save(artifact + "_exit_orders.json", read(OUT / (key + "_exit_orders.json")) + [x for x in tail["orders"] if x["side"] == "sell"])
        paths.append(result)
        save("resume_checkpoint.json", {"paths": paths, "process": os.getpid()})
        print(json.dumps({"path": key, "status": result["status"], "sessions": len(nav), "pending": result["source_pending"]}, ensure_ascii=False), flush=True)
    summary = {**old, "paths": paths, "resume_protocol": binding(OUT / "resume_protocol.json"),
               "status": "ALL_TWELVE_FIXED_NEXT_DIRECTION_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in paths) else "NEXT_DIRECTION_SOURCE_PENDING"}
    save("summary_complete.json", summary)
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    registry.append_event("source_repair", "LIMIT_NEXT_EIGHT_HELD_SOURCE_CONTINUATION", protocol)
    registry.close()
    save("state.json", {"status": summary["status"], "process": None, "registry_total": old["registry_total"], "official_compute_spent": 0})


if __name__ == "__main__":
    run()
