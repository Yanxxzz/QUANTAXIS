"""Finite, read-only data requests owned by the existing research host.

Models receive immutable receipts, never credentials or arbitrary fetch tools.
Completed queries survive interruption; a partial attempt remains in the audit.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .contracts import digest, timestamp, verify_artifacts
from .evidence import artifact
from .runtime_io import identity, lease, owned, read, terminate, write

FIELDS = ["date", "code", "open", "high", "low", "close", "volume", "amount", "turnover", "is_st"]
FINANCIAL = {"construction_in_progress": "在建工程", "net_fixed_assets": "固定资产净额",
             "total_assets": "资产总计", "contract_liabilities": "合同负债"}


def validate(spec):
    requests = spec.get("data_requests", [])
    if not isinstance(requests, list) or len(requests) > 2:
        raise ValueError("data_requests accepts at most two bounded requests")
    ids = set()
    for request in requests:
        if request.get("kind") not in {"stockdb_market", "akshare_financial"}:
            raise ValueError("Only fixed read-only StockDB/AKShare collectors are allowed")
        name = request.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name in ids:
            raise ValueError("Data request IDs must be unique, simple names")
        ids.add(name)
        codes = request.get("codes")
        if (not isinstance(codes, list) or not 1 <= len(codes) <= 128 or
                any(not isinstance(c, str) or not re.fullmatch(r"\d{6}", c) for c in codes) or
                len(set(codes)) != len(codes)):
            raise ValueError("Freeze 1..128 distinct six-digit securities before collecting")
        if request["kind"] == "akshare_financial" and any(c[0] not in "036" for c in codes):
            raise ValueError("This audited financial adapter supports Shanghai/Shenzhen securities only")
        start, end = date.fromisoformat(request["start"]), date.fromisoformat(request["end"])
        if start > end or (end - start).days > 1096:
            raise ValueError("Data interval must be ordered and no longer than three years")
        for key, limit in (("timeout_seconds", 900), ("query_timeout_seconds", 90)):
            value = request.get(key)
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f"Explicit {key} must be within 1..{limit}")
        if request.get("akshare_version") != "1.19.1":
            raise ValueError("Collector currently supports the audited AKShare 1.19.1 adapter")
        if request["kind"] == "stockdb_market":
            verify_artifacts([request["sdk"]])
            if Path(request["sdk"]["path"]).name != "stockdb.pyd":
                raise ValueError("Bind the local native StockDB SDK")


def folder(board, session, request):
    database = Path(board.db.execute("PRAGMA database_list").fetchone()[2]).resolve()
    return database.parent / "data-captures" / digest({"session": session}) / digest(request)


def jobs(request):
    if request["kind"] == "stockdb_market":
        return [("calendar", "")] + [("stockdb", code) for code in request["codes"]]
    return [("financial", code) for code in request["codes"]]


def check_receipt(value):
    verify_artifacts(value["artifacts"], required=value["status"] == "captured")
    return value


def query(root, request, kind, code, deadline, active):
    """At most two attempts, with each original attempt retained on disk."""
    key = f"{kind}-{code or 'all'}"
    target = root / "queries" / key
    complete = read(target / "complete.json")
    if complete:
        return check_receipt(complete)
    for number in (1, 2):
        attempt = target / str(number)
        previous = read(attempt / "process.json")
        if previous:
            terminate(previous)  # Identity is birth-time bound, never a naked PID.
        answer = read(attempt / "result.json")
        if answer:
            if answer["status"] == "captured":
                check_receipt(answer)
                write(target / "complete.json", answer)
                return answer
            continue
        if (attempt / "input.json").exists():
            write(attempt / "result.json", {"status": "interrupted", "artifacts": []})
            continue
        if not active() or time.time() >= deadline:
            raise InterruptedError("Data collection stopped; original bounds retained")
        attempt.mkdir(parents=True, exist_ok=True)
        write(attempt / "input.json", {"request": request, "kind": kind, "code": code,
                                      "parent": identity()})
        with (attempt / "stderr.txt").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-m", __name__, str(attempt)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            receipt = identity(process.pid)
            write(attempt / "process.json", receipt)
            limit = min(deadline, time.time() + request["query_timeout_seconds"])
            try:
                while process.poll() is None:
                    if not active():
                        raise InterruptedError("Data collection paused by host")
                    if time.time() >= limit:
                        raise TimeoutError("Bounded data query timed out")
                    time.sleep(.1)
            except (InterruptedError, TimeoutError) as exc:
                terminate(receipt)
                write(attempt / "result.json", {"status": "interrupted" if isinstance(exc, InterruptedError)
                      else "timeout", "error": str(exc), "artifacts": []})
                if isinstance(exc, InterruptedError):
                    raise
            finally:
                terminate(receipt)
                process.wait(timeout=5)
        answer = read(attempt / "result.json", {"status": "failed", "artifacts": []})
        if answer["status"] == "captured":
            check_receipt(answer)
            write(target / "complete.json", answer)
            return answer
    raise ValueError(f"Data query exhausted its two attempts: {key}")


def normalize(root, request, captures):
    import pandas as pd
    if request["kind"] == "stockdb_market":
        calendar = pd.DatetimeIndex(pd.to_datetime([r["trade_date"] for r in captures[0]]))
        calendar = calendar[(calendar >= request["start"]) & (calendar <= request["end"])]
        if not len(calendar) or not calendar.is_unique or not calendar.is_monotonic_increasing:
            raise ValueError("Independent calendar missing, duplicated or unordered")
        rows = [row for capture in captures[1:] for row in capture]
        frame = pd.DataFrame(rows, columns=FIELDS)
        frame["date"] = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d")
        if (frame.duplicated(["date", "code"]).any() or not frame["date"].isin(calendar).all()
                or not frame["code"].isin(request["codes"]).all()):
            raise ValueError("Unexpected market identity or date")
        for key in FIELDS[2:]:
            frame[key] = pd.to_numeric(frame[key], errors="raise")
        valid = frame[["open", "high", "low", "close"]].notna().all(axis=1)
        if ((frame.loc[valid, "high"] < frame.loc[valid, ["open", "close", "low"]].max(axis=1)).any()
                or (frame.loc[valid, "low"] > frame.loc[valid, ["open", "close", "high"]].min(axis=1)).any()):
            raise ValueError("Inconsistent OHLC input")
        indexed = frame.rename(columns={"code": "symbol"}).set_index(["date", "symbol"])
        grid = pd.MultiIndex.from_product([calendar, request["codes"]], names=["date", "symbol"])
        aligned = indexed.reindex(grid)
        aligned["source_row_present"] = aligned.index.isin(indexed.index)
        path = root / "market-grid.parquet"
        aligned.reset_index().to_parquet(path, index=False)
        calendar_path = root / "calendar.csv"
        pd.DataFrame({"date": calendar}).to_csv(calendar_path, index=False)
        return {"rows": len(frame), "grid_keys": len(grid), "missing_keys": len(grid) - len(frame),
                "calendar_days": len(calendar), "historical_backfill_allowed": False,
                "limitations": ["Unadjusted external prices; not PandaAI native Run inputs.",
                                "Missing rows remain NaN on an independent trading calendar.",
                                "Corporate actions and historical universe eligibility are not certified."],
                "artifacts": [artifact(path), artifact(calendar_path)]}
    rows = []
    for code, capture in zip(request["codes"], captures):
        seen = set()
        for item in capture:
            period = str(item["报告日"])
            period = date.fromisoformat(f"{period[:4]}-{period[4:6]}-{period[6:8]}").isoformat()
            if not request["start"] <= period <= request["end"]:
                continue
            if period in seen or item.get("类型") != "合并期末" or item.get("币种") != "CNY":
                raise ValueError("Ambiguous report period, consolidation scope or currency")
            seen.add(period)
            record = {"symbol": code, "report_period": period,
                      "provider_announcement_date_raw": item.get("公告日期"),
                      "provider_update_timestamp_raw": item.get("更新日期"),
                      "scope": "consolidated_end_of_period", "currency": "CNY", "unit": "yuan",
                      "historical_backfill_allowed": False}
            for key, field in FINANCIAL.items():
                value = item.get(field)
                record[key] = None if value is None or str(value).strip() in {"", "--"} else float(value)
            rows.append(record)
    if not rows:
        raise ValueError("No financial rows in the frozen report-period interval")
    path = root / "financial-current.json"
    write(path, rows)
    return {"rows": len(rows), "sample": rows[:16], "historical_backfill_allowed": False,
            "limitations": ["Current snapshots, not a historical disclosure/revision archive.",
                            "Provider announcement metadata is not proof of original as-of availability."],
            "artifacts": [artifact(path)]}


def capture(root, request, deadline, active, *, runner=None):
    runner = runner or query
    root.mkdir(parents=True, exist_ok=True)
    with lease(root / "capture.lock"):
        saved = read(root / "receipt.json")
        if saved:
            if saved["request"] != request:
                raise ValueError("Captured request changed")
            return check_receipt(saved)
        control = read(root / "bounds.json")
        if control is None:
            control = {"request": request, "deadline": min(deadline, time.time() + request["timeout_seconds"])}
            write(root / "bounds.json", control)
        if control["request"] != request:
            raise ValueError("Data request changed during recovery")
        deadline = min(deadline, control["deadline"])
        evidence, captures = [], []
        try:
            for kind, code in jobs(request):
                if not active():
                    raise InterruptedError("Data capture stopped")
                if time.time() >= deadline:
                    raise TimeoutError("Original capture deadline expired")
                value = runner(root, request, kind, code, deadline, active)
                evidence.extend(value["artifacts"])
                captures.append(read(value["data"]["path"]))
            if not active():
                raise InterruptedError("Data capture stopped before normalization")
            summary = normalize(root, request, captures)
            normalized = summary.pop("artifacts")
            evidence.extend(normalized)
            saved = {"status": "captured", "request": request, "summary": summary,
                     "captured_at": datetime.now(timezone.utc).isoformat(), "artifacts": evidence,
                     "normalized_artifacts": normalized,
                     "website_calls": 0, "funding_authorization": False}
        except InterruptedError:
            raise
        except (ValueError, TimeoutError, OSError, KeyError) as exc:
            saved = {"status": "failed", "request": request, "error": str(exc),
                     "artifacts": evidence, "website_calls": 0, "funding_authorization": False}
        write(root / "receipt.json", saved)
        return saved


def refresh(board, session, spec, active=None):
    active = active or (lambda: True)
    deadline = timestamp(spec["deadline"]).timestamp()
    output = []
    for request in spec.get("data_requests", []):
        root = folder(board, session, request)
        output.append(capture(root, request, deadline, active))
    return output


def material(board, session, spec):
    items = []
    for request in spec.get("data_requests", []):
        root = folder(board, session, request)
        value = read(root / "receipt.json")
        if value:
            check_receipt(value)
            items.append({"id": request["id"], "receipt": artifact(root / "receipt.json"),
                          "status": value["status"], "summary": value.get("summary"),
                          "error": value.get("error"), "artifacts": value.get("normalized_artifacts", []),
                          "scope": "external free-source input only; no source/PIT/admission authorization"})
    return {"items": items, "funding_authorization": False}


def child(attempt):
    """Fixed provider APIs in a cancellable, parent-owned process."""
    value = read(attempt / "input.json")
    def guard():
        while owned(value["parent"]):
            time.sleep(.25)
        os._exit(3)  # Parent died before it could persist/terminate the child.
    threading.Thread(target=guard, daemon=True).start()
    request, kind, code = value["request"], value["kind"], value["code"]
    artifacts, http = [], []
    try:
        if kind == "stockdb":
            verify_artifacts([request["sdk"]])
            sys.path.insert(0, str(Path(request["sdk"]["path"]).parent))
            import stockdb
            def fresh():
                return stockdb.init(host="127.0.0.1", port=7899, socket_timeout=15)
            selector = request["start"].replace("-", "") + "<" + request["end"].replace("-", "")
            rows = fresh().vals("日k", code, selector).get(",".join(FIELDS)).do()
            ids = fresh().vals("日k", code, selector).get("date,code").do()
            if (sorted(r[:2] for r in rows) != sorted(ids) or
                    any(str(r[1]) != code for r in rows) or len({tuple(r[:2]) for r in rows}) != len(rows)):
                raise ValueError("Independent StockDB identity projection mismatch")
            write(attempt / "identities.json", ids)
            artifacts.append(artifact(attempt / "identities.json"))
        else:
            import importlib.metadata
            import inspect
            import requests
            if importlib.metadata.version("akshare") != request["akshare_version"]:
                raise ValueError("AKShare adapter version changed")
            original = requests.sessions.Session.request
            def recorded(session, method, url, **kwargs):
                if len(http) >= 8 or method.upper() not in {"GET", "POST"}:
                    raise ValueError("Free provider request limit reached")
                # POST is the library's read-only query, never model-supplied.
                kwargs["timeout"] = (5, 15)
                entry = {"method": method, "url": url, "params": kwargs.get("params"),
                         "data": kwargs.get("data")}
                http.append(entry)
                response = original(session, method, url, **kwargs)
                path = attempt / f"http-{len(http):02d}.bin"
                path.write_bytes(response.content)
                entry.update(status=response.status_code, artifact=artifact(path))
                write(attempt / "http.json", http)
                response.raise_for_status()
                return response
            requests.sessions.Session.request = recorded
            import akshare as ak
            fn = ak.tool_trade_date_hist_sina if kind == "calendar" else ak.stock_financial_report_sina
            kwargs = {} if kind == "calendar" else {
                "stock": ("sh" if code.startswith("6") else "sz") + code, "symbol": "资产负债表"}
            implementation = artifact(Path(inspect.getsourcefile(fn)))
            frame = fn(**kwargs)
            rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
            write(attempt / "provider.json", {"api": fn.__name__, "params": kwargs,
                  "version": request["akshare_version"], "implementation": implementation})
            artifacts.append(artifact(attempt / "provider.json"))
            artifacts.extend(h["artifact"] for h in http if "artifact" in h)
            if http:
                artifacts.append(artifact(attempt / "http.json"))
        write(attempt / "data.json", rows)
        data = artifact(attempt / "data.json")
        result = {"status": "captured", "data": data, "artifacts": artifacts + [data]}
    except Exception as exc:
        artifacts.extend(h["artifact"] for h in http if "artifact" in h and h["artifact"] not in artifacts)
        if http:
            write(attempt / "http.json", http)
            artifacts.append(artifact(attempt / "http.json"))
        result = {"status": "failed", "error": type(exc).__name__ + ": " + str(exc),
                  "artifacts": artifacts}
    write(attempt / "result.json", result)


if __name__ == "__main__":
    child(Path(sys.argv[1]))
