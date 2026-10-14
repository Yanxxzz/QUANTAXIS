"""Pure supplied-panel entry clocks; no data, returns or account execution.

Raw OHLC and session limits must share the unadjusted basis. Only first-board
pullback strength compares close * cumulative factor under an explicitly named
total-return-factor role. A close-formed signal enters no earlier than the next
independent reference session. Entry-session eligibility, status, prices and
volume never select it; the account checks them only after reserving orders.
"""
from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd


class LimitEntrySignalError(ValueError):
    pass


FIELDS = ("close", "high", "low", "open", "up_limit", "down_limit", "amount",
          "volume_shares", "adjustment_factor", "eligible", "trade_status")
TOL = 1e-7
ROLE = "total_return_factor"


def _days(values):
    result = pd.DatetimeIndex(pd.to_datetime(list(values), errors="raise"))
    if (not len(result) or result.hasnans or result.tz is not None or
            not result.is_unique or not result.is_monotonic_increasing or
            not result.equals(result.normalize())):
        raise LimitEntrySignalError("Sorted unique timezone-free reference days required")
    return result


def _eligible(value):
    if isinstance(value, (bool, np.bool_)):
        return int(bool(value))
    return -1


def _status(value):
    if isinstance(value, (bool, np.bool_)):
        return -1
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"1", "1.0", "trading"}:
            return 1
        if value in {"0", "0.0", "suspended", "no_trade", "delisted"}:
            return 0
        return -1
    if isinstance(value, (int, float, np.integer, np.floating)) and np.isfinite(value):
        return int(value) if value in {0, 1} else -1
    return -1


def _normalize(panel, calendar):
    if not isinstance(panel, Mapping) or not set(FIELDS).issubset(panel):
        raise LimitEntrySignalError("Exact raw fields, eligible and trade_status panels required")
    days = _days(calendar)
    anchor = panel["close"]
    if not isinstance(anchor, pd.DataFrame) or not anchor.columns.is_unique or not len(anchor.columns):
        raise LimitEntrySignalError("Unique nonempty stock identity columns required")
    symbols = pd.Index(anchor.columns)
    if symbols.isna().any() or any(not isinstance(x, str) or not x for x in symbols):
        raise LimitEntrySignalError("Supplied symbol identities must be nonempty strings")
    values = {}
    for name in FIELDS:
        field = panel[name]
        if not isinstance(field, pd.DataFrame):
            raise LimitEntrySignalError("Every field must be an independently dated DataFrame")
        index = _days(field.index)
        if (not index.equals(days) or not field.columns.is_unique or
                set(field.columns) != set(symbols)):
            raise LimitEntrySignalError("Full reference grid and stock identities must align; missing bars use NaN")
        field = field.loc[:, symbols]
        if name in {"eligible", "trade_status"}:
            raw = field.to_numpy()
            if name == "eligible" and raw.dtype == np.bool_:
                values[name] = raw.astype(np.int8)
            else:
                parser = _eligible if name == "eligible" else _status
                values[name] = np.fromiter((parser(x) for x in raw.flat), dtype=np.int8,
                                           count=raw.size).reshape(raw.shape)
        else:
            try:
                values[name] = field.to_numpy(dtype=float, na_value=np.nan)
            except (TypeError, ValueError):
                values[name] = field.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    return days.strftime("%Y-%m-%d").tolist(), symbols.tolist(), values


def _price_valid(x):
    valid = np.ones(x["close"].shape, dtype=bool)
    for name in ("close", "high", "low", "open", "up_limit", "down_limit"):
        valid &= np.isfinite(x[name]) & (x[name] > 0)
    valid &= ((x["low"] <= x["open"] + TOL) & (x["open"] <= x["high"] + TOL) &
              (x["low"] <= x["close"] + TOL) & (x["close"] <= x["high"] + TOL) &
              (x["low"] >= x["down_limit"] - TOL) &
              (x["high"] <= x["up_limit"] + TOL) &
              (x["down_limit"] <= x["up_limit"]))
    return valid


def _base(mask, days, symbols):
    if not isinstance(mask, Mapping):
        raise LimitEntrySignalError("Precomputed base mask must map (reference date, code) to booleans")
    known_days, known_codes = set(days), set(symbols)
    result = {}
    for key, value in mask.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise LimitEntrySignalError("Base keys must be (date, code)")
        try:
            date = _days([key[0]])[0].strftime("%Y-%m-%d")
        except (TypeError, ValueError) as error:
            raise LimitEntrySignalError("Invalid base reference date") from error
        code = key[1]
        if date not in known_days or code not in known_codes:
            raise LimitEntrySignalError("Base identities outside supplied source grid")
        canonical = (date, code)
        if canonical in result:
            raise LimitEntrySignalError("Duplicate canonical base identity")
        result[canonical] = _eligible(value) == 1
    return result


