"""Exact wealth-unit accounting and explicitly noncausal market projections.

The caller supplies executions, marks and pre-known beta vintages. This module
does not simulate fills, estimate betas, select securities or fetch data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

import numpy as np


class AttributionError(ValueError):
    """Missing or inconsistent accounting inputs must not be silently repaired."""


@dataclass(frozen=True)
class Trade:
    code: str
    quantity_change: float
    price: float
    fee: float


@dataclass(frozen=True)
class BetaExposure:
    value: float
    formation_date: str


def _finite(value, label, *, positive=False, nonnegative=False):
    try:
        x = float(value)
    except (TypeError, ValueError) as exc:
        raise AttributionError(f"Invalid {label}") from exc
    if not isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise AttributionError(f"Invalid {label}")
    return x


def _date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise AttributionError("ISO formation/accounting date required") from exc
    if parsed.isoformat() != value:
        raise AttributionError("Canonical ISO date required")
    return parsed


def _projection(wealth, pnl, beta, market_return, day):
    if wealth == 0:
        return 0.0, 0.0, 0.0, True
    if beta is None or market_return is None:
        return 0.0, 0.0, pnl, False
    if _date(beta.formation_date) >= _date(day):
        raise AttributionError("Beta must be known before the holding day")
    b = _finite(beta.value, "beta")
    m = _finite(market_return, "same-leg market return")
    projected = wealth * b * m
    return projected, pnl - projected, 0.0, True


def account_day(
    *, day: str, previous_nav: float, cash_before: float,
    quantities_before: Mapping[str, float], previous_close: Mapping[str, float],
    open_marks: Mapping[str, float], close_marks: Mapping[str, float],
    trades: Sequence[Trade], cash_after: float,
    overnight_beta: Mapping[str, BetaExposure] | None = None,
    intraday_beta: Mapping[str, BetaExposure] | None = None,
    overnight_market: Mapping[str, float | None] | None = None,
    intraday_market: Mapping[str, float | None] | None = None,
    tolerance: float = 1e-10,
) -> dict:
    """Split close-to-close NAV changes at actual open transactions.

    Previous holdings earn previous-close-to-open PnL; post-trade holdings earn
    open-to-close PnL. Fees belong to their traded security. Missing projections
    leave price PnL explicitly unprojected and never change exact accounting.
    Fractional adjusted-price units are permitted, but short positions are not.
    """
    _date(day)
    tolerance = _finite(tolerance, "tolerance", positive=True)
    nav0 = _finite(previous_nav, "previous NAV", positive=True)
    cash0 = _finite(cash_before, "cash before", nonnegative=True)
    cash1 = _finite(cash_after, "cash after", nonnegative=True)
    before = {str(c): _finite(q, f"quantity {c}", nonnegative=True) for c, q in quantities_before.items() if q != 0}
    after = dict(before)
    fees = defaultdict(float)
    traded_values = defaultdict(float)
    expected_cash = cash0
    for trade in trades:
        c = str(trade.code)
        q = _finite(trade.quantity_change, "trade quantity")
        p = _finite(trade.price, "trade price", positive=True)
        fee = _finite(trade.fee, "trade fee", nonnegative=True)
        if c not in open_marks or abs(p - _finite(open_marks[c], "open mark", positive=True)) > tolerance * max(1.0, p):
            raise AttributionError("Trade price differs from supplied execution-time mark")
        after[c] = after.get(c, 0.0) + q
        if after[c] < -tolerance:
            raise AttributionError("Sell exceeds held quantity")
        after[c] = max(0.0, after[c])
        expected_cash -= q * p + fee
        fees[c] += fee
        traded_values[c] += abs(q * p)
    cash_error = cash1 - expected_cash
    if abs(cash_error) > tolerance * max(1.0, nav0):
        raise AttributionError("Cash does not reconcile to supplied trades and fees")
    before_value = cash0 + sum(q * _finite(previous_close.get(c), f"previous close {c}", positive=True) for c, q in before.items())
    if abs(before_value - nav0) > tolerance * max(1.0, nav0):
        raise AttributionError("Previous NAV does not reconcile to positions and cash")
    codes = sorted(set(before) | set(after) | set(fees))
    rows = []
    overnight_beta = overnight_beta or {}
    intraday_beta = intraday_beta or {}
    overnight_market = overnight_market or {}
    intraday_market = intraday_market or {}
    for c in codes:
        q0, q1 = before.get(c, 0.0), after.get(c, 0.0)
        op = _finite(open_marks.get(c), f"open mark {c}", positive=True)
        cl = _finite(close_marks.get(c), f"close mark {c}", positive=True)
        prior = _finite(previous_close.get(c), f"previous close {c}", positive=True) if q0 else op
        overnight_pnl = q0 * (op - prior)
        intraday_pnl = q1 * (cl - op)
        o = _projection(q0 * prior, overnight_pnl, overnight_beta.get(c), overnight_market.get(c), day)
        i = _projection(q1 * op, intraday_pnl, intraday_beta.get(c), intraday_market.get(c), day)
        row = {
            "date": day, "code": c, "quantity_before": q0, "quantity_after": q1,
            "previous_close": prior, "open_mark": op, "close_mark": cl,
            "overnight_price_pnl": overnight_pnl, "intraday_price_pnl": intraday_pnl,
            "price_pnl": overnight_pnl + intraday_pnl, "fee": fees[c],
            "net_contribution": overnight_pnl + intraday_pnl - fees[c],
            "traded_value": traded_values[c],
            "overnight_market_projection": o[0], "intraday_market_projection": i[0],
            "market_projection": o[0] + i[0],
            "statistical_residual_pnl": o[1] + i[1], "unprojected_price_pnl": o[2] + i[2],
            "overnight_projection_known": o[3], "intraday_projection_known": i[3],
            "overnight_beta": overnight_beta[c].value if c in overnight_beta else None,
            "intraday_beta": intraday_beta[c].value if c in intraday_beta else None,
            "overnight_beta_formation_date": overnight_beta[c].formation_date if c in overnight_beta else None,
            "intraday_beta_formation_date": intraday_beta[c].formation_date if c in intraday_beta else None,
        }
        rows.append(row)
    nav1 = cash1 + sum(q * _finite(close_marks.get(c), f"close mark {c}", positive=True) for c, q in after.items() if q)
    sum_price = sum(x["price_pnl"] for x in rows)
    sum_fees = sum(x["fee"] for x in rows)
    accounting_error = (nav1 - nav0) - (sum_price - sum_fees)
    if abs(accounting_error) > tolerance * max(1.0, nav0):
        raise AttributionError("Price PnL less fees does not reconcile to NAV")
    projection = sum(x["market_projection"] for x in rows)
    residual = sum(x["statistical_residual_pnl"] for x in rows)
    unknown = sum(x["unprojected_price_pnl"] for x in rows)
    projection_error = sum_price - (projection + residual + unknown)
    if abs(projection_error) > tolerance * max(1.0, nav0):
        raise AttributionError("Projection components do not reconcile to price PnL")
    return {
        "date": day, "previous_nav": nav0, "nav": nav1, "net_return": nav1 / nav0 - 1,
        "nav_change": nav1 - nav0, "cash_after": cash1, "price_pnl": sum_price,
        "fees": sum_fees, "market_projection": projection,
        "statistical_residual_pnl": residual, "unprojected_price_pnl": unknown,
        "accounting_error": accounting_error, "cash_error": cash_error,
        "projection_error": projection_error, "positions_after": {c: q for c, q in after.items() if q},
        "security_rows": rows,
    }


def summarize(days: Sequence[dict], *, folds: int = 5) -> dict:
    """Aggregate exact contributions without treating residuals as causal alpha."""
    if not days or folds < 1 or folds > len(days):
        raise AttributionError("Nonempty chronological days and valid fold count required")
    dates = [x["date"] for x in days]
    if dates != sorted(set(dates)):
        raise AttributionError("Unique ordered accounting days required")
    for previous, current in zip(days, days[1:]):
        if abs(current["previous_nav"] - previous["nav"]) > 1e-10:
            raise AttributionError("Daily NAV chain is broken")
    by_code = defaultdict(lambda: defaultdict(float))
    fields = ("price_pnl", "fee", "net_contribution", "market_projection", "statistical_residual_pnl", "unprojected_price_pnl", "traded_value")
    for day in days:
        for row in day["security_rows"]:
            for key in fields:
                by_code[row["code"]][key] += row[key]
    securities = [{"code": c, **dict(values)} for c, values in by_code.items()]
    securities.sort(key=lambda x: (-x["net_contribution"], x["code"]))
    positive = [x["net_contribution"] for x in securities if x["net_contribution"] > 0]
    positive_total = sum(positive)
    weights = [x / positive_total for x in positive] if positive_total else []
    fold_rows = []
    for index, positions in enumerate(np.array_split(np.arange(len(days)), folds), 1):
        chunk = [days[int(p)] for p in positions]
        row = {"fold": index, "start": chunk[0]["date"], "end": chunk[-1]["date"], "days": len(chunk),
               "starting_nav": chunk[0]["previous_nav"], "ending_nav": chunk[-1]["nav"],
               "net_return": chunk[-1]["nav"] / chunk[0]["previous_nav"] - 1}
        for key in ("nav_change", "price_pnl", "fees", "market_projection", "statistical_residual_pnl", "unprojected_price_pnl"):
            row[key] = sum(x[key] for x in chunk)
        fold_rows.append(row)
    result = {
        "holding_days": len(days), "start": dates[0], "end": dates[-1],
        "initial_nav": days[0]["previous_nav"], "final_nav": days[-1]["nav"],
        "cumulative_net_return": days[-1]["nav"] / days[0]["previous_nav"] - 1,
        "max_absolute_accounting_error": max(abs(x["accounting_error"]) for x in days),
        "max_absolute_cash_error": max(abs(x["cash_error"]) for x in days),
        "max_absolute_projection_error": max(abs(x["projection_error"]) for x in days),
        "contributing_security_count": len(securities), "positive_contributor_count": len(positive),
        "negative_contributor_count": sum(x["net_contribution"] < 0 for x in securities),
        "positive_net_contribution_sum": positive_total,
        "negative_net_contribution_sum": sum(x["net_contribution"] for x in securities if x["net_contribution"] < 0),
        "concentration": {"positive_contribution_hhi": sum(x*x for x in weights),
                          **{f"top_{n}_share_of_positive_contributions": sum(weights[:n]) for n in (1, 5, 10, 20)}},
        "securities": securities, "folds": fold_rows,
        "interpretation": "Market projection and remaining statistical PnL are mechanical association accounting, not causal market/alpha attribution.",
    }
    for key in ("nav_change", "price_pnl", "fees", "market_projection", "statistical_residual_pnl", "unprojected_price_pnl"):
        result[key] = sum(x[key] for x in days)
    return result
