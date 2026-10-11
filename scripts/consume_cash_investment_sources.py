"""Consume a frozen unused source cohort; reuse monetary parser, never labels."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.cash_investment import parse_current_annual_investment_cash


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def consume_case(db, case):
    """Verify capture identity and retained text before binding current amounts."""
    result = {k: case[k] for k in ("symbol", "announcement_id", "report_date")}
    try:
        row = db.execute(
            "SELECT period,original_json_gzip,source_sha256 FROM captures WHERE code=? AND annid=?",
            (case["symbol"], case["announcement_id"]),
        ).fetchone()
        if (not row or row[0] != case["report_date"] or row[2] != case["capture_sha256"]
                or hashlib.sha256(row[1]).hexdigest() != row[2]):
            raise ValueError("exact captured identity/blob changed or absent")
        stock = json.loads(gzip.decompress(row[1]))["stock"]
        if (str(stock["code"]) != case["symbol"]
                or str(stock["announcement_id"]) != case["announcement_id"]
                or stock["report_date"] != case["report_date"]
                or stock["record_sha256"] != case["source_record_sha256"]):
            raise ValueError("stock identity or record binding changed")
        prov = dict(stock["provenance"])
        textpath = Path(case["text_path"])
        if (prov["text_sha256"] != case["text_sha256"]
                or Path(prov["text_path"]).resolve() != textpath.resolve()):
            raise ValueError("retained text identity changed")
        compressed = textpath.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != case["text_sha256"]:
            raise ValueError("retained text gzip changed")
        content = gzip.decompress(compressed)
        prov.update(text_hash_reverified=True,
                    text_content_sha256=hashlib.sha256(content).hexdigest(),
                    pdf_hash_reverified=False)
        result.update(
            parse_current_annual_investment_cash(content.decode("utf-8"), stock, prov),
            capture_sha256=row[2], stock_record_sha256=stock["record_sha256"],
            text_path=str(textpath), text_sha256=case["text_sha256"],
            published_at=stock.get("published_at"),
            stored_available_date=stock.get("available_date"),
            comparison_change_barriers_preserved=stock.get("comparison_change_barriers"),
            monetary_source_facts_only=True, financial_or_production_qualified=False,
        )
    except Exception as error:
        result.update(status="pending", error_type=type(error).__name__, reason=str(error)[:384])
    return result


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, required=True)
    cli.add_argument("--protocol-sha", required=True)
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print("DEFERRED_NO_SOURCE_CONSUMPTION")
        return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(p["deadline_utc"])
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError("human deadline reached")
    out = Path(p["output"])
    if out.exists():
        raise ValueError("receipt exists; no automatic replay")
    for binding in p["bindings"].values():
        if sha(binding["path"]) != binding["sha256"]:
            raise ValueError("source/runtime binding changed")
    selected = p["selected"]
    if len({c["symbol"] for c in selected}) != len(selected):
        raise ValueError("cohort contains duplicate company identity")
    excluded = {tuple(c) for c in p["excluded_identities"]}
    if any((c["symbol"], c["announcement_id"]) in excluded for c in selected):
        raise ValueError("completed source identity selected again")
    with sqlite3.connect(Path(p["registry"]).resolve().as_uri()+"?mode=ro", uri=True) as d:
        row = d.execute("SELECT payload FROM research_events WHERE event_id=? AND subject=?",
                        ("cash-investment-source-freeze:"+args.protocol_sha, p["id"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("source-only registration required")
    records = []
    with sqlite3.connect(Path(p["captures"]).resolve().as_uri()+"?mode=ro", uri=True) as d:
        for case in selected:
            if datetime.now(timezone.utc) >= deadline:
                break
            records.append(consume_case(d, case))
    result = {
        "status": "FIXED_CASH_INVESTMENT_SOURCE_COMPLETED" if len(records) == len(selected)
                  else "SOURCE_PENDING_DEADLINE_PARTIAL_RETAINED",
        "selected_count": len(selected), "consumed_count": len(records), "records": records,
        "counts": dict(Counter(r["status"] for r in records)),
        "new_current_amount_facts": sum(f.get("status") == "current_annual_amount_bound"
            for r in records for f in r.get("fields", {}).values()),
        "protocol_sha256": args.protocol_sha,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "new_contexts": 0, "price_or_return_labels_read": False,
        "HTTP_or_PDF_calls": 0, "official_calls": 0, "production_qualified": False,
        "limits": "Current same-table monetary facts; policy, scope, versions, full PIT and native input parity remain unqualified",
    }
    with out.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "selected_count", "consumed_count", "counts", "new_current_amount_facts")}, ensure_ascii=False))
    print("receipt_sha256", sha(out))


if __name__ == "__main__":
    main()
