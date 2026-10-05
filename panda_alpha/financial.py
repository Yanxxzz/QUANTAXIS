"""Migrate public filing evidence into AXIS without re-querying legacy providers.

An announcement index is not a financial value database. Date-only disclosures
become eligible on the following calendar day (then on a trading decision date).
Missing values and unresolved corrections remain blockers, never zero values.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable

SCHEMA_VERSION = 1
BAOSTOCK_FINANCIAL_APIS = {
    "profit": "query_profit_data", "operation": "query_operation_data",
    "growth": "query_growth_data", "balance": "query_balance_data",
    "cash_flow": "query_cash_flow_data", "dupont": "query_dupont_data",
}
EASTMONEY_FINANCIAL_REPORTS = {"income":"RPT_DMSK_FN_INCOME", "balance":"RPT_DMSK_FN_BALANCE", "cashflow":"RPT_DMSK_FN_CASHFLOW"}


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _code(value: Any) -> str:
    code = str(value).lower().removeprefix("sh.").removeprefix("sz.").removeprefix("bj.")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("filing code must be a six-digit security identifier")
    return code


def _day(value: str) -> str:
    return date.fromisoformat(str(value)[:10]).isoformat()


def availability(published_at: str, not_before_date: str | None = None) -> str:
    """Never substitute the accounting period for disclosure availability."""
    published = date.fromisoformat(_day(published_at))
    conservative = (published + timedelta(days=1)).isoformat()
    if not_before_date:
        return max(conservative, _day(not_before_date))
    return conservative


def _amount(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return str(result) if result.is_finite() else None


def _report_date(title: str, category: str) -> str | None:
    # This is an explicitly labelled metadata inference, not pubDate or a
    # verified report-period link for a generic correction notice.
    endings = {"年报": "12-31", "半年报": "06-30", "一季报": "03-31", "三季报": "09-30"}
    years = set(re.findall(r"(?<!\d)(20\d{2})\s*年", title))
    if category not in endings or "报告" not in title or len(years) != 1:
        return None
    return years.pop() + "-" + endings[category]


def normalize_announcement(row: dict, provenance: dict) -> dict:
    code = _code(row["symbol"])
    published = str(row["published_at"])
    period = row.get("target_report_date") or _report_date(row.get("title", ""), row.get("category", ""))
    out = {"_id": "cninfo:" + str(row["announcement_id"]), "source": "cninfo_cached_public_index",
           "code": code, "symbol": code, "announcement_id": str(row["announcement_id"]),
           "report_date": _day(period) if period else None,
           "report_period_status": "explicit_record" if row.get("target_report_date") else
                                   ("title_metadata_only" if period else "unresolved"),
           "published_at": published, "pub_date": _day(published),
           "available_date": availability(published, row.get("not_before_date")),
           "time_precision": row.get("time_precision", "date"),
           "title": row.get("title", ""), "category": row.get("category", ""),
           "pdf_url": row.get("pdf_url"), "detail_url": row.get("detail_url"),
           "pdf_sha256": row.get("pdf_sha256"), "version_label": row.get("version_label"),
           "source_path": provenance["source_path"], "source_sha256": provenance["source_sha256"],
           "financial_values_available": False, "full_pit_certified": False}
    return out


def normalize_financial(row: dict, provenance: dict, *, source_index_verified: bool = False) -> dict:
    code = _code(row["symbol"])
    period = _day(row.get("report_date") or row["target_report_date"])
    published = str(row["published_at"])
    original_hash = row.get("pdf_sha256")
    hash_valid = bool(isinstance(original_hash, str) and re.fullmatch(r"[0-9a-f]{64}", original_hash))
    original_amount = _amount(row.get("amount_yuan"))
    accepted = row.get("status") == "ok" and original_amount is not None and hash_valid and source_index_verified
    status = "verified_within_supplied_documents" if accepted else "blocked_or_unverified"
    out = {"source": "cninfo_original_pdf_cached_extraction", "code": code, "symbol": code,
           "announcement_id": str(row["announcement_id"]), "report_date": period,
           "published_at": published, "pub_date": _day(published),
           "available_date": availability(published, row.get("not_before_date")),
           "time_precision": row.get("time_precision", "date"), "field": "contract_liability",
           "values": {"contract_liability": original_amount if accepted else None},
           "extracted_amount_yuan": original_amount, "currency": "CNY", "unit": "yuan",
           "value_status": status, "extraction_status": row.get("status", "unknown"),
           "pdf_sha256": original_hash, "original_document_hash_recorded": hash_valid,
           "original_pdf_reverified_locally": provenance.get("original_pdf_reverified_locally", False),
           "pdf_url": row.get("pdf_url"), "page": row.get("page"), "evidence": row.get("evidence"),
           "document_kind": row.get("document_kind", "report"),
           "version_label": row.get("version_label"), "chain_status": row.get("chain_status", "unreviewed"),
           "previous_amount_yuan": _amount(row.get("previous_amount_yuan")),
           "revision_delta_yuan": _amount(row.get("revision_delta_yuan")),
           "same_day_revised_report_ids": row.get("same_day_revised_report_ids", []),
           "source_index_path": row.get("source_index"), "source_index_sha256": row.get("source_index_sha256"),
           "source_index_verified": source_index_verified,
           "source_path": provenance["source_path"], "source_sha256": provenance["source_sha256"],
           "source_sqlite_sidecars": provenance.get("sqlite_sidecars", []),
           "full_pit_certified": False}
    content = {key: value for key, value in out.items() if key not in {"source_path", "source_sha256", "original_pdf_reverified_locally", "source_sqlite_sidecars"}}
    out["record_sha256"] = _hash(_canonical(content).encode())
    out["_id"] = "cninfo:" + code + ":" + period + ":" + str(row["announcement_id"]) + ":" + out["record_sha256"][:16]
    return out


def normalize_correction_extractions(row: dict, provenance: dict, *,
                                     source_index_verified: bool = False) -> list[dict]:
    """Recover successful local body extracts without reviewing their revision chain.

    The mapping's parent identifies the notice and source index. Every nested
    extraction must identify those same original bytes, disclosure and period.
    Numeric extraction success does not resolve an unknown correction scope.
    """
    records = []
    for link in row.get("period_links", []):
        extracted = link.get("extraction")
        if not isinstance(extracted, dict) or extracted.get("status") != "ok":
            continue
        identity = ("symbol", "announcement_id", "published_at", "pdf_url", "pdf_sha256")
        if (any(extracted.get(key) is None or str(extracted[key]) != str(row.get(key)) for key in identity)
                or not link.get("report_date") or extracted.get("report_date") != link["report_date"]):
            raise ValueError("Nested correction extraction identity/period mismatch")
        combined = {**row, **extracted, "report_date": link["report_date"],
                    "chain_status": "unreviewed",
                    "document_kind": "revised_report" if row.get("version_label") == "revised_full_report" else "correction",
                    "same_day_revised_report_ids": link.get("same_day_revised_report_ids", [])}
        document = normalize_financial(combined, provenance, source_index_verified=source_index_verified)
        document["correction_mapping_evidence"] = {
            "unknown_scope_barrier": row.get("unknown_scope_barrier", True),
            "scope_evidence": row.get("scope_evidence"), "body_status": row.get("body_status"),
            "value_linkage": link.get("value_linkage"), "evidence_source": link.get("evidence_source"),
            "original_ids_before_notice": link.get("original_ids_before_notice", []),
            "revision_chain_verified": False}
        records.append(document)
    return records


def _same_financial_document_value(candidate: dict, existing: dict) -> bool:
    """Keep a richer reviewed chain instead of adding its unreviewed duplicate."""
    if (candidate.get("value_status") != "verified_within_supplied_documents"
            or existing.get("value_status") != "verified_within_supplied_documents"):
        return False
    keys = ("code", "report_date", "announcement_id", "published_at", "available_date", "pdf_sha256", "field", "values")
    if any(candidate.get(key) != existing.get(key) for key in keys):
        return False
    return all(candidate.get(key) is None or candidate.get(key) == existing.get(key)
               for key in ("previous_amount_yuan", "revision_delta_yuan"))


def select_financial_asof(records: Iterable[dict], *, code: str, report_date: str,
                         decision_date: str, corrections: Iterable[dict] = ()) -> dict:
    """Select only supplied vintages; an unparsed/latest report invalidates carry-forward."""
    code, report_date, decision_date = _code(code), _day(report_date), _day(decision_date)
    eligible = [row for row in records if row.get("code") == code and row.get("report_date") == report_date
                and row.get("available_date", "9999") <= decision_date]
    if not eligible:
        return {"status": "missing_disclosed_value", "value": None}
    latest_date = max(row["available_date"] for row in eligible)
    latest = [row for row in eligible if row["available_date"] == latest_date]
    # An older diagnostic extraction of the *same* original PDF does not defeat
    # a later verified parse. Preserve every stored row, but reconcile identical
    # document bytes and identical amount evidence before selecting the vintage.
    verified_copies={(row.get("announcement_id"),row.get("pdf_sha256")):
                     row["values"]["contract_liability"] for row in latest
                     if row.get("value_status")=="verified_within_supplied_documents"}
    latest=[row for row in latest if row.get("value_status")=="verified_within_supplied_documents" or
            (row.get("announcement_id"),row.get("pdf_sha256")) not in verified_copies or
            row.get("extracted_amount_yuan") not in (None,verified_copies[(row.get("announcement_id"),row.get("pdf_sha256"))])]
    reviewed_corrections={row["announcement_id"] for row in eligible
                          if row.get("value_status")=="verified_within_supplied_documents" and
                          row.get("chain_status") in {"linked_correction","linked_revised_report"}}
    for correction in corrections:
        if correction.get("code") != code or correction.get("available_date", "9999") > decision_date:
            continue
        linked = {link.get("report_date") for link in correction.get("period_links", [])}
        if correction.get("announcement_id") in reviewed_corrections:
            continue
        correction_publication=correction.get("pub_date",correction.get("published_at","")[:10])
        if not linked and correction_publication and correction_publication < report_date:
            continue
        if correction.get("unknown_scope_barrier") and (not linked or report_date in linked):
            return {"status": "blocked_by_unresolved_correction_scope", "value": None,
                    "announcement_id": correction.get("announcement_id")}
    if any(row.get("value_status") != "verified_within_supplied_documents" for row in latest):
        return {"status": "blocked_by_unparsed_or_unverified_latest_document", "value": None}
    values = {row.get("values", {}).get("contract_liability") for row in latest}
    if len(values) != 1 or None in values:
        return {"status": "ambiguous_same_day_versions", "value": None}
    row = latest[0]
    for current in latest:
        previous = current.get("previous_amount_yuan")
        if previous is not None:
            earlier = [item for item in eligible if item["available_date"] < latest_date
                       and item.get("value_status") == "verified_within_supplied_documents"]
            prior_day=max((item["available_date"] for item in earlier),default=None)
            prior_values={item["values"]["contract_liability"] for item in earlier if item["available_date"]==prior_day}
            if prior_values!={previous}:
                return {"status": "revision_chain_mismatch", "value": None}
    return {"status": "ok_within_supplied_documents", "value": next(iter(values)),
            "announcement_id": row["announcement_id"], "available_date": row["available_date"],
            "pdf_sha256": row["pdf_sha256"], "scope": "sample_only_not_full_market_pit"}


def query_financial_asof(db: Any, *, code: str, report_date: str, decision_date: str) -> dict:
    query = {"code": _code(code), "report_date": _day(report_date), "available_date": {"$lte": _day(decision_date)}}
    records = list(db["stock_financial_pit"].find(query))
    known_ids={row["announcement_id"] for row in records}
    # A newly disclosed but unparsed full report must not silently carry forward
    # a known old amount just because only the old PDF was extracted.
    for filing in db["stock_filing_index"].find(query):
        if filing["announcement_id"] not in known_ids and "报告" in filing.get("title","") and not re.search(r"摘要|提示性|英文|取消|说明公告",filing.get("title","")):
            records.append({**filing,"value_status":"unparsed_indexed_report","values":{"contract_liability":None}})
    corrections = db["stock_financial_revision"].find({"code": _code(code), "available_date": {"$lte": _day(decision_date)}})
    return select_financial_asof(records, code=code, report_date=report_date,
                                decision_date=decision_date, corrections=corrections)


def normalize_baostock_financial(row: dict, dataset: str, observed_at: str) -> dict:
    if dataset not in BAOSTOCK_FINANCIAL_APIS:
        raise ValueError("unsupported BaoStock financial dataset")
    # Historical statDate is never used as pubDate. Current retrieval may contain
    # restated history; preserve this limitation until original vintages reconcile.
    published, period = _day(row["pubDate"]), _day(row["statDate"])
    values = {key: _amount(value) for key, value in row.items() if key not in {"code", "pubDate", "statDate"}}
    payload_hash = _hash(_canonical(row).encode())
    return {"_id": "baostock:" + dataset + ":" + _code(row["code"]) + ":" + period + ":" + payload_hash[:16],
            "source": "baostock_current_financial_snapshot", "dataset": dataset, "code": _code(row["code"]),
            "report_date": period, "pub_date": published, "published_at": published,
            "available_date": availability(published), "values": values,
            "missing_fields": [key for key, value in values.items() if value is None],
            "observed_at": observed_at, "source_response_sha256": payload_hash,
            "value_status": "provider_pubdate_available_original_vintages_unverified",
            "original_revision_history_verified": False, "full_pit_certified": False}


def fetch_baostock_financial(api: Any, code: str, year: int, quarter: int,
                            datasets: Iterable[str] = BAOSTOCK_FINANCIAL_APIS) -> list[dict]:
    """Use an already logged-in, free BaoStock session; caller controls finite scope."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1 through 4")
    normalized = _code(code)
    if not normalized.startswith(("6", "00", "30")):
        raise ValueError("BaoStock SH/SZ financial interface requires a SH/SZ code")
    provider_code = ("sh." if normalized.startswith("6") else "sz.") + normalized
    out = []
    observed = datetime.now(timezone.utc).isoformat()
    for dataset in datasets:
        method = BAOSTOCK_FINANCIAL_APIS.get(dataset)
        if not method:
            raise ValueError("unsupported BaoStock financial dataset")
        result = getattr(api, method)(code=provider_code, year=int(year), quarter=int(quarter))
        if str(result.error_code) != "0":
            raise RuntimeError("BaoStock financial request failed: " + str(result.error_code))
        while result.next():
            row = dict(zip(result.fields, result.get_row_data()))
            if not {"code", "pubDate", "statDate"}.issubset(row):
                raise ValueError("BaoStock response lacks publication/period metadata")
            out.append(normalize_baostock_financial(row, dataset, observed))
    return out


