#!/usr/bin/env python3
"""Read-only local archive row/receipt/custody verification, without evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.legacy_archive import (ARCHIVE_COLLECTION, RECEIPT_COLLECTION, file_sha256,
                                       verify_archive_samples, write_json, utcnow)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/legacy_prices_archive"))
    args = parser.parse_args(argv)
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    try:
        sources = []
        for path in sorted(args.output_dir.glob("*.audit.json")):
            audit = json.loads(path.read_text(encoding="utf-8"))
            if file_sha256(Path(audit["path"])) != audit["file_sha256"]:
                raise ValueError("Original file changed after import audit")
            verification = verify_archive_samples(db, audit)
            count = db[ARCHIVE_COLLECTION].count_documents({"source_id": audit["source_id"]})
            receipt = db[RECEIPT_COLLECTION].find_one({"source_id": audit["source_id"]}, {"_id": 0})
            if count != audit["rows"] or not receipt or receipt.get("status") != "archive_complete" or receipt.get("rows") != count:
                raise ValueError("Archive physical count/complete receipt/source audit differ")
            sources.append({"source_id": audit["source_id"], "physical_rows": count, "codes": audit["codes"],
                "start": audit["start"], "end": audit["end"], "sealed_rows": audit["sealed_rows"],
                "outside_requested_window_rows": audit["outside_requested_window_rows"],
                "custody_verification": verification, "receipt": receipt})
        if not sources:
            raise ValueError("No source audits found")
        report = {"status": "archive_custody_verified", "collection": ARCHIVE_COLLECTION,
            "physical_rows": sum(s["physical_rows"] for s in sources), "sources": sources,
            "unique_codes": len(db[ARCHIVE_COLLECTION].distinct("code")),
            "indexes": list(db[ARCHIVE_COLLECTION].list_indexes()), "verified_at": utcnow(),
            "policy": "Isolation and custody only; no adjustment/PIT/all-A acceptance or research fallback",
            "source_access": "local original Parquet and Mongo only; no network/evaluation"}
        write_json(args.output_dir / "verification.json", report)
        print(json.dumps({"status": report["status"], "physical_rows": report["physical_rows"],
                          "unique_codes": report["unique_codes"], "samples": sum(len(s["custody_verification"]["samples"]) for s in sources)}), flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
