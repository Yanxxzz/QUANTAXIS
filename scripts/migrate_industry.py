#!/usr/bin/env python3
"""Append independently reconciled CAPCO scope to separate AXIS collections."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".runtime" / "python"), str(ROOT)]
from panda_alpha.industry import IndustryVintage


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def content_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_verified_pack(source_dir, scope_path):
    """Bind accepted rows to the hash-verified panel and original source files."""
    source_dir, scope_path = Path(source_dir), Path(scope_path)
    receipt_path = source_dir / "industry_parse_receipt.json"
    receipt = read(receipt_path)
    if receipt["status"] != "PUBLISHED_INDUSTRY_FORMATION_PANEL_RECONCILED":
        raise ValueError("Independently reconciled source receipt required")
    codes = sorted(set(read(scope_path)["source_codes"]))
    if len(codes) != 1140 or any(not re.fullmatch(r"\d{6}", code) for code in codes):
        raise ValueError("The accepted1140 source scope is required")
    panel_path = source_dir / "industry_formation_panel.csv"
    if sha(panel_path) != receipt["panel_sha256"]:
        raise ValueError("Formation panel source hash mismatch")
    with panel_path.open(encoding="utf-8", newline="") as stream:
        panel = list(csv.DictReader(stream))
    if len(panel) != receipt["panel_rows"] or set(x["symbol"] for x in panel) != set(codes):
        raise ValueError("Scope/panel identity mismatch")
    if any(x["publication_date"] >= x["decision_date"] for x in panel):
        raise ValueError("Industry availability boundary violated")
    acquisitions = {x["vintage"]: x for x in read(source_dir / "industry_acquisition_receipt.json")["sources"]}
    pack = []
    for accepted in receipt["source_editions"]:
        vintage = accepted["vintage_id"]
        if accepted["independent_category_major_mismatches"] or accepted["independent_checked_scope_rows"] != accepted["scope1140_records"]:
            raise ValueError("Accepted scope independent reconciliation is incomplete")
        acquisition = acquisitions[vintage]
        paths = {"pdf": source_dir / "industry_source" / f"{vintage}.pdf",
                 "article": source_dir / "industry_source" / f"{vintage}.html",
                 "primary_text": source_dir / "industry_source" / f"{vintage}.pypdf.txt",
                 "parsed_json": source_dir / f"industry_{vintage}.json"}
        if sha(paths["pdf"]) != accepted["pdf_sha256"] or sha(paths["article"]) != accepted["article_sha256"] or sha(paths["primary_text"]) != accepted["primary_text_sha256"]:
            raise ValueError("Original source document fingerprint mismatch")
        parsed = read(paths["parsed_json"])
        if parsed["vintage_id"] != vintage or parsed["publication_date"] != accepted["publication_date"] or parsed["source_sha256"] != accepted["pdf_sha256"]:
            raise ValueError("Parsed edition provenance mismatch")
        rows = {code: parsed["records"][code] for code in codes if code in parsed["records"]}
        IndustryVintage(vintage, parsed["publication_date"], parsed["source_sha256"], rows)
        reference = {x["symbol"]: x for x in panel if x["vintage_id"] == vintage and x["status"] == "VERIFIED_PUBLISHED_CLASSIFICATION"}
        if set(rows) != set(reference) or len(rows) != accepted["scope1140_records"]:
            raise ValueError("Edition/panel accepted row identity mismatch")
        if any(rows[code]["category_code"] != reference[code]["category_code"] or rows[code]["major_code"] != reference[code]["major_code"] or str(rows[code]["source_page"]) != reference[code]["source_page"] for code in rows):
            raise ValueError("Edition differs from independent hash-bound formation panel")
        metadata = {"vintage_id": vintage, "publication_date": accepted["publication_date"],
            "source_sha256": accepted["pdf_sha256"], "parse_receipt_sha256": sha(receipt_path),
            "source_scope_codes": codes, "source_scope_count": len(codes), "scope_sha256": content_sha(codes),
            "scope_file_sha256": sha(scope_path), "accepted_record_count": len(rows),
            "source_full_record_count": accepted["source_records"], "full_a_industry_verified": False,
            "scope": "independently_reconciled1140_migration_source", "status": "verified_scoped",
            "source_paths": {k: str(v) for k, v in paths.items()}, "source_file_sha256": {k: sha(v) for k, v in paths.items()},
            "article_url": acquisition["article_url"], "pdf_url": acquisition["pdf_url"],
            "panel_sha256": receipt["panel_sha256"], "independently_verified_fields": ["symbol", "category_code", "major_code"],
            "same_publication_day_usable": False, "latest_edition_issuer_fallback": False}
        metadata["content_sha256"] = content_sha(metadata)
        documents = []
        for code, row in sorted(rows.items()):
            doc = {**row, "code": code, "vintage_id": vintage, "publication_date": accepted["publication_date"],
                "source_sha256": accepted["pdf_sha256"], "parse_receipt_sha256": metadata["parse_receipt_sha256"],
                "scope_sha256": metadata["scope_sha256"], "full_a_industry_verified": False}
            doc["content_sha256"] = content_sha(doc)
            documents.append(doc)
        pack.append((metadata, documents))
    return pack


def migrate_pack(db, pack):
    """Insert immutable rows first and activate an edition only after verification."""
    editions, records = db["stock_industry_editions"], db["stock_industry_pit"]
    editions.create_index([("vintage_id", 1)], unique=True)
    records.create_index([("vintage_id", 1), ("source_sha256", 1), ("code", 1)], unique=True)
    # Check every existing identity before any mutation, so a source conflict is
    # not silently converted into a newer replacement snapshot.
    for metadata, documents in pack:
        old = list(editions.find({"vintage_id": metadata["vintage_id"]}, {"_id": 0}))
        if old and (len(old) != 1 or old[0] != metadata):
            raise ValueError("Existing edition conflicts with immutable source identity")
        for doc in documents:
            identity = {k: doc[k] for k in ("vintage_id", "source_sha256", "code")}
            old = list(records.find(identity, {"_id": 0}))
            if old and (len(old) != 1 or old[0] != doc):
                raise ValueError("Existing industry row conflicts with immutable source identity")
    reports = []
    for metadata, documents in pack:
        inserted = 0
        for doc in documents:
            identity = {k: doc[k] for k in ("vintage_id", "source_sha256", "code")}
            change = records.update_one(identity, {"$setOnInsert": doc}, upsert=True)
            inserted += change.upserted_id is not None
        stored = list(records.find({"vintage_id": metadata["vintage_id"], "source_sha256": metadata["source_sha256"]}, {"_id": 0}))
        if len(stored) != len(documents) or {x["code"]: x for x in stored} != {x["code"]: x for x in documents}:
            raise ValueError("Edition import verification failed; edition not activated")
        change = editions.update_one({"vintage_id": metadata["vintage_id"]}, {"$setOnInsert": metadata}, upsert=True)
        reports.append({"vintage_id": metadata["vintage_id"], "stored_rows": len(stored), "inserted_rows": int(inserted),
            "inserted_edition": change.upserted_id is not None, "source_scope_count": metadata["source_scope_count"],
            "source_sha256": metadata["source_sha256"], "full_a_industry_verified": False})
    return {"status": "ACCEPTED_SCOPE_IMPORTED_AND_READ_BACK_VERIFIED", "collections_written": ["stock_industry_pit", "stock_industry_editions"],
        "editions": reports, "prices_or_financial_records_written": False, "full_a_industry_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "research_runs" / "f141_data_completion_20261006")
    parser.add_argument("--scope-file", type=Path, default=ROOT / "research_runs" / "risk_relation_rotation_20261006" / "source_protocol.json")
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--report", type=Path, default=ROOT / "research_runs" / "f141_industry_axis_20261006" / "migration_receipt.json")
    args = parser.parse_args(argv)
    pack = load_verified_pack(args.source_dir, args.scope_file)
    from pymongo import MongoClient
    with MongoClient(args.uri, serverSelectionTimeoutMS=5000) as client:
        client.admin.command("ping")
        report = migrate_pack(client[args.database], pack)
    report.update({"parse_receipt_sha256": sha(args.source_dir / "industry_parse_receipt.json"),
        "scope_file_sha256": sha(args.scope_file), "query_helper_sha256": sha(ROOT / "panda_alpha" / "industry.py"),
        "provider_sha256": sha(ROOT / "panda_alpha" / "data.py"), "migration_script_sha256": sha(__file__),
        "editions_metadata_sha256": {m["vintage_id"]: m["content_sha256"] for m, _ in pack}})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
