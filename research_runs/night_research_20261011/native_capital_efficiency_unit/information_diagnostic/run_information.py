"""Deferred fixedE/F descriptive information analysis; no new factor/search."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def bound(item):
    raw = Path(item["path"]).read_bytes()
    if sha(raw) != item["sha256"]:
        raise ValueError("Frozen information input changed: " + item["path"])
    return raw


def metric_summary(periods, arm, metric):
    values = [p["arms"][arm][metric] for p in periods if p["arms"][arm][metric] is not None]
    if not values:
        return {"periods_observed": 0, "mean": None, "ICIR_ddof1_not_annualized": None,
                "win_gt_zero": None, "win_gt_02": None}
    a = np.asarray(values, dtype=float)
    sd = float(a.std(ddof=1)) if len(a) > 1 else None
    return {"periods_observed": len(a), "mean": float(a.mean()),
            "ICIR_ddof1_not_annualized": float(a.mean() / sd) if sd is not None and sd > 1e-12 else None,
            "win_gt_zero": float(np.mean(a > 0)), "win_gt_02": float(np.mean(a > .02))}


def pearson(a, b):
    if len(a) < 30 or np.std(a) <= 1e-12 or np.std(b) <= 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protocol-sha", required=True)
    args = parser.parse_args()
    raw = args.protocol.read_bytes()
    if sha(raw) != args.protocol_sha:
        raise ValueError("Exact Root-frozen E information protocol required")
    protocol = json.loads(raw)
    if sha(Path(__file__).read_bytes()) != protocol["driver_sha256"]:
        raise ValueError("Frozen E analysis driver changed")
    output = Path(protocol["output_path"])
    if output.exists():
        raise ValueError("Completed diagnostic exists; no automatic replay")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(protocol["deadline_utc"]):
        raise ValueError("User research deadline reached")
    with sqlite3.connect(Path(protocol["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM research_events WHERE event_id=? AND kind='execution_repair' AND subject=?",
            (protocol["registration_event_prefix"] + args.protocol_sha, protocol["registered_E_trial_key"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("Root must register this exact existing-E analysis before labels")
    for ref in protocol["bindings"].values():
        bound(ref)
    old = protocol["bindings"]["old_information_driver"]
    spec = importlib.util.spec_from_file_location("frozen_existing_information_helpers", old["path"])
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    e = pd.read_parquet(protocol["bindings"]["E_signal_cache"]["path"], columns=["date", "symbol", "E"])
    f = pd.read_parquet(protocol["bindings"]["F_signal_cache"]["path"], columns=["date", "symbol", "F141_CONTROL"])
    for frame in (e, f):
        frame.date = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
        if frame.duplicated(["date", "symbol"]).any():
            raise ValueError("Fixed signal identity duplicated")
    if not e[["date", "symbol"]].equals(f[["date", "symbol"]]):
        raise ValueError("E/F ordered original32905 support differs; no implicit join/crop")
    frame = e.merge(f, on=["date", "symbol"], validate="one_to_one", sort=False)
    if len(frame) != 32905 or sorted(frame.date.unique()) != protocol["formations"]:
        raise ValueError("Frozen original51 phase/source changed")
    if not np.isfinite(frame[["E", "F141_CONTROL"]].to_numpy()).all():
        raise ValueError("True missing signals cannot be dropped/reweighted")
    # The native savedE is already the unchanged1/99-clipped/Z signal.
    # F is direction0; negate its saved +ZF only for information alignment.
    frame["aligned_F141"] = -frame.F141_CONTROL
    calendar = json.loads(bound(protocol["bindings"]["input_calendar"]))["dates"]
    targets, group_status = {}, {}
    for day, group in frame.groupby("date", sort=True):
        targets[day], group_status[day] = {}, {}
        for arm, column, held_bin in (("E", "E", 9), ("aligned_F141", "F141_CONTROL", 0)):
            bins = pd.qcut(group[column], 10, labels=False, duplicates="drop")
            ready = bins.nunique() == 10
            group_status[day][arm] = "groups_available" if ready else "insufficient_distinct_quantile_groups"
            targets[day][arm] = sorted(group.loc[bins.eq(held_bin), "symbol"].tolist()) if ready else []
    score_key_sha = sha(canonical(frame[["date", "symbol"]].to_dict("records")))
    target_sha = sha(canonical(targets))
    # First price/label read only after full fixed scores/targets/denominators.
    quotes = pd.read_parquet(protocol["bindings"]["quotes"]["path"], columns=["date", "symbol", "close", "adjustment"])
    quotes.date = pd.to_datetime(quotes.date).dt.strftime("%Y-%m-%d")
    if quotes.duplicated(["date", "symbol"]).any():
        raise ValueError("Frozen source prices duplicated")
    basis = helpers.validate_quote_adjustment_units(quotes)
    prices = quotes.set_index(["date", "symbol"])["close"]
    periods = []
    for day, group in frame.groupby("date", sort=True):
        k = calendar.index(day)
        held = calendar[k + 1:k + 6]
        if len(held) != 5:
            raise ValueError("Fixed fiveheld calendar tail unavailable")
        labels = []
        for code in group.symbol:
            actual = [prices.get((d, code), np.nan) for d in [day] + held]
            labels.append(float(actual[-1] / actual[0] - 1) if all(np.isfinite(v) and v > 0 for v in actual) else np.nan)
        labels = np.asarray(labels)
        observed = np.isfinite(labels)
        market = float(labels[observed].mean()) if observed.any() else None
        armstats = {}
        labelseries = pd.Series(labels, index=group.symbol.to_numpy())
        for arm in ("E", "aligned_F141"):
            scores = group[arm].to_numpy()
            top = labelseries.reindex(targets[day][arm])
            valid = top.notna()
            armstats[arm] = {"source_signal_denominator": len(group), "finite_signals": len(group),
                "observed_labels": int(observed.sum()), "missing_labels": int((~observed).sum()),
                "direction_aligned_IC": pearson(scores[observed], labels[observed]),
                "direction_aligned_RankIC": helpers.rankcorr(scores[observed], labels[observed]),
                "groups_status": group_status[day][arm], "fixed_top_group_source_count": len(top),
                "top_observed_labels": int(valid.sum()), "top_missing_labels": int((~valid).sum()),
                "top_mean_label": float(top[valid].mean()) if valid.any() else None,
                "top_minus_same_observed_market": float(top[valid].mean() - market) if valid.any() and market is not None else None,
                "distinct_scores": int(pd.Series(scores).nunique()), "largest_score_tie": int(pd.Series(scores).value_counts().max())}
        periods.append({"formation": day, "source_count": len(group), "base_close_day": day,
            "held_sessions": held, "terminal_close_day": held[-1], "arms": armstats,
            "missing_fixed_six_close_labels": int((~observed).sum()), "same_observed_market_mean_label": market,
            "E_F_score_Spearman_before_label_filter": helpers.rankcorr(group.E, group.aligned_F141),
            "E_F_score_Pearson_before_label_filter": pearson(group.E.to_numpy(), group.aligned_F141.to_numpy()),
            "partial_rank_E_label_given_F": helpers.partial(group.loc[observed, "E"], labels[observed], group.loc[observed, "aligned_F141"])})
    folds = []
    for positions in np.array_split(np.arange(51), 5):
        part = [periods[int(i)] for i in positions]
        folds.append({"n": len(part), "first": part[0]["formation"], "last": part[-1]["formation"],
            "arms": {arm: {"IC": metric_summary(part, arm, "direction_aligned_IC"),
                            "RankIC": metric_summary(part, arm, "direction_aligned_RankIC")} for arm in ("E", "aligned_F141")}})
    partials = [p["partial_rank_E_label_given_F"] for p in periods if p["partial_rank_E_label_given_F"] is not None]
    summary = {arm: {"IC": metric_summary(periods, arm, "direction_aligned_IC"),
                    "RankIC": metric_summary(periods, arm, "direction_aligned_RankIC")} for arm in ("E", "aligned_F141")}
    result = {"id": protocol["id"], "status": "COMPLETED_FIXED_E_F_51_PERIOD_INFORMATION_DIAGNOSTIC",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": args.protocol_sha,
        "driver_sha256": protocol["driver_sha256"], "source_rows": 32905, "periods": 51,
        "score_key_sha256_frozen_before_prices": score_key_sha, "top_membership_sha256_frozen_before_prices": target_sha,
        "bindings": protocol["bindings"], "price_basis_review": basis, "arm_summary": summary,
        "mean_partial_rank_E_label_given_F": float(np.mean(partials)) if partials else None,
        "partial_observed_periods": len(partials), "period_summaries": periods,
        "five_descriptive_folds_inherited": folds, "limitations": protocol["limitations"],
        "original_labels_cache_not_relabelled_as_close_labels": True,
        "new_stocklevel_label_or_price_signal_copies": 0, "new_signal_or_factor_defined": False,
        "new_weight_sign_window_or_group_scan": False, "new_contexts": 0, "official_calls": 0,
        "source_registry_sharedstate_masks_or_memory_written": False, "formal_admission": False, "fresh_OOS": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sha256": sha(output.read_bytes()), "arm_summary": summary,
                     "mean_partial": result["mean_partial_rank_E_label_given_F"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
