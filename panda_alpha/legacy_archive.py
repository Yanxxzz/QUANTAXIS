"""Content-verified offline bars preserved outside every research provider.

Archiving is a custody operation. It does not certify raw/adjusted prices,
point-in-time features, exchange completeness or new research eligibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

ARCHIVE_COLLECTION = "stock_day_legacy_archive"
RECEIPT_COLLECTION = "panda_axis_legacy_archive_sync"
LOGICAL_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume",
                   "amount", "pct_chg", "is_st", "total_mv", "pb", "pe_ttm")
REQUIRED_COLUMNS = {"date", "code", "open", "high", "low", "close", "volume", "amount"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def normalize_codes(values):
    # Exact producer normalization in stockdb_factor_eval/engine_v2/execution.py.
    text = values.astype("string").str.strip()
    numeric = text.str.fullmatch(r"\d+(?:\.0+)?", na=False)
    text.loc[numeric] = text.loc[numeric].str.replace(r"\.0+$", "", regex=True).str.zfill(6)
    return text.astype(str)


def audit_price_file(path: Path, *, batch_size=32768, requested_start=20190920,
                     requested_end=20260918, sealed_through=20210919):
    """Recompute both producer hashes and coverage with bounded memory.

    Source files are sorted by (date,code). Refuse unsorted input rather than
    calculate a different logical hash by sorting unrelated local batches.
    """
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    path = path.resolve()
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported legacy manifest schema")
    digest = file_sha256(path)
    if digest != manifest.get("file_sha256"):
        raise ValueError("Legacy file SHA256 mismatch; archive has not been modified")
    parquet = pq.ParquetFile(path)
    columns = parquet.schema_arrow.names
    if not REQUIRED_COLUMNS <= set(columns) or columns != manifest.get("columns"):
        raise ValueError("Legacy price schema does not match manifest")
    selected = [c for c in LOGICAL_COLUMNS if c in columns]
    logical = hashlib.sha256("|".join(selected).encode("utf-8"))
    rows = duplicates = sealed_rows = outside_rows = 0
    codes, dates, by_code = set(), set(), {}
    nulls = {c: 0 for c in columns}
    nonfinite = {c: 0 for c in columns}
    previous = None
    for batch in parquet.iter_batches(batch_size=batch_size):
        frame = batch.to_pandas()
        work = frame.loc[:, selected].copy()
        work["code"] = normalize_codes(work["code"])
        work["date"] = pd.to_numeric(work["date"], errors="raise").astype(np.int64)
        if not work["code"].str.fullmatch(r"\d{6}").all():
            raise ValueError("Unknown source security identifiers")
        keys = list(zip(work["date"].tolist(), work["code"].tolist()))
        if any(a > b for a, b in zip(keys, keys[1:])) or (previous and keys and previous > keys[0]):
            raise ValueError("Legacy rows are not sorted by date/code")
        duplicates += sum(a == b for a, b in zip(keys, keys[1:])) + bool(previous and keys and previous == keys[0])
        if keys:
            previous = keys[-1]
        logical.update(pd.util.hash_pandas_object(work, index=False).to_numpy(dtype=np.uint64).tobytes())
        rows += len(frame)
        sealed_rows += int((work.date <= sealed_through).sum())
        outside_rows += int(((work.date < requested_start) | (work.date > requested_end)).sum())
        codes.update(work.code)
        dates.update(int(d) for d in work.date.unique())
        aggregate = work.groupby("code", sort=False).date.agg(["count", "min", "max"])
        for code, count, first, last in aggregate.itertuples(name=None):
            item = by_code.setdefault(code, {"rows": 0, "start": int(first), "end": int(last)})
            item["rows"] += int(count)
            item["start"] = min(item["start"], int(first))
            item["end"] = max(item["end"], int(last))
        for name in columns:
            nulls[name] += int(batch.column(columns.index(name)).null_count)
            if pd.api.types.is_numeric_dtype(frame[name]):
                nonfinite[name] += int((~np.isfinite(frame[name].to_numpy(dtype=float)) & frame[name].notna()).sum())
    observed = {"rows": rows, "codes": len(codes), "start": min(dates) if dates else None,
                "end": max(dates) if dates else None, "logical_hash": logical.hexdigest()}
    for key in ["rows", "codes", "start", "end", "logical_hash"]:
        if observed[key] != manifest.get(key):
            raise ValueError(f"Legacy {key} differs from manifest")
    if rows != parquet.metadata.num_rows or duplicates:
        raise ValueError("Legacy row count or duplicate identifying rows invalid")
    return {"path": str(path), "manifest_path": str(manifest_path), "source_id": digest,
            "file_sha256": digest, "file_bytes": path.stat().st_size,
            "manifest_sha256": file_sha256(manifest_path), "manifest": manifest, **observed,
            "schema": [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in parquet.schema_arrow],
            "duplicate_date_code_rows": duplicates, "original_null_counts": nulls,
            "original_nonfinite_counts": nonfinite, "source_dates": sorted(dates), "by_code": by_code,
            "sealed_rows": sealed_rows, "outside_requested_window_rows": outside_rows,
            "requested_window": [requested_start, requested_end], "audited_at": utcnow(),
            "unit_mapping": {"source_volume": "shares", "qa_vol": "original.volume / 100",
                             "source_amount": "CNY", "qa_amount": "original.amount",
                             "basis": "same local cache producer; execution sample compared with independent stored BaoStock raw bars"},
            "archive_policy": {"collection": ARCHIVE_COLLECTION, "all_original_fields": "preserved",
                               "research_eligibility": "archive_only", "adjustment": "unknown_unverified",
                               "financial_pit": "pending", "coverage_acceptance": "pending",
                               "sealed_data": "storage and custody audit only; no evaluation"}}


def archive_document(original: dict, source_id: str, row_number: int):
    """Keep the source dictionary untouched, including null, zero and NaN."""
    raw_code = str(original["code"]).strip()
    if raw_code.endswith(".0"):
        raw_code = raw_code[:-2]
    code = raw_code.zfill(6)
    day = datetime.strptime(str(original["date"]), "%Y%m%d")
    volume = original["volume"]
    return {"_id": f"{source_id}:{row_number}", "source_id": source_id, "source_row": row_number,
            "source": "legacy_stockdb_archive", "code": code, "date": day.strftime("%Y-%m-%d"),
            # QUANTAXIS QA_util_date_stamp and axis_sync use local midnight.
            "date_stamp": day.timestamp(),
            **{k: original[k] for k in ["open", "high", "low", "close", "amount"]},
            "vol": None if volume is None else volume / 100,
            "adjustment": "unknown_unverified", "research_eligibility": "archive_only", "original": dict(original)}


def migrate_price_file(db, audit: dict, *, batch_size=5000, progress=None):
    """Resume by deterministic source/row identity; touch only archive collections."""
    import pyarrow.parquet as pq
    from pymongo import InsertOne
    from pymongo.errors import BulkWriteError
    source_id = audit["source_id"]
    collection, receipts = db[ARCHIVE_COLLECTION], db[RECEIPT_COLLECTION]
    previous = receipts.find_one({"source_id": source_id}) or {}
    cursor = int(previous.get("next_row", 0))
    if not 0 <= cursor <= audit["rows"]:
        raise ValueError("Invalid durable archive cursor")
    receipt = {"source_id": source_id, "dataset": ARCHIVE_COLLECTION, "source": "legacy_stockdb_archive",
               "source_path": audit["path"], "source_manifest": audit["manifest"],
               "file_sha256": audit["file_sha256"], "logical_hash": audit["logical_hash"],
               "schema": audit["schema"], "unit_mapping": audit["unit_mapping"],
               "archive_policy": audit["archive_policy"], "expected_rows": audit["rows"],
               "codes": audit["codes"], "start": audit["start"], "through": audit["end"],
               "status": "importing", "next_row": cursor, "updated_at": utcnow()}
    receipts.update_one({"source_id": source_id}, {"$set": receipt}, upsert=True)
    position = 0
    try:
        for batch in pq.ParquetFile(audit["path"]).iter_batches(batch_size=batch_size):
            end = position + len(batch)
            if end <= cursor:
                position = end
                continue
            start = max(0, cursor - position)
            originals = batch.slice(start).to_pylist()
            documents = [archive_document(row, source_id, position + start + i) for i, row in enumerate(originals)]
            try:
                collection.bulk_write([InsertOne(doc) for doc in documents], ordered=False)
            except BulkWriteError as exc:
                if exc.details.get("writeConcernErrors") or any(e["code"] != 11000 for e in exc.details.get("writeErrors", [])):
                    raise
                # A chunk may have committed before its durable cursor was saved.
                # Duplicate identities are custody evidence, never overwritten.
                if collection.count_documents({"_id": {"$in": [d["_id"] for d in documents]}}) != len(documents):
                    raise ValueError("Archive retry chunk is incomplete") from exc
            position = end
            receipt.update({"next_row": position, "updated_at": utcnow()})
            receipts.update_one({"source_id": source_id}, {"$set": receipt}, upsert=True)
            if progress:
                progress(dict(receipt))
        count = collection.count_documents({"source_id": source_id})
        if count != audit["rows"]:
            raise ValueError(f"Archive stored {count} rows but expected {audit['rows']}")
        collection.create_index([("source_id", 1), ("code", 1), ("date", 1)])
        receipt.update({"status": "archive_complete", "rows": count, "next_row": count, "completed_at": utcnow()})
        receipts.update_one({"source_id": source_id}, {"$set": receipt}, upsert=True)
        return receipt
    except BaseException as exc:
        receipt.update({"status": "archive_interrupted", "last_error": f"{type(exc).__name__}: {exc}", "updated_at": utcnow()})
        receipts.update_one({"source_id": source_id}, {"$set": receipt}, upsert=True)
        raise


def unit_comparison_samples(db, path: Path):
    """Read-only local independent-source sample, strictly in the open window."""
    import pandas as pd
    frame = pd.read_parquet(path, filters=[("date", ">=", 20260914), ("date", "<=", 20260918),
                                          ("code", "in", ["000001", "600000", "600519"])])
    samples = []
    for row in frame.to_dict("records"):
        day = datetime.strptime(str(row["date"]), "%Y%m%d").strftime("%Y-%m-%d")
        official = db.stock_day.find_one({"source": "baostock", "code": row["code"], "date": day})
        if not official or not official.get("volume_shares") or not official.get("amount"):
            continue
        samples.append({"code": row["code"], "date": day, "legacy_volume": row["volume"],
                        "baostock_raw_volume_shares": official["volume_shares"],
                        "legacy_amount": row["amount"], "baostock_amount_cny": official["amount"],
                        "volume_ratio_to_shares": row["volume"] / official["volume_shares"],
                        "amount_ratio_to_cny": row["amount"] / official["amount"]})
    verified = len(samples) >= 3 and all(abs(s["volume_ratio_to_shares"] - 1) < .001 and
                                        abs(s["amount_ratio_to_cny"] - 1) < .001 for s in samples)
    return {"status": "observed_share_and_cny_units" if verified else "pending", "samples": samples,
            "scope": "legacy producer unit mapping only; precision, adjustment and PIT remain unverified",
            "source_access": "existing local Mongo documents only; no network calls", "observed_at": utcnow()}


def same_original_value(left, right):
    """BSON round trips NaN without making it equal to a fabricated null/zero."""
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(same_original_value(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_original_value(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, int) and isinstance(right, int) and not isinstance(left, bool) and not isinstance(right, bool):
        # BSON Int64 is an int subtype; integer width remains in Arrow schema.
        return left == right
    return type(left) is type(right) and left == right


def verify_archive_samples(db, audit, positions=None):
    """Bounded custody check of every field for fixed source rows, not returns."""
    import pyarrow.parquet as pq
    count = audit["rows"]
    targets = sorted(set(positions if positions is not None else [0, 1, 17, count // 2, count - 2, count - 1]))
    if any(p < 0 or p >= count for p in targets):
        raise ValueError("Invalid archive verification sample position")
    verified, position = [], 0
    for batch in pq.ParquetFile(audit["path"]).iter_batches(batch_size=32768):
        for sample in targets:
            if not position <= sample < position + len(batch):
                continue
            original = batch.slice(sample - position, 1).to_pylist()[0]
            expected = archive_document(original, audit["source_id"], sample)
            stored = db[ARCHIVE_COLLECTION].find_one({"_id": expected["_id"]})
            if stored is None:
                raise ValueError(f"Archive sample row {sample} absent")
            bad = [k for k in expected if not same_original_value(expected[k], stored.get(k))]
            if bad:
                raise ValueError(f"Archive sample row {sample} differs in {bad}")
            verified.append({"source_row": sample, "code": stored["code"], "date": stored["date"],
                             "original_columns_verified": sorted(original), "qa_mapping_verified": True,
                             "value_preservation": "exact values, types, nulls and NaN compared"})
        position += len(batch)
    return {"source_id": audit["source_id"], "status": "fixed_rows_all_fields_match",
            "samples": verified, "observed_at": utcnow(),
            "policy": "Offline custody verification only; adjustment, PIT and research eligibility remain pending"}
