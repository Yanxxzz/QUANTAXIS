#!/usr/bin/env python3
"""Read-only migration inventory. Record counts never certify historical PIT."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.data import AxisProvider
from panda_alpha.universe import window_lifecycles


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def audit(provider, start, end):
    """Inspect durable receipts and the real database without creating labels."""
    db = provider.db
    lifecycles = window_lifecycles(provider._records("stock_lifecycle"), start, end)
    raw = {r["code"]: r for r in provider._records("panda_axis_sync", {"dataset": "stock_day"})}
    factors = {r["code"]: r for r in provider._records("panda_axis_sync", {"dataset": "stock_adj"})}
    panels = {r["code"]: r for r in provider._records("panda_axis_sync", {"dataset": "stock_adjusted_day"})}
    completed, adjustment_ready, incomplete = [], [], []
    quality = Counter()
    for row in lifecycles:
        code = row["code"]
        receipt = raw.get(code, {})
        # Exchange metadata and vendor price data have independent provenance.
        price_source = "eastmoney" if row.get("sse") == "bj" else row.get("source")
        query_complete = (receipt.get("source") == price_source
                          and receipt.get("status") in {"complete", "partial"}
                          and receipt.get("start", "9999") <= start
                          and receipt.get("through", "") >= end
                          and receipt.get("scope", "").startswith("successful_requested_query"))
        if query_complete:
            completed.append(code)
            for field in ("source_missing_dates", "suspended_dates", "unknown_missing_dates",
                          "off_lifecycle_dates"):
                quality[field] += len(receipt.get(field, []))
            quality["duplicate_dates"] += receipt.get("duplicate_dates", 0)
        else:
            incomplete.append(code)
        factor, panel = factors.get(code, {}), panels.get(code, {})
        verified_factor = (factor.get("source") == price_source and factor.get("status") == "complete"
                           and factor.get("history_complete") is True and factor.get("through", "") >= end)
        verified_panel = (panel.get("source") == price_source and panel.get("status") == "complete"
                          and panel.get("start", "9999") <= start and panel.get("through", "") >= end)
        if query_complete and (verified_factor or verified_panel):
            adjustment_ready.append(code)
    collections = provider.catalog()["collections"]
    collections["stock_day_legacy_archive"] = db["stock_day_legacy_archive"].count_documents({})
    collections["panda_financial_provenance"] = db["panda_financial_provenance"].count_documents({})
    price_sources = list(db["stock_day"].aggregate([
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": "$source", "rows": {"$sum": 1}}}, {"$sort": {"_id": 1}}]))
    capabilities = {name: provider._capability(name, start, end)["status"]
                    for name in ("all_a", "delisted", "pit_financial", "minute")}
    blockers = [name + " independent acceptance pending" for name in ("all_a", "delisted", "pit_financial")
                if capabilities[name] != "verified"]
    if incomplete:
        blockers.append("source-supported lifecycle raw histories incomplete")
    if len(adjustment_ready) != len(lifecycles):
        blockers.append("same-source adjustment evidence incomplete")
    if any(quality[k] for k in ("source_missing_dates", "unknown_missing_dates", "off_lifecycle_dates", "duplicate_dates")):
        blockers.append("completed queries have unreconciled stock-day quality gaps")
    if not lifecycles:
        blockers.append("no lifecycle evidence")
    source_mix = Counter(r.get("source", "unknown") for r in lifecycles)
    payload = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "status": "transport_inventory_ready_for_acceptance" if not blockers else "partial_migration_checkpoint",
        "window": {"start": start, "end": end},
        "stored_collections": collections,
        "raw_price_rows_by_source_in_window": price_sources,
        "source_supported_lifecycles": {"count": len(lifecycles), "by_source": dict(source_mix),
                                        "definition_sha256": digest(lifecycles),
                                        "scope": "source-supported identities; independent exchange completeness pending"},
        "raw_queries": {"completed_codes": completed, "incomplete_codes": incomplete,
                        "completed_count": len(completed), "incomplete_count": len(incomplete),
                        "receipt_set_sha256": digest(sorted(raw.values(), key=lambda r: r["code"])),
                        "quality_counts": dict(quality)},
        "adjustment_ready_codes": adjustment_ready,
        "capabilities": capabilities,
        "migration": {"can_retire_legacy": False,
                      "blockers": blockers + ["inventory does not perform full-panel price/calendar/PIT acceptance"],
                      "acceptance_scope": "receipt and database inventory only; independent full-panel validation required",
                      "missing_values_imputed": False, "archival_legacy_is_active_fallback": False},
        "permissions": {"network_requests": 0, "data_mutations": 0, "sealed_oos_evaluations": 0,
                        "platform_calls": 0, "recharge_spend": 0},
    }
    payload["manifest_sha256"] = digest(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--start", default="2019-09-20")
    parser.add_argument("--end", default="2026-09-18")
    parser.add_argument("--output", type=Path, default=Path("research_runs/migration_acceptance.json"))
    args = parser.parse_args()
    result = audit(AxisProvider(args.uri, args.database), args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": result["status"], "collections": result["stored_collections"],
                      "completed_raw_queries": result["raw_queries"]["completed_count"],
                      "incomplete_raw_queries": result["raw_queries"]["incomplete_count"],
                      "adjustment_ready": len(result["adjustment_ready_codes"]),
                      "migration": result["migration"], "output": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