def _write_batch(db: Any, name: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    from pymongo import ReplaceOne, UpdateOne
    if name == "stock_filing_index":
        operations=[]
        for row in rows:
            fields={key:value for key,value in row.items() if key!="_id" and not (key=="report_date" and value is None)}
            operations.append(UpdateOne({"_id":row["_id"]},{"$set":fields,"$addToSet":{"categories":row.get("category","")}},upsert=True))
    else:
        operations=[ReplaceOne({"_id":row["_id"]},row,upsert=True) for row in rows]
    db[name].bulk_write(operations, ordered=False)
    return len(rows)


def migrate_legacy_financial(workspace: str | Path, db: Any, *, batch_size: int = 1000,
                             progress: Any = None) -> dict:
    """Perform idempotent local Mongo upserts; old raw sources are read-only."""
    workspace = Path(workspace).resolve()
    base = workspace / "factor_workflow" / "filing_market_index"
    if not base.is_dir():
        raise ValueError("legacy filing corpus not found")
    if not 1 <= batch_size <= 5000:
        raise ValueError("batch_size must be 1 through 5000")
    db["stock_filing_index"].create_index([("code", 1), ("available_date", 1)])
    db["stock_financial_pit"].create_index([("code", 1), ("report_date", 1), ("available_date", 1)])
    db["stock_financial_revision"].create_index([("code", 1), ("available_date", 1)])
    hashes = {}; provenance_rows = []; counts = Counter(); failures = []
    def source(path: Path) -> tuple[dict, bytes]:
        raw = path.read_bytes(); digest = _hash(raw)
        hashes[str(path.resolve())] = digest
        record = {"_id": digest, "source_path": path.relative_to(workspace).as_posix(),
                  "source_sha256": digest, "bytes": len(raw), "legacy_source_unmodified": True}
        if path.suffix==".sqlite":
            record["sqlite_sidecars"]=[]
            wal=Path(str(path)+"-wal")
            if wal.is_file() and wal.stat().st_size:
                wal_raw=wal.read_bytes();wal_hash=_hash(wal_raw);hashes[str(wal.resolve())]=wal_hash
                record["sqlite_sidecars"].append({"source_path":wal.relative_to(workspace).as_posix(),
                                                 "source_sha256":wal_hash,"bytes":len(wal_raw)})
        provenance_rows.append(record)
        return record, raw
    # Authoritative complete windows only; quarantine diagnostic windows stay out.
    pending = []
    for directory in (base / "f208_warmup_2020_2021", base / "full_5y"):
        for path in sorted(directory.glob("*.json")):
            provenance, raw = source(path); payload = json.loads(raw)
            if payload.get("kind") != "market_public_filing_index" or payload.get("query_complete") is not True:
                failures.append({"source_path": provenance["source_path"], "reason": "incomplete_index_window"}); continue
            filings, rejected = payload.get("filings", []), payload.get("rejected", [])
            if payload.get("total_announcements") != len(filings) + len(rejected):
                raise ValueError("announcement index denominator mismatch")
            counts["index_windows"] += 1; counts["rejected_index_rows"] += len(rejected)
            for row in filings:
                pending.append(normalize_announcement(row, provenance))
                if len(pending) >= batch_size:
                    counts["announcement_rows_upserted"] += _write_batch(db, "stock_filing_index", pending); pending=[]
            if progress and counts["index_windows"] % 100 == 0:
                progress({"index_windows": counts["index_windows"], "announcement_rows_upserted": counts["announcement_rows_upserted"]})
    counts["announcement_rows_upserted"] += _write_batch(db, "stock_filing_index", pending)
    # Metadata for complete revised reports and pre-update archives remains distinct.
    versions = {}
    for name in ("warmup_versions_2019fy_2021h1_20260928_v1.json", "full_versions_2019fy_2026h1_20260928_v1.json"):
        path = base/name
        if not path.exists():continue
        provenance, raw=source(path)
        for row in json.loads(raw).get("filings", []):
            versions[str(row["announcement_id"])]=row
            db["stock_filing_index"].update_one({"_id":"cninfo:"+str(row["announcement_id"])},
                {"$set":{"version_label":row.get("version_label"),"report_date":row.get("target_report_date"),
                         "report_period_status":"version_inventory_metadata"}},upsert=False)
    def verified_index(row: dict) -> bool:
        path = Path(row.get("source_index", "")); expected = row.get("source_index_sha256")
        if str(path.resolve()) not in hashes and path.is_file():source(path)
        return bool(expected and hashes.get(str(path.resolve())) == expected)
    statements=[]
    for path in sorted(base.glob("*.sqlite")):
        provenance,_=source(path)
        conn=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True);conn.row_factory=sqlite3.Row
        try:
            if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():continue
            for record in conn.execute("SELECT * FROM documents"):
                row=dict(record);version=versions.get(str(row["announcement_id"]),{})
                row["version_label"]=version.get("version_label")
                row["document_kind"]="revised_report" if row["version_label"]=="revised_full_report" else "report"
                document=normalize_financial(row,provenance,source_index_verified=verified_index(row))
                statements.append(document);counts["extraction_"+str(row["status"])]+=1
        finally:conn.close()
    # Reviewed single-stock correction ledgers retain their explicit before/after chain.
    ledger_paths=[base/"600603_mapped_corrections_asof_20260928_v1.json",
                  base/"688719_mapped_corrections_asof_20260928_v2.json"]
    ledger_paths.extend((workspace/"factor_workflow"/"filing_samples").glob("*_versions_v2.json"))
    for path in ledger_paths:
        if not path.exists():continue
        provenance,raw=source(path);payload=json.loads(raw);ledger=payload.get("ledger",payload)
        for row in ledger.get("events",[]):
            statements.append(normalize_financial(row,provenance,source_index_verified=verified_index(row)))
    correction_path=base/"correction_period_mapping_body_20260929_v4.json"
    if correction_path.exists():
        provenance,raw=source(correction_path);mapping=json.loads(raw);pending=[]
        for row in mapping.get("documents",[]):
            document={"_id":"cninfo:"+str(row["announcement_id"]),"code":_code(row["symbol"]),
                      "announcement_id":str(row["announcement_id"]),"published_at":row["published_at"],
                      "pub_date":_day(row["published_at"]),"available_date":availability(row["published_at"]),
                      "title":row.get("title"),"classification":row.get("classification"),
                      "unknown_scope_barrier":row.get("unknown_scope_barrier",True),
                      "scope_evidence":row.get("scope_evidence"),"body_status":row.get("body_status"),
                      "period_links":row.get("period_links",[]),"pdf_confirmed_periods":row.get("pdf_confirmed_periods",[]),
                      "source_path":provenance["source_path"],"source_sha256":provenance["source_sha256"],
                      "full_pit_certified":False}
            pending.append(document)
            if len(pending)>=batch_size:counts["correction_rows_upserted"]+=_write_batch(db,"stock_financial_revision",pending);pending=[]
            try:
                extracted = normalize_correction_extractions(row, provenance, source_index_verified=verified_index(row))
            except ValueError as exc:
                failures.append({"source_path": provenance["source_path"],
                                 "announcement_id": str(row["announcement_id"]), "reason": str(exc)})
                continue
            counts["mapping_successful_extractions"] += len(extracted)
            for candidate in extracted:
                if any(_same_financial_document_value(candidate, prior) for prior in statements):
                    counts["mapping_duplicate_document_values"] += 1
                else:
                    statements.append(candidate)
                    counts["mapping_extraction_rows_upserted"] += 1
        counts["correction_rows_upserted"]+=_write_batch(db,"stock_financial_revision",pending)
    for offset in range(0,len(statements),batch_size):_write_batch(db,"stock_financial_pit",statements[offset:offset+batch_size])
    for offset in range(0,len(provenance_rows),batch_size):_write_batch(db,"panda_financial_provenance",provenance_rows[offset:offset+batch_size])
    codes=sorted({row["code"] for row in statements});accepted=[row for row in statements if row["value_status"]=="verified_within_supplied_documents"]
    distinct={row["_id"] for row in statements}
    first=db["stock_filing_index"].find_one({},sort=[("pub_date",1)])
    last=db["stock_filing_index"].find_one({},sort=[("pub_date",-1)])
    receipt={"_id":"legacy_public_financial_migration_v1","capability":"pit_financial","status":"partial",
             "source":"cached_cninfo_original_documents","schema_version":SCHEMA_VERSION,
             "start":first["pub_date"] if first else None,"end":last["pub_date"] if last else None,
             "fields":["contract_liability"],"codes":codes,"full_universe_complete":False,
             "record_count":len(distinct),"accepted_extraction_rows":len(accepted),
             "unique_accepted_records":len({row["_id"] for row in accepted}),
             "announcement_count":db["stock_filing_index"].count_documents({}),
             "correction_count":db["stock_financial_revision"].count_documents({}),
             "evidence":{"original_pdf_hashes_recorded":len({row["pdf_sha256"] for row in statements if row.get("pdf_sha256")}),
                         "source_files":len({row["_id"] for row in provenance_rows}),
                         "source_manifest_sha256":_hash(_canonical(sorted(hashes.items())).encode()),
                         "availability_policy":"publication_next_calendar_day_then_trading_calendar",
                         "publication_is_separate_from_report_period":True,"missing_values_filled_with_zero":False},
             "limitations":["financial_values_are_sparse_samples_not_full_A_history","generic_correction_scopes_can_block_old_values",
                            "current_HS_universe_is_not_historical_membership","original_PDF_bytes_not_all_retained_but_extraction_hashes_preserved",
                            "all_other_financial_fields_pending","PandaAI_runtime_parity_unverified"],
             "counts":dict(counts),"incomplete_windows":failures,"raw_evidence_modified":False,
             "migrated_at":datetime.now(timezone.utc).isoformat()}
    db["panda_axis_validation"].replace_one({"_id":receipt["_id"]},receipt,upsert=True)
    return receipt


