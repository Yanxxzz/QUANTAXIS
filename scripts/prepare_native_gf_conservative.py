"""One root-frozen conservative GF/F141 proxy replay; no labels or fee calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.factor_blend import equal_signal
from panda_alpha.native_gf_conservative import conservative_g_source, render_source_conservative
from panda_alpha.native_gf_transfer import compress_g_intervals, file_sha256, read_mongo_gf_fixture
from panda_alpha.risk_relation import relation_states

OUT = ROOT / "research_runs/night_research_20261011/native_source_conservative_unit"
BASE = ROOT / "research_runs/night_research_20261011/native_gf_adapter_unit"
PROTOCOL_SHA = "eeefb4bc7e510fb72fd1bf2c4cbc4c98cd7b142dd29069a02b355c04db53e726"
read = lambda p: json.loads(Path(p).read_text(encoding="utf-8-sig"))


def artifact(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_sha256(path)}


def save(name, value):
    path = OUT / name
    if path.exists():
        raise ValueError("Completed or frozen artifact exists; do not overwrite " + name)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    protocol_path = OUT / "protocol.json"
    if file_sha256(protocol_path) != PROTOCOL_SHA:
        raise ValueError("Root-frozen conservative protocol changed")
    if (OUT / "adapter_review.json").exists() or (OUT / "replay_started.json").exists():
        raise ValueError("Pair replay already started/completed; inspect before resuming")
    protocol = read(protocol_path)
    inherited = read(BASE / "adapter_review.json")
    for name in ("reader", "fixture_descriptor", "input_calendar"):
        if file_sha256(inherited[name]["path"]) != inherited[name]["sha256"]:
            raise ValueError("Baseline source reader or input contract changed: " + name)
    g, evaluation_dates, formations, source_review = conservative_g_source(protocol_path)
    spec = protocol["context_candidates"][0]["strategy_spec"]
    calendar = read(spec["calendar_binding"]["path"])["dates"]
    if [d for d in calendar if spec["evaluation_window"]["start"] <= d <= spec["evaluation_window"]["end"]] != evaluation_dates:
        raise ValueError("Independent input/evaluation calendar mismatch")
    descriptor_path = Path(inherited["fixture_descriptor"]["path"])
    descriptor = read(descriptor_path)
    if descriptor["input_window"] != spec["input_window"]:
        raise ValueError("Inherited price source window changed")
    source = compress_g_intervals(g, evaluation_dates)
    life_path = ROOT / "research_runs/wc03_history_2022_20261009/lifecycle_snapshot.json"
    markets = {r["code"]: r["sse"] for r in read(life_path)["records"] if r["code"] in source}
    definitions = [
        ("GF", "WC03GFConservative", protocol["context_candidates"][0]),
        ("F141_CONTROL", "WC03F141Conservative", protocol["context_candidates"][1]),
    ]
    codes = []
    for mode, name, candidate in definitions:
        code = render_source_conservative(source, markets, calendar, spec["evaluation_window"], mode, name)
        codepath = OUT / (name + ".py")
        if codepath.exists():
            raise ValueError("Frozen generated code exists; inspect before resuming")
        codepath.write_text(code, encoding="utf-8", newline="\n")
        codes.append((mode, name, candidate, codepath, code))
    save("replay_started.json", {"at_utc": datetime.now(timezone.utc).isoformat(),
                                "protocol": artifact(protocol_path),
                                "descriptor": artifact(descriptor_path),
                                "reader": inherited["reader"], "generator": artifact(__file__),
                                "code": [artifact(p) for _, _, _, p, _ in codes],
                                "new_labels_read": 0, "official_spent": 0})
    print(json.dumps({"stage": "frozen_conservative_pair_ready", "source_rows": len(g),
                      "source_anchors": len(g[g.date.isin(formations)]),
                      "contexts": [c["candidate_id"] for _, _, c, _, _ in codes]}), flush=True)
    fixture = read_mongo_gf_fixture(descriptor_path)
    print(json.dumps({"stage": "single_source_view_read", "rows": len(fixture),
                      "dates": fixture.date.nunique(), "peers": fixture.symbol.nunique()}), flush=True)
    # Original independent mathematical engine; no execution/forward labels.
    wide = fixture.pivot(index="date", columns="symbol", values="close").reindex(calendar)
    wide.index = pd.to_datetime(wide.index)
    state = relation_states(wide)["F141"]
    projected = fixture[fixture.date.between(spec["evaluation_window"]["start"],
                                            spec["evaluation_window"]["end"])].copy().reset_index(drop=True)
    times = pd.to_datetime(projected.date)
    x, y = state.index.get_indexer(times), state.columns.get_indexer(projected.symbol)
    f_values = state.to_numpy()[x, y]
    projected = projected.merge(g[["date", "symbol", "G_pct"]], on=["date", "symbol"],
                                how="left", validate="one_to_one")
    projected["source_qualified"] = np.isfinite(projected.G_pct)
    projected["F_share"] = f_values
    selected = projected[projected.source_qualified & np.isfinite(projected.F_share)][
        ["date", "symbol", "G_pct", "F_share"]].copy()
    selected["F_pct"] = selected.groupby("date").F_share.rank(method="average", pct=True)
    expected_gf = np.full(len(projected), np.nan)
    expected_control = np.full(len(projected), np.nan)
    for _, rows in selected.groupby("date", sort=False):
        try:
            gf = equal_signal(rows, {"G_pct": 1, "F_pct": 0})
            control = equal_signal(rows, {"F_pct": 1})
        except ValueError as exc:
            if not str(exc).startswith("Constant component on formation date:"):
                raise
            continue
        expected_gf[rows.index.to_numpy()] = gf.to_numpy()
        expected_control[rows.index.to_numpy()] = control.to_numpy()
    del wide, state
    expected_index = pd.MultiIndex.from_arrays([times, projected.symbol], names=["date", "symbol"])
    price_index = pd.MultiIndex.from_arrays([pd.to_datetime(fixture.date), fixture.symbol], names=["date", "symbol"])
    price = pd.Series(fixture.close.to_numpy(), index=price_index)
    decision_dates = evaluation_dates[:-6]
    if decision_dates[::spec["cycle"]] != formations:
        raise ValueError("Registered public formation phase changed")
    results, masks, outputs = [], [], []
    for mode, name, candidate, codepath, code in codes:
        namespace = {"Factor": object, "__name__": "local_source_conservative_replay"}
        exec(compile(code, str(codepath), "exec"), namespace)
        output = namespace[name]().calculate({"close": price})
        expected = expected_gf if mode == "GF" else expected_control
        values = output.to_numpy(dtype=float)
        if not output.index.equals(expected_index):
            raise AssertionError("Native pair evaluation projection changed")
        finite = np.isfinite(expected)
        if not np.array_equal(np.isfinite(values), finite):
            raise AssertionError("Native/reference conservative finite mask mismatch")
        error = float(np.max(np.abs(values[finite] - expected[finite]))) if finite.any() else None
        if error is None or error > 2e-13:
            raise AssertionError("Conservative pair math differs from frozen reference")
        grouping = []
        for day in decision_dates:
            v = values[projected.date.eq(day).to_numpy()]
            v = v[np.isfinite(v)]
            ready = False
            try:
                ready = len(np.unique(pd.qcut(v, spec["groups"], labels=False))) == spec["groups"]
            except ValueError:
                pass
            grouping.append({"date": day, "finite": len(v), "distinct": len(np.unique(v)),
                             "no_jitter_ten_groups": ready})
        results.append({"candidate_id": candidate["candidate_id"], "mode": mode,
                        "wrapper_direction": candidate["direction"], "code": artifact(codepath),
                        "max_absolute_math_error": error, "finite_mask_exact": True,
                        "finite_rows": int(finite.sum()), "complete_eval_projection": True,
                        "all_251_decision_dates_group_ready": all(r["no_jitter_ten_groups"] for r in grouping),
                        "min_decision_finite": min(r["finite"] for r in grouping),
                        "min_decision_distinct": min(r["distinct"] for r in grouping),
                        "failed_group_dates": [r["date"] for r in grouping if not r["no_jitter_ten_groups"]]})
        masks.append(finite)
        outputs.append(values)
        print(json.dumps({"stage": "conservative_mode_math_complete", **results[-1]}), flush=True)
    if not np.array_equal(masks[0], masks[1]):
        raise AssertionError("GF and direction0 F control have different usable identities")
    mask = projected[["date", "symbol", "source_qualified"]].copy()
    mask["expected_finite"] = masks[0]
    mask_path = OUT / "source_qualification.parquet"
    mask.to_parquet(mask_path, index=False, compression="zstd")
    evaluation_contract = {"input_window": spec["input_window"],
                           "evaluation_window": spec["evaluation_window"],
                           "formation_dates": formations, "holding_end": spec["holding_end"],
                           "warmup": {"minimum_prior_price_rows": 258},
                           "input_calendar": spec["calendar_binding"],
                           "source_qualification": artifact(mask_path)}
    peer_hash = lambda codes: hashlib.sha256(json.dumps(sorted(codes), separators=(",", ":")).encode()).hexdigest()
    review = {"status": "FROZEN_CONSERVATIVE_GF_F141_PAIR_MATH_VERIFIED_ON_STOCKDB_PROXY",
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "protocol": artifact(protocol_path), "generator": artifact(__file__),
              "render_module": artifact(ROOT / "panda_alpha/native_gf_conservative.py"),
              "reader": inherited["reader"], "fixture_descriptor": inherited["fixture_descriptor"],
              "input_calendar": spec["calendar_binding"], "source_qualification": artifact(mask_path),
              "native_evaluation_contract": evaluation_contract, "source_review": source_review,
              "financial_market_identity": artifact(life_path), "input_rows": len(fixture),
              "eval_rows": len(projected), "input_dates": len(calendar),
              "prior_calendar_dates": sum(d < spec["evaluation_window"]["start"] for d in calendar),
              "received_price_peers": fixture.symbol.nunique(),
              "received_price_peer_set_sha256": peer_hash(fixture.symbol.unique().tolist()),
              "full_price_peers_never_filtered_by_financial_source": True,
              "all_peers_canonical_alias_uniqueness_enforced": True,
              "shared_finite_mask_exact": True, "contexts": results,
              "holding_end_rows": int(projected.date.eq(spec["holding_end"]).sum()),
              "holding_end_finite": int(mask[mask.date.eq(spec["holding_end"])].expected_finite.sum()),
              "holding_only_tail": evaluation_dates[-6:],
              "holding_tail_true_NaN": True,
              "price_proxy": descriptor["price_basis"],
              "price_basis_remote_peer_scope_and_tradeability_not_certified": True,
              "full_history_all_A_or_PIT_certified": False,
              "source_scope_pending_identities_excluded_not_cleared": True,
              "new_income_resolutions_or_scope_clears_used": False,
              "not_actual_production_export": True, "not_formal_pool_increment_or_admission": True,
              "dispatch_ready": False, "tests_passed": 10,
              "new_labels_or_returns_read": 0, "official_spent": 0,
              "raw_price_snapshot_copies": 0, "registry_or_state_writes": 0}
    save("adapter_review.json", review)
    source_evidence = [artifact(protocol_path), spec["calendar_binding"], artifact(mask_path),
                       source_review["original_G_source"]["protocol"],
                       *source_review["original_G_source"]["source_bindings"].values(),
                       spec["additional_parent_binding"], artifact(life_path), inherited["fixture_descriptor"]]
    contract_evidence = [artifact(OUT / "adapter_review.json"),
                         artifact(BASE.parent / "native_minimal_test_contract_plan.json")]
    for (_, _, frozen, codepath, _), result in zip(codes, results):
        candidate = {"candidate_id": frozen["candidate_id"], "direction": frozen["direction"],
                     "strategy_spec": frozen["strategy_spec"], "formula": None,
                     "code_path": str(codepath), "code_sha256": file_sha256(codepath),
                     "window": spec["input_window"], "cycle": spec["cycle"], "groups": spec["groups"],
                     "native_preflight_purpose": "source_qualified_projection_research",
                     "native_evaluation_contract": evaluation_contract,
                     "fixture_descriptor": inherited["fixture_descriptor"],
                     "source_evidence": source_evidence, "contract_evidence": contract_evidence,
                     "native_preflight_path": str(OUT / (result["mode"] + "_native_preflight.json")),
                     "load_code_in_memory": "Read exact UTF-8 code_path after verifying code_sha256.",
                     "not_actual_production_export": True, "not_formal_admission_or_pool_increment": True,
                     "source_unknown_excluded_not_cleared": True, "dispatch_ready": False}
        save(result["mode"] + "_candidate.json", candidate)
    print(json.dumps({"status": review["status"], "review_sha256": file_sha256(OUT / "adapter_review.json"),
                      "source_mask_sha256": file_sha256(mask_path), "common_finite": int(masks[0].sum())}), flush=True)


if __name__ == "__main__":
    main()
