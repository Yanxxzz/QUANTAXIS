"""Exactly one public metadata POST, no retries or response-body retention."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.collect_announcement_metadata import exception_diagnostics


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--expected-protocol-sha", required=True)
    args = ap.parse_args()
    if sha(args.protocol) != args.expected_protocol_sha:
        raise ValueError("Diagnostic protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    for b in p["bindings"].values():
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("Diagnostic input/runtime changed: " + b["path"])
    if p["request"]["endpoint"] != "https://www.cninfo.com.cn/new/hisAnnouncement/query":
        raise ValueError("Only the exact root-frozen public metadata endpoint")
    out = args.protocol.parent / "diagnostic_receipt.json"
    if out.exists():
        raise ValueError("Single diagnostic already completed; do not repeat")
    dbp = Path(p["bindings"]["metadata_before"]["path"])
    db = sqlite3.connect(dbp)
    count = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    if count != p["HTTP_attempts_before"] or count >= p["shared_attempt_budget"]:
        raise ValueError("Shared source attempts changed or exhausted")
    next_seq = count + 1
    # The journal commit precedes the network call: a crash cannot silently
    # obtain another attempt or claim the first request never happened.
    db.execute("INSERT INTO pages VALUES(?,?,?,NULL,NULL)",
               (next_seq, "diagnostic:" + args.expected_protocol_sha, json.dumps(p["request"]["payload"], sort_keys=True)))
    db.commit()
    import requests
    session = requests.Session()
    tick = time.monotonic()
    result = {"protocol_sha256": args.expected_protocol_sha, "HTTP_attempt_before": count,
              "HTTP_attempt_seq": next_seq, "endpoint": p["request"]["endpoint"],
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "HTTP_status": None,
              "response_body_read_or_retained": False, "source_scope_or_API_completeness_certified": False}
    try:
        response = session.post(p["request"]["endpoint"], data=p["request"]["payload"],
                                timeout=p["request"]["timeout_seconds"], stream=True)
        result.update(HTTP_status=response.status_code, status="HEADERS_RECEIVED_METADATA_QUERY_NOT_ACCEPTED")
        response.close()
    except Exception as exc:
        result.update(status="CONNECTION_DIAGNOSTIC_PENDING", diagnostics=exception_diagnostics(exc))
    finally:
        session.close()
    result["elapsed_seconds"] = round(time.monotonic() - tick, 4)
    db.execute("UPDATE pages SET error=? WHERE seq=?", (json.dumps(result, ensure_ascii=False, sort_keys=True), next_seq))
    db.commit()
    result["unique_metadata_rows_unchanged"] = db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    db.close()
    result["metadata_after"] = {"path": str(dbp), "sha256": sha(dbp)}
    result.update(new_contexts=0, official_spent=0, no_retry=True, source_mask_applied=False)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
