"""Pre-open descriptors and fixed-horizon labels for two limit-up closes.

This module consumes supplied raw prices, same-session price limits and an
independent reference calendar. It neither fetches data nor infers exchange
rules, suspension status, factor roles, queue fills or a profitable direction.
Missing reference sessions remain missing; no backfill or trading-day
compression is permitted. Same-source cumulative adjustment factors may only
be used under the explicit ``total_return_factor`` contract.
"""
from __future__ import annotations

import inspect
import math

import numpy as np
import pandas as pd


class LimitEventInputError(ValueError):
    """Input identities or the independent calendar violate the contract."""


FIELDS = ("raw_close", "raw_high", "raw_low", "up_limit", "down_limit",
          "amount", "adjustment_factor", "eligible")
FEATURES = ("prior_extension_20", "locked_board_fraction", "board_amount_surge")
_TOLERANCE = 1e-7
_FACTOR_ROLE = "total_return_factor"


def _day(value):
    try:
        value = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise LimitEventInputError("Invalid reference date") from error
    if pd.isna(value) or value.tzinfo is not None or value != value.normalize():
        raise LimitEventInputError("Reference dates must be timezone-free whole days")
    return value


def _calendar(calendar):
    days = pd.DatetimeIndex([_day(value) for value in calendar], name="date")
    if not len(days) or not days.is_unique or not days.is_monotonic_increasing:
        raise LimitEventInputError("Independent calendar must be sorted and unique")
    return days


def _normalize(bars, days):
    """Accept a date/symbol long table or columns=(field, symbol) wide table."""
    frame = pd.DataFrame(bars)
    if isinstance(frame.columns, pd.MultiIndex):
        if frame.columns.nlevels != 2 or not frame.columns.is_unique:
            raise LimitEventInputError("Wide columns must be unique (field, symbol) pairs")
        if not frame.index.is_unique:
            raise LimitEventInputError("Duplicate wide reference dates")
        frame = frame.copy()
        frame.index.name = "date"
        frame.columns.names = ["field", "symbol"]
        # dropna=False retains missing reference bars instead of compressing time.
        stack_options = ({"future_stack": True}
                         if "future_stack" in inspect.signature(pd.DataFrame.stack).parameters
                         else {"dropna": False})
        frame = frame.stack(level="symbol", **stack_options).reset_index()
    else:
        frame = frame.copy()
    aliases = {"close": "raw_close", "high": "raw_high", "low": "raw_low",
               "entry_eligible": "eligible", "code": "symbol"}
    frame = frame.rename(columns={key: value for key, value in aliases.items()
                                  if key in frame and value not in frame})
    if not {"date", "symbol", *FIELDS}.issubset(frame):
        raise LimitEventInputError("Raw fields, amount, factor and eligibility are required")
    if not frame.columns.is_unique or frame["symbol"].isna().any():
        raise LimitEventInputError("Duplicate fields or missing symbol identities")
    dates = frame["date"].unique()
    frame["date"] = frame["date"].map({date: _day(date) for date in dates})
    frame["symbol"] = frame["symbol"].astype(str)
    if (frame["symbol"].str.len().eq(0).any() or
            frame.duplicated(["date", "symbol"]).any()):
        raise LimitEventInputError("Missing or duplicate raw-bar identities")
    if not frame["date"].isin(days).all():
        raise LimitEventInputError("Raw dates must belong to the supplied reference calendar")
    frame = frame[["date", "symbol", *FIELDS]].copy()
    for name in FIELDS[:-1]:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    # Strings such as 'False', numerical flags and missing roles are not booleans.
    frame["eligible"] = frame["eligible"].map(
        lambda value: isinstance(value, (bool, np.bool_)) and bool(value))
    return frame.sort_values(["symbol", "date"])


def _positive(values):
    values = np.asarray(values, dtype=float)
    return np.isfinite(values) & (values > 0)


def _up_closes(frame):
    close, high, low, up, down = (frame[name].to_numpy(dtype=float) for name in
                                  ("raw_close", "raw_high", "raw_low", "up_limit", "down_limit"))
    valid = np.logical_and.reduce([_positive(value) for value in (close, high, low, up, down)])
    valid &= ((low <= close + _TOLERANCE) & (close <= high + _TOLERANCE) &
              (high <= up + _TOLERANCE) & (low >= down - _TOLERANCE) & (up >= down))
    return valid & (np.abs(close - up) <= _TOLERANCE)


