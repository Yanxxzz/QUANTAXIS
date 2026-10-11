"""Consume a fixed unused filing cohort for expense/revenue facts, no scores."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.statement_source import read_bound_statement
from panda_alpha.research_spending import parse_current_annual_research_spending


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, required=True)
    cli.add_argument("--protocol-sha", required=True)
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print("DEFERRED_SOURCE_ONLY_NOT_CONSUMED")
        return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen source protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(p["deadline_utc"])
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError("human deadline reached")
    out = Path(p["output"])
    if out.exists():
        raise ValueError("receipt exists; no automatic source replay")
    for b in p["bindings"].values():
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("source/runtime binding changed")
    with sqlite3.connect(Path(p["registry"]).resolve().as_uri()+"?mode=ro", uri=True) as d:
        row = d.execute("SELECT payload FROM research_events WHERE event_id=? AND subject=?",
                        ("research-spending-source-freeze:"+args.protocol_sha, p["id"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("source-only freeze required")
    rows = []
    with sqlite3.connect(Path(p["captures"]).resolve().as_uri()+"?mode=ro", uri=True) as d:
        for case in p["selected"]:
            if datetime.now(timezone.utc) >= deadline:
                break
            result = {k: case[k] for k in ["symbol", "announcement_id", "report_date"]}
            try:
                text, stock, prov = read_bound_statement(d, case)
                result.update(parse_current_annual_research_spending(text, stock, prov),
                    published_at=stock.get("published_at"), stored_available_date=stock.get("available_date"),
                    comparison_change_barriers_preserved=stock.get("comparison_change_barriers"),
                    source_identity={k: case[k] for k in ["capture_sha256", "source_record_sha256", "text_path", "text_sha256"]})
            except Exception as error:
                result.update(status="pending", error_type=type(error).__name__, reason=str(error)[:384])
            rows.append(result)
    result = {"status": "RESEARCH_SPENDING_FIXED_SOURCE_UNIT_COMPLETED" if len(rows) == len(p["selected"])
              else "SOURCE_PENDING_DEADLINE_PARTIAL_RETAINED", "records": rows,
              "selected_count": len(p["selected"]), "consumed_count": len(rows),
              "counts": dict(Counter(r["status"] for r in rows)),
              "new_current_monetary_facts": sum(f.get("status") == "current_annual_amount_bound"
                  for r in rows for f in r.get("fields", {}).values()),
              "protocol_sha256": args.protocol_sha, "new_contexts": 0,
              "HTTP_PDF_price_labels_official_calls": 0, "financial_or_production_qualified": False,
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "limits": "Expensed R&D not total or capitalized R&D; current same-table annual monetary facts only, no live native period/value or complete policy/scope/version/PIT qualification"}
    with out.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        f.write("\n")
    print(json.dumps({k: result[k] for k in ["status", "counts", "new_current_monetary_facts", "new_contexts"]}, ensure_ascii=False))
    print("receipt_sha256", sha(out))


if __name__ == "__main__":
    main()