def normalize_eastmoney_financial(row: dict, dataset: str, *, observed_at: str,
                                  page_sha256: str, source_path: str) -> dict:
    if dataset not in EASTMONEY_FINANCIAL_REPORTS:
        raise ValueError("unsupported EastMoney financial table")
    code=_code(row["SECURITY_CODE"])
    published,period=_day(row["NOTICE_DATE"]),_day(row["REPORT_DATE"])
    metadata={key:row.get(key) for key in ("SECUCODE","SECURITY_TYPE_CODE","MARKET","REPORT_TYPE_CODE","DATA_STATE")}
    numeric={key:_amount(value) for key,value in row.items() if key not in {
        "SECUCODE","SECURITY_CODE","INDUSTRY_CODE","ORG_CODE","SECURITY_NAME_ABBR","INDUSTRY_NAME",
        "MARKET","SECURITY_TYPE_CODE","TRADE_MARKET_CODE","DATE_TYPE_CODE","REPORT_TYPE_CODE","DATA_STATE",
        "NOTICE_DATE","REPORT_DATE"}}
    response_hash=_hash(_canonical(row).encode())
    return {"_id":"eastmoney:"+dataset+":"+code+":"+period+":"+response_hash[:16],
            "source":"eastmoney_public_current_financial_snapshot","dataset":dataset,"code":code,
            "report_date":period,"published_at":str(row["NOTICE_DATE"]),"pub_date":published,
            "available_date":availability(published),"observed_at":observed_at,
            "values":numeric,"missing_fields":[key for key,value in numeric.items() if value is None],
            "metadata":metadata,"raw_response_row":row,"source_response_sha256":response_hash,
            "source_page_sha256":page_sha256,"source_path":source_path,
            "value_status":"reported_publication_date_current_restated_snapshot",
            "original_revision_history_verified":False,"pit_usable":False,"full_pit_certified":False}


