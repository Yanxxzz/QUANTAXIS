"""Deferred fixed51 E information beyond G/F, descriptive only and no replay."""
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

ARMS = ["E", "G", "GF", "aligned_F141"]


def sha(raw): return hashlib.sha256(raw).hexdigest()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def bound(item):
    raw = Path(item["path"]).read_bytes()
    if sha(raw) != item["sha256"]: raise ValueError("Frozen information binding changed: " + Path(item["path"]).name)
    return raw


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def restore_checked_G(GF, F_control, source_G, tolerance=2e-12):
    recovered = 2 * np.asarray(GF, dtype=float) + np.asarray(F_control, dtype=float)
    source = np.asarray(source_G, dtype=float)
    if recovered.shape != source.shape or not np.isfinite(recovered).all() or not np.isfinite(source).all():
        raise ValueError("Exact common-source G recovery domain mismatch")
    error = float(np.max(np.abs(recovered - source)))
    if error > tolerance: raise ValueError("Native GF/F source equation no longer recovers original G component")
    # SOURCE-derived component preserves true ties. Do not rank the tiny
    # cancellation noise in recovered; this does not invert GP or redefine G.
    return source, error


def rank(values): return pd.Series(np.asarray(values, dtype=float)).rank(method="average").to_numpy()


def partial_rank(x, y, controls):
    """Rank-space OLS residual correlation, intercept and fixed named controls."""
    if len(x) < 30:
        return {"status": "insufficient_observed_labels_pending", "value": None, "n": len(x)}
    vectors = [rank(x), rank(y), *[rank(v) for v in controls]]
    if not all(np.isfinite(v).all() for v in vectors):
        return {"status": "nonfinite_rank_domain_pending", "value": None, "n": len(x)}
    if any(float(np.std(v)) <= 1e-12 for v in vectors):
        return {"status": "constant_rank_operand_pending", "value": None, "n": len(x)}
    # Standardising rank columns only improves conditioning; intercept OLS
    # and residual correlation are invariant to these nonzero affine scales.
    normal = [(v - v.mean()) / v.std(ddof=0) for v in vectors]
    design = np.column_stack([np.ones(len(x)), *normal[2:]])
    if np.linalg.matrix_rank(design) != design.shape[1]:
        return {"status": "rank_control_design_degenerate_pending", "value": None, "n": len(x)}
    rx = normal[0] - design @ np.linalg.lstsq(design, normal[0], rcond=None)[0]
    ry = normal[1] - design @ np.linalg.lstsq(design, normal[1], rcond=None)[0]
    if np.std(rx) <= 1e-12 or np.std(ry) <= 1e-12:
        return {"status": "zero_residual_rank_variance_pending", "value": None, "n": len(x)}
    return {"status": "descriptive_partial_rank_observed", "value": float(np.corrcoef(rx, ry)[0, 1]), "n": len(x)}


def summary(values):
    usable = np.asarray([v for v in values if v is not None], dtype=float)
    return {"observed_periods": len(usable), "mean": float(usable.mean()) if len(usable) else None,
            "positive_periods": int((usable > 0).sum()) if len(usable) else 0,
            "ICIR_ddof1_not_annualized": float(usable.mean() / usable.std(ddof=1)) if len(usable) > 1 and usable.std(ddof=1) > 1e-12 else None}


def fixed_close_labels(prices, calendar, day, symbols):
    """Exactly the already-executed E information reader's six positive closes."""
    k = calendar.index(day); held = calendar[k + 1:k + 6]
    if len(held) != 5: raise ValueError("Fixed five-session close label tail unavailable")
    labels = []
    for symbol in symbols:
        actual = [prices.get((d, symbol), np.nan) for d in [day] + held]
        labels.append(float(actual[-1] / actual[0] - 1) if all(np.isfinite(v) and v > 0 for v in actual) else np.nan)
    return np.asarray(labels), held


