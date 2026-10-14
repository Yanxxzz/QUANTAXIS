"""Bounded, post-hoc pre-entry descriptors of the already completed39 buys.

This is selection diagnosis, not a new account replay or fixed-horizon label
calculation. The complete-account outcomes are already exposed. Hypotheses are
frozen before the new entry-history values, and no fitted cutoff is selected.
"""
from pathlib import Path
from datetime import datetime, timezone
from bisect import bisect_right
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding, client
from panda_alpha.limit_gate_diagnostics import build_limit_gate_diagnostics
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key

OUT = ROOT / "research_runs/limit_gate_diagnosis_20261014"
OLD = ROOT / "research_runs/limit_reversal_optimization_20261014"


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                     allow_nan=False), encoding="utf-8")


def run():
    if (OUT / "protocol.json").exists():
        raise RuntimeError("Fixed diagnosis exists: collect, never repeat")
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    calendar = meta["calendar"]
    trades = pd.read_csv(OLD / "completed_trades3.csv", dtype={"date": str, "symbol": str})
    buys = trades[trades.path.eq("filtered_30bp") & trades.side.eq("buy")].copy()
    if len(buys) != 39 or buys.duplicated(["date", "symbol"]).any():
        raise ValueError("Existing fixed39-entry cohort changed")
    nav = pd.read_csv(OLD / "completed_daily_nav3.csv", dtype={"date": str})
    previous = dict(zip(nav.date, nav.filtered_30bp_nav.shift(1).fillna(100000)))
    entries = [{"date": x.date, "symbol": x.symbol, "planned_slot_cash": .2 * previous[x.date],
                "recorded_order_quantity": float(x.quantity)} for x in buys.itertuples()]
    positions = {d: i for i, d in enumerate(calendar)}
    requested = {}
    for row in entries:
        t = positions[row["date"]]
        if t < 23:
            raise ValueError("Fixed entry lacks original reference history")
        requested.setdefault(row["symbol"], set()).update(calendar[t - 23:t - 2])
    manifest = {c: sorted(days) for c, days in sorted(requested.items())}
    protocol = {"authorization": binding(OUT / "authorization.json"),
                "night_authority": binding(ROOT / "research_runs/night_limit_gate_20261015/authorization.json"),
                "mechanism": binding(OUT / "mechanism_gate_review.json"),
                "reader": binding(__file__), "runtime": binding(ROOT / "panda_alpha/limit_gate_diagnostics.py"),
                "tests": binding(ROOT / "tests/test_limit_gate_diagnostics.py"), "actual_synthetic_tests_passed": 14,
                "old_trades": binding(OLD / "completed_trades3.csv"), "old_nav": binding(OLD / "completed_daily_nav3.csv"),
                "old_account": binding(OLD / "resumed_summary3.json"),
                "trough_attribution": binding(OUT / "filtered_drawdown_review.json"),
                "source_meta": binding(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json"),
                "fixed_entry_identities": entries, "exact_requested_history": manifest,
                "feature_clock": "t-23..t-3 prices; t-22..t-3 amounts/volume; mandatory2boards excluded",
                "trend_domain": "0<=same prior_extension20<=old0.18595041322314065; zero logical boundary not selected from losses",
                "capacity_diagnostic": "recorded old raw reserved buy quantity<=1% of minimum received positive volume in the20 preboard sessions; not future queue capacity",
                "risk_descriptor": "sqrt(mean(min(simpleTRreturn,0)^2))*sqrt252 on same20 changes; no empirical threshold",
                "label_role": "Existing maximal-DD holdings and realized outcomes already exposed; no new event-horizon outcomes",
                "posthoc_not_new_validation_or_independent_alpha": True,
                "no_performance_fitted_cutoff_or_account_counterfactual": True, "official_compute_spent": 0}
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    if registry.historical != 415 or registry.total != 574:
        raise ValueError("Inspect live registry; never reset its denominator")
    registrations = []
    specs = {"NONNEGATIVE_PREBOARD_DOMAIN": protocol["trend_domain"],
             "PREBOARD_DOWNSIDE_RISK": protocol["risk_descriptor"],
             "PREBOARD_LIQUIDITY_SUPPORT": protocol["capacity_diagnostic"]}
    for name, spec in specs.items():
        candidate = {"candidate_id": "LIMIT_GATE_DIAG_" + name + "_20261014", "direction": 1,
                     "strategy_spec": {"feature": spec, "cohort": entries, "source": protocol["source_meta"]["sha256"],
                                       "runtime": protocol["runtime"]["sha256"], "purpose": "posthoc actual-entry risk diagnosis; not return strategy or OOS"}}
        window = {"start": "2024-01-02", "end": meta["end"]}
        definition = strategy_definition(candidate, window, 1, 2)
        registry.record(candidate, window, 1, 2)
        registrations.append({"name": name, "key": trial_key(definition), "candidate": candidate})
    protocol["registrations"] = registrations; protocol["registry_total"] = registry.total
    registry.close(); save("protocol.json", protocol)
    save("state.json", {"status": "RUNNING_THREE_FIXED_NEW_ENTRY_DIAGNOSTICS", "process": os.getpid(),
                        "argv": str(Path(__file__).resolve()), "registry_total": protocol["registry_total"], "official_compute_spent": 0})
    cfg = read(ROOT / "config/panda-alpha.local.json")
    with client(cfg) as c:
        db = c[meta["source_profile"]["database"]]
        fields = {k: 1 for k in ["code", "date", "close", "amount", "volume_shares", "adjustment", "source_row_sha256"]}; fields["_id"] = 0
        query = {"source": "stockdb", "$or": [{"code": code, "date": {"$in": days}} for code, days in manifest.items()]}
        raw = list(db.stock_day.find(query, fields).sort([("code", 1), ("date", 1)]))
        factors = list(db.stock_adj.find({"source": "stockdb", "code": {"$in": list(manifest)},
                                         "date": {"$lte": max(max(days) for days in manifest.values())}},
                                        {"_id": 0, "code": 1, "date": 1, "adj": 1}).sort([("code", 1), ("date", 1)]))
    frame = pd.DataFrame(raw)
    if frame.duplicated(["date", "code"]).any() or not frame.adjustment.eq("none").all():
        raise ValueError("Raw history identity/adjustment role changed")
    fg = {}
    for row in factors:
        if not np.isfinite(row["adj"]) or row["adj"] <= 0:
            raise ValueError("Invalid retained factor")
        fg.setdefault(row["code"], []).append(row)
    adj = []
    for row in raw:
        stock = fg.get(row["code"], []); dates = [x["date"] for x in stock]
        if len(dates) != len(set(dates)):
            raise ValueError("Duplicate source factor date")
        n = bisect_right(dates, row["date"]) - 1
        adj.append(stock[n]["adj"] if n >= 0 else np.nan)
    frame["adjustment_factor"] = adj
    index = pd.DatetimeIndex(pd.to_datetime(calendar))
    panel = {name: frame.pivot(index="date", columns="code", values=name) for name in ["close", "adjustment_factor", "amount", "volume_shares"]}
    for name in panel:
        panel[name].index = pd.to_datetime(panel[name].index)
        panel[name] = panel[name].reindex(index=index, columns=sorted(manifest))
    values = build_limit_gate_diagnostics(panel, calendar, entries, adjustment_factor_role="total_return_factor")
    value_records = json.loads(values.to_json(orient="records"))
    save("entry_descriptor_values.json", {"rows": value_records, "actual_entries": len(entries),
                                          "no_new_event_labels_or_account_replay": True, "posthoc_only": True})
    source = {"raw_rows": len(raw), "requested_identity_keys": sum(map(len, manifest.values())),
              "raw_identity_sha256": hashlib.sha256("".join(x["code"]+"|"+x["date"]+"|"+x.get("source_row_sha256","")+"\n" for x in raw).encode()).hexdigest(),
              "factor_rows": len(factors), "factor_sha256": hashlib.sha256(json.dumps(factors, sort_keys=True).encode()).hexdigest(),
              "complete_original_source_replayed": False, "raw_market_panel_saved": False,
              "actual_new_future_labels_or_account_paths": 0}
    save("source_receipt.json", source)
    attribution = read(OUT / "filtered_drawdown_review.json")["paths"][0]
    tails = {(x["entry_date"], x["code"]) for x in attribution["trough_inventory"]}
    values["maxDD_held_entry"] = [tuple(x) in tails for x in values[["date", "symbol"]].itertuples(index=False, name=None)]
    values["inside_maxDD_entry_window"] = values.date.le("2024-07-25")
    def describe(x):
        def median(field):
            v=x[field].dropna(); return float(v.median()) if len(v) else None
        return {"n": len(x), "finite_trend": int(x.prior_extension20.notna().sum()),
                "negative_pretrend": int(x.prior_extension20.lt(0).sum()),
                "nonnegative_pretrend": int(x.prior_extension20.ge(0).sum()),
                "median_trend": median("prior_extension20"), "median_downside_semi": median("downside_semideviation20"),
                "median_amount_CNY": median("median_amount20"), "median_slot_cash_to_amount": median("liquidity_ratio"),
                "known_capacity": int(x.liquidity_capacity_pass_1pct.notna().sum()),
                "capacity_pass": int(x.liquidity_capacity_pass_1pct.fillna(False).sum())}
    groups = {"all39_actual_entries": describe(values), "maxDD5_held_entries": describe(values[values.maxDD_held_entry]),
              "maxDD3_closed_entries": describe(values[values.inside_maxDD_entry_window & ~values.maxDD_held_entry]),
              "remaining_actual_entries": describe(values[~values.inside_maxDD_entry_window])}
    tail_rows = values[values.maxDD_held_entry].copy()
    summary = {"status": "THREE_FIXED_ENTRY_DIAGNOSTICS_COMPLETE", "registry_total": protocol["registry_total"],
               "source": source, "groups": groups, "tail_entry_descriptors": json.loads(tail_rows.to_json(orient="records")),
               "interpretation": "posthoc8-entry maximal-DD block with5 held/3 prior profit exits;39 endogenous actual entries. No causality, fixed-horizon event statistics, classifier skill or full strategy counterfactual.",
               "financial_gate_source_PIT": "PENDING; no historical clean/unclean labels imputed",
               "no_new_IC_or_backtest_or_fitted_volatility_threshold": True, "new_diagnostic_contexts": 3,
               "official_compute_spent": 0, "formal_eligible": False}
    save("summary.json", summary)
    save("state.json", {"status": summary["status"], "process": None, "registry_total": protocol["registry_total"],
                        "official_compute_spent": 0, "recovery": "Collect all complete values only; no diagnosis/source repeat. Prospective gate testing needs a separately frozen complete candidate population, common support/control and return experiment."})
    print(json.dumps({"status": summary["status"], "registry": protocol["registry_total"], "groups": groups}, ensure_ascii=False))


if __name__ == "__main__":
    run()
