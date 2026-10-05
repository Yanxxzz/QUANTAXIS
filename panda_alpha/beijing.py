"""BSE identities and same-vendor raw/adjusted daily bars.

The exchange's code mapping includes old NEEQ Select listing dates. Those
dates must not make a security a BSE A-share before the exchange opened.
Current quote lists alone do not certify a historical delisted universe.
"""
from __future__ import annotations

import html
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .data import normalize_code

BSE_OPEN = "2021-11-15"
MAPPING_URL = "https://www.bseinfo.net/service/code_mapping.html"
DIRECTORY_URL = "https://www.bse.cn/nqxxController/nqxxCnzq.do"
LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def response_cache(session, url, params, path: Path, *, method="GET") -> tuple[bytes, dict]:
    """Cache exact source bytes and query identity; never cache an HTTP denial.

    Network retries belong to the bounded caller. A successful cached response
    is independently verified before parsing and contains no session headers.
    """
    path = Path(path)
    metadata_path = path.with_suffix(path.suffix + ".json")
    query = {"url": url, "method": method, "params": params}
    fingerprint = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
    if path.exists() and metadata_path.exists():
        raw = path.read_bytes()
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        if meta.get("query_sha256") != fingerprint or meta.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("Source response cache query/hash mismatch")
        return raw, meta
    if method == "POST":
        response = session.post(url, data=params, timeout=30)
    else:
        response = session.get(url + "?" + urlencode(params, safe=":,+"), timeout=30)
    response.raise_for_status()
    raw = response.content
    meta = {**query, "query_sha256": fingerprint, "sha256": hashlib.sha256(raw).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(), "path": str(path.resolve())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    metadata_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw, meta


def parse_official_directory(raw: bytes | str) -> dict:
    """Strict JSON/JSONP parser; an exchange snapshot is not a historical pool."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    raw = raw.strip()
    wrapped = re.fullmatch(r"(?:null|[A-Za-z_$][\w.$]*)\s*\((.*)\)\s*;?", raw, re.S)
    payload = json.loads(wrapped.group(1) if wrapped else raw)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ValueError("Unexpected official BSE directory response")
    page = payload[0]
    for key in ("number", "numberOfElements", "size", "totalElements", "totalPages"):
        if not isinstance(page.get(key), int) or isinstance(page[key], bool):
            raise ValueError("Missing official BSE paging denominator")
    rows = page.get("content")
    if (not isinstance(rows, list) or not rows or page["numberOfElements"] != len(rows)
            or not 0 < page["totalElements"] <= 5000 or not 0 < page["size"] <= 500
            or page["totalPages"] != math.ceil(page["totalElements"] / page["size"])):
        raise ValueError("Incomplete official BSE directory page")
    return page


def _directory_record(row: dict, source: dict) -> dict:
    code = normalize_code(row["xxzqdm"])
    name = row.get("xxzqjc")
    if not code.startswith("920") or not isinstance(name, str) or not name.strip() or "\ufffd" in name:
        raise ValueError("Unexpected BSE current identity or damaged source name")
    if row.get("xxfcbj") != "2" or row.get("xxzqjb") != "T":
        raise ValueError("Official directory returned a different market scope")
    listed = datetime.strptime(row["fxssrq"], "%Y%m%d").date().isoformat()
    snapshot = datetime.strptime(row["xxjsrq"], "%Y%m%d").date().isoformat()
    if listed > snapshot:
        raise ValueError("Official IPO date is after the source snapshot")
    return {"code": code, "name": name, "select_or_ipo_date": listed, "ipo_date": max(BSE_OPEN, listed),
            "exchange": "BJ", "source": "bse_official_current_directory", "source_url": DIRECTORY_URL,
            "source_snapshot_date": snapshot, "source_sha256": source["sha256"],
            "source_path": source["path"], "source_observed_at": source["observed_at"],
            "current_suspension_marker": row.get("xxtpbz"), "current_trading_status_marker": row.get("xxzrzt"),
            "current_directory_present": True, "source_name_damaged": False, "historical_universe_complete": False}


def official_current_list(session, cache_dir: Path, *, progress=None) -> tuple[list[dict], dict]:
    records, pages, denominator = {}, [], None
    base = {"typejb": "T", "xxfcbj[]": 2, "xxzqdm": "", "sortfield": "xxzqdm", "sorttype": "asc"}
    for number in range(100):
        raw, source = response_cache(session, DIRECTORY_URL, {"page": number, **base},
                                     Path(cache_dir) / f"page_{number:05d}.response", method="POST")
        page = parse_official_directory(raw)
        current = (page["totalElements"], page["totalPages"], page["size"])
        if page["number"] != number or denominator not in (None, current):
            raise ValueError("Official BSE directory changed its paging denominator")
        denominator = current
        expected = min(page["size"], page["totalElements"] - number * page["size"])
        if len(page["content"]) != expected:
            raise ValueError("Official BSE directory page lost records")
        for row in page["content"]:
            record = _directory_record(row, source)
            if record["code"] in records:
                raise ValueError("Duplicate BSE identity while paging official directory")
            records[record["code"]] = record
        pages.append(source)
        if progress:
            progress({"directory_page": number + 1, "pages": page["totalPages"], "rows": len(records)})
        if number + 1 == page["totalPages"]:
            if len(records) != page["totalElements"]:
                raise ValueError("Official BSE directory total disagrees with observed identities")
            snapshots = sorted({r["source_snapshot_date"] for r in records.values()})
            if len(snapshots) != 1:
                raise ValueError("Official BSE directory snapshot changed during paging")
            return sorted(records.values(), key=lambda r: r["code"]), {
                "source": "bse_official_current_directory", "total": len(records), "total_pages": len(pages),
                "source_snapshot_date": snapshots[0], "pages": pages, "current_directory_complete": True,
                "historical_universe_complete": False, "historical_exit_dates_certified": False}
    raise ValueError("Official BSE directory pagination bound exhausted")


def official_aliases(text: str) -> list[dict]:
    result = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                 for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) != 5 or not cells[0].isdigit():
            continue
        _, name, listed, old, new = cells
        year, month, day = (int(v) for v in listed.split("/"))
        listed = f"{year:04d}-{month:02d}-{day:02d}"
        result.append({"code": normalize_code(new), "old_code": normalize_code(old),
                       "name": name, "select_or_ipo_date": listed,
                       "ipo_date": max(BSE_OPEN, listed), "exchange": "BJ",
                       "source": "bse_official_code_mapping", "source_url": MAPPING_URL,
                       "source_name_damaged": "\ufffd" in name})
    if not result or len({r["code"] for r in result}) != len(result):
        raise ValueError("Official BSE code mapping empty or contains duplicate new identities")
    return result


def official_exit_record(text: str, code: str, source: dict, *, ipo_date: str | None = None) -> dict:
    """Extract an explicit effective exit date, never the earlier decision date.

    Source bytes/hash must be verified by the caller. Transfer notices can add
    retired old-code identities absent from today's 920xxx mapping.
    """
    code = normalize_code(code)
    compact = re.sub(r"\s+", "", text)
    identity = re.search(r"证券代码[:：]([0-9]{6})证券简称[:：](.+?)(?:公告编号|主办券商)", compact)
    if identity is None or identity[1] != code:
        raise ValueError("Official exit notice returned a different identity")
    date = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
    patterns = [r"公司股票将于" + date + r"被北京证券交易所终止上市并摘牌",
                r"股票终止上市日期为" + date,
                r"同意公司于" + date + r"起在北交所终止上市"]
    dates, excerpts = set(), []
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            year, month, day = map(int, match.groups())
            dates.add(datetime(year, month, day).date().isoformat())
            excerpts.append(match.group(0))
    if len(dates) != 1:
        raise ValueError("Official notice has no unique explicit effective BSE exit date")
    if ipo_date is None and "2021年" in compact and re.search(
            r"同年11月15日北京证券交易所.{0,20}设立.{0,20}公司身份转换为北交所上市公司", compact):
        ipo_date = BSE_OPEN
    out_date = next(iter(dates))
    if not ipo_date or not BSE_OPEN <= ipo_date < out_date:
        raise ValueError("Official exit identity needs independent BSE listing-date evidence")
    pub_date = source["pub_date"]
    datetime.strptime(pub_date, "%Y-%m-%d")
    if pub_date > out_date or not re.fullmatch(r"[0-9a-f]{64}", source.get("source_sha256", "")):
        raise ValueError("Exit evidence publication/hash is invalid")
    return {"code": code, "name": identity[2], "exchange": "BJ", "ipo_date": ipo_date,
            "delisted_date": out_date, "listing_status": "0", "exit_status": "official_effective_exit_verified",
            "exit_kind": "exchange_transfer" if "因转板" in compact else "delisting",
            "membership_status": "source_listing_and_exit_dates_verified",
            "current_directory_present": False, "historical_universe_complete": False,
            "source": "bse_official_exit_notice", "scope": "beijing_a_lifecycle",
            "source_name_damaged": False, "exit_evidence": {**source, "effective_date_excerpts": excerpts}}


def json_response(session, url, params):
    # Preserve the vendor's comma/plus filter syntax, as used by its quote page.
    response = session.get(url + '?' + urlencode(params, safe=':,+'), timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("rc") != 0 or not isinstance(data.get("data"), dict):
        raise ValueError("Vendor response is incomplete or unavailable")
    return data["data"]


def current_list(session) -> list[dict]:
    records, total = {}, None
    for page in range(1, 51):
        payload = json_response(session, LIST_URL, {"pn": page, "pz": 100, "po": 1,
            "np": 1, "fltt": 2, "invt": 2, "fid": "f12", "fs": "m:0+t:81+s:2048",
            "fields": "f12,f13,f14,f26"})
        count = payload.get("total")
        if not isinstance(count, int) or count < 1 or total not in (None, count):
            raise ValueError("BSE current list total changed while paging")
        total = count
        rows = payload.get("diff", [])
        if not rows:
            raise ValueError("BSE list paging ended before reported total")
        for row in rows:
            code = normalize_code(row["f12"])
            if not code.startswith("920") or row.get("f13") != 0 or code in records:
                raise ValueError("Unexpected/duplicate BSE quote identity")
            date = str(row.get("f26", ""))
            if len(date) != 8 or not date.isdigit():
                raise ValueError("BSE IPO date unavailable")
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            records[code] = {"code": code, "name": row["f14"], "ipo_date": max(BSE_OPEN, date),
                             "exchange": "BJ", "source": "eastmoney_current_bse_list"}
        if len(records) == total:
            return list(records.values())
        if len(records) > total:
            raise ValueError("BSE list exceeds reported total")
    raise ValueError("BSE current list pagination limit exhausted")


def lifecycles(current: list[dict], aliases: list[dict]) -> list[dict]:
    current_by_code = {r["code"]: r for r in current}
    merged = {r["code"]: dict(r) for r in aliases}
    for row in current:
        old = merged.get(row["code"], {})
        if old and old["ipo_date"] != row["ipo_date"]:
            raise ValueError(f"Conflicting BSE listing date: {row['code']}")
        merged[row["code"]] = {**old, **row, "source": "bse_official_mapping_and_directory" if old else row["source"]}
    for code, row in merged.items():
        row.update(listing_status="1" if code in current_by_code else "historical_identity_pending_exit",
                   delisted_date=None, membership_status="listing_date_verified_exit_not_certified",
                   current_directory_present=code in current_by_code,
                   exit_status="not_current_exit_date_unverified" if code not in current_by_code else "current_active_at_snapshot",
                   scope="beijing_a_lifecycle", historical_universe_complete=False)
    return sorted(merged.values(), key=lambda r: r["code"])


def parse_klines(payload: dict, code: str, start: str, end: str, adjustment: str) -> list[dict]:
    if payload.get("code") != code or payload.get("market") != 0:
        raise ValueError("BSE history returned a different security")
    records = []
    for line in payload.get("klines", []):
        values = line.split(",")
        if len(values) != 11:
            raise ValueError("BSE kline has an unexpected field schema")
        date = values[0]
        if datetime.strptime(date, "%Y-%m-%d").date().isoformat() != date:
            raise ValueError("BSE history date has an unexpected format")
        if not start <= date <= end:
            raise ValueError("BSE history outside requested window")
        numbers = [float(v) for v in values[1:]]
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("BSE history contains missing/nonfinite numeric values")
        opening, close, high, low, volume, amount, amplitude, pct, change, turnover = numbers
        if min(opening, close, high, low) <= 0 or volume < 0 or amount < 0 or high < max(opening, close, low) or low > min(opening, close, high):
            raise ValueError("BSE history violates OHLC/volume constraints")
        row = {"code": code, "date": date, "open": opening, "close": close, "high": high,
               "low": low, "vol": volume, "volume_shares": volume * 100, "amount": amount,
               "volume_unit": "lots_100_shares", "amount_unit": "CNY", "source": "eastmoney",
               "exchange": "BJ", "source_pct_chg": pct, "source_change": change,
               "source_turnover_pct": turnover, "source_adjustment": adjustment,
               "trade_status": None, "is_st": None,
               "execution_state": "historical_suspension_and_ST_evidence_required"}
        if adjustment == "none" and volume > 0:
            average = amount / (volume * 100)
            if not low * 0.99 <= average <= high * 1.01:
                raise ValueError("BSE amount/volume units disagree with raw traded price range")
        records.append(row)
    if len({r["date"] for r in records}) != len(records):
        raise ValueError("BSE history has duplicate dates")
    if [r['date'] for r in records] != sorted(r['date'] for r in records):
        raise ValueError("BSE history dates are not ascending")
    return records


def history(session, code: str, start: str, end: str, adjustment: str, *, cache_dir: Path | None = None,
            endpoint: str = HISTORY_URL):
    mode = {"none": 0, "hfq": 2}[adjustment]
    params = {"secid": "0." + code, "klt": 101,
        "fqt": mode, "beg": start.replace("-", ""), "end": end.replace("-", ""),
        "lmt": 10000, "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
    source = None
    if cache_dir is None:
        payload = json_response(session, endpoint, params)
    else:
        key = hashlib.sha256(json.dumps({"url": endpoint, "params": params}, sort_keys=True).encode()).hexdigest()[:16]
        raw, source = response_cache(session, endpoint, params, Path(cache_dir) / f"{code}_{adjustment}_{key}.response")
        decoded = json.loads(raw.decode("utf-8"))
        if decoded.get("rc") != 0 or not isinstance(decoded.get("data"), dict):
            raise ValueError("Vendor response is incomplete or unavailable")
        payload = decoded["data"]
    records = parse_klines(payload, code, start, end, adjustment)
    if source:
        for row in records:
            row.update(source_response_sha256=source["sha256"], source_response_path=source["path"],
                       source_observed_at=source["observed_at"], source_url=endpoint)
    return records