def summarize_periods(periods):
    return {"RankIC": {arm: summary([p["RankIC"][arm] for p in periods]) for arm in ARMS},
            "score_rank_correlation_before_label_filter": {pair: summary([p["score_rank_correlation_before_label_filter"][pair] for p in periods]) for pair in ["E_G", "E_F", "G_F"]},
            "partial_rank_E_label_given_G_F": summary([p["partial_rank_E_label_given_G_F"]["value"] for p in periods]),
            "partial_rank_G_label_given_F": summary([p["partial_rank_G_label_given_F"]["value"] for p in periods])}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protocol-sha"); parser.add_argument("--execute", action="store_true"); args = parser.parse_args()
    raw = args.protocol.read_bytes(); p = json.loads(raw)
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_NO_SIGNAL_VALUE_OR_LABEL_READ", "protocol_sha256": sha(raw), "new_contexts": 0})); return
    if not args.protocol_sha or sha(raw) != args.protocol_sha or sha(Path(__file__).read_bytes()) != p["driver_sha256"]:
        raise ValueError("Root-frozen exact descriptive analysis protocol/runtime required")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]): raise ValueError("Human research deadline reached")
    output = Path(p["output_path"])
    if output.exists(): raise ValueError("Completed diagnostic exists; no automatic replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?", (p["registration_event_prefix"] + args.protocol_sha, p["existing_E_trial_key"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0: raise ValueError("Root exact0context descriptive analysis freeze required before values/labels")
    for item in p["bindings"].values(): bound(item)
    helpers = load_module(p["bindings"]["existing_decomposition_helpers"]["path"], "bound_G_source_label_helpers")
    source = helpers.source_constant(bound(p["bindings"]["original_GF_code"]))
    E = pd.read_parquet(p["bindings"]["E_cache"]["path"], columns=["date", "symbol", "E"])
    old = pd.read_parquet(p["bindings"]["original_GF_F_cache"]["path"], columns=["date", "symbol", "GF", "F141_CONTROL"])
    for frame in [E, old]:
        frame.date = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
        if frame.duplicated(["date", "symbol"]).any(): raise ValueError("Ambiguous fixed signal identity")
    if len(E) != 32905 or not E[["date", "symbol"]].equals(old[["date", "symbol"]]): raise ValueError("Exact original32905 ordered identities required; no implicit rejoin/crop")
    frame = E.merge(old, on=["date", "symbol"], validate="one_to_one", sort=False)
    if sorted(frame.date.unique()) != p["formations"] or not np.isfinite(frame[["E", "GF", "F141_CONTROL"]]).all().all():
        raise ValueError("Original51 complete signal support changed")
    frame["SOURCE_G_pct"] = [helpers.lookup(source, day, symbol) for day, symbol in zip(frame.date, frame.symbol)]
    frame["G"] = frame.groupby("date").SOURCE_G_pct.transform(helpers.z)
    canonical_G, error = restore_checked_G(frame.GF, frame.F141_CONTROL, frame.G)
    frame["G"] = canonical_G; frame["aligned_F141"] = -frame.F141_CONTROL
    identity_sha = sha(canonical(frame[["date", "symbol"]].to_dict("records")))
    correlations = {}
    for day, group in frame.groupby("date", sort=True):
        correlations[day] = {"E_G": helpers.rankcorr(group.E, group.G), "E_F": helpers.rankcorr(group.E, group.aligned_F141), "G_F": helpers.rankcorr(group.G, group.aligned_F141)}
    calendar = json.loads(bound(p["bindings"]["input_calendar"]))["dates"]
    # Preserve scores/denominators before label availability; do not save a
    # stock-level signal/price/label cache or create a portfolio/group target.
    quotes = pd.read_parquet(p["bindings"]["quotes"]["path"], columns=["date", "symbol", "close", "adjustment"])
    quotes.date = pd.to_datetime(quotes.date).dt.strftime("%Y-%m-%d")
    if quotes.duplicated(["date", "symbol"]).any(): raise ValueError("Frozen close identities ambiguous")
    price_basis = helpers.validate_quote_adjustment_units(quotes)
    prices = quotes.set_index(["date", "symbol"])["close"]; periods = []
    for day, group in frame.groupby("date", sort=True):
        labels, held = fixed_close_labels(prices, calendar, day, group.symbol)
        observed = np.isfinite(labels); y = labels[observed]
        periods.append({"formation": day, "base_close_day": day, "held_sessions": held, "terminal_close_day": held[-1],
                        "source_count": len(group), "observed_labels": int(observed.sum()), "missing_labels": int((~observed).sum()),
                        "score_rank_correlation_before_label_filter": correlations[day],
                        "RankIC": {arm: helpers.rankcorr(group.loc[observed, arm], y) for arm in ARMS},
                        "partial_rank_E_label_given_G_F": partial_rank(group.loc[observed, "E"], y, [group.loc[observed, "G"], group.loc[observed, "aligned_F141"]]),
                        "partial_rank_G_label_given_F": partial_rank(group.loc[observed, "G"], y, [group.loc[observed, "aligned_F141"]])})
    inherited = json.loads(bound(p["bindings"]["old_official_pair_review"]))["contexts"][0]["descriptive_five_contiguous_folds"]
    folds = []
    for number, positions in enumerate(np.array_split(np.arange(51), 5), 1):
        part = [periods[int(i)] for i in positions]; prior = inherited[number - 1]
        if (len(part), part[0]["formation"], part[-1]["formation"]) != (prior["n"], prior["first"], prior["last"]):
            raise ValueError("Inherited five fold boundaries changed")
        folds.append({"fold": number, "n": len(part), "first": prior["first"], "last": prior["last"], **summarize_periods(part)})
    result = {"id": p["id"], "status": "COMPLETED_FIXED51_E_G_F_DESCRIPTIVE_CONDITIONAL_INFORMATION", "protocol_sha256": args.protocol_sha,
              "completed_at_utc": datetime.now(timezone.utc).isoformat(), "driver_sha256": p["driver_sha256"], "bindings": p["bindings"],
              "source_rows": 32905, "periods": 51, "same_ordered_identity_sha256_before_labels": identity_sha,
              "G_recovery": {"equation": "ZG=2*GF+F141_CONTROL;alignedF=-F141_CONTROL", "max_source_equation_recovery_error": error,
                             "tolerance": 2e-12, "ranks_use_same_source_ZG_preserving_true_ties": True,
                             "floating_cancellation_noise_not_ranked": True, "raw_GP_inverted": False, "early_different_population_G_not_used": True},
              "price_basis_review": price_basis, "summary": summarize_periods(periods), "five_descriptive_folds_inherited": folds,
              "period_summaries": periods, "limitations": p["limitations"], "new_contexts": 0, "new_factor_or_blend_definition": False,
              "new_weights_sign_window_or_group_search": False, "new_stocklevel_caches": 0, "economic_paths_replayed": 0,
              "official_calls": 0, "shared_state_registry_or_masks_written": False, "formal_admission": False, "fresh_OOS": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sha256": sha(output.read_bytes()), "status": result["status"], "summary": result["summary"]}, ensure_ascii=False))


if __name__ == "__main__": main()