def migrate_eastmoney_financial(db: Any, cache_dir: str | Path, *, start: str="2019-01-01",
                                end: str="2026-09-18", datasets: Iterable[str]=EASTMONEY_FINANCIAL_REPORTS,
                                workers: int=4, progress: Any=None) -> dict:
    """Finite, resumable bulk queries of the provider's public financial tables.

    Latest restated values remain a separate snapshot layer. Matching NOTICE_DATE
    to an old announcement cannot prove that a current numeric value was present
    in that original announcement; original PDFs/vintages still govern PIT use.
    """
    start,end=_day(start),_day(end)
    if start>end or not 1<=workers<=4:raise ValueError("invalid date bounds or workers")
    cache_dir=Path(cache_dir).resolve();cache_dir.mkdir(parents=True,exist_ok=True)
    db["stock_financial_provider_snapshot"].create_index([("source",1),("dataset",1),("code",1),("report_date",1)])
    summaries={};failures=[]
    for dataset in datasets:
        report=EASTMONEY_FINANCIAL_REPORTS.get(dataset)
        if not report:raise ValueError("unsupported EastMoney financial dataset")
        query={"reportName":report,"columns":"ALL","pageSize":500,"sortColumns":"REPORT_DATE,SECURITY_CODE",
               "sortTypes":"-1,1","filter":f"(REPORT_DATE>='{start}')(REPORT_DATE<='{end}')(NOTICE_DATE<='{end}')",
               "source":"WEB","client":"WEB"}
        fingerprint=_hash(_canonical(query).encode())[:16]
        target=cache_dir/(dataset+"_"+fingerprint);target.mkdir(exist_ok=True)
        def fetch(page: int) -> dict:
            path=target/f"page_{page:05d}.json"
            if path.exists():
                cached=json.loads(path.read_text(encoding="utf-8"))
                if cached.get("query")==query and cached.get("page")==page:return cached
                raise ValueError("changed financial page cache identity")
            params={**query,"pageNumber":page}
            url="https://datacenter-web.eastmoney.com/api/data/v1/get?"+urllib.parse.urlencode(params)
            error=None
            for attempt in range(3):
                try:
                    request=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"})
                    with urllib.request.urlopen(request,timeout=45) as response:raw=response.read()
                    payload=json.loads(raw)
                    if payload.get("success") is not True or not isinstance(payload.get("result"),dict):
                        raise RuntimeError("EastMoney public financial request returned no successful result")
                    cached={"query":query,"page":page,"observed_at":datetime.now(timezone.utc).isoformat(),
                            "response_sha256":_hash(raw),"response":payload}
                    temporary=path.with_suffix(".tmp");temporary.write_text(json.dumps(cached,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
                    temporary.replace(path);return cached
                except (OSError,ValueError,RuntimeError) as exc:
                    error=exc
                    if attempt<2:time.sleep(0.3*(attempt+1))
            raise RuntimeError("EastMoney public financial page failed after bounded retries") from error
        first=fetch(1);result=first["response"]["result"];pages=int(result["pages"]);expected=int(result["count"])
        if not 0<=pages<=2000:raise ValueError("financial request exceeded finite page limit")
        stats=Counter();field_counts=Counter();codes=set();record_ids=set();periods=set();notice_dates=set();cached_pages=[]
        def accept(payload: dict):
            result=payload["response"]["result"]
            if int(result["pages"])!=pages or int(result["count"])!=expected:
                raise RuntimeError("financial table changed during pagination; cached snapshot remains incomplete")
            rows=[]
            for raw_row in result.get("data") or []:
                row=normalize_eastmoney_financial(raw_row,dataset,observed_at=payload["observed_at"],
                    page_sha256=payload["response_sha256"],source_path=str(target/f"page_{payload['page']:05d}.json"))
                rows.append(row);record_ids.add(row["_id"]);codes.add(row["code"]);periods.add(row["report_date"]);notice_dates.add(row["pub_date"])
                field_counts.update(key for key,value in row["values"].items() if value is not None)
            _write_batch(db,"stock_financial_provider_snapshot",rows)
            stats["rows"]+=len(rows);stats["pages"]+=1;cached_pages.append(payload["response_sha256"])
            if progress and (stats["pages"]%20==0 or stats["pages"]==pages):
                progress({"dataset":dataset,"pages":stats["pages"],"expected_pages":pages,"rows":stats["rows"]})
        accept(first)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(fetch,page):page for page in range(2,pages+1)}
            for future in as_completed(futures):
                try:accept(future.result())
                except Exception as exc:
                    failures.append({"dataset":dataset,"page":futures[future],"error_type":type(exc).__name__})
        complete=stats["rows"]==expected and stats["pages"]==pages and len(record_ids)==expected and not any(item["dataset"]==dataset for item in failures)
        summaries[dataset]={"report_name":report,"requested_report_start":start,"requested_end":end,
                            "expected_rows":expected,"rows":stats["rows"],"unique_records":len(record_ids),
                            "expected_pages":pages,"accepted_pages":stats["pages"],"complete_provider_snapshot":complete,
                            "code_count":len(codes),"report_period_count":len(periods),"field_nonmissing_counts":dict(field_counts),
                            "publication_bounds":[min(notice_dates),max(notice_dates)] if notice_dates else [],
                            "source_pages_hash":_hash(_canonical(sorted(cached_pages)).encode()),
                            "original_historical_vintages_verified":False,"pit_usable":False}
        if progress:progress({"dataset_complete":dataset,"summary":summaries[dataset]})
    receipt={"_id":"eastmoney_financial_snapshot_"+start+"_"+end,"capability":"financial_provider_snapshot",
             "status":"verified" if all(item["complete_provider_snapshot"] for item in summaries.values()) else "partial",
             "source":"eastmoney_public_datacenter","start":start,"end":end,"datasets":summaries,"failures":failures,
             "full_pit_certified":False,"full_universe_complete":False,
             "evidence":{"api":"https://datacenter-web.eastmoney.com/api/data/v1/get",
                         "report_date_is_accounting_period":True,"notice_date_is_reported_publication":True,
                         "observed_versions_preserved":True,"raw_pages_retained":True,"missing_values_filled_with_zero":False},
             "limitations":["latest_restated_numbers_cannot_be_backdated_to_original_NOTICE_DATE",
                            "original_filing_amounts_and_revision_chains_require_reconciliation",
                            "provider_current_security_set_is_not_historical_universe"],
             "migrated_at":datetime.now(timezone.utc).isoformat()}
    db["panda_axis_validation"].replace_one({"_id":receipt["_id"]},receipt,upsert=True)
    return receipt