def _strict_economic_above(close, factor, prior_close, prior_factor):
    # Original print precision, without anchoring either price to a future factor.
    return (Decimal(str(close)) * Decimal(str(factor)) >
            Decimal(str(prior_close)) * Decimal(str(prior_factor)))


def build_limit_entry_signals(panel, calendar, base_mask, *, adjustment_factor_role=None):
    """Return masks keyed by ENTRY session plus compact formation/coverage rows.

    recovery_parent_mask is the same known base lower-limit-touch event shifted
    to next open; recovery_mask additionally requires its close above lower.
    first_board_parent_mask enters b+2 after a confirmed first upper close b,
    twenty complete/no-upper prior traded sessions and normal non-upper b+1.
    All those bars require positive shares. Parent and target share a known
    economic-factor pair; the target additionally requires b+1 low above lower
    and economic close above b-1.
    Neither first-board mask inherits the old 18.60-percent threshold.

    market_mask retains today's old base only when prior-day known-cohort lower
    closes do not outnumber upper closes. No valid cohort is UNKNOWN and blocks.
    Partial source coverage is recorded; missing rows are not counted as known
    non-limit closes, and no fitted coverage threshold or all-A claim is made.
    amount is only a coverage descriptor, never a breadth/cohort gate.
    """
    days, symbols, x = _normalize(panel, calendar)
    if adjustment_factor_role not in {None, ROLE}:
        raise LimitEntrySignalError("Unknown adjustment-factor basis")
    base = _base(base_mask, days, symbols)
    index = {d: i for i, d in enumerate(days)}
    codes = {c: i for i, c in enumerate(symbols)}
    prices = _price_valid(x)
    trading = x["trade_status"] == 1
    eligible = x["eligible"] == 1
    normal = prices & trading
    shares = np.isfinite(x["volume_shares"]) & (x["volume_shares"] > 0)
    up = normal & (np.abs(x["close"] - x["up_limit"]) <= TOL)
    down = normal & (np.abs(x["close"] - x["down_limit"]) <= TOL)
    recovery_parent, recovery, market, allowed = {}, {}, {}, {}
    market_rows = []
    for t, date in enumerate(days):
        if t == 0:
            row = {"entry_date": date, "source_date": None, "source_identity_count": len(symbols),
                   "status": "UNKNOWN_NO_PREVIOUS_REFERENCE_SESSION", "market_allowed": False}
        else:
            p = t - 1
            observed = eligible[p] & normal[p] & shares[p]
            n = int(observed.sum())
            upper, lower = int((observed & up[p]).sum()), int((observed & down[p]).sum())
            permitted = n > 0 and lower <= upper
            row = {"entry_date": date, "source_date": days[p], "source_identity_count": len(symbols),
                   "eligible_true_count": int(eligible[p].sum()),
                   "eligible_false_count": int((x["eligible"][p] == 0).sum()),
                   "unknown_eligible_count": int((x["eligible"][p] == -1).sum()),
                   "known_nontrading_count": int((x["trade_status"][p] == 0).sum()),
                   "unknown_status_count": int((x["trade_status"][p] == -1).sum()),
                   "missing_or_invalid_raw_price_limit_count": int((~prices[p]).sum()),
                   "missing_or_nonpositive_shares_count": int((~shares[p]).sum()),
                   "missing_or_nonpositive_amount_count": int((~np.isfinite(x["amount"][p]) | (x["amount"][p] <= 0)).sum()),
                   "eligible_trading_source_unknown_count": int((eligible[p] & (~normal[p] | ~shares[p])).sum()),
                   "observed_cohort_count": n, "known_upper_closes": upper if n else None,
                   "known_lower_closes": lower if n else None, "market_allowed": permitted,
                   "status": "KNOWN_COHORT_ONLY" if n else "UNKNOWN_NO_VALID_COHORT"}
        allowed[date] = row["market_allowed"]
        market_rows.append(row)
    for (date, code), yes in base.items():
        if not yes:
            continue
        t, j = index[date], codes[code]
        if allowed[date]:
            market[(date, code)] = True
        if (t + 1 < len(days) and eligible[t, j] and normal[t, j] and shares[t, j] and
                abs(x["low"][t, j] - x["down_limit"][t, j]) <= TOL):
            entry = (days[t + 1], code)
            recovery_parent[entry] = True
            if x["close"][t, j] > x["down_limit"][t, j] + TOL:
                recovery[entry] = True

    first_normal = normal & shares
    complete20 = pd.DataFrame(first_normal).rolling(20, min_periods=20).sum().shift(1).eq(20).to_numpy()
    no_up20 = pd.DataFrame(up).rolling(20, min_periods=20).sum().shift(1).eq(0).to_numpy()
    first_board = up & shares & eligible & complete20 & no_up20
    formation, first_parent, first_target, parent_rows = {}, {}, {}, []
    for b, j in np.argwhere(first_board):
        b, j = int(b), int(j)
        code = symbols[j]
        formation[(days[b], code)] = True
        row = {"symbol": code, "first_board_date": days[b],
               "pullback_date": days[b + 1] if b + 1 < len(days) else None,
               "entry_date": days[b + 2] if b + 2 < len(days) else None,
               "parent_signal": False, "target_signal": False,
               "economic_factor_role_known": adjustment_factor_role == ROLE}
        if b + 2 >= len(days):
            row["reason"] = "NEXT_REFERENCE_ENTRY_UNAVAILABLE"
        elif not (eligible[b + 1, j] and first_normal[b + 1, j]):
            row["reason"] = "PULLBACK_SOURCE_OR_FORMATION_ELIGIBILITY_UNKNOWN_OR_INELIGIBLE"
        elif up[b + 1, j]:
            row["reason"] = "PULLBACK_STILL_CLOSES_AT_UPPER"
        else:
            key = (days[b + 2], code)
            factors = [x["adjustment_factor"][b - 1, j], x["adjustment_factor"][b + 1, j]]
            factor_known = adjustment_factor_role == ROLE and all(np.isfinite(f) and f > 0 for f in factors)
            row["economic_pair_known"] = factor_known
            clear_lower = x["low"][b + 1, j] > x["down_limit"][b + 1, j] + TOL
            row["pullback_low_above_lower"] = bool(clear_lower)
            if not factor_known:
                row["reason"] = "ECONOMIC_COMPARISON_SOURCE_UNKNOWN"
            else:
                row["parent_signal"] = True
                first_parent[key] = True
                stronger = _strict_economic_above(x["close"][b + 1, j], factors[1],
                                                  x["close"][b - 1, j], factors[0])
                row["pullback_economic_close_above_preboard"] = bool(stronger)
                row["target_signal"] = bool(clear_lower and stronger)
                row["reason"] = "TARGET_PASSED" if row["target_signal"] else "PULLBACK_FILTER_NOT_PASSED"
                if row["target_signal"]:
                    first_target[key] = True
        parent_rows.append(row)
    return {"recovery_mask": recovery, "recovery_parent_mask": recovery_parent,
            "market_mask": market, "market_allowed_by_day": allowed,
            "market_coverage": market_rows, "first_board_mask": first_target,
            "first_board_parent_mask": first_parent, "first_board_formation_mask": formation,
            "first_board_parent_rows": parent_rows,
            "coverage": {"source_identity_count": len(symbols), "reference_sessions": len(days),
                         "base_true_candidates": sum(base.values()),
                         "recovery_parent_entries": len(recovery_parent), "recovery_target_entries": len(recovery),
                         "market_base_entries": len(market), "first_board_formations": len(formation),
                         "first_board_parent_entries": len(first_parent), "first_board_target_entries": len(first_target),
                         "eligible_upper_closes_with_unknown_prior20": int((up & eligible & ~complete20).sum()),
                         "eligible_upper_closes_with_invalid_current_shares": int((up & eligible & ~shares).sum()),
                         "first_board_parent_economic_pair_unknown": sum(row["reason"] == "ECONOMIC_COMPARISON_SOURCE_UNKNOWN" for row in parent_rows),
                         "eligible_upper_closes_with_known_prior_up": int((up & eligible & complete20 & ~no_up20).sum())},
            "contracts": {"price_basis": "unadjusted OHLC/session limits; close*cumulative factor only for first-board strength",
                          "first_board_target_factor_role": adjustment_factor_role,
                          "entry_session_prices_or_volume_used_for_selection": False,
                          "next_open_entry_eligibility_or_status_used_for_selection": False,
                          "returns_or_fill_quality_computed": False,
                          "historical_market_membership_or_full_A_coverage_certified": False,
                          "known_nontrading_role": "Excluded from traded breadth; source evidence does not create a non-limit close",
                          "partial_unknown_role": "Unknown statuses/prices/eligibility remain separate; counts describe only the observed original-source cohort",
                          "source_only_held_status_is_predictive": False}}