def build_event_features(bars, calendar, *, adjustment_factor_role=None):
    """Return every eligible pre-open two-board candidate, without filtering.

    For bid session t, prior_extension_20 uses the full 21-reference-session
    adjusted close block t-23..t-3; locked_board_fraction uses t-2 and t-1;
    board_amount_surge is log(amount[t-1] / median(amount[t-22:t-2])).
    The denominator block contains exactly 20 sessions and excludes both boards.
    Unknown histories produce NaN features but do not remove valid candidates.
    ``touch_event`` uses t's eventual low only as a separate diagnostic and is
    neither a pre-open filter nor evidence of a fill. Eligibility and t's limit
    are supplied as pre-open roles; their publication history is not certified.
    """
    days = _calendar(calendar)
    frame = _normalize(bars, days)
    result = []
    for symbol, stock in frame.groupby("symbol", sort=True):
        stock = stock.set_index("date").reindex(days)
        up = _up_closes(stock)
        eligible = stock["eligible"].eq(True).to_numpy()
        candidates = np.flatnonzero(up[:-2] & up[1:-1] & eligible[:-2] &
                                    eligible[1:-1] & eligible[2:]) + 2
        close = stock["raw_close"].to_numpy(dtype=float)
        high = stock["raw_high"].to_numpy(dtype=float)
        low = stock["raw_low"].to_numpy(dtype=float)
        upper = stock["up_limit"].to_numpy(dtype=float)
        lower = stock["down_limit"].to_numpy(dtype=float)
        factor = stock["adjustment_factor"].to_numpy(dtype=float)
        amount = stock["amount"].to_numpy(dtype=float)
        for t in candidates:
            extension = math.nan
            extension_reason = "insufficient_reference_history"
            if adjustment_factor_role != _FACTOR_ROLE:
                extension_reason = "adjustment_factor_role_unknown"
            elif t >= 23:
                prices, factors = close[t - 23:t - 2], factor[t - 23:t - 2]
                if (_positive(prices).all() and _positive(factors).all() and
                        np.isfinite(prices * factors).all()):
                    adjusted = prices * factors
                    if (adjusted > 0).all():
                        extension = float(adjusted[-1] / adjusted[0] - 1.)
                    extension_reason = ("complete_21_reference_sessions"
                                        if math.isfinite(extension) else "invalid_adjusted_price")
                    if not math.isfinite(extension):
                        extension = math.nan
                else:
                    extension_reason = "missing_or_invalid_reference_close_or_factor"
            locked = float(np.mean((np.abs(high[t - 2:t] - upper[t - 2:t]) <= _TOLERANCE) &
                                   (np.abs(low[t - 2:t] - upper[t - 2:t]) <= _TOLERANCE)))
            surge, surge_reason = math.nan, "insufficient_reference_history"
            if t >= 22:
                denominator = amount[t - 22:t - 2]
                if _positive(denominator).all() and _positive([amount[t - 1]]).all():
                    surge = float(math.log(amount[t - 1]) - math.log(float(np.median(denominator))))
                    surge_reason = "complete_20_reference_sessions"
                else:
                    surge_reason = "missing_or_nonpositive_amount"
            touch = (bool(low[t] <= lower[t] + _TOLERANCE)
                     if _positive([low[t], lower[t]]).all() else pd.NA)
            result.append({"date": days[t].strftime("%Y-%m-%d"), "symbol": symbol,
                           "first_board_date": days[t - 2].strftime("%Y-%m-%d"),
                           "second_board_date": days[t - 1].strftime("%Y-%m-%d"),
                           "bid_price": float(lower[t]) if _positive([lower[t]]).all() else math.nan,
                           "prior_extension_20": extension,
                           "prior_extension_reason": extension_reason,
                           "locked_board_fraction": locked,
                           "board_amount_surge": surge,
                           "board_amount_reason": surge_reason,
                           "touch_event": touch, "fill_certified": False})
    columns = ["date", "symbol", "first_board_date", "second_board_date", "bid_price",
               "prior_extension_20", "prior_extension_reason", "locked_board_fraction",
               "board_amount_surge", "board_amount_reason", "touch_event", "fill_certified"]
    return pd.DataFrame(result, columns=columns).sort_values(["date", "symbol"]).reset_index(drop=True)


