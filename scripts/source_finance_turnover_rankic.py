"""Frozen source-only I/O for the finance/turnover RankIC diagnostic.

This file does not define an economic score, read prices/labels, register research,
or invoke PandaAI.  Root must freeze protocol.json before invoking any action.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Public source-code archive; the frozen private runtime and receipts are unchanged.
OUT = ROOT / "research_runs/finance_turnover_rankic_20261015"
OLD = ROOT / "research_runs/limit_turnover_margin_20261015"
PREV = ROOT / "research_runs/finance_conditional_exit_20261015"
CONT = ROOT / "research_runs/finance_continuous_exit_20261015"
BASE = ROOT / "research_runs/finance_domain_validation_20261015/credit_crowding"
META = ROOT / "research_runs/limit_reversal_20261014/source_metadata.json"
FEATURE_FIELDS = ("net_flow", "balance_change", "turnover_relative")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bind(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value, *, exclusive=False):
    path = Path(path)
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def identity(row):
    code, day = str(row["SCODE"]).zfill(6), str(row["DATE"])[:10]
    if len(code) != 6 or not code.isdigit() or len(day) != 10:
        raise ValueError("Invalid financing source identity")
    return code, day


def old_sources():
    """Source names are fixed; no directory scans or unplanned fallback inputs."""
    return [
        {"known": OLD / "finance_protocol.json", "key": "requested_keys", "rows": OLD / "finance_source/selected.json"},
        {"known": PREV / "diagnostic_source/plan.json", "key": "keys", "rows": PREV / "diagnostic_source/selected.json"},
        {"known": PREV / "account_source/selected.json", "key": "new_requested_keys", "rows": PREV / "account_source/selected.json"},
        {"known": CONT / "source/selected.json", "key": "new_requested_keys", "rows": CONT / "source/selected.json"},
        {"known": CONT / "late_source/selected.json", "key": "new_requested_keys", "rows": CONT / "late_source/selected.json"},
    ]


def feature_sources():
    return [PREV / "account_source/features.json", CONT / "source/features.json", CONT / "late_source/features.json"]


def check_bindings(bindings):
    for item in bindings:
        if sha(item["path"]) != item["sha256"]:
            raise RuntimeError("Frozen source binding changed: " + item["path"])


def specification():
    protocol_path = OUT / "protocol.json"
    if not protocol_path.exists():
        raise RuntimeError("Root protocol.json must exist before source planning")
    p = read(protocol_path)
    symbols = p["source_symbols"]
    dates = p["formation_dates"]
    universe = {str(code).zfill(6) for _, code in read(BASE / "values.json")["masks"]["control"]}
    expected = []
    for prefix in ("00", "60"):
        group = sorted(code for code in universe if code.startswith(prefix))
        if len(group) < 30:
            raise RuntimeError("Frozen original common domain has fewer than 30 identities for " + prefix)
        expected.extend(group[:30])
    if symbols != expected or len(set(symbols)) != 60:
        raise RuntimeError("source_symbols differs from original 00/60 dictionary-first 30+30 rule")
    cal = read(META)["calendar"]
    if len(cal) != len(set(cal)) or cal != sorted(cal):
        raise RuntimeError("Reference calendar is not unique and ordered")
    expected_dates = [d for d in cal if "2026-05-06" <= d <= "2026-08-28"]
    if dates != expected_dates or not dates:
        raise RuntimeError("formation_dates must include every retained reference date in the frozen window")
    index = {d: i for i, d in enumerate(cal)}
    roster = []
    for code in symbols:
        for day in dates:
            j = index[day]
            if j < 21:
                raise RuntimeError("Insufficient retained history")
            fd, td = cal[j - 7:j - 1], cal[j - 21:j - 1]
            if len(fd) != 6 or len(td) != 20 or fd[-1] != cal[j - 2] or td[-1] != fd[-1]:
                raise RuntimeError("Feature source clock violation")
            roster.append({"code": code, "date": day, "finance_dates": fd, "turnover_dates": td})
    return p, cal, roster


def old_inventory(*, amounts=False):
    known, rows, bindings = set(), {}, []
    for item in old_sources():
        meta = read(item["known"])
        keys = {tuple(k) for k in meta[item["key"]]}
        known.update(keys)
        data = meta if item["known"] == item["rows"] else read(item["rows"])
        for row in data["rows"]:
            key = identity(row)
            if key not in keys:
                raise RuntimeError("Old selected identity not covered by its completed requested keys")
            if amounts:
                candidate = {"SCODE": key[0], "DATE": key[1], **{k: row.get(k) for k in ("RZYE", "RZMRE", "RZCHE", "RZJME")}}
                if key in rows and rows[key] != candidate:
                    raise RuntimeError("Conflicting old financing values at identical key")
                rows[key] = candidate
            else:
                rows[key] = True
        bindings.extend([bind(item["known"]), bind(item["rows"])])
    # De-duplicate bindings while preserving the fixed inventory order.
    bindings = list({b["path"]: b for b in bindings}.values())
    return known, rows, bindings


def plan():
    if (OUT / "source_plan.json").exists():
        raise RuntimeError("Source plan already frozen; do not rebuild")
    _, _, roster = specification()
    known, existing, bindings = old_inventory(amounts=False)
    wanted = {(r["code"], d) for r in roster for d in r["finance_dates"]}
    delta = wanted - known
    buckets = defaultdict(list)
    for code, day in sorted(delta):
        buckets[code, day[:7]].append([code, day])
    requests = []
    for (code, month), keys in sorted(buckets.items()):
        if len(keys) > 500:
            raise RuntimeError("Single issuer/month exceeds one-page contract")
        token = hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()[:20]
        requests.append({"token": token, "code": code, "month": month, "keys": keys, "start": keys[0][1], "end": keys[-1][1]})
    if len(requests) > 600:
        raise RuntimeError("Frozen 600-request maximum would be exceeded")
    original = read(OLD / "finance_protocol.json")
    if original["endpoint"] != "https://datacenter-web.eastmoney.com/api/data/v1/get" or original["reportName"] != "RPTA_WEB_RZRQ_GGMX":
        raise RuntimeError("Original public source endpoint/report changed")
    turnover_protocol = read(OLD / "turnover_protocol.json")
    months = {d[:7] for row in roster for d in row["turnover_dates"]}
    turnover_sources = [bind(x["path"]) for x in turnover_protocol["sources"] if Path(x["path"]).name[:7] in months]
    if {Path(x["path"]).name[:7] for x in turnover_sources} != months:
        raise RuntimeError("One or more required retained turnover months are absent")
    p = {
        "schema": "finance-turnover-rankic-source-v1", "frozen_at": now(),
        "protocol": bind(OUT / "protocol.json"), "source_io": bind(__file__),
        "calendar": bind(META), "original_common_domain": bind(BASE / "values.json"),
        "formation_identities": len(roster), "roster": roster,
        "keys": [list(k) for k in sorted(wanted)], "new_keys": [list(k) for k in sorted(delta)],
        "old_reused_rows": len(wanted & existing.keys()), "old_known_absent": len((wanted & known) - existing.keys()),
        "requests": requests, "planned_new_requests": len(requests), "max_new_requests": 600,
        "endpoint": original["endpoint"], "reportName": original["reportName"], "columns": original["columns"],
        "page_size": 500, "max_pages": 1, "no_auto_retry": True,
        "finance_sources": bindings, "feature_sources": [bind(p) for p in feature_sources()],
        "turnover_protocol": bind(OLD / "turnover_protocol.json"), "turnover_sources": turnover_sources,
        "feature_helper": bind(PREV / "diagnose.py"),
        "unknown_policy": "Preserve old known absence and failed exact new keys as unknown; never fill zero or substitute identities.",
        "boundary": "Source-only archival observed-finance-scope on fixed 60 symbols. No quotes, returns, outcomes, factor score, registry, account API or official run. Neither historical PIT nor general full-A coverage is certified.",
        "official_cost": 0,
    }
    save(OUT / "source_plan.json", p, exclusive=True)
    return {k: p[k] for k in ("formation_identities", "old_reused_rows", "old_known_absent", "planned_new_requests", "max_new_requests")}


def frozen_plan():
    p = read(OUT / "source_plan.json")
    check_bindings([p[k] for k in ("protocol", "source_io", "calendar", "original_common_domain", "turnover_protocol", "feature_helper")])
    check_bindings(p["finance_sources"] + p["feature_sources"] + p["turnover_sources"])
    return p


def collect():
    """Root invokes this action after authorizing the frozen exact request plan."""
    import requests
    import time
    p = frozen_plan()
    if (OUT / "receipt.json").exists():
        raise RuntimeError("Completed source receipt exists; no recollection")
    folder = OUT / "source_requests"
    folder.mkdir(exist_ok=True)
    save(OUT / "source_collect_attempt.json", {"at": now(), "plan": bind(OUT / "source_plan.json")}, exclusive=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/rzrq/"})
    newrows, failures, receipts = {}, [], []
    for number, q in enumerate(p["requests"], 1):
        token = q["token"]
        target = set(map(tuple, q["keys"]))
        attempt = folder / (token + "_attempt.json")
        filt = '(SCODE in ("' + q["code"] + '"))' + "(DATE>='" + q["start"] + "')(DATE<='" + q["end"] + "')"
        params = {"reportName": p["reportName"], "columns": p["columns"], "source": "WEB", "sortColumns": "DATE,SCODE", "sortTypes": "1,1", "pageNumber": 1, "pageSize": 500, "filter": filt}
        save(attempt, {"at": now(), "keys": q["keys"], "params": params, "plan": bind(OUT / "source_plan.json")}, exclusive=True)
        response = None
        try:
            response = session.get(p["endpoint"], params=params, timeout=35)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result")
            if not payload.get("success"):
                if payload.get("message") == "返回数据为空":
                    result = {"pages": 0, "count": 0, "data": []}
                else:
                    raise ValueError(str(payload.get("message")))
            if not isinstance(result, dict) or int(result["pages"]) > 1 or len(result["data"]) != int(result["count"]):
                raise ValueError("Unverified pagination/count shape")
            seen, selected = set(), {}
            for row in result["data"]:
                key = identity(row)
                if key in seen or key[0] != q["code"] or not q["start"] <= key[1] <= q["end"]:
                    raise ValueError("Duplicate or out-of-range returned identity")
                seen.add(key)
                if key in target:
                    selected[key] = {"SCODE": key[0], "DATE": key[1], **{k: row.get(k) for k in ("RZYE", "RZMRE", "RZCHE", "RZJME")}}
            receipt = {"at": now(), "url": response.url, "response_sha256": hashlib.sha256(response.content).hexdigest(), "requested": len(target), "returned_in_range": len(seen), "selected_delta": len(selected), "ignored_non_delta": len(seen) - len(selected), "known_absent": len(target) - len(selected)}
            save(folder / (token + "_selected.json"), {"rows": [selected[k] for k in sorted(selected)]}, exclusive=True)
            save(folder / (token + "_receipt.json"), receipt, exclusive=True)
            receipts.append(receipt)
            newrows.update(selected)
        except Exception as exc:
            failure = {"at": now(), "token": token, "keys": q["keys"], "error": str(exc)[:800], "url": response.url if response is not None else None, "response_sha256": hashlib.sha256(response.content).hexdigest() if response is not None else None, "fallback": "Exact keys unknown; no retry"}
            save(folder / (token + "_failure.json"), failure, exclusive=True)
            failures.append(failure)
        save(OUT / "source_collect_checkpoint.json", {"completed_requests": number, "total_requests": len(p["requests"]), "failed_requests": len(failures), "new_selected_rows": len(newrows)})
        if number % 20 == 0:
            print(json.dumps({"source_requests_completed": number, "failed": len(failures)}), flush=True)
        time.sleep(0.1)
    session.close()
    save(OUT / "newselected.json", {"rows": [newrows[k] for k in sorted(newrows)], "new_requested_keys": p["new_keys"]}, exclusive=True)
    r = {"status": "COMPLETE" if not failures else "COMPLETE_WITH_FROZEN_UNKNOWN_FAILURES", "completed_at": now(), "plan": bind(OUT / "source_plan.json"), "newselected": bind(OUT / "newselected.json"), "new_requests": len(p["requests"]), "successful_requests": len(receipts), "failed_requests": len(failures), "new_requested_keys": len(p["new_keys"]), "new_rows": len(newrows), "new_absent_or_failed": len(p["new_keys"]) - len(newrows), "old_reused_rows": p["old_reused_rows"], "old_known_absent": p["old_known_absent"], "official_cost": 0}
    save(OUT / "receipt.json", r, exclusive=True)
    return r


def cached_features(roster, cal):
    wanted = {(r["code"], r["date"]): r for r in roster}
    out = {}
    for path in feature_sources():
        for row in read(path)["rows"]:
            key = str(row["code"]).zfill(6), row["date"]
            if key not in wanted:
                continue
            source = wanted[key]
            if row.get("latest_report_date") != source["finance_dates"][-1]:
                raise RuntimeError("Old successful feature clock differs from frozen source clock")
            values = {k: row.get(k) for k in FEATURE_FIELDS}
            # Only a previously successful pair bypasses derivation. Unknowns
            # remain subject to exact source validation, never turned into zero.
            if not all(finite(values[k]) for k in ("net_flow", "turnover_relative")):
                continue
            if key in out and any(out[key][k] != values[k] for k in FEATURE_FIELDS):
                raise RuntimeError("Conflicting successful cached feature identity")
            out[key] = {**values, "reused_from": str(path), "latest_report_date": row["latest_report_date"]}
    return out


def load_turnover_exact(keys, bindings):
    import pyarrow as pa
    import pyarrow.parquet as pq
    wanted, out = set(keys), {}
    by_month = defaultdict(set)
    for code, day in wanted:
        by_month[day[:7]].add((code, day))
    for src in bindings:
        path = Path(src["path"])
        month_keys = by_month.get(path.name[:7], set())
        if not month_keys:
            continue
        schema = pq.ParquetFile(path).schema_arrow
        codes = sorted({c for c, _ in month_keys})
        days = sorted({d for _, d in month_keys})
        code_type, date_type = schema.field("code").type, schema.field("date").type
        code_filter = [int(c) for c in codes] if pa.types.is_integer(code_type) else codes
        if pa.types.is_integer(date_type):
            date_filter = [int(d.replace("-", "")) for d in days]
        elif pa.types.is_string(date_type) or pa.types.is_large_string(date_type):
            # Retained schema has YYYYMMDD dates. No inferred new date role.
            date_filter = [d.replace("-", "") for d in days]
        else:
            raise RuntimeError("Unverified retained turnover date type")
        frame = pq.read_table(path, columns=["date", "code", "turnover"], filters=[("code", "in", code_filter), ("date", "in", date_filter)]).to_pandas()
        for row in frame.itertuples(index=False):
            code = str(row.code).zfill(6)
            rawday = str(row.date)
            day = rawday[:4] + "-" + rawday[4:6] + "-" + rawday[6:8]
            key = code, day
            if key not in month_keys:
                continue
            if key in out:
                raise RuntimeError("Duplicate retained turnover identity")
            value = float(row.turnover) if row.turnover is not None else None
            out[key] = value if finite(value) and value >= 0 else None
    return out


def derive():
    p = frozen_plan()
    if (OUT / "qualification_rows.json").exists():
        raise RuntimeError("Completed features exist; do not rederive")
    receipt = read(OUT / "receipt.json")
    if receipt["plan"] != bind(OUT / "source_plan.json") or receipt["newselected"] != bind(OUT / "newselected.json"):
        raise RuntimeError("Source completion binding mismatch")
    save(OUT / "source_derive_attempt.json", {"at": now(), "plan": bind(OUT / "source_plan.json"), "receipt": bind(OUT / "receipt.json")}, exclusive=True)
    cal = read(META)["calendar"]
    cached = cached_features(p["roster"], cal)
    known, fin, _ = old_inventory(amounts=True)
    new = read(OUT / "newselected.json")
    newkeys = set(map(tuple, new["new_requested_keys"]))
    if newkeys & known or newkeys != set(map(tuple, p["new_keys"])):
        raise RuntimeError("New source keys duplicate old consumption or differ from plan")
    for row in new["rows"]:
        key = identity(row)
        if key not in newkeys or key in fin:
            raise RuntimeError("New selected financing key violates exact delta")
        fin[key] = row
    required_turn = {(r["code"], d) for r in p["roster"] if (r["code"], r["date"]) not in cached for d in r["turnover_dates"]}
    turn = load_turnover_exact(required_turn, p["turnover_sources"])
    spec = importlib.util.spec_from_file_location("frozen_rankic_source_feature_helper", PREV / "diagnose.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    rows, literal, counts = [], {}, defaultdict(Counter)
    for item in p["roster"]:
        code, day = item["code"], item["date"]
        key = code, day
        reused = key in cached
        values = cached[key] if reused else helper.features(code, item["finance_dates"], item["turnover_dates"], fin, turn)
        cleaned = {k: float(values[k]) if finite(values.get(k)) else None for k in FEATURE_FIELDS}
        # Flow and turnover each require their exact validity contract; either
        # missing side leaves the pair unknown and absent from the literal.
        pair = cleaned["net_flow"] is not None and cleaned["turnover_relative"] is not None
        row = {"code": code, "date": day, **cleaned, "latest_report_date": item["finance_dates"][-1], "source_pair_qualified": pair, "reused_successful_feature": reused}
        rows.append(row)
        counts[day]["requested"] += 1
        counts[day]["finance_finite"] += cleaned["net_flow"] is not None
        counts[day]["turnover_finite"] += cleaned["turnover_relative"] is not None
        counts[day]["pair_qualified"] += pair
        counts[day]["reused_successful_feature"] += reused
        if pair:
            literal.setdefault(code, {})[day] = [cleaned["net_flow"], cleaned["turnover_relative"]]
    save(OUT / "source_features.json", {"schema": "source-features-full-requested-grid-v1", "rows": rows, "plan": bind(OUT / "source_plan.json"), "feature_helper": p["feature_helper"], "unknown_filled": False}, exclusive=True)
    save(OUT / "source_literal.json", literal, exclusive=True)
    legal_pairs = sum(len(dates) for dates in literal.values())
    q = {"status": "COMPLETE_SOURCE_FEATURES_ONLY", "completed_at": now(), "scope": "archival observed-finance-scope, fixed 60 symbols; not general full-A or historical PIT", "literal_schema": "code -> date -> [net_flow, turnover_relative], qualified pairs only", "requested_identities": len(rows), "legal_pair_rows": legal_pairs, "unknown_pair_rows": len(rows) - legal_pairs, "reused_successful_features": len(cached), "new_feature_identities_derived": len(rows) - len(cached), "exact_turnover_keys_consumed": len(required_turn), "exact_turnover_keys_present": len(turn), "per_date": [{"date": day, **dict(counts[day])} for day in sorted(counts)], "source_features": bind(OUT / "source_features.json"), "source_literal": bind(OUT / "source_literal.json"), "official_cost": 0, "new_economic_contexts": 0, "prices_labels_returns_read": False, "not_pit_certification": True}
    save(OUT / "qualification_rows.json", q, exclusive=True)
    return q


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "collect", "derive"))
    args = parser.parse_args()
    result = {"plan": plan, "collect": collect, "derive": derive}[args.action]()
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
