"""Deferred sparse two-anchor E/native + COMBINED two-fee replay only.

Default reads the prototype only. Root must register the single new E window
before signal/source/price reads. No original GF/MID/F or24path replay, no API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from panda_alpha import study


def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def artifact(path): return {"path": str(Path(path).resolve()), "sha256": sha(path)}
def pandas_hash(frame): return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()


def save_new(name, data):
    path = OUT / name
    if path.exists(): raise ValueError("Owned output exists; no implicit overwrite: " + name)
    path.write_text(json.dumps(study._clean(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def require_registration(p):
    from panda_alpha.registry import strategy_definition, trial_key
    definition = strategy_definition(p["candidate"], p["registration_window"], 5, 10)
    if definition != p["definition"] or trial_key(definition) != p["trial_key"]:
        raise ValueError("Frozen single forward E window definition changed")
    uri = (ROOT / "research_runs/research_trials.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT payload FROM research_events WHERE kind='registration' AND subject=?", (p["trial_key"],)).fetchall()
    if not any((entry := json.loads(row[0])).get("candidate_id") == p["candidate"]["candidate_id"] and entry.get("definition") == definition for row in rows):
        raise ValueError("Root E/new-window registration required before actual E/source/price/labels")


def bound_inputs(p):
    for name, item in p["bindings"].items():
        if sha(item["path"]) != item["sha256"]: raise ValueError("Frozen binding changed: " + name)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]): raise ValueError("Human research deadline reached")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def signals(p, digest):
    from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
    from panda_alpha.native_gf_conservative import render_source_conservative
    if any((OUT / x).exists() for x in ["formation_receipt.json", "E_formation_signals.parquet", "ForwardCapitalEfficiency.py"]):
        raise ValueError("Signal stage already started/completed; no implicit refit")
    cap = module(p["bindings"]["capital_renderer"]["path"], "bound_capital_helpers")
    original = read(p["bindings"]["old_economic_protocol"]["path"])
    old_receipt = read(p["bindings"]["old_formation_receipt"]["path"])
    cached = pd.read_parquet(p["bindings"]["old_signals"]["path"])
    contract = pd.read_parquet(p["bindings"]["forward_source_contract"]["path"])
    qualified = contract[contract.candidate_source_qualified.eq(True)].copy()
    keys5 = [*cap.FINANCIAL_KEY, "source_amount_overlay_sha256"]
    baseline = pd.read_parquet(original["bindings"]["original_components"]["path"], columns=["date", *keys5, "GP_delta"])
    baseline = baseline[baseline.date.eq(original["base_date"])].drop(columns="date")
    financial = qualified.merge(baseline, on=keys5, how="left", validate="many_to_one", sort=False)
    if len(financial) != 2324 or financial.duplicated(["date", "symbol"]).any() or not np.isfinite(financial.GP_delta).all():
        raise ValueError("Original qualified financial SOURCE/GP variance carrier changed")
    if {d: int(financial.date.eq(d).sum()) for d in p["anchors"]} != p["qualified_counts"]:
        raise ValueError("Full financial source counts changed")
    financial["G_pct"] = financial.groupby(["date", "sector"]).GP_delta.rank(method="average", pct=True)
    # Only reproduce the existing source variance carrier, never an old GF run.
    amounts = cap.canonical_amounts(pd.read_parquet(p["bindings"]["annual_amounts"]["path"], columns=cap.AMOUNT_FILE_KEY + cap.AMOUNTS))
    accepted = financial[cap.FINANCIAL_KEY].drop_duplicates()
    amounts = amounts.merge(accepted, on=cap.FINANCIAL_KEY, how="inner", validate="many_to_one", sort=False)
    selected = cap.project_capital_source(financial, amounts, accepted.to_dict("records"))
    if len(selected) != 2324: raise ValueError("No automatic financial E exclusion permitted")
    rows = cached.merge(selected[["date", *cap.FINANCIAL_KEY, "E_raw"]], on=["date", *cap.FINANCIAL_KEY], how="left", validate="one_to_one", sort=False)
    if len(rows) != 2271 or not np.isfinite(rows.E_raw).all() or not rows[["date", "symbol"]].equals(cached[["date", "symbol"]]):
        raise ValueError("Exact original F-common source identity support changed")
    if {d: int(rows.date.eq(d).sum()) for d in p["anchors"]} != p["common_counts"]:
        raise ValueError("Original1138/1133 common support changed")
    before = financial[["date", "symbol", "G_pct"]].merge(cached[["date", "symbol", "G_pct"]], on=["date", "symbol"], validate="one_to_one", suffixes=("_full", "_cache"))
    if not np.array_equal(before.G_pct_full.to_numpy(), before.G_pct_cache.to_numpy()):
        raise ValueError("Legacy G source rank/variance carrier no longer matches cache")
    reference = cap.capital_reference(rows)
    if not np.isfinite(reference["E"]).all() or not np.allclose(reference["F141_CONTROL"], rows.F141_CONTROL, rtol=0, atol=2e-13):
        raise ValueError("New E variance or exact inherited F reference pending")
    calendar = read(p["bindings"]["calendar"]["path"])["dates"]
    feature_calendar = [day for day in calendar if day <= p["anchors"][-1]]
    source = {code: [[r.date, r.date, float(r.G_pct)] for r in part.itertuples(index=False)] for code, part in financial.groupby("symbol", sort=True)}
    e_source = {code: [[r.date, r.date, float(r.E_raw)] for r in part.itertuples(index=False)] for code, part in selected.groupby("symbol", sort=True)}
    lifecycle = read(original["bindings"]["lifecycle"]["path"])
    markets = {item["code"]: item["sse"] for item in lifecycle["records"] if item["code"] in source}
    original_code = render_source_conservative(source, markets, feature_calendar, {"start": p["anchors"][0], "end": p["anchors"][-1]}, "GF", "ForwardOriginalSourceCarrier")
    code = cap.render_capital_code(original_code, e_source, "ForwardCapitalEfficiency")
    fixture = read_mongo_gf_fixture(p["bindings"]["feature_descriptor"]["path"])
    if fixture.date.max() > p["anchors"][-1]: raise ValueError("Feature reader escaped last formation")
    frame = fixture[["date", "symbol", "close"]].copy(); frame.date = pd.to_datetime(frame.date); frame = frame.set_index(["date", "symbol"])
    if pandas_hash(frame) != old_receipt["feature_input_frame_sha256"]: raise ValueError("Original full-peer feature input hash changed")
    namespace = {"Factor": object, "__name__": "registered_sparse_E_forward"}; exec(compile(code, "<ForwardCapitalEfficiency>", "exec"), namespace)
    output = namespace["ForwardCapitalEfficiency"]().calculate({"close": frame.close})
    formed = output.rename("E").reset_index(); formed.date = formed.date.dt.strftime("%Y-%m-%d")
    expected_index = frame.index[frame.index.get_level_values("date").to_series(index=frame.index).between(p["anchors"][0], p["anchors"][-1])]
    if not output.index.equals(expected_index): raise ValueError("Sparse native full projection index changed")
    if not formed.loc[~formed.date.isin(p["anchors"]), "E"].isna().all(): raise ValueError("Nonanchor rows must remain genuine NaN")
    native = formed[np.isfinite(formed.E)].merge(rows[["date", "symbol"]], on=["date", "symbol"], how="outer", indicator=True, validate="one_to_one")
    if not native._merge.eq("both").all(): raise ValueError("Actual native E finite mask differs from old F mask; root new control context required")
    effective = formed[np.isfinite(formed.E)][["date", "symbol", "E"]].merge(rows[["date", "symbol"]].assign(_position=np.arange(len(rows))), on=["date", "symbol"], validate="one_to_one").sort_values("_position").drop(columns="_position").reset_index(drop=True)
    if not np.allclose(effective.E, reference["E"], rtol=0, atol=2e-13): raise ValueError("Native E differs from fixed source/raw-F cache reference")
    positions = {d: i for i, d in enumerate(calendar)}; schedule = {}; group_counts = []
    for day in p["anchors"]:
        sample = effective[effective.date.eq(day)]; bins = pd.qcut(sample.E, 10, labels=False, duplicates="raise")
        if len(sample) < 30 or bins.nunique() != 10: raise ValueError("Both exact formation dates need ten genuine groups; no jitter/crop")
        entry = calendar[positions[day] + 1]
        schedule[entry] = {"decision_date": day, "codes": sorted(sample.loc[bins.eq(9), "symbol"])}
        group_counts.append({"date": day, "finite": len(sample), "group_sizes": sample.groupby(bins).size().to_dict()})
    if list(schedule) != [x["entry"] for x in p["intervals"]]: raise ValueError("Original next-open phase changed")
    (OUT / "ForwardCapitalEfficiency.py").write_bytes(code.encode("utf-8")); path = OUT / "E_formation_signals.parquet"; effective.to_parquet(path, index=False, compression="zstd")
    save_new("formation_receipt.json", {"status": "E_TWO_ANCHOR_NATIVE_REFERENCE_AND_MASK_READY", "protocol_sha256": digest,
             "signals": artifact(path), "code": artifact(OUT / "ForwardCapitalEfficiency.py"), "schedule": schedule,
             "schedule_sha256": study.object_hash(schedule), "qualified_counts": p["qualified_counts"], "common_counts": p["common_counts"],
             "groups": group_counts, "feature_input_frame_sha256": pandas_hash(frame), "output_sha256": pandas_hash(output),
             "same_old_F_mask": True, "other_dates_true_NaN": True, "source_roles_unchanged": True,
             "old_native_signals_or24_wealth_paths_replayed": False, "exit_labels_or_economic_returns_read": False,
             "source_or_full_price_copies": False, "official_compute": 0, "fresh_blind_OOS": False})
    print(json.dumps({"status": "E_TWO_ANCHOR_NATIVE_REFERENCE_AND_MASK_READY", "common_counts": p["common_counts"]}))


def economics(p, digest):
    frozen = read(OUT / "formation_receipt.json")
    if frozen["protocol_sha256"] != digest or frozen.get("same_old_F_mask") is not True or sha(frozen["signals"]["path"]) != frozen["signals"]["sha256"]:
        raise ValueError("Completed exact E source/target preflight required")
    if study.object_hash(frozen["schedule"]) != frozen["schedule_sha256"]: raise ValueError("Frozen E targets changed")
    if (OUT / "economic_summary.json").exists(): raise ValueError("Complete E economics exists; no replay")
    old = module(p["bindings"]["old_driver"]["path"], "old_forward_quote_helpers")
    repair = module(p["bindings"]["old_pending_driver"]["path"], "old_forward_patch_helpers")
    original = read(p["bindings"]["old_economic_protocol"]["path"])
    old_formation = read(p["bindings"]["old_formation_receipt"]["path"])
    old_source_summary = read(p["bindings"]["old_partial_summary"]["path"])
    if old_source_summary["quote_content_sha256"] != p["expected_original_quote_content_sha256"]:
        raise ValueError("Frozen original pending4 prepatch quote-book content hash changed")
    book, quote_hash = repair.patched_book(old, original, old_formation, old_source_summary, read(p["bindings"]["source_patch"]["path"]))
    if quote_hash != p["expected_original_quote_content_sha256"]:
        raise ValueError("Exact old quote book required; no reprice/rebuild of old controls")
    calendar = read(p["bindings"]["calendar"]["path"])["dates"]
    days = [day for day in calendar if p["holding_start"] <= day <= p["holding_end"]]
    checkpoint_path = OUT / "checkpoint.json"
    checkpoint = read(checkpoint_path) if checkpoint_path.exists() else {"protocol_sha256": digest, "completed": {}, "errors": []}
    if checkpoint["protocol_sha256"] != digest: raise ValueError("Owned checkpoint protocol changed")
    for cost in p["costs"]:
        key = format(cost, ".3f")
        if key in checkpoint["completed"]: continue
        run = study.replay_portfolio("E", frozen["schedule"], days, book, cost)
        if run["accounting"]["max_absolute_accounting_error"] > 1e-8: raise ValueError("E accounting error")
        record = {"scope": "COMBINED", "name": "E", "cost": cost, "metrics": run["metrics"], "fees": run["accounting"]["fees"],
                  "blocked_orders": run["blocked_orders"], "terminal_positions": run["final_open_positions"],
                  "terminal_liquidation_pending": run["terminal_liquidation_pending"], "risk_metadata_status": run["exposures"]["status"],
                  "accounting_error": run["accounting"]["max_absolute_accounting_error"]}
        daily = [{"scope": "COMBINED", "name": "E", "cost": cost, **{k: r[k] for k in ["date", "previous_nav", "nav", "net_return", "nav_change", "cash_after", "price_pnl", "fees"]}} for r in run["daily"]]
        checkpoint["completed"][key] = {"record": record, "daily": daily}
        checkpoint_path.write_text(json.dumps(study._clean(checkpoint), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        del run
    if set(checkpoint["completed"]) != {"0.003", "0.005"}: raise ValueError("Exactly both complete fixed E paths required")
    controls = read(p["bindings"]["old_recovered_summary"]["path"])
    if controls["status"] != "ALL_24_CORE_WEALTH_COMPLETE_WITH_ORIGINAL20_METADATA_LOSS": raise ValueError("Original24 core wealth incomplete")
    old_daily = pd.concat([pd.read_parquet(p["bindings"]["old20_ledger"]["path"]), pd.read_parquet(p["bindings"]["old4_ledger"]["path"])], ignore_index=True)
    E_daily = pd.DataFrame([r for x in checkpoint["completed"].values() for r in x["daily"]]); comparisons = []
    for cost in p["costs"]:
        ep = E_daily[E_daily.cost.eq(cost)].set_index("date")
        if ep.index.tolist() != days: raise ValueError("E combined holding calendar incomplete")
        for name in ["F141_CONTROL", "COMMON_MARKET"]:
            rows = old_daily[old_daily.scope.eq("COMBINED") & old_daily.name.eq(name) & old_daily.cost.eq(cost)].set_index("date")
            if not ep.index.equals(rows.index): raise ValueError("Exact old common control dayledger mismatch")
            comparisons.append({"cost": cost, "control": name, "relative_terminal_wealth": float(ep.nav.iloc[-1] / rows.nav.iloc[-1] - 1),
                                "daily_net_return_correlation": float(ep.net_return.corr(rows.net_return)),
                                "old20_risk_details_unknown_not_reconstructed": name == "F141_CONTROL",
                                "control_metadata_origin": "original20_lost_risk_details" if name == "F141_CONTROL" else "original4_pending_repair_retained_metadata"})
    ledger = OUT / "E_daily_net_ledger.parquet"; E_daily.to_parquet(ledger, index=False, compression="zstd")
    save_new("economic_summary.json", {"status": "BOTH_FIXED_COMBINED_E_COST_PATHS_COMPLETE", "protocol_sha256": digest,
             "formation_receipt": artifact(OUT / "formation_receipt.json"), "quote_content_sha256": quote_hash,
             "results": [checkpoint["completed"][format(c, ".3f")]["record"] for c in p["costs"]], "comparisons": comparisons,
             "ledger": artifact(ledger), "holding_days": len(days), "errors": [], "old24_paths_replayed": False,
             "old20_risk_details_unknown": True, "old_fees_daily_NAV_core_reused_only": True,
             "QFQ_daily_bar_execution_proxy": True, "official_compute": 0, "new_windows": 1,
             "short_window_Sharpe_descriptive_only": True,
             "fresh_blind_OOS": False, "formal_admission": False, "shared_state_written": False})
    checkpoint.update(status="COMPLETED", ledger=artifact(ledger), economic_summary=artifact(OUT / "economic_summary.json"),
                      completed={key: {"record": value["record"]} for key, value in checkpoint["completed"].items()},
                      completed_daily_rows_retained_only_in_unique_parquet=True)
    checkpoint_path.write_text(json.dumps(study._clean(checkpoint), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "BOTH_FIXED_COMBINED_E_COST_PATHS_COMPLETE", "new_E_paths": 2, "old24_paths_replayed": False}))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--stage", choices=["plan", "signals", "economics"], default="plan")
    parser.add_argument("--protocol-sha"); parser.add_argument("--resume", action="store_true"); args = parser.parse_args()
    path = OUT / "protocol.json"; p = read(path)
    if args.stage == "plan": print(json.dumps({"status": "DEFERRED_NO_ACTUAL_E_OR_LABEL_READ", "protocol_sha256": sha(path), "trial_key": p["trial_key"]})); return
    if not args.protocol_sha or sha(path) != args.protocol_sha: raise ValueError("Root exact protocol SHA required")
    require_registration(p); bound_inputs(p)
    if args.stage == "signals": signals(p, args.protocol_sha)
    else:
        if (OUT / "checkpoint.json").exists() and not args.resume: raise ValueError("Owned checkpoint exists; verify process stopped before explicit resume")
        economics(p, args.protocol_sha)


if __name__ == "__main__": main()