def future_labels(bars, calendar, events, *, horizon=5, adjustment_factor_role=None):
    """Compute gross fixed-horizon labels separately from pre-open features.

    Cost is supplied bid_price * factor[t]; the return uses close[t+horizon] *
    factor[t+horizon]. MAE is the minimum adjusted low over t+1..t+horizon,
    relative to that cost (entry-session extrema excluded). Every future
    reference session must contain positive finite close, low and same-source
    factor. Neither suspension nor absent claims are filled. Labels describe
    a hypothetical bid cost, not a completed queue fill or strategy exit.
    """
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise LimitEventInputError("Positive integer reference-session horizon required")
    days = _calendar(calendar)
    frame = _normalize(bars, days)
    events = pd.DataFrame(events).copy()
    if not {"date", "symbol", "bid_price"}.issubset(events):
        raise LimitEventInputError("Event date, symbol and pre-known bid_price required")
    if events[["date", "symbol"]].isna().any().any():
        raise LimitEventInputError("Missing event identities")
    events["date"] = pd.DatetimeIndex([_day(value) for value in events["date"]])
    events["symbol"] = events["symbol"].astype(str)
    if events.duplicated(["date", "symbol"]).any() or not events["date"].isin(days).all():
        raise LimitEventInputError("Duplicate events or event outside reference calendar")
    event_symbols = set(events["symbol"])
    by_symbol = {symbol: stock.set_index("date").reindex(days)
                 for symbol, stock in frame.groupby("symbol", sort=False)
                 if symbol in event_symbols}
    positions = {date: i for i, date in enumerate(days)}
    returns, maes, reasons = [], [], []
    for row in events.itertuples(index=False):
        value, mae, reason = math.nan, math.nan, "adjustment_factor_role_unknown"
        if adjustment_factor_role == _FACTOR_ROLE:
            t, stock = positions[row.date], by_symbol.get(row.symbol)
            if t + horizon >= len(days):
                reason = "future_reference_window_incomplete"
            elif stock is None:
                reason = "event_symbol_source_missing"
            else:
                bid = pd.to_numeric(pd.Series([row.bid_price]), errors="coerce").iloc[0]
                entry_factor = stock["adjustment_factor"].iloc[t]
                block = stock.iloc[t + 1:t + horizon + 1]
                close = block["raw_close"].to_numpy(dtype=float)
                low = block["raw_low"].to_numpy(dtype=float)
                factor = block["adjustment_factor"].to_numpy(dtype=float)
                if (_positive([bid, entry_factor]).all() and _positive(close).all() and
                        _positive(low).all() and _positive(factor).all() and
                        (low <= close + _TOLERANCE).all()):
                    cost = float(bid * entry_factor)
                    adjusted_close, adjusted_low = close * factor, low * factor
                    if (math.isfinite(cost) and cost > 0 and
                            np.isfinite(adjusted_close).all() and np.isfinite(adjusted_low).all()):
                        value = float(adjusted_close[-1] / cost - 1.)
                        mae = float(adjusted_low.min() / cost - 1.)
                        if math.isfinite(value) and math.isfinite(mae):
                            reason = "complete_future_reference_window"
                        else:
                            value, mae, reason = math.nan, math.nan, "invalid_adjusted_price"
                    else:
                        reason = "invalid_adjusted_price"
                else:
                    reason = "missing_or_invalid_reference_price_or_factor"
        returns.append(value)
        maes.append(mae)
        reasons.append(reason)
    events["date"] = events["date"].dt.strftime("%Y-%m-%d")
    events[f"future_return_{horizon}"] = returns
    events[f"future_mae_{horizon}"] = maes
    events["label_reason"] = reasons
    events["label_complete"] = np.isfinite(returns) & np.isfinite(maes)
    return events
