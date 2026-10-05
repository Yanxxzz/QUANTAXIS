#!/usr/bin/env python3
"""Sync QA-supported TDX or BaoStock into QUANTAXIS Mongo schemas.

Examples:
  python scripts/axis_sync.py --codes 000001 600000 --start 2021-01-01 --end 2026-09-30
  python scripts/axis_sync.py --all-market --start 2021-01-01 --end 2026-09-30

The all-market task is current Shanghai/Shenzhen A shares. Delisted stocks,
historical membership, Beijing, financial PIT, and long minute history remain
separate acceptance tasks. A download receipt never marks these complete.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panda_alpha.data import normalize_code


# Verified against upstream QUANTAXIS/QAUtil/QASetting.py. A host can be supplied
# as host:port for the official 7711/7719/443 alternate endpoints.
DEFAULT_HOSTS = ["124.71.187.122:7709", "119.97.185.59:7709", "47.107.64.168:7709",
                 "124.70.75.113:7709", "124.71.9.153:7709", "123.60.84.66:7709",
                 "47.107.228.47:7719", "120.46.186.223:7709", "116.205.163.254:7709",
                 "113.105.73.88:7711", "114.80.80.222:7711", "119.147.171.206:443"]


def safe_disconnect(api):
    # pytdx raises "disconnect err" when connect failed before a socket existed.
    try:
        api.disconnect()
    except Exception:
        pass


def endpoint(value: str, default_port: int) -> tuple[str, int]:
    if ":" in value:
        host, port = value.rsplit(":", 1)
        return host, int(port)
    return value, default_port


def parse_security_list(body: bytes, decode_price) -> list[dict]:
    """pytdx 1.72 wire schema, tolerating only a final truncated GBK name byte.

    Security identifiers, numeric fields, and packet boundaries remain strict.
    A replacement character and raw hex preserve evidence of the name truncation.
    """
    if len(body) < 2:
        raise ValueError("Truncated TDX security-list count")
    count = struct.unpack("<H", body[:2])[0]
    if len(body) != 2 + 29 * count:
        raise ValueError("TDX security-list payload size disagrees with record count")
    records = []
    for i in range(count):
        values = struct.unpack("<6sH8s4sBI4s", body[2 + i * 29:2 + (i + 1) * 29])
        code_bytes, volunit, raw_name, _, decimal_point, raw_price, _ = values
        code = code_bytes.decode("ascii", errors="strict")
        if len(code) != 6 or not code.isdigit():
            raise ValueError("Corrupt TDX security code")
        payload = raw_name.rstrip(b"\x00")
        truncated = False
        try:
            name = payload.decode("gbk", errors="strict")
        except UnicodeDecodeError as exc:
            # Fixed eight bytes can cut a two-byte Chinese character at byte 7.
            if len(payload) != 8 or exc.start != 7 or exc.end != 8 or exc.reason != "incomplete multibyte sequence":
                raise
            name = payload.decode("gbk", errors="replace")
            truncated = True
        price = decode_price(raw_price)
        if not math.isfinite(price) or price < 0:
            raise ValueError("Corrupt TDX pre-close quote")
        records.append(OrderedDict(code=code, volunit=volunit, decimal_point=decimal_point,
                                   name=name, pre_close=price, name_raw_hex=raw_name.hex(),
                                   name_truncated=truncated))
    return records


def install_tdx_name_compat():
    # Scoped to this synchronization process; never edit the installed pytdx package.
    import pytdx.hq as hq
    from pytdx.helper import get_volume
    original = hq.GetSecurityList

    class StrictSecurityList(original):
        def parseResponse(self, body_buf):
            return parse_security_list(body_buf, get_volume)

    hq.GetSecurityList = StrictSecurityList


def market(code: str) -> int:
    if code.startswith("6"):
        return 1
    if code.startswith(("00", "30")):
        return 0
    raise ValueError(f"{code}: current SH/SZ A-share TDX endpoint only")


def date_string(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def qa_date_stamp(value: str) -> float:
    # Match QUANTAXIS.QAUtil.QADate.QA_util_date_stamp (local midnight).
    return datetime.strptime(value, "%Y-%m-%d").timestamp()


def fetch_bars(api, code: str, start: str, end: str, *, index: bool = False, max_pages: int = 40):
    """Page backwards until start or exhaustion; never assume requested history exists."""
    records, reached_start, exhausted = [], False, False
    latest_available = None
    fetch = api.get_index_bars if index else api.get_security_bars
    exchange = 1 if index else market(code)
    pages = 0
    for page in range(max_pages):
        batch = fetch(9, exchange, code, page * 800, 800)
        pages += 1
        if batch is None:
            raise RuntimeError(f"TDX returned None fetching bars for {code}")
        if not batch:
            exhausted = True
            break
        oldest = min(str(r["datetime"])[:10] for r in batch)
        latest = max(str(r["datetime"])[:10] for r in batch)
        latest_available = max(latest_available or latest, latest)
        for row in batch:
            date = str(row["datetime"])[:10]
            if start <= date <= end:
                records.append({"code": code, "date": date, "date_stamp": qa_date_stamp(date),
                                **{k: row[k] for k in ["open", "high", "low", "close", "vol", "amount"]}})
        if oldest <= start:
            reached_start = True
            break
        if len(batch) < 800:
            exhausted = True
            break
    unique = {r["date"]: r for r in records}
    records = [unique[d] for d in sorted(unique)]
    return records, {"pages": pages, "reached_start": reached_start, "exhausted": exhausted,
                     "truncated": not reached_start and not exhausted,
                     "latest_available": latest_available,
                     "first_date": records[0]["date"] if records else None,
                     "last_date": records[-1]["date"] if records else None}


def fetch_stock_list(api):
    records = []
    for exchange in [0, 1]:
        count = api.get_security_count(exchange)
        if not count:
            raise RuntimeError(f"TDX security count unavailable for market {exchange}")
        for offset in range(0, count, 1000):
            batch = api.get_security_list(exchange, offset)
            if batch is None or not batch:
                raise RuntimeError(f"TDX security list incomplete at {exchange}/{offset}")
            for row in batch:
                code = normalize_code(row["code"])
                if (exchange == 1 and code.startswith("6")) or (exchange == 0 and code.startswith(("00", "30"))):
                    records.append({**row, "code": code, "sse": "sh" if exchange else "sz",
                                    "source": "tdx_current_shsz", "membership_pit": False})
    return records


def fetch_xdxr(api, code: str):
    batch = api.get_xdxr_info(market(code), code)
    if batch is None:
        raise RuntimeError(f"TDX returned None fetching xdxr for {code}")
    rename = {"panhouliutong": "liquidity_after", "panqianliutong": "liquidity_before",
              "houzongguben": "shares_after", "qianzongguben": "shares_before"}
    out = []
    for row in batch:
        date = f"{row['year']:04d}-{row['month']:02d}-{row['day']:02d}"
        out.append({"code": code, "date": date,
                    **{rename.get(k, k): v for k, v in row.items() if k not in {"year", "month", "day"}}})
    return out


def upsert_many(collection, records: list[dict], keys: list[str]) -> int:
    if not records:
        return 0
    from pymongo import UpdateOne
    collection.bulk_write([UpdateOne({k: r[k] for k in keys}, {"$set": r}, upsert=True) for r in records], ordered=False)
    return len(records)


def merge_calendar_receipt(previous: dict | None, receipt: dict) -> dict:
    """Preserve complete same-source overlapping calendar query evidence only."""
    if not previous or receipt["dataset"] != "trade_calendar" or receipt["status"] != "complete":
        return receipt
    if previous.get("status") != "complete" or previous.get("source") != receipt["source"]:
        return receipt
    old_start, old_end = previous.get("start"), previous.get("through")
    if old_start and old_end and receipt["start"] <= old_end and old_start <= receipt["through"]:
        return {**receipt, "start": min(old_start, receipt["start"]),
                "through": max(old_end, receipt["through"]), "scope": "complete_overlapping_same_source_calendar_queries",
                "retained_previous_query": {"start": old_start, "through": old_end},
                "latest_query": {"start": receipt["start"], "through": receipt["through"]}}
    return receipt


def baostock_code(code: str) -> str:
    return ("sh." if market(code) else "sz.") + code


def baostock_records(result) -> list[dict]:
    """Require successful protocol completion, including errors during iteration."""
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock {result.error_code}: {result.error_msg}")
    rows = []
    while result.next():
        row = result.get_row_data()
        if len(row) != len(result.fields):
            raise ValueError("BaoStock row length disagrees with field schema")
        rows.append(dict(zip(result.fields, row)))
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock interrupted query {result.error_code}: {result.error_msg}")
    return rows


def fetch_baostock_bars(bs, code: str, start: str, end: str) -> list[dict]:
    rows = baostock_records(bs.query_history_k_data_plus(baostock_code(code),
        "date,code,open,high,low,close,volume,amount,tradestatus,isST",
        start_date=start, end_date=end, frequency="d", adjustflag="3"))
    out = []
    for row in rows:
        date = date_string(row["date"])
        if row["code"] != baostock_code(code) or not start <= date <= end:
            raise ValueError("BaoStock returned a mismatched code or out-of-range date")
        # BaoStock volume is shares; QA/TDX stock_day.vol is lots of 100 shares.
        numeric_fields = ["open", "high", "low", "close", "volume", "amount"]
        missing_fields = [k for k in numeric_fields if not row[k].strip()]
        numbers = {k: None if k in missing_fields else float(row[k]) for k in numeric_fields}
        if not all(v is None or math.isfinite(v) for v in numbers.values()):
            raise ValueError("BaoStock raw bar has non-finite numbers")
        out.append({"code": code, "date": date, "date_stamp": qa_date_stamp(date),
                    **{k: numbers[k] for k in ["open", "high", "low", "close", "amount"]},
                    "vol": numbers["volume"] / 100 if numbers["volume"] is not None else None, "volume_shares": numbers["volume"],
                    "volume_unit": "lots_100_shares", "amount_unit": "CNY", "source": "baostock",
                    "raw_adjustflag": "3", "trade_status": row["tradestatus"], "is_st": row["isST"],
                    "missing_numeric_fields": missing_fields,
                    "data_status": "suspended" if row["tradestatus"] == "0" else "unknown_missing" if missing_fields else "priced_tradable"})
    if len({r["date"] for r in out}) != len(out):
        raise ValueError("BaoStock returned duplicate raw bar dates")
    return out


def fetch_baostock_factors(bs, code: str, end: str, metadata: dict):
    # SDK default is only 2015 onward: explicitly request all source history.
    origin = "1990-01-01"
    rows = baostock_records(bs.query_adjust_factor(baostock_code(code), start_date=origin, end_date=end))
    out = []
    for row in rows:
        date = date_string(row["dividOperateDate"])
        if row["code"] != baostock_code(code) or not origin <= date <= end:
            raise ValueError("BaoStock returned a mismatched adjustment factor")
        values = {k: float(row[k]) for k in ["backAdjustFactor", "foreAdjustFactor", "adjustFactor"]}
        if not all(math.isfinite(v) and v > 0 for v in values.values()):
            raise ValueError("BaoStock adjustment factor is nonpositive or nonfinite")
        out.append({"code": code, "date": date, "factor_date": date,
                    "adj": values["backAdjustFactor"], "fore_adj": values["foreAdjustFactor"],
                    "source_adjust_factor": values["adjustFactor"], "kind": "cumulative_hfq_ipo",
                    "source": "baostock", "observed_through": end})
    out.sort(key=lambda r: r["date"])
    if len({r["date"] for r in out}) != len(out):
        raise ValueError("BaoStock returned duplicate adjustment factor dates")
    ipo = metadata.get("ipo_date")
    # Require the actual IPO baseline; an empty factor response is not proof.
    history_complete = bool(ipo and ipo >= origin and out and out[0]["date"] == ipo and out[0]["adj"] == 1)
    return out, {"history_complete": history_complete, "factor_origin": origin,
                 "ipo_date": ipo, "factor_method": "cumulative_hfq_ipo; qfq rebased at sample_end"}


def fetch_baostock_basic(bs, code: str | None = None):
    rows = baostock_records(bs.query_stock_basic(**({"code": baostock_code(code)} if code else {})))
    out = []
    for row in rows:
        raw = row["code"]
        if not raw.startswith(("sh.", "sz.")):
            continue
        normalized = normalize_code(raw[3:])
        if not normalized.startswith(("6", "00", "30")) or row["type"] != "1":
            continue
        out.append({"code": normalized, "name": row["code_name"], "sse": raw[:2],
                    "ipo_date": row["ipoDate"] or None, "delisted_date": row["outDate"] or None,
                    "listing_status": row["status"], "security_type": row["type"],
                    "source": "baostock", "membership_pit": False})
    return out


def fetch_baostock_calendar(bs, start: str, end: str):
    rows = baostock_records(bs.query_trade_dates(start_date=start, end_date=end))
    from datetime import timedelta
    expected = set()
    day = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while day <= last:
        expected.add(day.isoformat())
        day += timedelta(days=1)
    received = [date_string(r["calendar_date"]) for r in rows]
    if set(received) != expected or len(received) != len(expected) or any(r["is_trading_day"] not in {"0", "1"} for r in rows):
        raise ValueError("BaoStock trade-date query did not cover every calendar date exactly once")
    return [{"date": r["calendar_date"], "exchange": "SSE", "source": "baostock_trade_dates"}
            for r in rows if r["is_trading_day"] == "1"]


def sync_baostock(args) -> int:
    import baostock as bs
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    report = {"source": "BaoStock (QUANTAXIS-supported) -> QA-compatible Mongo schemas",
              "start": args.start, "end": args.end, "all_market": args.all_market,
              "tasks": args.tasks, "success": [], "failures": [],
              "pending": ["historical_all_a_membership", "beijing", "delisted_coverage", "minute", "pit_financial"],
              "units": {"vol": "lots_100_shares", "volume_shares": "raw_shares", "amount": "CNY"}}
    def checkpoint():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    def receipt(dataset, code, rows, status="complete", **extra):
        doc = {"dataset": dataset, "code": code, "status": status, "start": args.start,
               "through": args.end, "rows": rows, "source": "baostock",
               "synced_at": datetime.now(timezone.utc).isoformat(), **extra}
        if dataset == "trade_calendar":
            previous = db.panda_axis_sync.find_one({"dataset": dataset, "code": code}, {"_id": 0})
            doc = merge_calendar_receipt(previous, doc)
        db.panda_axis_sync.update_one({"dataset": dataset, "code": code}, {"$set": doc}, upsert=True)
        report["success"].append(doc)
        checkpoint()
    def failure(dataset, code, exc):
        doc = {"dataset": dataset, "code": code, "status": "failed", "error": str(exc),
               "source": "baostock", "start": args.start, "through": args.end}
        db.panda_axis_sync.update_one({"dataset": dataset, "code": code}, {"$set": doc}, upsert=True)
        report["failures"].append(doc)
        checkpoint()
    logged_in = False
    stage = "login"
    try:
        login = bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError(f"BaoStock login {login.error_code}: {login.error_msg}")
        logged_in = True
        for collection, keys in [(db.stock_day, [("code", 1), ("date_stamp", 1)]),
                (db.stock_adj, [("code", 1), ("date", 1), ("source", 1)]),
                (db.stock_list, [("code", 1)]), (db.trade_calendar, [("exchange", 1), ("date", 1)]),
                (db.panda_axis_sync, [("dataset", 1), ("code", 1)])]:
            collection.create_index(keys, unique=True)
        stage = "stock_list"
        requested = None if args.all_market else sorted({normalize_code(c) for item in args.codes for c in item.split(",")})
        listed = fetch_baostock_basic(bs) if args.all_market else []
        if requested is not None:
            for code in requested:
                listed.extend(fetch_baostock_basic(bs, code))
        metadata = {r["code"]: r for r in listed}
        codes = sorted(r["code"] for r in listed if r["listing_status"] == "1") if args.all_market else requested
        if "stock_list" in args.tasks:
            upsert_many(db.stock_list, listed, ["code"])
            receipt("stock_list", "SHSZ" if args.all_market else "SAMPLE", len(listed),
                    scope="source_current_metadata_not_historical_universe", observed_asof=datetime.now(timezone.utc).date().isoformat())
        if "calendar" in args.tasks:
            stage = "calendar"
            dates = fetch_baostock_calendar(bs, args.start, args.end)
            upsert_many(db.trade_calendar, dates, ["exchange", "date"])
            receipt("trade_calendar", "SSE", len(dates), scope="independent_calendar")
        for code in codes:
            for dataset in args.tasks:
                if dataset not in {"stock_day", "stock_adj"}:
                    continue
                try:
                    if dataset == "stock_day":
                        rows = fetch_baostock_bars(bs, code, args.start, args.end)
                        upsert_many(db.stock_day, rows, ["code", "date_stamp"])
                        excluded = [{"date": r["date"], "trade_status": r["trade_status"],
                                     "missing_numeric_fields": r["missing_numeric_fields"], "data_status": r["data_status"]}
                                    for r in rows if r["data_status"] != "priced_tradable"]
                        receipt(dataset, code, len(rows), status="complete" if rows and not excluded else "partial",
                                first_date=rows[0]["date"] if rows else None, last_date=rows[-1]["date"] if rows else None,
                                priced_tradable_rows=len(rows) - len(excluded), excluded_from_normalized=excluded,
                                scope="successful_requested_query; calendar_coverage_requires_acceptance")
                    else:
                        rows, evidence = fetch_baostock_factors(bs, code, args.end, metadata.get(code, {}))
                        upsert_many(db.stock_adj, rows, ["code", "date", "source"])
                        receipt(dataset, code, len(rows), status="complete" if evidence["history_complete"] else "partial", **evidence)
                except Exception as exc:
                    failure(dataset, code, exc)
            print(json.dumps({"code": code, "completed": len(report["success"]), "failures": len(report["failures"])}), flush=True)
    except Exception as exc:
        report["failures"].append({"dataset": stage, "error": str(exc)})
    finally:
        if logged_in:
            bs.logout()
        client.close()
    partial = [r for r in report["success"] if r["status"] != "complete"]
    report["partial_tasks"] = [{"dataset": r["dataset"], "code": r["code"]} for r in partial]
    report["status"] = "failed" if report["failures"] else "partial_sync_requires_acceptance" if partial else "synced_scope_requires_acceptance"
    report["migration"] = {"can_retire_legacy": False, "reason": "Independent scoped coverage/PIT/delisted acceptance has not completed"}
    checkpoint()
    print(json.dumps({"report": str(args.report.resolve()), "status": report["status"], "pending": report["pending"]}, ensure_ascii=False))
    return 1 if report["failures"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--codes", nargs="+", help="sample codes, separated by spaces or commas")
    selection.add_argument("--all-market", action="store_true", help="current Shanghai/Shenzhen A shares")
    parser.add_argument("--start", required=True, type=date_string)
    parser.add_argument("--end", required=True, type=date_string)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--source", choices=["tdx", "baostock"], default="baostock",
                        help="QA-supported source; BaoStock is the currently verified daily path (default)")
    parser.add_argument("--host", action="append", help="TDX host or host:port; repeat for failover")
    parser.add_argument("--port", type=int, default=7709)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--tasks", nargs="+", choices=["stock_day", "stock_xdxr", "stock_adj", "stock_list", "calendar"])
    parser.add_argument("--report", type=Path, default=Path("research/axis_sync_report.json"))
    args = parser.parse_args(argv)
    if args.start > args.end or args.max_pages < 1:
        parser.error("start must not exceed end and max-pages must be positive")
    args.tasks = args.tasks or (["stock_day", "stock_adj", "stock_list", "calendar"] if args.source == "baostock"
                               else ["stock_day", "stock_xdxr", "stock_list", "calendar"])
    if args.source == "baostock":
        if "stock_xdxr" in args.tasks:
            parser.error("BaoStock supplies stock_adj factors; stock_xdxr is the TDX-only event feed")
        if "stock_day" in args.tasks and "stock_adj" not in args.tasks:
            args.tasks.append("stock_adj")
        return sync_baostock(args)
    if "stock_adj" in args.tasks:
        parser.error("TDX supplies stock_xdxr events; stock_adj is the BaoStock factor feed")
    try:
        from pytdx.hq import TdxHq_API
        from pymongo import MongoClient
    except ImportError as exc:
        parser.error(f"Install minimal data dependencies first: pandas numpy pymongo pytdx ({exc})")
    install_tdx_name_compat()
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    api, selected_host = None, None
    errors = []
    for supplied in args.host or DEFAULT_HOSTS:
        host, port = endpoint(supplied, args.port)
        candidate = TdxHq_API(raise_exception=True)
        try:
            if candidate.connect(host, port, time_out=4):
                if not candidate.get_security_count(0):
                    raise RuntimeError("TDX connection handshake succeeded but security-count probe failed")
                if "stock_day" in args.tasks:
                    if not candidate.get_security_bars(9, 0, "000001", 0, 1):
                        raise RuntimeError("TDX metadata available but day-bar probe failed")
                if "calendar" in args.tasks:
                    if not candidate.get_index_bars(9, 1, "000001", 0, 1):
                        raise RuntimeError("TDX metadata available but index-calendar probe failed")
                api, selected_host = candidate, f"{host}:{port}"
                break
            errors.append(f"{host}:{port}: connection unavailable")
        except Exception as exc:
            errors.append(f"{host}:{port}: {type(exc).__name__}")
        safe_disconnect(candidate)
    if api is None:
        report = {"status": "source_unreachable", "source": "TDX", "start": args.start, "end": args.end,
                  "connection_failures": errors, "rows_downloaded": 0,
                  "migration": {"can_retire_legacy": False, "reason": "No real source download completed"}}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        client.close()
        print(json.dumps({"report": str(args.report.resolve()), "status": report["status"], "connection_failures": errors}, ensure_ascii=False))
        return 1
    report = {"source": "TDX -> QUANTAXIS Mongo schemas", "host": selected_host,
              "start": args.start, "end": args.end, "all_market": args.all_market,
              "tasks": args.tasks, "success": [], "failures": [],
              "pending": ["historical_all_a_membership", "beijing", "delisted", "minute", "pit_financial"]}

    def receipt(dataset, code, rows, status="complete", **extra):
        doc = {"dataset": dataset, "code": code, "status": status, "start": args.start,
               "through": args.end, "rows": rows, "source": "tdx", "host": selected_host,
               "synced_at": datetime.now(timezone.utc).isoformat(), **extra}
        db.panda_axis_sync.update_one({"dataset": dataset, "code": code}, {"$set": doc}, upsert=True)
        report["success"].append(doc)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    stage = "indexes"
    try:
        db.stock_day.create_index([("code", 1), ("date_stamp", 1)], unique=True)
        db.stock_xdxr.create_index([("code", 1), ("date", 1)], unique=True)
        db.stock_list.create_index([("code", 1)], unique=True)
        db.trade_calendar.create_index([("exchange", 1), ("date", 1)], unique=True)
        db.panda_axis_sync.create_index([("dataset", 1), ("code", 1)], unique=True)
        if args.all_market or "stock_list" in args.tasks:
            stage = "stock_list"
            listed = fetch_stock_list(api)
            if "stock_list" in args.tasks:
                upsert_many(db.stock_list, listed, ["code"])
                receipt("stock_list", "SHSZ", len(listed), scope="current_shsz_only",
                        truncated_names=sum(bool(r.get("name_truncated")) for r in listed))
        else:
            listed = []
        codes = [r["code"] for r in listed] if args.all_market else [normalize_code(c) for item in args.codes for c in item.split(",")]
        codes = sorted(set(codes))
        if "calendar" in args.tasks:
            stage = "calendar"
            bars, span = fetch_bars(api, "000001", args.start, args.end, index=True, max_pages=args.max_pages)
            dates = [{"date": r["date"], "exchange": "SSE", "source": "tdx_sse_composite"} for r in bars]
            upsert_many(db.trade_calendar, dates, ["exchange", "date"])
            covered = span["reached_start"] and dates and span["latest_available"] >= args.end
            receipt("trade_calendar", "SSE", len(dates), status="complete" if covered else "partial", **span)
        for code in codes:
            for dataset in args.tasks:
                if dataset not in {"stock_day", "stock_xdxr"}:
                    continue
                try:
                    stage = dataset
                    if dataset == "stock_day":
                        bars, span = fetch_bars(api, code, args.start, args.end, max_pages=args.max_pages)
                        upsert_many(db.stock_day, bars, ["code", "date_stamp"])
                        receipt(dataset, code, len(bars), status="partial" if span["truncated"] else "complete", **span)
                    else:
                        actions = fetch_xdxr(api, code)
                        if not actions and db.stock_xdxr.count_documents({"code": code}):
                            raise RuntimeError("Empty xdxr response conflicts with stored actions; reconcile source")
                        upsert_many(db.stock_xdxr, actions, ["code", "date"])
                        receipt(dataset, code, len(actions))
                except Exception as exc:
                    db.panda_axis_sync.update_one({"dataset": dataset, "code": code},
                        {"$set": {"status": "failed", "error": str(exc), "through": args.end}}, upsert=True)
                    report["failures"].append({"dataset": dataset, "code": code, "error": str(exc)})
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"code": code, "completed": len(report["success"]), "failures": len(report["failures"])}, ensure_ascii=False), flush=True)
    except Exception as exc:
        report["failures"].append({"dataset": stage, "error": str(exc)})
    finally:
        safe_disconnect(api)
        client.close()
    partial = [r for r in report["success"] if r["status"] != "complete"]
    report["partial_tasks"] = [{"dataset": r["dataset"], "code": r["code"]} for r in partial]
    report["status"] = "failed" if report["failures"] else "partial_sync_requires_acceptance" if partial else "synced_scope_requires_acceptance"
    report["migration"] = {"can_retire_legacy": False, "reason": "Independent scoped coverage/PIT/delisted acceptance has not completed"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report.resolve()), "status": report["status"], "pending": report["pending"]}, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
