#!/usr/bin/env python3
"""Audit and retain original offline bar caches in an isolated QA archive."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.legacy_archive import (ARCHIVE_COLLECTION, audit_price_file, migrate_price_file,
                                       unit_comparison_samples, write_json, utcnow)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", type=Path, required=True)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/legacy_prices_archive"))
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 50000:
        parser.error("batch-size must be between 1 and 50000")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audits = []
    for path in args.files:
        audit = audit_price_file(path)
        audits.append(audit)
        write_json(args.output_dir / (path.stem + ".audit.json"), audit)
        print(json.dumps({"status": "hash_and_schema_verified", "path": str(path),
                          "rows": audit["rows"], "codes": audit["codes"]}), flush=True)
    if args.audit_only:
        return 0
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    receipts, started, last_print = [], utcnow(), 0
    try:
        # The execution source carries independent real same-date unit evidence.
        execution = next((a for a in audits if a["start"] <= 20260914 <= a["end"]), None)
        units = unit_comparison_samples(db, Path(execution["path"])) if execution else {"status": "pending"}
        write_json(args.output_dir / "unit_comparison.json", units)
        if units["status"] != "observed_share_and_cny_units":
            raise ValueError("Legacy source units are not established by stored independent-source evidence")
        for audit in audits:
            def progress(receipt):
                nonlocal last_print
                write_json(args.output_dir / "progress.json", {"status": "importing", "started_at": started,
                           "collection": ARCHIVE_COLLECTION, "current": receipt, "completed_sources": receipts})
                if time.monotonic() - last_print >= 20:
                    print(json.dumps({"source_id": receipt["source_id"], "next_row": receipt["next_row"],
                                      "expected_rows": receipt["expected_rows"]}), flush=True)
                    last_print = time.monotonic()
            receipt = migrate_price_file(db, audit, batch_size=args.batch_size, progress=progress)
            receipts.append(receipt)
            write_json(args.output_dir / "receipts.json", receipts)
        report = {"status": "archive_complete", "collection": ARCHIVE_COLLECTION,
                  "started_at": started, "completed_at": utcnow(), "sources": receipts,
                  "rows": sum(r["rows"] for r in receipts), "unit_evidence": units,
                  "policy": "Original fields retained; no stock_day overwrite or research fallback; adjustment/PIT pending"}
        write_json(args.output_dir / "progress.json", report)
        print(json.dumps({"status": report["status"], "rows": report["rows"], "collection": ARCHIVE_COLLECTION}), flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
