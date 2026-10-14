"""Second exact status repair: only two unfinished next-open parent suffixes."""
from collections import Counter
from pathlib import Path
import os
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding, limit_price
from scripts.research_limit_stoploss import held_status_repairs
from scripts.research_limit_next_directions import reuse_002868_status
from scripts.resume_limit_next_directions import save, OUT
from panda_alpha.limit_event_source import load_retained_panel
from panda_alpha.limit_reversal import simulate
from panda_alpha.evaluation import performance
from panda_alpha.registry import ResearchRegistry


def run():
    if (OUT / "parent_resume_protocol.json").exists():
        raise RuntimeError("The parent continuation already exists: collect, never replay")
    old = read(OUT / "summary_complete.json")
    pending = [x for x in old["paths"] if x["status"] == "SOURCE_PENDING"]
    if len(pending) != 2 or any(x["unit"] != "recovery_parent_next_open" or
                              x["source_pending"] != [{"date": "2026-04-30", "reason": "held_unknown_status:601718:2026-04-30"}]
                              for x in pending):
        raise ValueError("Only the two bound parent accounts may continue")
    frozen = read(OUT / "clock_repair_protocol.json")
    if binding(ROOT / "panda_alpha/limit_reversal.py") != frozen["engine"]:
        raise ValueError("Original economic runtime changed")
    initial = read(OUT / "601718_source_asof_initial.json")
    if not initial["factor_equal"] or not initial["asof_factor"] or initial["asof_factor"]["adj"] <= 0:
        raise ValueError("Exact held suspension factor unknown")
    official = read(OUT / "601718_20260430_status_review.json")
    # The fixed receipt is independently reviewed by root before this dispatch;
    # it is not inferred from the raw data hole or the vendor ST flag.
    proof = read(OUT / "601718_application_contract.json")
    if proof["official_review"] != binding(OUT / "601718_20260430_status_review.json") or proof["suspension_date"] != "2026-04-30":
        raise ValueError("Official application receipt changed")
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    meta["start"] = initial["quote_start"]
    meta["calendar"] = [d for d in calendar if d >= meta["start"]]
    meta["source_rows"] = initial["raw_rows"]
    protocol = {"prior_results": binding(OUT / "summary_complete.json"),
                "original_protocol": binding(OUT / "protocol.json"),
                "first_source_continuation": binding(OUT / "resume_protocol.json"),
                "frozen_masks": binding(OUT / "frozen_masks.json"),
                "source_review": binding(OUT / "601718_20260430_status_review.json"),
                "asof": binding(OUT / "601718_source_asof_initial.json"),
                "application": binding(OUT / "601718_application_contract.json"),
                "engine": binding(ROOT / "panda_alpha/limit_reversal.py"), "runner": binding(__file__),
                "quote_start": meta["start"], "pending_paths_only": [x["path"] for x in pending],
                "all_ten_complete_paths_not_replayed": True,
                "new_contexts": 0, "official_compute_spent": 0}
    save("parent_resume_protocol.json", protocol)
    save("state.json", {"status": "RESUMING_TWO_BOUND_PARENT_ACCOUNTS", "process": os.getpid(),
                        "argv": str(Path(__file__).resolve()), "registry_total": old["registry_total"], "official_compute_spent": 0})
    panel, receipt, bars = load_retained_panel(read(ROOT / "config/panda-alpha.local.json"), meta, include_strategy=True)
    if receipt["factor_record_hash"] != read(OUT / "source_receipt.json")["factor_record_hash"]:
        raise ValueError("Original factors changed")
    del panel
    bars, _ = held_status_repairs(bars)
    bars, _ = reuse_002868_status(bars)
    bars = bars[bars.date.ge(meta["start"])].copy()
    status = pd.DataFrame([{"date": "2026-04-29", "symbol": "600370", "adjustment_factor": 6.202},
                           {"date": "2026-04-28", "symbol": "600537", "adjustment_factor": 3.422},
                           {"date": "2026-04-30", "symbol": "601718", "adjustment_factor": initial["asof_factor"]["adj"]}])
    if any(((bars.symbol == x.symbol) & (bars.date == x.date)).any() for x in status.itertuples()):
        raise ValueError("A previously missing exact status row is now present: inspect instead of duplicate")
    status["trade_status"] = "suspended"; status["source_status"] = "source_explicit_suspension"
    status["suspension_confirmed"] = True; status["entry_eligible"] = False
    bars = pd.concat([bars, status], ignore_index=True)
    patches = read(OUT / "three_status_source_asof_complete.json")["bounded_execution_band_patches"] + [proof["execution_band_patch"]]
    for patch in patches:
        mask = bars.symbol.eq(patch["code"]) & bars.date.ge(patch["effective_start"]) & bars.date.lt(patch["exclusive_end"])
        for side in ("up", "down"):
            bars.loc[mask, side + "_limit"] = [limit_price(v, patch["rate"], side) for v in bars.loc[mask, "prevclose"]]
    save("parent_resume_input_receipt.json", {**receipt, "exact_status_rows": 3, "band_patches": patches,
                                              "frozen_masks_reused": True, "no_quote_or_volume_filled": True})
    mask = {tuple(x): True for x in read(OUT / "frozen_masks.json")["recovery_parent_mask"] if x[0] >= "2026-04-30"}
    paths = []
    for item in old["paths"]:
        if item["status"] != "SOURCE_PENDING":
            paths.append(item); continue
        prefix = item["artifact_prefix"]
        nav = pd.read_csv(OUT / (prefix + "_nav.csv"), dtype={"date": str})
        trades = pd.read_csv(OUT / (prefix + "_trades.csv"), dtype={"date": str, "symbol": str})
        tail = simulate(bars, calendar, cost=item["cost_single_side"], evaluation_start="2026-04-30",
                        entry_mode="next_open", preopen_signal_candidates=mask, adjustment_factor_role="total_return_factor",
                        resume_state={"cash": item["terminal_cash"], "nav": item["last_completed_nav"], "holdings": item["terminal_holdings"]})
        nav = pd.concat([nav, pd.DataFrame(tail["daily"]).drop(columns=["positions"], errors="ignore")], ignore_index=True)
        trades = pd.concat([trades, pd.DataFrame(tail["trades"])], ignore_index=True)
        if nav.date.duplicated().any():
            raise ValueError("Repeated continuation date")
        result = tail["summary"]
        result.update(path=item["path"], unit=item["unit"], signal_mask=item["signal_mask"], entry_mode=item["entry_mode"],
                      artifact_prefix=item["path"] + "_final", completed_sessions=len(nav), requested_sessions=659,
                      total_fees=item["total_fees"] + result["total_fees"],
                      blocked_orders=dict(Counter(item["blocked_orders"]) + Counter(result["blocked_orders"])),
                      buys=int(trades.side.eq("buy").sum()), completed_round_trips=int(trades.side.eq("sell").sum()),
                      sale_reasons=dict(Counter(trades.loc[trades.side.eq("sell"), "reason"])))
        result["metrics"] = performance(nav.nav / nav.nav.shift(1).fillna(100000) - 1) if result["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" else None
        artifact = result["artifact_prefix"]
        save(artifact + "_summary.json", result)
        nav.to_csv(OUT / (artifact + "_nav.csv"), index=False, encoding="utf-8-sig")
        trades.to_csv(OUT / (artifact + "_trades.csv"), index=False, encoding="utf-8-sig")
        save(artifact + "_exit_orders.json", read(OUT / (prefix + "_exit_orders.json")) + [x for x in tail["orders"] if x["side"] == "sell"])
        paths.append(result)
        print({"path": item["path"], "status": result["status"], "sessions": len(nav), "pending": result["source_pending"]}, flush=True)
    final = {**old, "paths": paths, "parent_resume_protocol": binding(OUT / "parent_resume_protocol.json"),
             "status": "ALL_TWELVE_FIXED_NEXT_DIRECTION_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in paths) else "NEXT_DIRECTION_SOURCE_PENDING"}
    save("summary_final.json", final)
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    registry.append_event("source_repair", "LIMIT_NEXT_601718_PARENT_ONLY_CONTINUATION", protocol); registry.close()
    save("state.json", {"status": final["status"], "process": None, "registry_total": old["registry_total"], "official_compute_spent": 0})


if __name__ == "__main__":
    run()
