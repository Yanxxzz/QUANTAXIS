"""Deferred original-full-scope E build; no official or economic execution."""
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
        raise ValueError("Owned completed output exists; no implicit overwrite: " + name)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def pandas_hash(value):
    import pandas as pd
    return hashlib.sha256(pd.util.hash_pandas_object(value, index=True).values.tobytes()).hexdigest()


def require_registration(p):
    from panda_alpha.registry import strategy_definition, trial_key
    definition = strategy_definition(p["candidate_draft"], p["evaluation_window"], 5, 10)
    if definition != p["registration_definition"] or trial_key(definition) != p["registration_trial_key"]:
        raise ValueError("Frozen single E definition changed")
    uri = (ROOT / "research_runs/research_trials.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT sequence,payload FROM research_events WHERE kind='registration' AND subject=?", (p["registration_trial_key"],)).fetchall()
    for sequence, payload in rows:
        record = json.loads(payload)
        if record.get("candidate_id") == p["candidate_draft"]["candidate_id"] and record.get("definition") == definition:
            return {"sequence": sequence, "trial_key": p["registration_trial_key"]}
    raise ValueError("Root E registration required before real financial/price/output reads")


def constants(code):
    cls = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef))
    values = {t.id: ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    return cls.name, values


def run_code(code, name, close):
    from panda_alpha.native_preflight import _FactorFixture, FactorSeriesFixture
    namespace = {"Factor": _FactorFixture, "__name__": "registered_capital_native"}
    exec(compile(code, "<" + name + ">", "exec"), namespace)
    return namespace[name]().calculate({"close": FactorSeriesFixture(close)})


def execute(p, protocol_sha):
    registered = require_registration(p)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("Human research deadline reached")
    for label, item in p["bindings"].items():
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Frozen capital binding changed: " + label)
    if any((OUT / name).exists() for name in ["preparation_review.json", "E_candidate.json", "E_native_preflight.json", "WC03CapitalEfficiency.py", "E_formation_signals.parquet"]):
        raise ValueError("Owned E preparation already started/completed; no automatic replay")

    import numpy as np
    import pandas as pd
    from native_capital import AMOUNTS, AMOUNT_FILE_KEY, FINANCIAL_KEY, canonical_amounts, project_capital_source, capital_intervals, render_capital_code
    from panda_alpha.native_gf_conservative import conservative_g_source
    from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
    from panda_alpha.native_preflight import build_native_preflight

    closure = read(p["bindings"]["final28_closure"]["path"])
    if (closure["identity_fields"] != FINANCIAL_KEY or closure["original_identity_count"] != 1736
            or closure["unresolved_identities"] or closure["four_positive_source_amounts_verified_for_original1736"] is not True
            or closure["total_source_metadata_closed_including_prior1708"] != 1736
            or closure["roles_column_map"] != dict(zip(["revenue_current", "revenue_comparative", "assets_closing", "assets_opening"], AMOUNTS))):
        raise ValueError("Root-frozen full1736 source closure no longer holds; no reduced-scope fallback")
    source, evaluation_calendar, formations, source_receipt = conservative_g_source(p["bindings"]["original_protocol"]["path"])
    accepted = source[FINANCIAL_KEY].drop_duplicates()
    if len(source) != 161366 or len(accepted) != 1736 or len(source[source.date.isin(formations)]) != 32905:
        raise ValueError("Exact original full financial support changed")
    amounts = canonical_amounts(pd.read_parquet(p["bindings"]["annual_amounts"]["path"], columns=AMOUNT_FILE_KEY + AMOUNTS))
    amounts = amounts.merge(accepted, on=FINANCIAL_KEY, how="inner", validate="many_to_one", sort=False)
    selected = project_capital_source(source, amounts, accepted.to_dict("records"))
    if not selected[["date"] + FINANCIAL_KEY].reset_index(drop=True).equals(source[["date"] + FINANCIAL_KEY].reset_index(drop=True)):
        raise ValueError("E source projection changed original ordered report/date identities")
    e_source = capital_intervals(selected, evaluation_calendar)
    original_code = Path(p["bindings"]["original_GF_code"]["path"]).read_text(encoding="utf-8")
    _, literal = constants(original_code)
    code = render_capital_code(original_code, e_source, "WC03CapitalEfficiency")
    _, new_literal = constants(code)
    if any(new_literal[k] != literal[k] for k in ["SOURCE", "MARKETS", "INPUT_CALENDAR", "EVALUATION_START", "EVALUATION_END"]):
        raise ValueError("Capital wrapper changed legacy finance/F input calendar or SOURCE")
    old_candidate = read(p["bindings"]["original_GF_candidate"]["path"])
    contract = old_candidate["native_evaluation_contract"]
    decisions = evaluation_calendar[:-6]
    if len(decisions) != 251 or len(formations) != 51 or decisions[::5] != formations or len(evaluation_calendar[1:]) != 256:
        raise ValueError("Original complete251/51/256 phase changed")
    fixture = read_mongo_gf_fixture(p["bindings"]["fixture_descriptor"]["path"])
    frame = fixture[["date", "symbol", "close"]].copy()
    frame.date = pd.to_datetime(frame.date)
    frame = frame.set_index(["date", "symbol"])
    original_proof = read(p["bindings"]["original_GF_preflight"]["path"])["proof"]
    input_hash = pandas_hash(frame)
    if input_hash != original_proof["input_frame_sha256"]:
        raise ValueError("Actual original Mongo full-peer input hash changed")
    output = run_code(code, "WC03CapitalEfficiency", frame.close)
    mask = pd.read_parquet(p["bindings"]["original_source_mask"]["path"])
    index = pd.MultiIndex.from_arrays([pd.to_datetime(mask.date), mask.symbol], names=["date", "symbol"])
    if not output.index.equals(index):
        raise ValueError("Capital output changed the exact original full evaluation skeleton")
    finance = pd.Series(np.nan, index=index)
    days = index.get_level_values("date").strftime("%Y-%m-%d").to_numpy()
    symbols = index.get_level_values("symbol").to_numpy()
    for symbol, intervals in e_source.items():
        positions = np.flatnonzero(symbols == symbol)
        for first, last, value in intervals:
            take = positions[(days[positions] >= first) & (days[positions] <= last)]
            finance.iloc[take] = value
    source_exact = np.array_equal(np.isfinite(finance), mask.source_qualified.to_numpy(dtype=bool))
    finite_exact = np.array_equal(np.isfinite(output), mask.expected_finite.to_numpy(dtype=bool))
    # The original mask and unchanged full-peer F method are already certified
    # on this exact input SHA. Reference only the new E operand transformation.
    reference = pd.Series(np.nan, index=index)
    eligible = finance[mask.expected_finite.to_numpy(dtype=bool)]
    for day, rows in eligible.groupby(level="date", sort=False):
        clipped = rows.clip(*rows.quantile([.01, .99]).tolist())
        sd = float(clipped.std(ddof=0))
        if np.isfinite(sd) and sd > 1e-12:
            reference.loc[rows.index] = (clipped - clipped.mean()) / sd
    valid = np.isfinite(output) & np.isfinite(reference)
    error = float(np.max(np.abs(output[valid].to_numpy() - reference[valid].to_numpy()))) if valid.any() else None
    dates = []
    for day in decisions:
        rows = output.xs(pd.Timestamp(day), level="date").dropna()
        try:
            ready = len(rows) >= 10 and len(np.unique(pd.qcut(rows.to_numpy(), 10, labels=False, duplicates="raise"))) == 10
        except ValueError:
            ready = False
        dates.append({"date": day, "finite": len(rows), "distinct": int(rows.nunique()), "group_ready": ready})
    anchors = output[output.index.get_level_values("date").isin(pd.to_datetime(formations))].dropna().rename("E").reset_index()
    anchors.date = anchors.date.dt.strftime("%Y-%m-%d")
    old_keys = pd.read_parquet(p["bindings"]["original_formation_signals"]["path"], columns=["date", "symbol"])
    keys_exact = len(anchors) == 32905 and anchors[["date", "symbol"]].equals(old_keys)
    tail = output[output.index.get_level_values("date").isin(pd.to_datetime(evaluation_calendar[-6:]))]
    ready = source_exact and finite_exact and keys_exact and error is not None and error <= 2e-13 and tail.isna().all() and all(x["group_ready"] for x in dates)
    review = {"status": "E_FULL_SCOPE_NATIVE_READY_REUSE_F_CONTROLS" if ready else "E_FULL_SCOPE_PREFLIGHT_PENDING_NO_DISPATCH_OR_CROP",
              "registered_context": registered, "protocol_sha256": protocol_sha,
              "source_receipt": source_receipt, "source_qualification_exact": source_exact,
              "shared_finite_mask_exact": finite_exact, "original_f_formation_keys_exact": keys_exact,
              "controls_reuse_allowed": ready, "old_input_frame_sha256": input_hash,
              "output_sha256": pandas_hash(output), "max_absolute_E_reference_error": error,
              "financial_source_rows": len(source), "financial_report_identities": len(accepted),
              "formation_dates": 51, "common_anchor_rows": len(anchors), "holding_days": 256,
              "all_251_decisions_group_ready": all(x["group_ready"] for x in dates), "per_decision_date": dates,
              "original_SOURCE_and_all_peer_F_method_preserved": True, "holding_tail_true_NaN": bool(tail.isna().all()),
              "remaining_source_exclusions": 0, "new_F_definition_or_official_job": False,
              "new_labels_or_returns_read": False, "new_economic_results_read": False,
              "official_spent": 0, "registry_state_writes": 0, "full_PIT": False, "fresh_blind_OOS": False,
              "formal_admission": False, "support_mismatch_requires_root_decision_not_automatic_new_control": not finite_exact or not keys_exact}
    save_new("preparation_review.json", review)
    if not ready:
        print(json.dumps({"status": review["status"], "source_exact": source_exact, "finite_exact": finite_exact, "keys_exact": keys_exact}))
        return
    code_path = OUT / "WC03CapitalEfficiency.py"
    code_path.write_bytes(code.encode("utf-8"))
    candidate = deepcopy(old_candidate)
    candidate.update(p["candidate_draft"])
    candidate.update(code_path=str(code_path), code_sha256=sha(code_path), native_preflight_path=str(OUT / "E_native_preflight.json"), dispatch_ready=False)
    candidate["source_evidence"] = [*old_candidate["source_evidence"], p["bindings"]["final28_closure"], p["bindings"]["annual_amounts"],
                                    p["bindings"]["capital_helper"], p["bindings"]["capital_renderer"], artifact(OUT / "protocol.json")]
    candidate["contract_evidence"] = [*old_candidate["contract_evidence"], artifact(OUT / "preparation_review.json"),
                                      p["bindings"]["capital_tests"], p["bindings"]["renderer_tests"]]
    candidate["source_metadata"] = p["source_metadata"]
    in_memory = deepcopy(candidate)
    in_memory["code"] = code
    preflight = build_native_preflight(in_memory, candidate["window"], 5, 10, p["bindings"]["fixture_descriptor"]["path"],
                                      [x["path"] for x in candidate["source_evidence"]], [x["path"] for x in candidate["contract_evidence"]],
                                      candidate["native_preflight_path"], fields=["close"], fixture_reader=read_mongo_gf_fixture)
    candidate["dispatch_ready"] = bool(preflight.get("research_replay_passed"))
    save_new("E_candidate.json", candidate)
    if candidate["dispatch_ready"]:
        anchors.to_parquet(OUT / "E_formation_signals.parquet", index=False, compression="zstd")
    print(json.dumps({"status": preflight.get("status"), "research_replay_passed": candidate["dispatch_ready"], "candidate": artifact(OUT / "E_candidate.json")}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-sha")
    parser.add_argument("--execute-after-registration", action="store_true")
    args = parser.parse_args()
    path = OUT / "protocol.json"
    p = read(path)
    if not args.execute_after_registration:
        print(json.dumps({"status": "DEFERRED_NO_REAL_E_SOURCE_PRICE_OR_OUTPUT_READ", "protocol_sha256": sha(path), "candidate_id": p["candidate_draft"]["candidate_id"]}))
        return
    if p["status"] != "FROZEN_E_NATIVE_AND_ECONOMIC_SCOPE_AWAIT_ROOT_REGISTRATION" or not args.protocol_sha or sha(path) != args.protocol_sha:
        raise ValueError("Root-frozen exact E protocol SHA required")
    execute(p, args.protocol_sha)


if __name__ == "__main__":
    main()
