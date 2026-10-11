"""One frozen annual-source cash pilot with matched controls and costs."""
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
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(study._clean(value), f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        f.write("\n")


def run_inputs(p):
    source = json.loads(Path(p["bindings"]["source_receipt"]["path"]).read_text(encoding="utf-8"))
    records = source["records"]
    codes = [r["symbol"] for r in records]
    if len(codes) != 80 or len(set(codes)) != 80:
        raise ValueError("frozen source denominator changed")
    idx = pd.MultiIndex.from_product([pd.to_datetime(p["anchors"]), codes], names=["date", "symbol"])
    values = {k: pd.Series(np.nan, index=idx, dtype=float) for k in p["field_mapping"]}
    for r in records:
        if r["status"] != "three_current_annual_cash_amounts_bound":
            continue
        if not r.get("stored_available_date") or r["stored_available_date"] >= p["window"]["start"]:
            raise ValueError("bound annual source must predate first formation")
        for name, key in p["field_mapping"].items():
            field = r["fields"][key]
            if field["current_start"] != "2025-01-01" or field["current_end"] != "2025-12-31":
                raise ValueError("cashflow fiscal period mismatch")
            values[name].loc[(slice(None), r["symbol"])] = float(field["current_amount_yuan"])
    close = pd.read_parquet(p["bindings"]["quotes"]["path"], columns=["date", "symbol", "close"])
    close["date"] = pd.to_datetime(close.date)
    close = close[close.date.isin(pd.to_datetime(p["anchors"])) & close.symbol.isin(codes)]
    if close.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate formation close identity")
    values["close"] = close.set_index(["date", "symbol"]).close.reindex(idx)
    kernels = json.loads(Path(p["bindings"]["fixed_kernels"]["path"]).read_text(encoding="utf-8"))["candidates"]
    signals = pd.DataFrame(index=idx)
    for c, name in zip(kernels, ["MAIN", "CONTROL"]):
        if c["direction"] != 1:
            raise ValueError("unchanged direction required")
        ns = {"Factor": _FactorFixture, "__name__": "annual_cash_frozen_pilot"}
        exec(compile(c["code"], "<unchanged-cash-kernel>", "exec"), ns)
        classes = [v for v in ns.values() if isinstance(v, type) and v is not _FactorFixture and issubclass(v, _FactorFixture)]
        if len(classes) != 1:
            raise ValueError("one fixed Factor subclass required")
        value = classes[0]().calculate({k: FactorSeriesFixture(v) for k, v in values.items()})
        if not isinstance(value, pd.Series) or not value.index.equals(idx) or value.name != "value":
            raise ValueError("source kernel/index changed")
        if np.isinf(value.to_numpy(dtype=float)).any():
            raise ValueError("infinite source signal")
        signals[name] = value
    signals = signals.reset_index()
    signals["date"] = signals.date.dt.strftime("%Y-%m-%d")
    schedules = {k: {} for k in ["MAIN", "CONTROL", "COMMON_EQUAL_SOURCE"]}
    positions = {d: i for i, d in enumerate(p["calendar"])}
    coverage, target_rows = [], []
    for day in p["anchors"]:
        rows = signals[signals.date.eq(day)].dropna(subset=["MAIN", "CONTROL"])
        entry = p["calendar"][positions[day]+1]
        target = {"formation_date": day, "entry_date": entry, "common_count": len(rows)}
        for name in ["MAIN", "CONTROL"]:
            groups, status = study.quantile_groups(rows[["symbol", name]].rename(columns={name: "value"}), 10, 30)
            if status != "groups_available":
                raise ValueError("frozen common groups pending: "+day+":"+name+":"+status)
            schedules[name][entry] = {"decision_date": day, "codes": groups["10"]}
            target[name] = groups["10"]
        common = sorted(rows.symbol.tolist())
        schedules["COMMON_EQUAL_SOURCE"][entry] = {"decision_date": day, "codes": common}
        target["common_codes_sha256"] = study.object_hash(common)
        target_rows.append(target)
        coverage.append({"formation_date": day, "source_denominator": 80, "common_finite": len(rows)})
    return codes, signals, schedules, coverage, target_rows


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, required=True)
    cli.add_argument("--protocol-sha", required=True)
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print("DEFERRED_NO_RETURN_LABELS_OR_PORTFOLIO_REPLAY")
        return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen pilot protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human deadline reached")
    out = Path(p["output"])
    if out.exists() or Path(p["target_receipt"]).exists():
        raise ValueError("existing complete or pending execution; inspect, never automatically replay")
    for b in p["bindings"].values():
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("source/runtime binding changed")
    reg = ResearchRegistry(p["registry"])
    keys = [trial_key(strategy_definition(c, p["window"], 5, 10)) for c in p["candidates"]]
    if any(k not in reg.registrations() for k in keys):
        raise ValueError("all source-specific economic controls need registration before labels")
    codes, signals, schedules, coverage, targets = run_inputs(p)
    write_new(p["target_receipt"], {"protocol_sha256": args.protocol_sha, "coverage": coverage,
        "targets": targets, "schedule_sha256": study.object_hash(schedules), "return_labels_not_yet_read": True})
    print("FROZEN_TARGETS_COMPLETE_NO_RETURN_RESULTS_OPENED", flush=True)
    # Executable price labels are read only after all fixed targets are saved.
    frame = study._quotes_frame(pd.read_parquet(p["bindings"]["quotes"]["path"]))
    frame = frame[frame.symbol.isin(codes) & frame.date.le(p["holding_end"])].copy()
    if set(frame.loc[frame.close.gt(0), "adjustment"].dropna().unique()) != {"qfq"}:
        raise ValueError("positive execution marks do not share the declared qfq unit")
    book = study.PotentialQuoteBook(frame)
    days = [d for d in p["calendar"] if p["holding_start"] <= d <= p["holding_end"]]
    checkpoint = Path(p["checkpoint"])
    completed = []
    write_new(checkpoint, {"protocol_sha256": args.protocol_sha, "completed": [], "status": "RUNNING"})
    runs, ledger = [], []
    try:
        for cost in p["costs"]:
            for name in ["MAIN", "CONTROL", "COMMON_EQUAL_SOURCE"]:
                print("RUNNING_FIXED_PATH", name, cost, flush=True)
                run = study.replay_portfolio(name, schedules[name], days, book, cost)
                if [r["date"] for r in run["daily"]] != days:
                    raise ValueError("full daily holding calendar changed")
                if run["accounting"]["max_absolute_accounting_error"] > 1e-8:
                    raise ValueError("portfolio accounting error")
                runs.append({"name": name, "cost": cost, "metrics": run["metrics"],
                    "accounting": run["accounting"], "exposures": run["exposures"]["summary"],
                    "turnover": run["turnover"], "blocked_orders": run["blocked_orders"],
                    "terminal_liquidation_pending": run["terminal_liquidation_pending"],
                    "terminal_position_count": len(run["final_open_positions"]),
                    "terminal_residual_fraction": sum(q*book.price(c, days[-1], "close")[0]
                        for c, q in run["final_open_positions"].items())/run["daily"][-1]["nav"]})
                ledger.extend({"name": name, "cost": cost, **{k: r[k] for k in ["date", "net_return", "nav", "fees"]}} for r in run["daily"])
                completed.append(name+":"+str(cost))
                checkpoint.write_text(json.dumps(study._clean({"protocol_sha256": args.protocol_sha,
                    "completed": completed, "completed_results": runs, "completed_daily_ledger": ledger,
                    "status": "RUNNING"}), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        # All paths are complete before candidate-level economic metrics are reported.
        ic = {name: [] for name in ["MAIN", "CONTROL"]}
        positions = {d: i for i, d in enumerate(p["calendar"])}
        label_marks = 0
        for day in p["anchors"]:
            rows = signals[signals.date.eq(day)].dropna(subset=["MAIN", "CONTROL"]).set_index("symbol")
            entry, end = [p["calendar"][positions[day]+n] for n in [1, 6]]
            forward = {}
            for code in rows.index:
                first, mark1 = book.price(code, entry, "open")
                last, mark2 = book.price(code, end, "open")
                forward[code] = last/first-1
                label_marks += int(mark1 or mark2)
            labels = pd.Series(forward)
            for name in ic:
                ic[name].append(float(rows[name].corr(labels, method="spearman")))
        result = {"status": "SIX_MATCHED_ANNUAL_SOURCE_COST_PATHS_COMPLETE", "protocol_sha256": args.protocol_sha,
            "results": runs, "RankIC": {k: float(np.mean(v)) for k, v in ic.items()},
            "RankIC_periods": len(p["anchors"]), "known_suspension_mark_labels": label_marks,
            "coverage": coverage, "holding_days": len(days), "holding_start": days[0], "holding_end": days[-1],
            "new_contexts": len(keys), "registry_total": reg.total, "official_calls": 0,
            "source_limited_preliminary": True, "full_PIT_or_native_input_equivalence": False,
            "formal_admission": False, "independent_alpha_confirmed": False, "no_partial_outcome_tuning": True,
            "source_vintage": "FY25 captured annual cash-flow input held fixed after its stored availability; H1 updates/version chain unqualified",
            "limits": p["limits"], "completed_at_utc": datetime.now(timezone.utc).isoformat()}
        write_new(p["daily_ledger"], ledger)
        result["daily_ledger"] = {"path": p["daily_ledger"], "sha256": sha(p["daily_ledger"])}
        write_new(out, result)
        checkpoint.write_text(json.dumps({"protocol_sha256": args.protocol_sha, "completed": completed,
            "status": "COMPLETED", "summary_sha256": sha(out)}, separators=(",", ":")), encoding="utf-8")
        for key in keys:
            reg.append_event("evaluation", key, {"receipt": {"path": str(out), "sha256": sha(out)},
                "source_limited_preliminary": True, "formal_admission": False, "new_contexts": 0},
                event_id="cash-annual-pilot-result:"+key+":"+sha(out))
        print(json.dumps({"status": result["status"], "RankIC": result["RankIC"],
            "results": [{k: r[k] for k in ["name", "cost", "metrics", "terminal_residual_fraction"]} for r in runs]}, ensure_ascii=False))
    except Exception as error:
        checkpoint.write_text(json.dumps(study._clean({"protocol_sha256": args.protocol_sha, "completed": completed,
            "completed_results": runs, "completed_daily_ledger": ledger, "status": "EXECUTION_PENDING",
            "error_type": type(error).__name__, "error": str(error)}), ensure_ascii=False, allow_nan=False), encoding="utf-8")
        raise
    finally:
        reg.db.close()


if __name__ == "__main__":
    main()
