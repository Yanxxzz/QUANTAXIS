"""Deferred fixed EF native build; root registration precedes all real inputs."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact(path):
    return {"path": str(Path(path).resolve()), "sha256": sha(path)}


def save_new(name, value):
    path = OUT / name
    if path.exists():
        raise ValueError("Owned EF output exists; no automatic replay: " + name)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def pandas_hash(value):
    import pandas as pd
    return hashlib.sha256(pd.util.hash_pandas_object(value, index=True).values.tobytes()).hexdigest()


def require_registration(p):
    from panda_alpha.registry import strategy_definition, trial_key
    definition = strategy_definition(p["candidate_draft"], p["evaluation_window"], 5, 10)
    if definition != p["registration_definition"] or trial_key(definition) != p["registration_trial_key"]:
        raise ValueError("Frozen single EF definition changed")
    uri = (ROOT / "research_runs/research_trials.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT sequence,payload FROM research_events WHERE kind='registration' AND subject=?", (p["registration_trial_key"],)).fetchall()
    for sequence, payload in rows:
        event = json.loads(payload)
        if event.get("candidate_id") == p["candidate_draft"]["candidate_id"] and event.get("definition") == definition:
            return {"sequence": sequence, "trial_key": p["registration_trial_key"]}
    raise ValueError("Root EF definition/window registration required before actual EF/source/price/output reads")


def constants(code):
    cls = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef))
    return {t.id: ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}


def execute(p, protocol_sha):
    registered = require_registration(p)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("Human research deadline reached")
    for label, item in p["bindings"].items():
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Frozen EF binding changed: " + label)
    output_names = ["preparation_review.json", "EF_candidate.json", "EF_native_preflight.json", "WC03EfficiencyF141Pair.py", "EF_formation_signals.parquet"]
    if any((OUT / name).exists() for name in output_names):
        raise ValueError("Owned EF preparation already started/completed; no automatic replay")

    import numpy as np
    import pandas as pd
    from native_pair import render_efficiency_f141_pair
    from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
    from panda_alpha.native_preflight import build_native_preflight, _FactorFixture, FactorSeriesFixture

    e_protocol = read(p["bindings"]["E_protocol"]["path"])
    e_prep = read(p["bindings"]["E_preparation"]["path"])
    e_proof = read(p["bindings"]["E_preflight"]["path"])
    required = ("controls_reuse_allowed", "shared_finite_mask_exact", "source_qualification_exact", "original_f_formation_keys_exact",
                "all_251_decisions_group_ready", "original_SOURCE_and_all_peer_F_method_preserved", "holding_tail_true_NaN")
    if (any(e_prep.get(k) is not True for k in required) or e_proof.get("research_replay_passed") is not True
            or e_proof["code_sha256"] != p["bindings"]["E_code"]["sha256"]
            or e_proof["proof"]["input_frame_sha256"] != p["expected_input_frame_sha256"]
            or e_proof["proof"]["output_sha256"] != e_prep["output_sha256"]
            or (e_prep["financial_report_identities"], e_prep["financial_source_rows"], e_prep["common_anchor_rows"], e_prep["formation_dates"], e_prep["holding_days"]) != (1736,161366,32905,51,256)
            or e_protocol["evaluation_window"] != p["evaluation_window"]
            or e_protocol["candidate_draft"]["native_evaluation_contract"] != p["candidate_draft"]["native_evaluation_contract"]):
        raise ValueError("Immutable standalone E source/input/full-mask proof changed")

    old_code = Path(p["bindings"]["E_code"]["path"]).read_text(encoding="utf-8")
    literal = constants(old_code)
    code = render_efficiency_f141_pair(old_code)
    if constants(code) != literal:
        raise ValueError("EF changed an E financial source, F parameter or calendar literal")
    evaluation_calendar = [day for day in literal["INPUT_CALENDAR"] if literal["EVALUATION_START"] <= day <= literal["EVALUATION_END"]]
    decisions = evaluation_calendar[:-6]
    formations = p["candidate_draft"]["native_evaluation_contract"]["formation_dates"]
    if (len(decisions), len(formations), len(evaluation_calendar[1:])) != (251,51,256) or decisions[::5] != formations:
        raise ValueError("Original 251/51/256 fixed phase changed")
    fixture = read_mongo_gf_fixture(p["bindings"]["fixture_descriptor"]["path"])
    frame = fixture[["date", "symbol", "close"]].copy()
    frame.date = pd.to_datetime(frame.date)
    frame = frame.set_index(["date", "symbol"])
    input_hash = pandas_hash(frame)
    if input_hash != p["expected_input_frame_sha256"]:
        raise ValueError("Original native actual full-peer input frame changed")
    namespace = {"Factor": _FactorFixture, "__name__": "registered_efficiency_f141_pair_native"}
    exec(compile(code, "<registered_efficiency_f141_pair_native>", "exec"), namespace)
    output = namespace["WC03EfficiencyF141Pair"]().calculate({"close": FactorSeriesFixture(frame.close)})
    mask = pd.read_parquet(p["bindings"]["original_source_mask"]["path"])
    index = pd.MultiIndex.from_arrays([pd.to_datetime(mask.date), mask.symbol], names=["date", "symbol"])
    if not output.index.equals(index):
        raise ValueError("EF output changed original full evaluation skeleton")
    finance = pd.Series(np.nan, index=index)
    days = index.get_level_values("date").strftime("%Y-%m-%d").to_numpy()
    symbols = index.get_level_values("symbol").to_numpy()
    for symbol, intervals in literal["E_SOURCE"].items():
        positions = np.flatnonzero(symbols == symbol)
        for first, last, value in intervals:
            take = positions[(days[positions] >= first) & (days[positions] <= last)]
            finance.iloc[take] = value
    source_exact = np.array_equal(np.isfinite(finance), mask.source_qualified.to_numpy(dtype=bool))
    finite_exact = np.array_equal(np.isfinite(output), mask.expected_finite.to_numpy(dtype=bool))
    dates = []
    for day in decisions:
        rows = output.xs(pd.Timestamp(day), level="date").dropna()
        try:
            ready = len(rows) >= 10 and len(np.unique(pd.qcut(rows.to_numpy(), 10, labels=False, duplicates="raise"))) == 10
        except ValueError:
            ready = False
        dates.append({"date": day, "finite": len(rows), "distinct": int(rows.nunique()), "group_ready": ready})
    anchors = output[output.index.get_level_values("date").isin(pd.to_datetime(formations))].dropna().rename("EF").reset_index()
    anchors.date = anchors.date.dt.strftime("%Y-%m-%d")
    e_cache = pd.read_parquet(p["bindings"]["E_signals"]["path"], columns=["date", "symbol", "E"])
    old_cache = pd.read_parquet(p["bindings"]["original_formation_signals"]["path"], columns=["date", "symbol", "F141_CONTROL"])
    keys_exact = (len(anchors) == len(e_cache) == len(old_cache) == 32905
                  and anchors[["date", "symbol"]].equals(e_cache[["date", "symbol"]])
                  and anchors[["date", "symbol"]].equals(old_cache[["date", "symbol"]])
                  and not anchors.duplicated(["date", "symbol"]).any())
    error = None
    if keys_exact and np.isfinite(e_cache.E).all() and np.isfinite(old_cache.F141_CONTROL).all():
        expected = .5 * (e_cache.E.to_numpy() - old_cache.F141_CONTROL.to_numpy())
        error = float(np.max(np.abs(anchors.EF.to_numpy() - expected)))
    tail = output[output.index.get_level_values("date").isin(pd.to_datetime(evaluation_calendar[-6:]))]
    ready = source_exact and finite_exact and keys_exact and error is not None and error <= 2e-12 and tail.isna().all() and all(x["group_ready"] for x in dates)
    review = {"status": "EF_FULL_SCOPE_NATIVE_READY_REUSE_GF_F_MARKET" if ready else "EF_PREFLIGHT_PENDING_NO_DISPATCH_CROP_OR_VARIANT",
              "registered_context": registered, "protocol_sha256": protocol_sha,
              "source_qualification_exact": source_exact, "shared_finite_mask_exact": finite_exact,
              "original_f_formation_keys_exact": keys_exact, "controls_reuse_allowed": bool(ready),
              "old_input_frame_sha256": input_hash, "output_sha256": pandas_hash(output),
              "max_absolute_saved_E_minus_F_control_reference_error": error,
              "financial_source_rows": 161366, "financial_report_identities": 1736,
              "formation_dates": 51, "common_anchor_rows": len(anchors), "holding_days": 256,
              "all_251_decisions_group_ready": all(x["group_ready"] for x in dates), "per_decision_date": dates,
              "original_SOURCE_and_all_peer_F_method_preserved": True, "immutable_E_SOURCE_and_all_old_gates_preserved": True,
              "only_native_class_and_component_AST_changed": True, "no_final_Z_or_signal_return_blend": True,
              "holding_tail_true_NaN": bool(tail.isna().all()), "remaining_source_exclusions": 0,
              "new_labels_or_returns_read": False, "new_economic_results_read": False, "official_spent": 0,
              "registry_state_writes": 0, "full_PIT": False, "fresh_blind_OOS": False, "formal_admission": False,
              "support_mismatch_requires_root_decision_not_automatic_new_control": not finite_exact or not keys_exact}
    save_new("preparation_review.json", review)
    if not ready:
        print(json.dumps({"status": review["status"], "source_exact": source_exact, "finite_exact": finite_exact, "keys_exact": keys_exact}))
        return
    code_path = OUT / "WC03EfficiencyF141Pair.py"
    code_path.write_bytes(code.encode("utf-8"))
    candidate = deepcopy(read(p["bindings"]["E_candidate"]["path"]))
    candidate.update(p["candidate_draft"])
    candidate.update(code_path=str(code_path), code_sha256=sha(code_path), native_preflight_path=str(OUT / "EF_native_preflight.json"), dispatch_ready=False)
    candidate["source_evidence"] = [*candidate["source_evidence"], p["bindings"]["E_code"], p["bindings"]["E_preflight"],
                                    p["bindings"]["E_preparation"], p["bindings"]["pair_renderer"], artifact(OUT / "protocol.json")]
    candidate["contract_evidence"] = [*candidate["contract_evidence"], p["bindings"]["pair_tests"], p["bindings"]["E_signals"],
                                      p["bindings"]["original_formation_signals"], artifact(OUT / "preparation_review.json")]
    candidate["source_metadata"] = p["source_metadata"]
    in_memory = deepcopy(candidate)
    in_memory["code"] = code
    preflight = build_native_preflight(in_memory, candidate["window"], 5, 10, p["bindings"]["fixture_descriptor"]["path"],
                                      [x["path"] for x in candidate["source_evidence"]], [x["path"] for x in candidate["contract_evidence"]],
                                      candidate["native_preflight_path"], fields=["close"], fixture_reader=read_mongo_gf_fixture)
    candidate["dispatch_ready"] = bool(preflight.get("research_replay_passed"))
    save_new("EF_candidate.json", candidate)
    if candidate["dispatch_ready"]:
        anchors.to_parquet(OUT / "EF_formation_signals.parquet", index=False, compression="zstd")
    print(json.dumps({"status": preflight.get("status"), "research_replay_passed": candidate["dispatch_ready"], "candidate": artifact(OUT / "EF_candidate.json")}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-sha")
    parser.add_argument("--execute-after-registration", action="store_true")
    args = parser.parse_args()
    path = OUT / "protocol.json"
    p = read(path)
    if not args.execute_after_registration:
        print(json.dumps({"status": "DEFERRED_NO_REAL_EF_SOURCE_PRICE_OUTPUT_OR_LABEL_READ", "protocol_sha256": sha(path),
                          "candidate_id": p["candidate_draft"]["candidate_id"], "registration_trial_key": p["registration_trial_key"]}))
        return
    if p["status"] != "FROZEN_FIXED_EF_NATIVE_AND_TWO_COST_SCOPE_AWAIT_ROOT_REGISTRATION" or not args.protocol_sha or sha(path) != args.protocol_sha:
        raise ValueError("Root-frozen exact EF protocol SHA required")
    execute(p, args.protocol_sha)


if __name__ == "__main__":
    main()
