"""One explicit source profile for a research window; acquisition is separate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def resolve_source(data, source=None):
    profiles = data.get("source_profiles", {})
    selected = source or data.get("active_market_source")
    if not selected:
        return {"name": "legacy_config", "database": data["database"],
                "mongo_uri": data["mongo_uri"], "reference_database": data["database"]}
    if selected not in profiles:
        raise ValueError(f"Unknown configured data source: {selected}; no automatic fallback")
    profile = dict(profiles[selected])
    required = {"database", "price_source", "reference_database", "calendar_source"}
    if not required <= profile.keys():
        raise ValueError(f"Incomplete source profile: {selected}")
    profile.update(name=selected, mongo_uri=data["mongo_uri"])
    return profile


def resolve_sync_plan(cfg, source, overrides):
    """A declared acquisition plan replaces repeated per-channel CLI arguments."""
    profile = resolve_source(cfg["data"], source)
    plan = dict(profile.get("sync_plan", {}))
    plan.update({key: value for key, value in overrides.items() if value is not None})
    required = {"codes_file", "start", "end", "sdk_dir", "acceptance", "output"}
    missing = sorted(required - plan.keys())
    if missing:
        raise ValueError("Source acquisition plan needs: " + ", ".join(missing))
    return plan


def factor_records_sha256(records):
    from .data import _date
    payload = sorted([{"code": str(r["code"]), "date": _date(r["date"]),
                       "adj": float(r["adj"]), "source": str(r["source"])} for r in records],
                     key=lambda r: (r["code"], r["date"]))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def provider_from_config(cfg, source=None):
    from .data import AxisProvider
    profile = resolve_source(cfg["data"], source)
    if profile["name"] == "legacy_config":
        return AxisProvider(profile["mongo_uri"], profile["database"])
    provider = AxisProvider(profile["mongo_uri"], profile["database"],
                            price_source=profile["price_source"],
                            reference_database=profile["reference_database"],
                            calendar_source=profile["calendar_source"])
    provider.source_selection = {"profile": profile,
        "sha256": hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=True).encode()).hexdigest(),
        "automatic_fallback": False}
    return provider


def source_status(cfg):
    """Report actual local inventories; paused collectors are never resumed here."""
    from pymongo import MongoClient
    data = cfg["data"]
    profiles = data.get("source_profiles", {})
    with MongoClient(data["mongo_uri"], serverSelectionTimeoutMS=5000) as client:
        result = []
        for name in profiles:
            profile = resolve_source(data, name)
            db = client[profile["database"]]
            result.append({"name": name, "active": name == data.get("active_market_source"),
                "role": profile.get("role"), "database": profile["database"],
                "price_source": profile["price_source"], "reference_database": profile["reference_database"],
                "collector_state": profile.get("collector_state", "manual"),
                "stored_daily_rows": db.stock_day.count_documents({"source": profile["price_source"]}),
                "adjustment_rows": db.stock_adj.count_documents({"source": profile["price_source"]}),
                "sync_receipts": db.panda_axis_sync.count_documents({"source": profile["price_source"]}),
                "resume_command": profile.get("resume_command"),
                "automatic_fallback": False})
    return {"active_market_source": data.get("active_market_source", "legacy_config"),
            "sources": result, "formal_full_market_certified": False,
            "note": "Inventory is not financial PIT, universe completeness or source retirement proof"}


def finalize_stockdb(cfg, output, *, additional_epochs=()):
    """Reconcile named source scope with independent calendar/lifecycle evidence."""
    from pymongo import MongoClient, UpdateOne
    from .stockdb_sync import file_sha
    output = Path(output)
    state = json.loads((output / "progress.json").read_text(encoding="utf-8"))
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    if state["status"] != "complete":
        raise ValueError("Complete source collection required before finalization")
    epochs = [Path(p) for p in additional_epochs] + [output]
    bindings, windows = [], []
    start, end = protocol["start"], protocol["end"]
    for root in epochs:
        epoch_protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
        epoch_state = json.loads((root / "progress.json").read_text(encoding="utf-8"))
        if epoch_protocol["codes"] != protocol["codes"] or epoch_protocol["source"] != "stockdb":
            raise ValueError("Epochs must preserve the same named StockDB scope")
        for row in epoch_state["windows"]:
            path = root / f"snapshots/{row['start']}_{row['end']}.parquet"
            if file_sha(path) != row["snapshot_sha256"]:
                raise ValueError("Source snapshot binding changed")
            windows.append(row)
            bindings.append({"path": str(path.resolve()), "sha256": row["snapshot_sha256"],
                             "protocol_sha256": file_sha(root / "protocol.json")})
    windows.sort(key=lambda row: row["start"])
    from datetime import date, timedelta
    if not windows or any(date.fromisoformat(b["start"]) != date.fromisoformat(a["end"]) + timedelta(days=1)
                          for a, b in zip(windows, windows[1:])):
        raise ValueError("Collected source epochs must have contiguous nonoverlapping windows")
    start, end = windows[0]["start"], windows[-1]["end"]
    from .evaluation import check_window
    check_window(start, end, cfg["research"]["sealed_windows"])
    profile = resolve_source(cfg["data"], "stockdb")
    with MongoClient(profile["mongo_uri"], serverSelectionTimeoutMS=5000) as client:
        db, reference = client[profile["database"]], client[profile["reference_database"]]
        calendar = sorted({r["date"] for r in reference.trade_calendar.find(
            {"source": profile["calendar_source"], "date": {"$gte": start, "$lte": end}}, {"date": 1})})
        receipt_source = {"baostock_trade_dates": "baostock", "tdx_index_calendar": "tdx"}.get(profile["calendar_source"], profile["calendar_source"])
        calendar_receipts = list(reference.panda_axis_sync.find({"dataset": "trade_calendar", "code": "SSE", "source": receipt_source, "status": "complete",
            "start": {"$lte": start}, "through": {"$gte": end}}))
        if not calendar or not calendar_receipts:
            raise ValueError("Independent complete calendar required to finalize price scope")
        lives = {}
        for r in reference.stock_lifecycle.find({}, {"_id": 0}):
            if r.get("ipo_date") and not r.get("lifecycle_issues"):
                lives.setdefault(r["code"], r)
        known = {(r["code"], r["date"]) for r in reference.stock_day.find(
            {"trade_status": "0", "date": {"$gte": start, "$lte": end}}, {"code": 1, "date": 1})}
        observed = {r["_id"]: set(r["dates"]) for r in db.stock_day.aggregate([
            {"$match": {"source": "stockdb", "date": {"$gte": start, "$lte": end}}},
            {"$group": {"_id": "$code", "dates": {"$addToSet": "$date"}}}], allowDiskUse=True)}
        results, ops = [], []
        for code in protocol["codes"]:
            life = lives.get(code)
            expected = [d for d in calendar if life and life["ipo_date"] <= d and
                        (not life.get("delisted_date") or d < life["delisted_date"])]
            present = observed.get(code, set())
            absent = sorted(set(expected) - present)
            suspended = [d for d in absent if (code, d) in known]
            unknown = sorted(set(absent) - set(suspended))
            off_lifecycle = sorted(present - set(expected)) if life else []
            status = "complete" if life and expected and not absent and not off_lifecycle else "partial"
            receipt = {"source": "stockdb", "dataset": "stock_day", "code": code,
                "start": start, "through": end, "status": status, "rows": len(present),
                "source_scope_collected": True, "source_missing_dates": unknown, "unknown_missing_dates": unknown,
                "suspended_dates": suspended, "off_lifecycle_dates": off_lifecycle,
                "duplicate_dates": 0, "historical_membership_complete": False,
                "metadata_source": life.get("source") if life else None,
                "protocol_sha256": hashlib.sha256(json.dumps(bindings, sort_keys=True).encode()).hexdigest(),
                "source_contract_sha256": protocol["source_contract_sha256"]}
            ops.append(UpdateOne({"_id": "stockdb:scope:" + code + ":" + receipt["protocol_sha256"]},
                                 {"$setOnInsert": receipt}, upsert=True))
            results.append({"code": code, "rows": len(present), "expected_dates": len(expected),
                            "status": status, "known_suspensions": len(suspended), "unknown_missing_dates": len(unknown),
                            "metadata_available": life is not None})
        db.panda_axis_sync.bulk_write(ops, ordered=True)
        factor_ops, snapshot_ops, expected_factors = [], [], {}
        for binding in bindings:
            snapshot_ops.append(UpdateOne({"_id": binding["sha256"]}, {"$setOnInsert": {
                **binding, "source": "stockdb", "kind": "raw_daily"}}, upsert=True))
        for root in epochs:
            path = root / "snapshots/factors.json"
            native = json.loads(path.read_text(encoding="utf-8"))
            if native.get("native_keys_match") is not True:
                raise ValueError("Native factor key/value census proof required")
            for row in native["factors"]:
                if row["code"] in protocol["codes"] and row["date"] <= end:
                    key = (row["code"], row["date"])
                    if key in expected_factors and expected_factors[key] != row:
                        raise ValueError("Source factor epochs disagree; do not merge vintages")
                    expected_factors[key] = row
            snapshot_ops.append(UpdateOne({"_id": file_sha(path)}, {"$setOnInsert": {
                "source": "stockdb", "kind": "cumulative_factors", "path": str(path.resolve()),
                "sha256": file_sha(path)}}, upsert=True))
        db.data_source_snapshots.bulk_write(snapshot_ops, ordered=True)
        factor_groups = {}
        for row in db.stock_adj.find({"source": "stockdb", "date": {"$lte": end}}, {"_id": 0}):
            factor_groups.setdefault(row["code"], []).append(row)
        actual = [r for code, records in factor_groups.items() if code in protocol["codes"] for r in records]
        if factor_records_sha256(actual) != factor_records_sha256(expected_factors.values()):
            raise ValueError("Stored factors do not match complete native source snapshots")
        for code in protocol["codes"]:
            records = factor_groups.get(code, [])
            payload_sha = factor_records_sha256(records)
            factor_ops.append(UpdateOne({"_id": "stockdb:verified-factor-payload:" + code + ":" + payload_sha},
                {"$setOnInsert": {"source": "stockdb", "dataset": "stock_adj", "code": code,
                 "start": "1990-01-01", "through": end, "rows": len(records), "status": "complete",
                 "history_complete": True, "factor_records_sha256": payload_sha,
                 "source_snapshot_sha256": sorted({r["source_snapshot_sha256"] for r in records})}}, upsert=True))
        db.panda_axis_sync.bulk_write(factor_ops, ordered=True)
    result = {"status": "SCOPED_RESEARCH_SOURCE_READY_FULL_MARKET_ACCEPTANCE_PENDING", "source": "stockdb",
        "start": start, "end": end, "source_rows": sum(r["rows"] for r in results), "factor_rows": state["factor_rows"],
        "quarantined_rows": state["quarantined_rows"], "codes": results,
        "calendar_sessions": len(calendar), "full_financial_PIT": False, "full_tradable_universe": False,
        "official_compute_spent": 0, "protocol_sha256": file_sha(output / "protocol.json"),
        "snapshot_bindings": bindings, "windows": len(windows)}
    (output / "source_ready.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def export_research_input(cfg, codes, start, end, output, source=None):
    """Prepare a reproducible partitioned input without evaluating a factor."""
    import pandas as pd
    from .evaluation import check_window
    check_window(start, end, cfg["research"]["sealed_windows"])
    provider = provider_from_config(cfg, source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    codes = sorted(set(codes))
    if not codes:
        raise ValueError("Explicit research code scope required")
    parts = []
    for offset in range(0, len(codes), 100):
        batch = codes[offset:offset+100]
        path = output / f"daily_{offset // 100:04d}.parquet"
        if path.exists():
            raise ValueError("Use a fresh output directory; research inputs are immutable")
        data = provider.daily(batch, start, end, cfg["data"]["adjustment"])
        data.frame.to_parquet(path, index=False, compression="zstd")
        parts.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "rows": len(data.frame), "codes": batch, "coverage": data.coverage})
    result = {"status": "SOURCE_INPUT_PREPARED_NOT_FACTOR_EVALUATED", "start": start, "end": end,
              "codes": codes, "rows": sum(p["rows"] for p in parts), "parts": parts,
              "source_selection": getattr(provider, "source_selection", None),
              "new_economic_trials": 0, "official_compute_spent": 0,
              "full_financial_PIT": False, "full_tradable_universe": False}
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
