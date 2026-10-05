"""QUANTAXIS-compatible Mongo data, explicit coverage, and migration acceptance.

This adapter uses the real ``stock_day``/``stock_xdxr``/``stock_list`` schemas.
It deliberately does not import QUANTAXIS' large optional dependency graph.
The corporate-action equation is the one in QUANTAXIS/QAData/data_fq.py:
https://github.com/yutiansut/QUANTAXIS/blob/master/QUANTAXIS/QAData/data_fq.py
Same-source xdxr or verified cumulative adjustment factors are required; a
security with zero events still needs a successful complete-history receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


class PendingDataError(ValueError):
    """Requested data has not been verified, so it cannot be silently substituted."""


@dataclass
class DailyData:
    frame: pd.DataFrame
    coverage: dict[str, Any]


def normalize_code(value: str) -> str:
    value = str(value).strip().upper().replace(".SH", "").replace(".SZ", "")
    if value.startswith(("SH", "SZ")):
        value = value[2:]
    if not value.isdigit() or len(value) > 6:
        raise ValueError(f"Invalid A-share code: {value!r}")
    return value.zfill(6)


def _date(value: Any) -> str:
    if isinstance(value, (str, int, np.integer)) and str(value).isdigit() and len(str(value)) == 8:
        return pd.to_datetime(str(value), format="%Y%m%d").date().isoformat()
    return pd.Timestamp(value).date().isoformat()


def adjust_prices(frame: pd.DataFrame, events: pd.DataFrame, adjustment: str) -> pd.DataFrame:
    """Apply QA's cash/rights/bonus equation, anchored inside this sample.

    Prices are adjusted; volume and amount remain QA/TDX's raw quantities.
    qfq is anchored to the last observed date, hfq to the first observed date.
    Events after the requested end date must not be supplied. Unsupported capital
    restructuring categories are rejected rather than treated as ordinary dividends.
    """
    if adjustment not in {"none", "qfq", "hfq"}:
        raise ValueError("adjustment must be none, qfq, or hfq")
    out = frame.copy()
    out["adj"] = 1.0
    if adjustment == "none" or events.empty or out.empty:
        return out
    required = {"date", "category"}
    if not required.issubset(events.columns):
        raise PendingDataError("stock_xdxr is missing date/category")
    events = events.copy()
    events["date"] = pd.to_datetime(events["date"]).dt.normalize()
    events["category"] = pd.to_numeric(events["category"], errors="raise")
    dates = pd.to_datetime(out["date"])
    relevant = events[(events["date"] > dates.min()) & (events["date"] <= dates.max())]
    if relevant["category"].isin([11, 12]).any():
        raise PendingDataError("Expansion/contraction actions require separately verified adjustment factors")
    relevant = relevant[relevant["category"] == 1].sort_values("date")
    if relevant["date"].duplicated().any():
        raise PendingDataError("Duplicate ex-rights events require reconciliation")
    action_fields = ["fenhong", "peigu", "peigujia", "songzhuangu"]
    if not relevant.empty and not set(action_fields).issubset(relevant.columns):
        raise PendingDataError("A category-1 xdxr event is missing cash/rights/bonus fields")
    for event in relevant.to_dict("records"):
        before = dates < event["date"]
        previous_close = float(out.loc[before, "close"].iloc[-1])
        values = [float(event[k]) for k in action_fields]
        if not np.isfinite(values).all():
            raise PendingDataError("A corporate-action value is not finite")
        dividend, rights, rights_price, bonus = values
        denominator = 10.0 + rights + bonus
        if denominator <= 0:
            raise PendingDataError("A corporate-action denominator is invalid")
        theoretical = (previous_close * 10.0 - dividend + rights * rights_price) / denominator
        ratio = theoretical / previous_close
        if not np.isfinite(ratio) or ratio <= 0:
            raise PendingDataError("A corporate-action adjustment ratio is invalid")
        if adjustment == "qfq":
            out.loc[before, "adj"] *= ratio
        else:
            out.loc[~before, "adj"] /= ratio
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * out["adj"]
    return out


def adjust_with_factors(frame: pd.DataFrame, factors: pd.DataFrame, adjustment: str,
                        *, history_complete: bool = False, asof_end: str | None = None) -> pd.DataFrame:
    """BaoStock cumulative back factors matched on effective date, never forward.

    The official factor document specifies IPO factor=1 and prices multiplied by
    the latest factor effective on/before that trading day. Rebasing cumulative
    back factors at the sample end gives qfq without using future actions.
    https://www.baostock.com/helpdocs/pdf/BaoStock复权因子简介.pdf
    """
    if adjustment not in {"none", "qfq", "hfq"}:
        raise ValueError("adjustment must be none, qfq, or hfq")
    out = frame.sort_values("date").copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["factor_date"] = pd.NaT
    out["adj"] = 1.0
    if adjustment == "none" or out.empty:
        return out
    if factors.empty:
        if not history_complete:
            raise PendingDataError("Empty adjustment factors have no verified history receipt")
        return out
    if not {"date", "adj"}.issubset(factors.columns):
        raise PendingDataError("stock_adj is missing date/adj")
    factors = factors.copy()
    factors["factor_date"] = pd.to_datetime(factors["date"]).dt.normalize()
    factors["cumulative"] = pd.to_numeric(factors["adj"], errors="coerce")
    anchor_date = pd.Timestamp(asof_end).normalize() if asof_end else out["date"].max()
    if anchor_date < out["date"].max():
        raise ValueError("Adjustment anchor precedes sampled bars")
    factors = factors[factors["factor_date"] <= anchor_date].sort_values("factor_date")
    if factors["factor_date"].duplicated().any() or not (np.isfinite(factors["cumulative"]) & (factors["cumulative"] > 0)).all():
        raise PendingDataError("Adjustment factors have duplicates or invalid values")
    matched = pd.merge_asof(out.drop(columns=["factor_date", "adj"]),
                            factors[["factor_date", "cumulative"]], left_on="date", right_on="factor_date",
                            direction="backward")
    if matched["cumulative"].isna().any() and not history_complete:
        raise PendingDataError("No predecessor adjustment factor; IPO baseline is unverified")
    matched["cumulative"] = matched["cumulative"].fillna(1.0)
    # Official hfq is IPO-anchored; qfq is anchored at the sample end and cannot
    # consume BaoStock's present-day fore factor, which may include later actions.
    anchor = (factors["cumulative"].iloc[-1] if not factors.empty else 1.0) if adjustment == "qfq" else 1.0
    matched["adj"] = matched["cumulative"] / anchor
    for col in ("open", "high", "low", "close"):
        matched[col] *= matched["adj"]
    return matched.drop(columns="cumulative")


class AxisProvider:
    def __init__(self, uri: str = "mongodb://127.0.0.1:27017", database: str = "quantaxis", db: Any = None):
        self.uri = uri
        self.database = database
        self._db = db
        self._client = None

    @property
    def db(self):
        if self._db is None:
            from pymongo import MongoClient
            self._client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
            self._client.admin.command("ping")
            self._db = self._client[self.database]
        return self._db

    def _records(self, name: str, query: dict | None = None) -> list[dict]:
        return list(self.db[name].find(query or {}, {"_id": 0}))

    def _receipts(self, name: str, codes: list[str], end: str) -> dict[str, dict]:
        records = self._records("panda_axis_sync", {"dataset": name, "code": {"$in": codes}})
        return {r["code"]: r for r in records if r.get("status") == "complete" and r.get("through", "") >= end}

    def _capability(self, name: str, start: str, end: str) -> dict:
        evidence = self._records("panda_axis_validation", {"capability": name})
        evidence = [r for r in evidence if r.get("status") == "verified"
                    and r.get("start", "9999") <= start and r.get("end", "") >= end
                    and r.get("evidence")]
        if evidence:
            return {"status": "verified", "evidence": evidence[-1]}
        reasons = {
            "all_a": "Current SH/SZ stock_list is not a point-in-time historical all-A membership ledger; Beijing coverage is separate.",
            "minute": "Minute history and session completeness have not been independently accepted.",
            "delisted": "TDX's current security list does not prove coverage of delisted securities.",
            "pit_financial": "Report dates are not announcement availability dates; financial PIT evidence is pending.",
        }
        return {"status": "pending", "reason": reasons[name]}

    def daily(self, codes: Iterable[str], start: str, end: str, adjustment: str = "qfq",
              expected_dates: Iterable[str] | None = None) -> DailyData:
        codes = sorted(set(normalize_code(c) for c in codes))
        if not codes:
            raise ValueError("At least one code is required")
        start, end = _date(start), _date(end)
        if start > end:
            raise ValueError("start must be on or before end")
        if adjustment not in {"none", "qfq", "hfq"}:
            raise ValueError("adjustment must be none, qfq, or hfq")
        rows = self._records("stock_day", {"code": {"$in": codes}, "date": {"$gte": start, "$lte": end}})
        columns = ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "adj", "factor_date", "adjustment", "source"]
        frame = pd.DataFrame(rows)
        duplicates = invalid_rows = 0
        excluded_rows = []
        if frame.empty:
            frame = pd.DataFrame(columns=columns)
        else:
            frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
            frame["symbol"] = frame["code"].map(normalize_code)
            frame["source"] = frame["source"] if "source" in frame else "tdx"
            if "vol" not in frame and "volume" not in frame:
                raise PendingDataError("stock_day is missing vol/volume")
            frame["volume"] = frame["vol"] if "vol" in frame else frame["volume"]
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                if col not in frame:
                    raise PendingDataError(f"stock_day is missing {col}")
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            duplicates = int(frame.duplicated(["date", "symbol"]).sum())
            frame = frame.drop_duplicates(["date", "symbol"], keep="last").sort_values(["symbol", "date"])
            numeric = frame[["open", "high", "low", "close", "volume", "amount"]]
            valid = np.isfinite(numeric).all(axis=1) & (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
            valid &= (frame["volume"] >= 0) & (frame["amount"] >= 0)
            valid &= (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
            valid &= (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
            # A source's explicit suspension bar is not an executable quote,
            # even if its repeated OHLC values look numerically valid.
            suspended = frame["trade_status"].astype(str).eq("0") if "trade_status" in frame else pd.Series(False, index=frame.index)
            valid &= ~suspended
            for index in frame.index[~valid]:
                row = frame.loc[index]
                trade_status = row.get("trade_status")
                missing_fields = row.get("missing_numeric_fields")
                excluded_rows.append({"date": _date(row["date"]), "symbol": row["symbol"],
                                      "reason": "source_explicit_suspension" if suspended.loc[index] else "invalid_or_missing_raw_bar",
                                      "trade_status": None if pd.isna(trade_status) else trade_status,
                                      "missing_numeric_fields": missing_fields if isinstance(missing_fields, list) else []})
            invalid_rows = int((~valid).sum())
            frame = frame.loc[valid].copy()
        xdxr_receipts = self._receipts("stock_xdxr", codes, end)
        factor_receipts = self._receipts("stock_adj", codes, end)
        missing_xdxr = sorted(set(codes) - set(xdxr_receipts))
        missing_adjustment = sorted(set(codes) - set(xdxr_receipts) - set(factor_receipts))
        if adjustment != "none" and missing_adjustment:
            raise PendingDataError("Corporate-action synchronization is unverified for: " + ", ".join(missing_adjustment))
        events = self._records("stock_xdxr", {"code": {"$in": codes}, "date": {"$lte": end}})
        factors = self._records("stock_adj", {"code": {"$in": codes}, "date": {"$lte": end}})
        adjusted = []
        adjustment_methods = {}
        for code, group in frame.groupby("symbol", sort=True):
            sources = set(group["source"])
            if len(sources) != 1:
                raise PendingDataError(f"{code}: mixed raw data providers require source reconciliation")
            receipt = factor_receipts.get(code)
            if receipt and sources == {receipt.get("source")}:
                code_factors = pd.DataFrame([f for f in factors if f.get("code") == code and f.get("source") == receipt["source"]])
                adjusted.append(adjust_with_factors(group, code_factors, adjustment,
                                                   history_complete=receipt.get("history_complete", False), asof_end=end))
                adjustment_methods[code] = "same-source cumulative stock_adj; qfq at sample_end, hfq at IPO"
            else:
                if adjustment != "none" and (code not in xdxr_receipts or sources != {xdxr_receipts[code].get("source", "tdx")}):
                    raise PendingDataError(f"{code}: same-source adjustment evidence unavailable")
                code_events = pd.DataFrame([e for e in events if e.get("code") == code])
                adjusted.append(adjust_prices(group, code_events, adjustment))
                adjustment_methods[code] = "QA xdxr cash/rights/bonus"
        if adjusted:
            frame = pd.concat(adjusted, ignore_index=True)
        frame["adjustment"] = adjustment
        if "source" not in frame:
            frame["source"] = "quantaxis_mongo"
        frame = frame.reindex(columns=columns).sort_values(["date", "symbol"]).reset_index(drop=True)
        calendar_source = "caller_supplied" if expected_dates is not None else "tdx_index_calendar"
        calendar_receipts = self._records("panda_axis_sync", {"dataset": "trade_calendar", "code": "SSE"})
        if expected_dates is None and any(r.get("source") == "baostock" for r in calendar_receipts):
            calendar_source = "baostock_trade_dates"
        calendar_verified = expected_dates is not None or any(
            r.get("status") == "complete" and r.get("start", "9999") <= start and r.get("through", "") >= end
            for r in calendar_receipts)
        if expected_dates is None:
            query = {"date": {"$gte": start, "$lte": end}, "exchange": "SSE"}
            if calendar_source == "baostock_trade_dates":
                query["source"] = "baostock_trade_dates"
            expected_dates = [r["date"] for r in self._records("trade_calendar", query)]
        expected = sorted({_date(d) for d in expected_dates if start <= _date(d) <= end})
        observed = {(r.symbol, _date(r.date)) for r in frame.itertuples()}
        missing_by_code = {c: [d for d in expected if (c, d) not in observed] for c in codes}
        expected_rows = len(expected) * len(codes)
        # Off-calendar rows do not improve completeness; report them separately.
        present_expected = sum((c, d) in observed for c in codes for d in expected)
        current_list = self._records("stock_list")
        listed = {normalize_code(r["code"]) for r in current_list if "code" in r and r.get("listing_status") != "0"}
        coverage = {
            "source": "QUANTAXIS-compatible MongoDB", "raw_sources": sorted(set(frame["source"])), "start": start, "end": end,
            "codes": codes, "adjustment": adjustment,
            "daily": {"status": "verified" if calendar_verified and expected_rows else "pending",
                      "rows": len(frame), "expected_rows": expected_rows,
                      "coverage": present_expected / expected_rows if calendar_verified and expected_rows else None,
                      "missing_by_code": missing_by_code, "invalid_rows": invalid_rows,
                      "excluded_rows": excluded_rows,
                      "duplicate_rows": duplicates, "off_calendar_rows": sum(d not in expected for _, d in observed),
                      "note": "Missing bars include IPO, suspension, source loss; no forward filling or zero imputation."},
            "calendar": {"status": "verified" if calendar_verified and expected else "pending",
                         "source": calendar_source, "sessions": len(expected)},
            "xdxr": {"status": "verified" if not missing_xdxr else "pending", "missing_codes": missing_xdxr},
            "adjustment_evidence": {"status": "verified" if not missing_adjustment and adjustment != "none" else "pending",
                                    "missing_codes": missing_adjustment, "methods": adjustment_methods},
            "units": {"volume": "QA stock_day/TDX lots (100 shares); BaoStock raw shares stored separately", "amount": "CNY"},
            "universe": {"current_list_count": len(listed), "requested_count": len(codes),
                         "current_list_requested_fraction": len(set(codes) & listed) / len(listed) if listed else None},
            **{name: self._capability(name, start, end) for name in ["all_a", "minute", "delisted", "pit_financial"]},
        }
        return DailyData(frame, coverage)

    def financial(self, codes: Iterable[str], start: str, end: str) -> DailyData:
        """Only explicitly timestamped announcements can enter a PIT feature panel."""
        codes = sorted(set(normalize_code(c) for c in codes))
        start, end = _date(start), _date(end)
        rows = self._records("financial", {"code": {"$in": codes}})
        accepted, rejected = [], 0
        for row in rows:
            available = next((row.get(k) for k in ["announcement_date", "ann_date", "publish_date"] if row.get(k)), None)
            if available is None:
                rejected += 1
                continue
            available = _date(available)
            if start <= available <= end:
                accepted.append({**row, "symbol": normalize_code(row["code"]), "available_at": available})
        return DailyData(pd.DataFrame(accepted), {
            "status": "pending" if rejected or not accepted else "announcement_timestamp_present",
            "accepted_rows": len(accepted), "missing_announcement_rows": rejected,
            "note": "Announcement timestamp presence is necessary; provenance and revisions still require PIT acceptance.",
        })


def migration_gate(coverage: dict[str, Any], *, require_all_a: bool = True,
                   require_minutes: bool = False, require_delisted: bool = True,
                   require_pit_financial: bool = True, require_adjustment: bool = True,
                   min_daily_coverage: float = 0.98) -> dict[str, Any]:
    """A receipt describes data; this acceptance gate permits legacy retirement."""
    blockers = []
    daily = coverage.get("daily", {})
    completeness = daily.get("coverage")
    if daily.get("status") != "verified" or completeness is None or completeness < min_daily_coverage:
        blockers.append("daily coverage/calendar acceptance incomplete")
    if daily.get("invalid_rows", 0) or daily.get("duplicate_rows", 0) or daily.get("off_calendar_rows", 0):
        blockers.append("daily quality violations require reconciliation")
    adjustment_status = coverage.get("adjustment_evidence", coverage.get("xdxr", {})).get("status")
    if require_adjustment and (coverage.get("adjustment") == "none" or adjustment_status != "verified"):
        blockers.append("corporate-action adjustment unverified")
    required = {"all_a": require_all_a, "minute": require_minutes,
                "delisted": require_delisted, "pit_financial": require_pit_financial}
    for name, enabled in required.items():
        if enabled and coverage.get(name, {}).get("status") != "verified":
            blockers.append(f"{name} acceptance pending")
    return {"can_retire_legacy": not blockers, "blockers": blockers,
            "min_daily_coverage": min_daily_coverage,
            "action": "retire legacy only after archival" if not blockers else "retain legacy evidence; use AXIS for verified scope"}
