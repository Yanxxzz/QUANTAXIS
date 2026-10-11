"""One registered R&D source pilot; F141 all-price peers, real signal blending."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from panda_alpha import study
from panda_alpha.fixed_study import review_fixed_portfolios
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(study._clean(value), f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        f.write("\n")


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, required=True)
    cli.add_argument("--protocol-sha", required=True)
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print("DEFERRED_NO_SOURCE_SIGNALS_OR_RETURN_LABELS")
        return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human deadline reached")
    if any(Path(p[k]).exists() for k in ["output", "target_receipt", "checkpoint"]):
        raise ValueError("completed or pending execution exists; no automatic replay")
    for b in p["bindings"].values():
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("source/runtime binding changed")
    reg = ResearchRegistry(p["registry"])
    keys = [trial_key(strategy_definition(c, p["window"], 5, 10)) for c in p["candidates"]]
    if any(k not in reg.registrations() for k in keys):
        raise ValueError("all source-specific contexts must precede signals/labels")
    source = json.loads(Path(p["bindings"]["source_receipt"]["path"]).read_text(encoding="utf-8"))
    records = source["records"]
    codes = [r["symbol"] for r in records]
    if len(codes) != 200 or len(set(codes)) != 200:
        raise ValueError("fixed source200 denominator changed")
    descriptor = json.loads(Path(p["bindings"]["price_descriptor"]["path"]).read_text(encoding="utf-8"))
    if descriptor["input_window"]["start"] > p["input_calendar"][0] or descriptor["input_window"]["end"] < p["window"]["end"]:
        raise ValueError("source-view warmup/window is incomplete")
    print("READING_BOUND_LOCAL_PRICE_INPUT_NO_RETURN_LABELS", flush=True)
    frame = read_mongo_gf_fixture(p["bindings"]["price_descriptor"]["path"])
    frame["date"] = pd.to_datetime(frame.date)
    wide = frame.pivot(index="date", columns="symbol", values="close").reindex(pd.to_datetime(p["input_calendar"]))
    # The exact historical F141 math is applied before any financial/source mask.
    ns = {"np": np, "pd": pd}
    exec(compile(Path(p["bindings"]["F141_math"]["path"]).read_text(encoding="utf-8"), "<bound-F141-price-math>", "exec"), ns)
    fwide = ns["peer_share"](wide)
    input_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    full_peer_count = frame.symbol.nunique()
    del frame
    idx = pd.MultiIndex.from_product([pd.to_datetime(p["anchors"]), codes], names=["date", "symbol"])
    values = {k: pd.Series(np.nan, index=idx, dtype=float) for k in ["r_n_d", "operating_revenue"]}
    for r in records:
        if r["status"] != "research_expense_and_revenue_current_annual_bound":
            continue
        if not r.get("stored_available_date") or r["stored_available_date"] >= p["window"]["start"]:
            raise ValueError("annual input must be available before first formation")
        for name, key in [("r_n_d", "research_expense"), ("operating_revenue", "operating_revenue")]:
            field = r["fields"][key]
            if field["current_start"] != "2025-01-01" or field["current_end"] != "2025-12-31":
                raise ValueError("current R&D/revenue spans differ")
            values[name].loc[(slice(None), r["symbol"])] = float(field["current_amount_yuan"])
    values["close"] = wide.rename_axis(index="date", columns="symbol").stack(dropna=False).reindex(idx)
    core = {"Factor": _FactorFixture, "__name__": "source-RD-interface"}
    exec(compile(Path(p["bindings"]["RD_kernel"]["path"]).read_text(encoding="utf-8"), "<fixed-RD-kernel>", "exec"), core)
    klass = [v for v in core.values() if isinstance(v, type) and v is not _FactorFixture and issubclass(v, _FactorFixture)]
    if len(klass) != 1:
        raise ValueError("one R&D kernel required")
    rd = klass[0]().calculate({k: FactorSeriesFixture(v) for k, v in values.items()})
    if not isinstance(rd, pd.Series) or rd.name != "value" or not rd.index.equals(idx):
        raise ValueError("R&D known-interface identity changed")
    f = fwide.rename_axis(index="date", columns="symbol").stack(dropna=False).reindex(idx)
    raw = pd.concat([rd.rename("RD_RAW"), f.rename("F141_RAW")], axis=1).reset_index()
    raw["date"] = raw.date.dt.strftime("%Y-%m-%d")
    schedules = {k: {} for k in ["RDI", "F141", "RDI_F141", "COMMON_EQUAL_SOURCE"]}
    positions = {d: i for i, d in enumerate(p["calendar"])}
    signals, coverage, targets, feature_rho = [], [], [], []
    for day in p["anchors"]:
        all_rows = raw[raw.date.eq(day)]
        rows = all_rows[np.isfinite(all_rows.RD_RAW) & np.isfinite(all_rows.F141_RAW)].copy()
        rows["RDI"] = rows.RD_RAW.rank(method="average", pct=True)
        rows["F141"] = (-rows.F141_RAW).rank(method="average", pct=True)
        rows["RDI_F141"] = (rows.RDI+rows.F141)/2
        entry = p["calendar"][positions[day]+1]
        target = {"formation_date": day, "entry_date": entry, "common_count": len(rows)}
        for name in ["RDI", "F141", "RDI_F141"]:
            groups, status = study.quantile_groups(rows[["symbol", name]].rename(columns={name: "value"}), 10, 30)
            if status != "groups_available":
                raise ValueError("common groups pending: "+day+":"+name+":"+status)
            schedules[name][entry] = {"decision_date": day, "codes": groups["10"]}
            target[name] = groups["10"]
        common = sorted(rows.symbol.tolist())
        schedules["COMMON_EQUAL_SOURCE"][entry] = {"decision_date": day, "codes": common}
        target["common_codes_sha256"] = study.object_hash(common)
        targets.append(target)
        coverage.append({"formation_date": day, "source_denominator": 200,
            "RD_source_finite": int(np.isfinite(all_rows.RD_RAW).sum()), "common_finite": len(rows)})
        feature_rho.append(float(rows.RDI.corr(rows.F141, method="spearman")))
        signals.append(rows[["date", "symbol", "RDI", "F141", "RDI_F141"]])
    signals = pd.concat(signals, ignore_index=True)
    write_new(p["target_receipt"], {"protocol_sha256": args.protocol_sha, "coverage": coverage, "targets": targets,
        "schedule_sha256": study.object_hash(schedules), "price_input_hash": input_hash,
        "all_received_price_peers_before_financial_selection": full_peer_count,
        "RDI_F141_signal_Spearman_median": float(np.median(feature_rho)), "return_labels_not_yet_read": True})
    print("ALL_FOUR_TARGETS_FROZEN_BEFORE_FORWARD_LABELS", flush=True)
    quotes = pd.read_parquet(p["bindings"]["execution_quotes"]["path"])
    quotes = quotes[quotes.symbol.isin(codes) & pd.to_datetime(quotes.date).le(pd.Timestamp(p["window"]["end"]))].copy()
    if set(quotes.loc[quotes.close.gt(0), "adjustment"].dropna().unique()) != {"qfq"}:
        raise ValueError("execution price basis changed")
    days = p["calendar"][1:]
    review = review_fixed_portfolios(schedules, signals, p["calendar"], p["anchors"], days, quotes, p["costs"], p["checkpoint"])
    write_new(p["daily_ledger"], review.pop("portfolio_daily"))
    result = {"status": "EIGHT_MATCHED_RD_F141_COST_PATHS_COMPLETE", "protocol_sha256": args.protocol_sha,
        **review, "coverage": coverage, "holding_days": len(days), "holding_start": days[0], "holding_end": days[-1],
        "source_limited_preliminary": True, "real_signal_blend_not_average_held_returns": True,
        "new_contexts": len(keys), "registry_total": reg.total, "official_calls": 0,
        "full_PIT_or_native_current_period_value_parity": False, "formal_admission": False,
        "price_peer_input_hash": input_hash, "no_partial_outcome_tuning": True,
        "daily_ledger": {"path": p["daily_ledger"], "sha256": sha(p["daily_ledger"])},
        "completed_at_utc": datetime.now(timezone.utc).isoformat(), "limits": p["limits"]}
    write_new(p["output"], result)
    for key in keys:
        reg.append_event("evaluation", key, {"receipt": {"path": p["output"], "sha256": sha(p["output"])},
            "new_contexts": 0, "source_limited_preliminary": True, "formal_admission": False},
            event_id="RD-source200-pilot-result:"+key+":"+sha(p["output"]))
    reg.db.close()
    print(json.dumps({"status": result["status"], "RankIC": result["RankIC"],
        "results": [{k: r[k] for k in ["name", "cost", "metrics", "terminal_residual_fraction"]} for r in result["results"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
