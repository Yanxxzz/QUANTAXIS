#!/usr/bin/env python3
"""Explicit same-source native HFQ repair after provider access is restored.

This repairs window-relative prices, never invents an IPO action origin and
never retries known-truncated factor histories as if that were new evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.axis_market_sync import (fetch_native_hfq_panel, install_baostock_transport,
                                     login_source, record_source_block, source_blocked_error,
                                     receipt_after_failure, utcnow, write_json)
from scripts.axis_sync import date_string, upsert_many


def audit_hfq_window(raw, panel):
    """Require native adjusted OHLC for every available raw tradable date."""
    def valid_prices(row):
        values = [row.get(k) for k in ["open", "high", "low", "close"]]
        return all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values) and row["high"] >= max(values) and row["low"] <= min(values)
    expected = {r["date"] for r in raw if r.get("trade_status") != "0" and
                not r.get("missing_numeric_fields") and valid_prices(r)}
    verified = {r["date"] for r in panel if r.get("trade_status") != "0" and r.get("valid_ohlc") and valid_prices(r)}
    return {"raw_tradable_dates": len(expected), "verified_hfq_dates": len(expected & verified),
            "raw_excluded_dates": sorted({r["date"] for r in raw} - expected),
            "missing_hfq_dates": sorted(expected - verified),
            "status": "complete" if expected and expected <= verified else "partial",
            "scope": "same_source_native_hfq_ohlc_on_requested_raw_tradable_dates",
            "absolute_ipo_anchor": "pending", "qfq_anchor": "sample_end_only"}


def choose_repair_codes(db, start, end, explicit=None):
    if explicit:
        return sorted(set(explicit))
    receipts = db.panda_axis_sync.find({"dataset": "stock_adj", "source": "baostock",
                                       "status": "partial", "history_complete": False})
    return sorted({r["code"] for r in receipts if db.stock_day.find_one({"source": "baostock", "code": r["code"],
                                              "date": {"$gte": start, "$lte": end}})})


def blocked_report(state, codes, start, end):
    return {"status": "source_blocked_waiting", "source": "baostock", "source_state": state,
            "codes": codes, "start": start, "end": end, "observed_at": utcnow(),
            "network_attempts": 0, "absolute_ipo_anchor": "pending",
            "resume": "After external source restoration, explicitly pass --resume-after-source-unblock",
            "policy": "No automatic monitor, identity change or retries while blocked"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date_string, required=True)
    parser.add_argument("--end", type=date_string, required=True)
    parser.add_argument("--codes", nargs="+")
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/adjustment_repair"))
    parser.add_argument("--resume-after-source-unblock", action="store_true")
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error("Invalid window")
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    logged_in, report = False, None
    try:
        codes = choose_repair_codes(db, args.start, args.end, args.codes)
        state = db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}, {"_id": 0})
        if state and not args.resume_after_source_unblock:
            report = blocked_report(state, codes, args.start, args.end)
            write_json(args.output_dir / "progress.json", report)
            print(json.dumps({"status": report["status"], "codes": codes, "network_attempts": 0}), flush=True)
            return 2
        report = {"source": "baostock", "start": args.start, "end": args.end,
                  "started_at": utcnow(), "codes": codes, "results": [], "status": "running"}
        if not codes:
            report["status"] = "no_pending_adjustments"
            write_json(args.output_dir / "progress.json", report)
            return 0
        import baostock as bs
        install_baostock_transport()
        try:
            login_source(bs, attempts=1)
            logged_in = True
            db.panda_axis_source_state.update_one({"source": "baostock"}, {"$set": {
                "source": "baostock", "status": "reachable", "observed_at": utcnow(),
                "restoration_probe": "explicit_same_identity_single_login"}}, upsert=True)
        except Exception as exc:
            if source_blocked_error(exc):
                state = record_source_block(db, "native_hfq_repair", exc)
                report = blocked_report(state, codes, args.start, args.end)
                report["network_attempts"] = 1
                write_json(args.output_dir / "progress.json", report)
                return 2
            raise
        for code in codes:
            state = db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}, {"_id": 0})
            if state:
                report.update(status="source_blocked_waiting", source_state=state)
                break
            raw = list(db.stock_day.find({"source": "baostock", "code": code, "date": {"$gte": args.start, "$lte": args.end}}, {"_id": 0}))
            try:
                if not raw:
                    raise ValueError("No same-source raw rows to verify the requested native HFQ window")
                panel = fetch_native_hfq_panel(bs, code, min(r["date"] for r in raw), max(r["date"] for r in raw))
                audit = audit_hfq_window(raw, panel)
                artifact = args.output_dir / "source_panels" / f"{code}.json"
                write_json(artifact, {"raw_dates": sorted(r["date"] for r in raw), "hfq": panel, "audit": audit})
                sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
                # Keep source suspensions/blanks in the source artifact. The
                # adjusted provider table contains prices verified for the raw
                # tradable rows only, so a suspended NaN cannot poison an
                # otherwise complete source price panel.
                raw_dates = {r["date"] for r in raw} - set(audit["raw_excluded_dates"])
                stored_panel = [r for r in panel if r["date"] in raw_dates and
                                r.get("trade_status") != "0" and r.get("valid_ohlc")]
                upsert_many(db.stock_adjusted_day, stored_panel, ["code", "date", "source"])
                receipt = {"dataset": "stock_adjusted_day", "code": code, "source": "baostock",
                    "status": audit["status"], "start": args.start, "through": args.end, "rows": len(stored_panel),
                    "source_response_rows": len(panel),
                    "adjustment": "source_supplied_hfq_prices", "evidence_scope": "native_hfq_price_window_relative",
                    "artifact_path": str(artifact.resolve()), "artifact_sha256": sha, "synced_at": utcnow(), **audit}
                db.panda_axis_sync.update_one({"dataset": "stock_adjusted_day", "code": code}, {"$set": receipt}, upsert=True)
                report["results"].append({"code": code, "rows": len(stored_panel), "source_response_rows": len(panel), **audit})
            except Exception as exc:
                failure = {"dataset": "stock_adjusted_day", "code": code, "source": "baostock", "rows": 0,
                           "status": "failed", "start": args.start, "through": args.end,
                           "synced_at": utcnow(), "error": f"{type(exc).__name__}: {exc}"}
                db.panda_axis_market_attempts.insert_one(dict(failure))
                previous = db.panda_axis_sync.find_one({"dataset": "stock_adjusted_day", "code": code}, {"_id": 0})
                preserved = receipt_after_failure(previous, failure)
                db.panda_axis_sync.update_one({"dataset": "stock_adjusted_day", "code": code}, {"$set": preserved}, upsert=True)
                report["results"].append({"code": code, "status": "failed", "error": failure["error"]})
                if source_blocked_error(exc):
                    state = record_source_block(db, "native_hfq_repair", exc)
                    report.update(status="source_blocked_waiting", source_state=state)
                    break
            write_json(args.output_dir / "progress.json", report)
        if report["status"] == "running":
            report["status"] = "relative_window_repaired_absolute_ipo_pending" if all(r["status"] == "complete" for r in report["results"]) else "relative_window_partial"
        report["completed_at"] = utcnow()
        write_json(args.output_dir / "progress.json", report)
        return 2 if report["status"] == "source_blocked_waiting" else int(any(r["status"] != "complete" for r in report["results"]))
    finally:
        if logged_in and not db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}):
            bs.logout()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
