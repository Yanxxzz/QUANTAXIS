"""Materialize bounded StockDB source snapshots for explicit local research.

The SDK is loaded only inside a disposable worker. Every query uses a fresh
connection and an independent identity projection. Source files are immutable;
failed transport is paused, never retried by the collector.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pandas as pd

FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount",
          "pre_close", "turnover", "pct_chg", "is_st", "total_mv", "pb", "pe_ttm")
SOURCE = "stockdb"
DATABASE = "quantaxis_stockdb_research"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise ValueError(f"Source artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def portable(value):
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def validate_identities(frame, start, end):
    if not frame.code.astype(str).str.fullmatch(r"\d{6}").all():
        raise ValueError("StockDB identity projection is invalid")
    if not pd.to_numeric(frame.date, errors="coerce").between(int(start), int(end)).all():
        raise ValueError("StockDB date projection is stale or outside the query")
    pd.to_datetime(frame.date.astype(str), format="%Y%m%d", errors="raise")
    if frame.duplicated(["code", "date"]).any():
        raise ValueError("StockDB returned duplicate identities")


def validate_factors(rows, keys):
    if not rows or set(keys) != {r[0] for r in rows} or len(rows) != len(set(keys)):
        raise ValueError("Complete native factor keys and values disagree")
    result = []
    for key, cum in rows:
        match = re.fullmatch(r"复权:(\d{6}):(\d{8})", key)
        if not match or not np.isfinite(float(cum)) or float(cum) <= 0:
            raise ValueError("Invalid same-source cumulative factor")
        day = datetime.strptime(match[2], "%Y%m%d").date().isoformat()
        result.append({"code": match[1], "date": day, "adj": float(cum), "source": SOURCE})
    return result


def normalize_frame(frame, allowed_codes, snapshot_sha):
    """Accept documented source OHLC/CNY/share rows; quarantine real violations."""
    accepted, rejected = [], []
    for values in frame.itertuples(index=False, name=None):
        raw = {k: portable(v) for k, v in zip(FIELDS, values)}
        code = str(raw["code"])
        if code not in allowed_codes:
            continue
        day = datetime.strptime(str(raw["date"]), "%Y%m%d").date().isoformat()
        required = [raw[k] for k in ("open", "high", "low", "close", "volume", "amount")]
        reason = None
        if any(v is None or not isinstance(v, (int, float)) or not np.isfinite(v) for v in required):
            reason = "missing_or_nonfinite_numeric"
        else:
            o, h, l, c, v, a = required
            if min(o, h, l, c) <= 0 or h < max(o, l, c) or l > min(o, h, c):
                reason = "invalid_ohlc"
            elif v <= 0 or a <= 0:
                reason = "activity_absent_or_invalid_not_inferred_suspension"
            elif not float(v).is_integer() or not l * .95 - .02 <= a / v <= h * 1.05 + .02:
                reason = "share_CNY_or_price_basis_pending"
        provenance = {"source_snapshot_sha256": snapshot_sha, "source_row_sha256": digest(raw)}
        if reason:
            rejected.append({"code": code, "date": day, "reason": reason, **provenance,
                             "source_record": raw, "research_eligibility": "candidate_only"})
            continue
        accepted.append({"code": code, "date": day,
            "date_stamp": datetime.strptime(day, "%Y-%m-%d").timestamp(),
            **{k: raw[k] for k in ("open", "high", "low", "close", "amount")},
            "vol": raw["volume"] / 100, "volume_shares": raw["volume"],
            "preclose": raw["pre_close"], "is_st": raw["is_st"], "trade_status": "1",
            "source": SOURCE, "adjustment": "none", "research_eligibility": "source_contract_scoped",
            "volume_unit": "shares; QA vol in 100-share lots", "amount_unit": "CNY",
            "price_basis_acceptance": "documented_untransformed_table_and_scoped_independent_control",
            **provenance})
    return accepted, rejected


def worker(args):
    sys.path.insert(0, str(args.sdk_dir))
    import stockdb
    def fresh():
        return stockdb.init(host="127.0.0.1", port=7899, socket_timeout=45)
    path = Path(args.snapshot)
    if args.kind == "factors":
        rows = fresh().get("复权*").get("cum").do()
        keys = fresh().keys("复权*").do()
        factors = validate_factors(rows, keys)
        save(path, {"raw_key_cum_pairs": rows, "factors": factors,
                    "native_keys_match": True, "source_rows": len(rows)})
    else:
        selector = args.start.replace("-", "") + "<" + args.end.replace("-", "")
        rows = fresh().vals("日k", "*", selector).get(",".join(FIELDS)).do()
        verify = fresh().vals("日k", "*", selector).get("date,code").do()
        frame = pd.DataFrame(rows, columns=FIELDS)
        check = pd.DataFrame(verify, columns=["date", "code"])
        if frame.empty:
            raise ValueError("Empty whole-market response requires source investigation")
        validate_identities(frame, args.start.replace("-", ""), args.end.replace("-", ""))
        validate_identities(check, args.start.replace("-", ""), args.end.replace("-", ""))
        left = frame[["date", "code"]].sort_values(["code", "date"]).reset_index(drop=True)
        right = check[["date", "code"]].sort_values(["code", "date"]).reset_index(drop=True)
        if not left.equals(right):
            raise ValueError("Independent complete daily identity projection disagrees")
        for day in (frame.date.min(), frame.date.max()):
            exact = pd.DataFrame(fresh().vals("日k", "*", str(day)).get("date,code").do(),
                                 columns=["date", "code"])
            expected = left[left.date == day].reset_index(drop=True)
            if not exact.sort_values(["code", "date"]).reset_index(drop=True).equals(expected):
                raise ValueError("Independent exact-date control disagrees")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("Worker cannot replace an existing source snapshot")
        frame.to_parquet(path, index=False, compression="zstd")
    print(json.dumps({"snapshot": str(path), "sha256": file_sha(path)}), flush=True)


def month_windows(start, end):
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    while first <= last:
        stop = min(first + pd.offsets.MonthEnd(0), last)
        yield first.date().isoformat(), stop.date().isoformat()
        first = stop + pd.Timedelta(days=1)


def run_sync(cfg, codes, start, end, output, sdk_dir, acceptance, *, resume=False):
    from .evaluation import check_window
    from .sources import resolve_source
    from pymongo import MongoClient, UpdateOne
    check_window(start, end, cfg["research"]["sealed_windows"])
    profile = resolve_source(cfg["data"], "stockdb")
    if profile["database"] != DATABASE or profile["price_source"] != SOURCE:
        raise ValueError("StockDB materialization requires its configured isolated research DB")
    codes = sorted(set(codes))
    if not codes or any(not re.fullmatch(r"\d{6}", code) for code in codes):
        raise ValueError("An explicit six-digit source scope is required")
    output = Path(output)
    sdk_dir = Path(sdk_dir)
    contract = json.loads(Path(acceptance).read_text(encoding="utf-8"))
    if (contract.get("status") != "VERIFIED_SCOPED_SOURCE_CONTRACT" or
            contract.get("source") != SOURCE or not contract.get("evidence") or
            contract.get("sdk_sha256") != file_sha(sdk_dir / "stockdb.pyd")):
        raise ValueError("Reviewed source contract and matching SDK required")
    for entry in contract["evidence"]:
        if file_sha(entry["path"]) != entry["sha256"]:
            raise ValueError("Source acceptance evidence changed")
    installation = sdk_dir.parent
    manifests = {str(installation / n / ".sync_manifest.json"): file_sha(installation / n / ".sync_manifest.json")
                 for n in ("data", "data1")}
    protocol = {"source": SOURCE, "database": DATABASE, "codes": codes, "start": start, "end": end,
                "source_manifests": manifests, "sdk_sha256": file_sha(sdk_dir / "stockdb.pyd"),
                "source_contract_sha256": file_sha(acceptance), "fields": list(FIELDS),
                "reader_sha256": file_sha(__file__), "calendar_source": profile["calendar_source"],
                "scope": "scoped_source_research_not_full_PIT_or_tradable_universe"}
    state_path = output / "progress.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"status": "running", "windows": []}
    if state["status"] == "complete":
        frozen = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
        if any(frozen[key] != protocol[key] for key in ("codes", "start", "end", "source", "database", "fields", "source_contract_sha256", "sdk_sha256")):
            raise ValueError("Completed source scope differs; use a new batch")
        for row in state["windows"]:
            path = output / f"snapshots/{row['start']}_{row['end']}.parquet"
            if file_sha(path) != row["snapshot_sha256"]:
                raise ValueError("Completed source snapshot changed")
        return state
    save(output / "protocol.json", protocol)
    if state["status"] == "source_paused" and not resume:
        raise ValueError("Source is paused; inspect evidence and explicitly resume")

    def checkpoint():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def collect(kind, first, last, path):
        if path.exists():
            return
        cmd = [sys.executable, "-X", "utf8", "-B", "-m", "panda_alpha.stockdb_sync",
               "--worker", "--kind", kind, "--start", first, "--end", last,
               "--sdk-dir", str(sdk_dir), "--snapshot", str(path)]
        result = subprocess.run(cmd, capture_output=True, timeout=90)
        if result.returncode != 0:
            # Avoid exposing native SDK configuration in a source failure log.
            raise RuntimeError("Native StockDB worker failed; no automatic retry")

    try:
        factor_path = output / "snapshots/factors.json"
        collect("factors", start, end, factor_path)
        factor_snapshot = json.loads(factor_path.read_text(encoding="utf-8"))
        factors = [r for r in factor_snapshot["factors"] if r["code"] in codes and r["date"] <= end]
        factor_sha = file_sha(factor_path)
        with MongoClient(profile["mongo_uri"], serverSelectionTimeoutMS=5000) as client:
            db = client[DATABASE]
            db.stock_day.create_index([("code", 1), ("date", 1)], unique=True)
            db.stock_adj.create_index([("code", 1), ("date", 1)], unique=True)
            db.stockdb_quarantine.create_index([("code", 1), ("date", 1)], unique=True)
            prior_factors = {(r["code"], r["date"]): r["adj"] for r in db.stock_adj.find({}, {"code": 1, "date": 1, "adj": 1})}
            if any((r["code"], r["date"]) in prior_factors and
                   prior_factors[(r["code"], r["date"])] != r["adj"] for r in factors):
                raise ValueError("Existing source factor vintage conflicts; do not overwrite")
            db.data_source_contracts.update_one({"_id": protocol["source_contract_sha256"]},
                {"$setOnInsert": {**contract, "source_contract_sha256": protocol["source_contract_sha256"],
                 "research_use": "local_proxy_source_contract", "formal_full_market_certified": False}}, upsert=True)
            if factors:
                db.stock_adj.bulk_write([UpdateOne({"code": r["code"], "date": r["date"]},
                    {"$setOnInsert": {**r, "source_snapshot_sha256": factor_sha}}, upsert=True) for r in factors], ordered=True)
            factor_counts = Counter(r["code"] for r in factors)
            db.panda_axis_sync.bulk_write([UpdateOne({"_id": SOURCE + ":factors:" + code + ":" + factor_sha},
                {"$setOnInsert": {"source": SOURCE, "dataset": "stock_adj", "code": code,
                 "start": "1990-01-01", "through": end, "rows": factor_counts[code], "status": "complete",
                 "history_complete": True, "factor_origin": "source IPO baseline one; native complete factor key census",
                 "evidence_sha256": factor_sha}}, upsert=True) for code in codes], ordered=True)
            completed = {(r["start"], r["end"]): r for r in state["windows"]}
            for first, last in month_windows(start, end):
                path = output / f"snapshots/{first}_{last}.parquet"
                if (first, last) in completed:
                    if file_sha(path) != completed[(first, last)]["snapshot_sha256"]:
                        raise ValueError("Previously completed source snapshot changed")
                    continue
                collect("daily", first, last, path)
                snapshot_sha = file_sha(path)
                raw = pd.read_parquet(path, columns=list(FIELDS))
                validate_identities(raw, first.replace("-", ""), last.replace("-", ""))
                accepted, rejected = normalize_frame(raw, set(codes), snapshot_sha)
                # Preflight collisions before a resumed batch can insert anything.
                existing = { (r["code"], r["date"]): r for r in db.stock_day.find(
                    {"date": {"$gte": first, "$lte": last}}, {"code": 1, "date": 1, "source_row_sha256": 1}) }
                if any((r["code"], r["date"]) in existing and
                       existing[(r["code"], r["date"])]["source_row_sha256"] != r["source_row_sha256"] for r in accepted):
                    raise ValueError("Existing StockDB research rows conflict; preserve both source vintages")
                if accepted:
                    for offset in range(0, len(accepted), 10000):
                        db.stock_day.bulk_write([UpdateOne({"code": r["code"], "date": r["date"]},
                            {"$setOnInsert": r}, upsert=True) for r in accepted[offset:offset+10000]], ordered=True)
                if rejected:
                    db.stockdb_quarantine.bulk_write([UpdateOne({"code": r["code"], "date": r["date"]},
                        {"$setOnInsert": r}, upsert=True) for r in rejected], ordered=True)
                receipt = {"source": SOURCE, "dataset": "stock_day", "start": first, "through": last,
                           "source_snapshot_sha256": snapshot_sha, "rows": len(accepted), "quarantined": len(rejected),
                           "status": "complete", "independent_identity_projection": True,
                           "source_contract_sha256": protocol["source_contract_sha256"]}
                db.panda_axis_sync.update_one({"_id": SOURCE + ":daily:" + snapshot_sha}, {"$setOnInsert": receipt}, upsert=True)
                summary = {"start": first, "end": last, "snapshot_sha256": snapshot_sha,
                           "source_rows": len(raw), "research_rows": len(accepted), "quarantined_rows": len(rejected)}
                if any(file_sha(path) != h for path, h in manifests.items()):
                    raise ValueError("StockDB provider package changed during acquisition; month not committed")
                state["windows"].append(summary)
                state.update(status="running", last_completed=last)
                checkpoint()
                print(json.dumps(summary), flush=True)
            state.update(status="complete", research_rows=db.stock_day.count_documents({"source": SOURCE}),
                         factor_rows=db.stock_adj.count_documents({"source": SOURCE}),
                         quarantined_rows=db.stockdb_quarantine.count_documents({}),
                         formal_full_market_certified=False, financial_PIT_certified=False,
                         official_compute_spent=0)
        checkpoint()
        return state
    except Exception as exc:
        state.update(status="source_paused", error_type=type(exc).__name__, reason=str(exc))
        checkpoint()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--kind", choices=["daily", "factors"])
    parser.add_argument("--sdk-dir", type=Path, required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    if not args.worker:
        parser.error("Use the unified data-sync command")
    worker(args)


if __name__ == "__main__":
    main()
