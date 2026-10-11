"""Fixed-kernel input redundancy on unused companies, without return labels."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture
from panda_alpha.registry import ResearchRegistry


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, required=True)
    cli.add_argument("--protocol-sha", required=True)
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print("DEFERRED_NO_SIGNAL_INPUT_CONSUMPTION")
        return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human deadline reached")
    out = Path(p["output"])
    if out.exists():
        raise ValueError("receipt exists; no automatic replay")
    for binding in p["bindings"].values():
        if sha(binding["path"]) != binding["sha256"]:
            raise ValueError("source/runtime binding changed")
    reg = ResearchRegistry(p["registry"])
    event = reg.db.execute("SELECT payload FROM research_events WHERE event_id=? AND subject=?",
        ("cash-input-diagnostic-freeze:"+args.protocol_sha, p["id"])).fetchone()
    if not event or json.loads(event[0]).get("new_contexts") != 0:
        raise ValueError("source diagnostic registration required")
    source = json.loads(Path(p["bindings"]["source_receipt"]["path"]).read_text(encoding="utf-8"))
    records = source["records"]
    codes = [r["symbol"] for r in records]
    if len(codes) != p["expected_companies"] or len(set(codes)) != len(codes):
        raise ValueError("source denominator changed")
    day = pd.Timestamp(p["reference_input_date"])
    idx = pd.MultiIndex.from_product([[day], codes], names=["date", "symbol"])
    values = {k: pd.Series(np.nan, index=idx, dtype=float) for k in p["field_mapping"]}
    availability_pending = []
    for r in records:
        available = r.get("stored_available_date")
        if not available or pd.Timestamp(available) > day:
            availability_pending.append(r["symbol"])
            continue
        if r["status"] != "three_current_annual_cash_amounts_bound":
            continue
        for name, key in p["field_mapping"].items():
            field = r["fields"][key]
            if field["current_start"] != "2025-01-01" or field["current_end"] != "2025-12-31":
                raise ValueError("cashflow fiscal period mismatch")
            values[name].loc[(day, r["symbol"])] = float(field["current_amount_yuan"])
    quotes = pd.read_parquet(p["bindings"]["existing_quotes"]["path"], columns=["date", "symbol", "close"])
    quotes["date"] = pd.to_datetime(quotes.date)
    quotes = quotes[(quotes.date == day) & quotes.symbol.isin(codes)]
    if quotes.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate same-source close key")
    values["close"] = quotes.set_index(["date", "symbol"]).close.reindex(idx)
    parent = json.loads(Path(p["bindings"]["fixed_kernels"]["path"]).read_text(encoding="utf-8"))
    outputs = []
    for c in parent["candidates"]:
        if c["direction"] != 1:
            raise ValueError("fixed economic direction changed")
        ns = {"Factor": _FactorFixture, "__name__": "cash_unused_input_diagnostic"}
        exec(compile(c["code"], "<frozen-cash-kernel>", "exec"), ns)
        classes = [v for v in ns.values() if isinstance(v, type) and v is not _FactorFixture and issubclass(v, _FactorFixture)]
        if len(classes) != 1:
            raise ValueError("one frozen Factor subclass required")
        value = classes[0]().calculate({k: FactorSeriesFixture(v) for k, v in values.items()})
        if not isinstance(value, pd.Series) or value.name != "value" or not value.index.equals(idx):
            raise ValueError("known source interface index mismatch")
        if np.isinf(value.to_numpy(dtype=float)).any():
            raise ValueError("infinite signal")
        outputs.append(value.droplevel("date"))
    if len(outputs) != 2:
        raise ValueError("two unchanged kernels required")
    pair = pd.concat([outputs[0].rename("main"), outputs[1].rename("control")], axis=1).dropna()
    n = int(np.ceil(len(pair)/10)) if len(pair) else 0
    top_main = set(pair.main.nlargest(n).index)
    top_control = set(pair.control.nlargest(n).index)
    cash, operating, capex = [values[k].droplevel("date") for k in p["field_mapping"]]
    states = Counter()
    for code in pair.index:
        if cash[code] < 0:
            states["negative_operating_net_cash"] += 1
        elif cash[code] < capex[code]:
            states["nonnegative_operating_cash_below_capex"] += 1
        else:
            states["operating_cash_covers_capex"] += 1
    result = {
        "status": "UNUSED_SOURCE_INPUT_DIAGNOSTIC_COMPLETE", "protocol_sha256": args.protocol_sha,
        "expected_companies": len(codes), "source_triplets": source["counts"].get("three_current_annual_cash_amounts_bound", 0),
        "common_finite_signals": len(pair), "availability_pending": availability_pending,
        "missing_close_keys": int(values["close"].isna().sum()),
        "main_control_Spearman": float(pair.main.corr(pair.control, method="spearman")) if len(pair) > 1 else None,
        "top_decile_count": n, "top_decile_overlap": len(top_main & top_control)/n if n else None,
        "top_decile_main_only": sorted(top_main-top_control), "top_decile_control_only": sorted(top_control-top_main),
        "funding_states": dict(states), "explicit_zero_capex": int((capex.loc[pair.index] == 0).sum()),
        "implied_negative_operating_inflow": int(((cash+operating).loc[pair.index] < 0).sum()),
        "source_pending": [{"symbol": r["symbol"], "reason": r.get("reason"),
             "field_reasons": {k: f.get("reason") for k, f in r.get("fields", {}).items() if f["status"] != "current_annual_amount_bound"}}
             for r in records if r["status"] != "three_current_annual_cash_amounts_bound"],
        "new_contexts": 0, "registry_total": reg.total, "return_labels_read": False, "official_calls": 0,
        "native_dispatch_preflight_passed": False, "production_or_PIT_qualified": False,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "limits": "One FY25 source cross-section on a bound close-index date; metadata availability, policy/scope/version and live native input parity unqualified; code order cohort is not market representative or independent alpha evidence",
    }
    with out.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    reg.append_event("source_repair", p["id"], {"status": result["status"], "result": {"path": str(out), "sha256": sha(out)},
        "new_contexts": 0, "labels_read": False}, event_id="cash-input-diagnostic-result:"+args.protocol_sha)
    reg.db.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
