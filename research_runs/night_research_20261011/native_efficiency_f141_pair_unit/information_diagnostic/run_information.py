"""Deferred fixed EF51 close-RankIC; reuse completed controls, no new strategy."""
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def bound(item):
    raw = Path(item["path"]).read_bytes()
    if sha(raw) != item["sha256"]:
        raise ValueError("Frozen EF information input changed: " + Path(item["path"]).name)
    return raw


def load_module(item, name):
    spec = importlib.util.spec_from_file_location(name, item["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summaries(periods, helpers):
    return {"EF_RankIC": helpers.summary([p["EF_RankIC"] for p in periods]),
            "signal_rank_correlation_before_label_filter": {
                pair: helpers.summary([p["signal_rank_correlation_before_label_filter"][pair] for p in periods])
                for pair in ["EF_aligned_F141", "EF_GF"]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=Path(__file__).with_name("protocol.json"))
    parser.add_argument("--protocol-sha")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    raw = args.protocol.read_bytes()
    p = json.loads(raw)
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_NO_EF_VALUE_OR_LABEL_READ", "protocol_sha256": sha(raw), "new_contexts": 0}))
        return
    if (not args.protocol_sha or sha(raw) != args.protocol_sha
            or sha(Path(__file__).read_bytes()) != p["driver_sha256"]
            or p["status"] != "FROZEN_FIXED_EF51_INFORMATION_AWAIT_ROOT_EXECUTION_REGISTRATION"):
        raise ValueError("Root-frozen exact EF information protocol/runtime required")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("Human research deadline reached")
    output = Path(p["output_path"])
    if output.exists():
        raise ValueError("Completed diagnostic exists; no automatic replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",
                         (p["registration_event_prefix"] + args.protocol_sha, p["existing_EF_trial_key"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("Root exact 0context information freeze required before real values/labels")
    for item in p["bindings"].values():
        bound(item)
    label_helpers = load_module(p["bindings"]["conditional_information_driver"], "fixed_completed_close_label_helpers_for_EF")
    score_helpers = load_module(p["bindings"]["existing_decomposition_helpers"], "fixed_rank_and_quote_unit_helpers_for_EF")
    native = json.loads(bound(p["bindings"]["EF_native_protocol"]))
    prep = json.loads(bound(p["bindings"]["EF_preparation"]))
    proof = json.loads(bound(p["bindings"]["EF_preflight"]))
    if (prep["protocol_sha256"] != p["bindings"]["EF_native_protocol"]["sha256"]
            or prep["registered_context"]["trial_key"] != p["existing_EF_trial_key"]
            or proof.get("research_replay_passed") is not True
            or proof["proof"]["input_frame_sha256"] != prep["old_input_frame_sha256"]
            or proof["proof"]["output_sha256"] != prep["output_sha256"]
            or native["candidate_draft"]["direction"] != 1
            or native["candidate_draft"]["strategy_spec"]["formation_dates"] != p["formations"]
            or any(prep.get(key) is not True for key in ["shared_finite_mask_exact", "original_f_formation_keys_exact", "controls_reuse_allowed", "all_251_decisions_group_ready"])):
        raise ValueError("Actual registered native EF source/mask/phase is not the frozen scope")
    e = pd.read_parquet(p["bindings"]["EF_cache"]["path"], columns=["date", "symbol", "EF"])
    old = pd.read_parquet(p["bindings"]["original_GF_F_cache"]["path"], columns=["date", "symbol", "GF", "F141_CONTROL"])
    for frame in [e, old]:
        frame.date = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
        if frame.duplicated(["date", "symbol"]).any():
            raise ValueError("Fixed signal identity is ambiguous")
    if len(e) != 32905 or not e[["date", "symbol"]].equals(old[["date", "symbol"]]):
        raise ValueError("Exact original32905 ordered keys required; no implicit join/crop")
    frame = e.merge(old, on=["date", "symbol"], validate="one_to_one", sort=False)
    if sorted(frame.date.unique()) != p["formations"] or not np.isfinite(frame[["EF", "GF", "F141_CONTROL"]].to_numpy()).all():
        raise ValueError("Complete fixed51 finite signal support changed")
    identity_sha = sha(canonical(frame[["date", "symbol"]].to_dict("records")))
    controls = json.loads(bound(p["bindings"]["completed_E_G_GF_F_diagnostic"]))
    prior_periods = controls["period_summaries"]
    if (controls.get("status") != "COMPLETED_FIXED51_E_G_F_DESCRIPTIVE_CONDITIONAL_INFORMATION"
            or controls["source_rows"] != 32905 or controls["periods"] != 51
            or controls["same_ordered_identity_sha256_before_labels"] != identity_sha
            or [row["formation"] for row in prior_periods] != p["formations"]):
        raise ValueError("Completed control RankIC diagnostic has a different source/formation identity")
    correlations = {day: {"EF_aligned_F141": score_helpers.rankcorr(group.EF, -group.F141_CONTROL),
                          "EF_GF": score_helpers.rankcorr(group.EF, group.GF)}
                    for day, group in frame.groupby("date", sort=True)}
    calendar = json.loads(bound(p["bindings"]["input_calendar"]))["dates"]
    # First quote/label consumption only after all fixed score keys/support.
    quotes = pd.read_parquet(p["bindings"]["quotes"]["path"], columns=["date", "symbol", "close", "adjustment"])
    quotes.date = pd.to_datetime(quotes.date).dt.strftime("%Y-%m-%d")
    if quotes.duplicated(["date", "symbol"]).any():
        raise ValueError("Bound historical close keys are ambiguous")
    basis = score_helpers.validate_quote_adjustment_units(quotes)
    prices = quotes.set_index(["date", "symbol"])["close"]
    periods = []
    for number, (day, group) in enumerate(frame.groupby("date", sort=True)):
        labels, held = label_helpers.fixed_close_labels(prices, calendar, day, group.symbol)
        observed = np.isfinite(labels)
        prior = prior_periods[number]
        if (prior["source_count"] != len(group) or prior["observed_labels"] != int(observed.sum())
                or prior["missing_labels"] != int((~observed).sum()) or prior["held_sessions"] != held):
            raise ValueError("Same bound close label/support contract diverged from completed controls")
        value = score_helpers.rankcorr(group.loc[observed, "EF"], labels[observed])
        periods.append({"formation": day, "source_count": len(group), "observed_labels": int(observed.sum()),
                        "missing_labels": int((~observed).sum()), "held_sessions": held,
                        "EF_RankIC": value, "RankIC_status": "observed" if value is not None else "degenerate_or_insufficient_pending",
                        "signal_rank_correlation_before_label_filter": correlations[day]})
    folds, reused_folds = [], []
    for number, positions in enumerate(np.array_split(np.arange(51), 5), 1):
        part = [periods[int(i)] for i in positions]
        prior = controls["five_descriptive_folds_inherited"][number - 1]
        if (len(part), part[0]["formation"], part[-1]["formation"]) != (prior["n"], prior["first"], prior["last"]):
            raise ValueError("Inherited fixed five fold boundaries changed")
        folds.append({"fold": number, "n": len(part), "first": prior["first"], "last": prior["last"], **summaries(part, label_helpers)})
        reused_folds.append({"fold": number, "n": prior["n"], "first": prior["first"], "last": prior["last"], "RankIC": prior["RankIC"]})
    result = {"id": p["id"], "status": "COMPLETED_FIXED_EF51_CLOSE_RANKIC_DIAGNOSTIC", "protocol_sha256": args.protocol_sha,
              "driver_sha256": p["driver_sha256"], "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_rows": 32905, "periods": 51, "same_ordered_identity_sha256_before_labels": identity_sha,
              "summary": summaries(periods, label_helpers), "five_descriptive_folds_inherited": folds, "period_summaries": periods,
              "control_RankIC_reused_from_completed_diagnostic": {"binding": p["bindings"]["completed_E_G_GF_F_diagnostic"],
                  "summary": controls["summary"]["RankIC"], "five_descriptive_folds_inherited": reused_folds,
                  "old_control_scores_RankIC_recomputed": False},
              "observed_label_rows": sum(row["observed_labels"] for row in periods),
              "missing_label_rows": sum(row["missing_labels"] for row in periods),
              "price_basis_review": basis, "label_contract": p["label_contract"], "bindings": p["bindings"], "limitations": p["limitations"],
              "new_contexts": 0, "new_factor_definition": False, "new_partial_or_weight_sign_window_search": False,
              "new_stocklevel_caches": 0, "economic_paths_replayed": 0, "official_calls": 0,
              "shared_state_registry_or_masks_written": False, "formal_admission": False, "fresh_OOS": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "sha256": sha(output.read_bytes()), "summary": result["summary"]}))


if __name__ == "__main__":
    main()
