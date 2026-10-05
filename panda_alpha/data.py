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
from collections import defaultdict
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
    value = str(value).strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if value.startswith(("SH.", "SZ.", "BJ.")):
        value = value[3:]
    elif value.startswith(("SH", "SZ", "BJ")):
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


def adjust_with_price_panel(frame: pd.DataFrame, panel: pd.DataFrame, adjustment: str) -> pd.DataFrame:
    """Use the vendor's daily HFQ OHLC, rebase QFQ at this sample's end.

    This keeps independently rounded source prices rather than inventing a
    precise action factor from their ratios. Missing adjusted stock-days block
    labels. The live QFQ endpoint, which may anchor beyond the sample, is unused.
    """
    if adjustment not in {"none", "hfq", "qfq"}:
        raise ValueError("Unknown adjustment")
    out = frame.sort_values("date").copy()
    out["adj"] = 1.0
    if adjustment == "none":
        return out
    if panel.empty or "date" not in panel:
        raise PendingDataError("Same-source adjusted price panel missing")
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    if panel["date"].duplicated().any():
        raise PendingDataError("Duplicate source adjusted price dates")
    panel = panel.set_index("date").reindex(out["date"])
    prices = panel[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(prices).all().all() or (prices <= 0).any().any():
        raise PendingDataError("Missing/nonpositive same-source adjusted OHLC")
    if not (prices["high"] >= prices[["open", "close", "low"]].max(axis=1)).all() or not (prices["low"] <= prices[["open", "close", "high"]].min(axis=1)).all():
        raise PendingDataError("Invalid same-source adjusted OHLC")
    scale = float(out["close"].iloc[-1] / prices["close"].iloc[-1]) if adjustment == "qfq" else 1.0
    out["adj"] = prices["close"].to_numpy() * scale / out["close"].to_numpy()
    out["factor_date"] = out["date"]
    for col in ["open", "high", "low", "close"]:
        out[col] = prices[col].to_numpy() * scale
    return out


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
        # A verified sample must never certify complete market/source retirement.
        global_evidence = [r for r in evidence if r.get("full_universe_complete") is True
                           and r.get("evidence_sha256") and r.get("scope") == "all_a_historical"]
        if global_evidence:
            return {"status": "verified", "evidence": global_evidence[-1]}
        reasons = {
            "all_a": "Current SH/SZ stock_list is not a point-in-time historical all-A membership ledger; Beijing coverage is separate.",
            "minute": "Minute history and session completeness have not been independently accepted.",
            "delisted": "TDX's current security list does not prove coverage of delisted securities.",
            "pit_financial": "Report dates are not announcement availability dates; financial PIT evidence is pending.",
        }
        observed = self._records("panda_axis_validation", {"capability": name})
        return {"status": "pending", "reason": reasons[name],
                "available_scoped_evidence": observed[-3:]}

    def universe(self, start: str, end: str) -> DailyData:
        """Return historical effective lifecycles, including inactive securities."""
        from .universe import window_lifecycles
        records = window_lifecycles(self._records("stock_lifecycle"), _date(start), _date(end))
        return DailyData(pd.DataFrame(records), {"status": "source_lifecycles_available" if records else "pending",
            "source_record_count": len(records), "sources": sorted({r.get("source", "unknown") for r in records}),
            "historical_complete": self._capability("all_a", _date(start), _date(end)),
            "note": "Source lifecycle scope is not independent exchange/PIT completeness acceptance"})

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
        if any(row.get("research_eligibility") in {"candidate_only", "archive_only"} for row in rows):
            raise PendingDataError("Candidate/archive prices require independent source acceptance before research")
        lifecycles = self._records("stock_lifecycle", {"code": {"$in": codes}})
        life_by_code = {}
        for row in lifecycles:
            if row.get("ipo_date") and not row.get("lifecycle_issues"):
                life_by_code.setdefault(row["code"], row)
        columns = ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "adj", "factor_date", "adjustment", "source",
                   "raw_open", "raw_high", "raw_low", "raw_close", "preclose", "trade_status", "is_st", "volume_shares", "exchange"]
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
            for col in ["open", "high", "low", "close"]:
                frame["raw_" + col] = frame[col]
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
            in_lifecycle = pd.Series(True, index=frame.index)
            for code, indices in frame.groupby("symbol").groups.items():
                life = life_by_code.get(code)
                if life:
                    in_lifecycle.loc[indices] = frame.loc[indices, "date"] >= pd.Timestamp(life["ipo_date"])
                    if life.get("delisted_date"):
                        in_lifecycle.loc[indices] &= frame.loc[indices, "date"] < pd.Timestamp(life["delisted_date"])
            valid &= ~suspended
            valid &= in_lifecycle
            for index in frame.index[~valid]:
                row = frame.loc[index]
                trade_status = row.get("trade_status")
                missing_fields = row.get("missing_numeric_fields")
                excluded_rows.append({"date": _date(row["date"]), "symbol": row["symbol"],
                                      "reason": "off_lifecycle_raw_bar" if not in_lifecycle.loc[index] else "source_explicit_suspension" if suspended.loc[index] else "invalid_or_missing_raw_bar",
                                      "trade_status": None if pd.isna(trade_status) else trade_status,
                                      "missing_numeric_fields": missing_fields if isinstance(missing_fields, list) else []})
            invalid_rows = int((~valid).sum())
            frame = frame.loc[valid].copy()
        xdxr_receipts = self._receipts("stock_xdxr", codes, end)
        factor_receipts = self._receipts("stock_adj", codes, end)
        price_receipts = self._receipts("stock_adjusted_day", codes, end)
        missing_xdxr = sorted(set(codes) - set(xdxr_receipts))
        missing_adjustment = sorted(set(codes) - set(xdxr_receipts) - set(factor_receipts) - set(price_receipts))
        if adjustment != "none" and missing_adjustment:
            raise PendingDataError("Corporate-action synchronization is unverified for: " + ", ".join(missing_adjustment))
        # Complete-event receipts count the whole source response, which may
        # include actions after this research window. Verify presence first;
        # filter to the requested end before any adjustment calculation.
        events = self._records("stock_xdxr", {"code": {"$in": codes}})
        factors = self._records("stock_adj", {"code": {"$in": codes}, "date": {"$lte": end}})
        price_panels = self._records("stock_adjusted_day", {"code": {"$in": codes}, "date": {"$gte": start, "$lte": end}})
        by_events, by_factors, by_prices = defaultdict(list), defaultdict(list), defaultdict(list)
        for records, destination in ((events, by_events), (factors, by_factors), (price_panels, by_prices)):
            for record in records:
                destination[record.get("code")].append(record)
        adjusted = []
        adjustment_methods = {}
        for code, group in frame.groupby("symbol", sort=True):
            sources = set(group["source"])
            if len(sources) != 1:
                raise PendingDataError(f"{code}: mixed raw data providers require source reconciliation")
            receipt = factor_receipts.get(code)
            if receipt and sources == {receipt.get("source")}:
                code_factors = pd.DataFrame([f for f in by_factors[code] if f.get("source") == receipt["source"]])
                if adjustment != "none" and code_factors.empty and receipt.get("rows", 0) > 0:
                    raise PendingDataError(f"{code}: receipted adjustment factors are absent")
                adjusted.append(adjust_with_factors(group, code_factors, adjustment,
                                                   history_complete=receipt.get("history_complete", False), asof_end=end))
                adjustment_methods[code] = "same-source cumulative stock_adj; qfq at sample_end, hfq at IPO"
            elif code in price_receipts and sources == {price_receipts[code].get("source")}:
                panel = pd.DataFrame([r for r in by_prices[code] if r.get("source") in sources])
                adjusted.append(adjust_with_price_panel(group, panel, adjustment))
                adjustment_methods[code] = "same-source daily HFQ prices; qfq rebased at sample_end"
            else:
                if adjustment != "none" and (code not in xdxr_receipts or sources != {xdxr_receipts[code].get("source")}):
                    raise PendingDataError(f"{code}: same-source adjustment evidence unavailable")
                source_events = [r for r in by_events[code] if r.get("source") in sources]
                if adjustment != "none" and by_events[code] and not source_events:
                    raise PendingDataError(f"{code}: xdxr rows have no matching verified source")
                if adjustment != "none" and not source_events and xdxr_receipts[code].get("rows", 0) > 0:
                    raise PendingDataError(f"{code}: receipted xdxr events are absent")
                code_events = pd.DataFrame([r for r in source_events if _date(r["date"]) <= end])
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
        source_suspensions = {(r["symbol"], r["date"]) for r in excluded_rows if r["reason"] == "source_explicit_suspension"}
        def active_dates(code):
            row = life_by_code.get(code)
            return [d for d in expected if row is None or
                    (row["ipo_date"] <= d and (not row.get("delisted_date") or d < row["delisted_date"]))]
        expected_by_code = {c: active_dates(c) for c in codes}
        missing_by_code = {c: [d for d in expected_by_code[c] if (c, d) not in observed and (c, d) not in source_suspensions] for c in codes}
        expected_rows = sum(len(dates) for dates in expected_by_code.values())
        reconciled_suspensions = sum((c, d) in source_suspensions for c, dates in expected_by_code.items() for d in dates)
        # Off-calendar rows do not improve completeness; report them separately.
        present_expected = sum((c, d) in observed for c, dates in expected_by_code.items() for d in dates)
        current_list = self._records("stock_list")
        listed = {normalize_code(r["code"]) for r in current_list if "code" in r and r.get("listing_status") != "0"}
        coverage = {
            "source": "QUANTAXIS-compatible MongoDB", "raw_sources": sorted(set(frame["source"])), "start": start, "end": end,
            "codes": codes, "adjustment": adjustment,
            "daily": {"status": "verified" if calendar_verified and expected_rows else "pending",
                      "rows": len(frame), "expected_rows": expected_rows,
                      "coverage": (present_expected + reconciled_suspensions) / expected_rows if calendar_verified and expected_rows else None,
                      "tradable_price_coverage": present_expected / expected_rows if calendar_verified and expected_rows else None,
                      "missing_by_code": missing_by_code, "invalid_rows": invalid_rows,
                      "known_suspended_rows": len(source_suspensions),
                      "unexplained_invalid_rows": invalid_rows - len(source_suspensions),
                      "membership_basis": "source_effective_ipo_and_delisted_dates" if len(life_by_code) == len(codes) else "unverified_codes_use_full_calendar",
                      "excluded_rows": excluded_rows,
                      "duplicate_rows": duplicates, "off_calendar_rows": sum(d not in expected for _, d in observed),
                      "note": "Known lifecycle bounds and explicit suspensions are reconciled; unknown source gaps stay missing. No forward filling or zero imputation."},
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

    def financial_asof(self, code: str, report_date: str, decision_date: str) -> dict:
        """Resolve actual disclosed revisions; provider snapshots are excluded."""
        from .financial import query_financial_asof
        return query_financial_asof(self.db, code=normalize_code(code),
                                   report_date=_date(report_date), decision_date=_date(decision_date))

    def catalog(self) -> dict:
        """Read actual stored collection counts without turning counts into PIT proof."""
        names = ["stock_lifecycle", "stock_day", "stock_adj", "stock_adjusted_day",
                 "trade_calendar", "stock_filing_index", "stock_financial_pit",
                 "stock_financial_revision", "stock_financial_provider_snapshot"]
        return {"collections": {name: self.db[name].count_documents({}) for name in names},
                "capabilities": self._records("panda_axis_validation"),
                "interpretation": "Stored records and scoped receipts; independent completeness/PIT gates still apply"}


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
    if daily.get("unexplained_invalid_rows", daily.get("invalid_rows", 0)) or daily.get("duplicate_rows", 0) or daily.get("off_calendar_rows", 0):
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
