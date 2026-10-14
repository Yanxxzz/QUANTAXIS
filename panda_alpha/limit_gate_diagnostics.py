"""Supplied-panel descriptors before the two mandatory upper-limit sessions.

Only fixed entry identities and their pre-existing cash-budget upper bounds are
consumed. This module does not detect events, fetch prices, evaluate a strategy,
read future labels, choose a gate or establish alpha. Price continuity requires
the caller's explicit same-source total_return_factor role. No reference-day
compression, interpolation or backfill is performed.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import math

import numpy as np
import pandas as pd


class LimitGateDiagnosticInputError(ValueError):
    """The supplied identity/calendar/price-role contract is invalid."""


FIELDS = ("close", "adjustment_factor", "amount")
ROLE = "total_return_factor"


def _dates(values, *, empty_ok=False):
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(list(values), errors="raise"))
    except (TypeError, ValueError) as error:
        raise LimitGateDiagnosticInputError("Invalid independently supplied reference dates") from error
    if ((not len(dates) and not empty_ok) or dates.hasnans or dates.tz is not None or
            not dates.is_unique or not dates.is_monotonic_increasing or
            not dates.equals(dates.normalize())):
        raise LimitGateDiagnosticInputError("Sorted unique timezone-free reference days required")
    return dates


def _positive(values):
    return np.isfinite(values) & (values > 0)


def build_limit_gate_diagnostics(panel, calendar, entryrows, *, adjustment_factor_role=None):
    """Return unfiltered descriptors for each supplied actual entry t >= 23.

    prior_extension20 compares close*factor at t-3 and t-23, requiring all21
    prices. downside_semideviation20 uses the20 intervening simple total-return
    changes: sqrt(mean(min(r,0)^2))*sqrt(252), with all20 sessions in the mean.
    median_amount20 uses exactly t-22..t-3 (20 positive finite CNY amounts).
    liquidity_ratio is planned_slot_cash / median_amount20. The supplied cash
    is the old preceding-NAV20% budget *upper bound*, not executable size or
    the final fill budget. The zero trend lower bound is only a possible future
    gate; no comparison, selection or threshold is applied here.

    Inputs are mappings of date-by-symbol DataFrames. Missing dates or fields
    within the independent grid are reindexed to NaN, never compressed. Invalid
    global factor roles/entry dates are rejected; missing data have independent
    NaN features and reason fields, so an amount issue does not erase prices.

    Optional volume_shares plus recorded_order_quantity add a fixed historical
    capacity descriptor on the same20 amount dates. All20 share volumes must
    be positive finite before min_shares20, reserved_quantity_over_min_shares20
    and liquidity_capacity_pass_1pct (ratio<=.01) are known. The numerator is
    the old recorded raw buy quantity, not a recomputed or executable new order.
    The minimum-volume role is a mechanism constraint, not an alpha finding.
    """
    if adjustment_factor_role != ROLE:
        raise LimitGateDiagnosticInputError("Explicit same-source total_return_factor role required")
    days = _dates(calendar)
    if not isinstance(panel, Mapping) or not set(FIELDS).issubset(panel):
        raise LimitGateDiagnosticInputError("Supplied close, factor and amount panels required")
    anchor = panel["close"]
    if not isinstance(anchor, pd.DataFrame) or not anchor.columns.is_unique:
        raise LimitGateDiagnosticInputError("Unique supplied raw-close stock identities required")
    symbols = pd.Index(anchor.columns)
    if (not len(symbols) or symbols.isna().any() or
            any(not isinstance(code, str) or not code for code in symbols)):
        raise LimitGateDiagnosticInputError("Nonempty string symbol identities required")
    aligned = {}
    fields = FIELDS + (("volume_shares",) if "volume_shares" in panel else ())
    for field in fields:
        source = panel[field]
        if not isinstance(source, pd.DataFrame) or not source.columns.is_unique:
            raise LimitGateDiagnosticInputError("Each field needs unique supplied date/symbol identities")
        index = _dates(source.index, empty_ok=True)
        if not index.isin(days).all() or not source.columns.isin(symbols).all():
            raise LimitGateDiagnosticInputError("Field identities outside supplied close/calendar grid")
        copy = source.copy(deep=False)
        copy.index = index
        copy = copy.reindex(index=days, columns=symbols)
        try:
            aligned[field] = copy.to_numpy(dtype=float, na_value=np.nan)
        except (TypeError, ValueError):
            aligned[field] = copy.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    entries = pd.DataFrame(entryrows).copy()
    if not {"date", "symbol", "planned_slot_cash"}.issubset(entries):
        raise LimitGateDiagnosticInputError("Fixed entry date/symbol/planned_slot_cash required")
    if entries[["date", "symbol"]].isna().any().any():
        raise LimitGateDiagnosticInputError("Missing fixed entry identities")
    try:
        entry_dates = pd.DatetimeIndex(pd.to_datetime(entries["date"], errors="raise"))
    except (TypeError, ValueError) as error:
        raise LimitGateDiagnosticInputError("Invalid fixed entry date") from error
    if (entry_dates.hasnans or entry_dates.tz is not None or
            not entry_dates.equals(entry_dates.normalize()) or not entry_dates.isin(days).all()):
        raise LimitGateDiagnosticInputError("Entry dates must be exact independently supplied reference days")
    entries["date"] = entry_dates
    if (not entries["symbol"].isin(symbols).all() or
            entries.duplicated(["date", "symbol"]).any()):
        raise LimitGateDiagnosticInputError("Unknown or duplicate fixed entry identities")
    positions = {date: i for i, date in enumerate(days)}
    if any(positions[date] < 23 for date in entry_dates):
        raise LimitGateDiagnosticInputError("Entry t must have all23 preceding independent reference sessions")
    entries["planned_slot_cash"] = pd.to_numeric(
        entries["planned_slot_cash"], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    capacity_inputs_provided = "volume_shares" in aligned and "recorded_order_quantity" in entries
    if "recorded_order_quantity" in entries:
        entries["recorded_order_quantity"] = pd.to_numeric(
            entries["recorded_order_quantity"], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    else:
        entries["recorded_order_quantity"] = np.nan
    cols = {code: j for j, code in enumerate(symbols)}
    result = []
    for row in entries.itertuples(index=False):
        t, j = positions[row.date], cols[row.symbol]
        close = aligned["close"][t - 23:t - 2, j]
        factor = aligned["adjustment_factor"][t - 23:t - 2, j]
        extension, semi = math.nan, math.nan
        extension_reason = semi_reason = "missing_or_nonpositive_reference_close_or_factor"
        price_valid = _positive(close) & _positive(factor)
        if price_valid.all():
            with np.errstate(over="ignore", under="ignore", invalid="ignore", divide="ignore"):
                adjusted = close * factor
                if _positive(adjusted).all():
                    value = adjusted[-1] / adjusted[0] - 1.
                    if np.isfinite(value):
                        extension = float(value)
                        extension_reason = "complete21_reference_prices_excluding_two_boards"
                    else:
                        extension_reason = "invalid_adjusted_endpoint_ratio"
                    returns = adjusted[1:] / adjusted[:-1] - 1.
                    value = np.sqrt(np.mean(np.minimum(returns, 0.) ** 2)) * np.sqrt(252.)
                    if np.isfinite(returns).all() and np.isfinite(value):
                        semi = float(value)
                        semi_reason = "complete20_simple_total_return_changes_all20_in_mean"
                    else:
                        semi_reason = "invalid_reference_total_return_changes"
                else:
                    extension_reason = semi_reason = "invalid_adjusted_price"
        amount = aligned["amount"][t - 22:t - 2, j]
        median, ratio = math.nan, math.nan
        amount_reason = "missing_or_nonpositive_reference_amount"
        ratio_reason = "median_amount_source_unknown"
        if _positive(amount).all():
            ordered = np.sort(amount)
            # Positive midpoint arithmetic retains finite medians without
            # overflowing the addition of two individually finite amounts.
            median = float(ordered[9] + (ordered[10] - ordered[9]) / 2.)
            amount_reason = "complete20_reference_amounts_excluding_two_boards"
            budget = float(row.planned_slot_cash)
            if math.isfinite(budget) and budget > 0:
                ratio = budget / median
                ratio_reason = "planned_budget_upper_bound_over_median_cash_amount"
                if not math.isfinite(ratio):
                    ratio, ratio_reason = math.nan, "invalid_budget_amount_ratio"
            else:
                ratio_reason = "planned_slot_cash_unknown_or_nonpositive"
        min_shares, quantity_ratio, capacity_pass = math.nan, math.nan, pd.NA
        volume_reason = capacity_reason = "optional_volume_or_recorded_quantity_not_provided"
        known_volume_slots = 0
        if capacity_inputs_provided:
            shares = aligned["volume_shares"][t - 22:t - 2, j]
            known_volume_slots = int(_positive(shares).sum())
            volume_reason = "missing_or_nonpositive_reference_share_volume"
            capacity_reason = "min20_share_volume_source_unknown"
            if _positive(shares).all():
                min_shares = float(shares.min())
                volume_reason = "complete20_positive_reference_share_volumes_excluding_two_boards"
                quantity = float(row.recorded_order_quantity)
                if math.isfinite(quantity) and quantity > 0:
                    quantity_ratio = quantity / min_shares
                    if math.isfinite(quantity_ratio):
                        capacity_pass = (Decimal(str(quantity)) * Decimal(100) <=
                                         Decimal(str(min_shares)))
                        capacity_reason = "fixed_old1pct_recorded_raw_quantity_over_min20_shares"
                    else:
                        quantity_ratio, capacity_reason = math.nan, "invalid_recorded_quantity_share_ratio"
                else:
                    capacity_reason = "recorded_order_quantity_unknown_or_nonpositive"
        result.append({"date": row.date.strftime("%Y-%m-%d"), "symbol": row.symbol,
                       "planned_slot_cash": float(row.planned_slot_cash),
                       "price_start": days[t - 23].strftime("%Y-%m-%d"),
                       "price_end": days[t - 3].strftime("%Y-%m-%d"),
                       "amount_start": days[t - 22].strftime("%Y-%m-%d"),
                       "amount_end": days[t - 3].strftime("%Y-%m-%d"),
                       "prior_extension20": extension, "prior_extension20_reason": extension_reason,
                       "downside_semideviation20": semi, "downside_semideviation20_reason": semi_reason,
                       "median_amount20": median, "median_amount20_reason": amount_reason,
                       "liquidity_ratio": ratio, "liquidity_ratio_reason": ratio_reason,
                       "known_price_slots21": int(price_valid.sum()),
                       "known_amount_slots20": int(_positive(amount).sum()),
                       "recorded_order_quantity": float(row.recorded_order_quantity),
                       "min_shares20": min_shares, "min_shares20_reason": volume_reason,
                       "reserved_quantity_over_min_shares20": quantity_ratio,
                       "liquidity_capacity_pass_1pct": capacity_pass,
                       "liquidity_capacity_reason": capacity_reason,
                       "known_volume_slots20": known_volume_slots,
                       "factor_role": ROLE, "fillable_size_certified": False})
    columns = ["date", "symbol", "planned_slot_cash", "price_start", "price_end",
               "amount_start", "amount_end", "prior_extension20", "prior_extension20_reason",
               "downside_semideviation20", "downside_semideviation20_reason", "median_amount20",
               "median_amount20_reason", "liquidity_ratio", "liquidity_ratio_reason",
               "known_price_slots21", "known_amount_slots20", "recorded_order_quantity",
               "min_shares20", "min_shares20_reason", "reserved_quantity_over_min_shares20",
               "liquidity_capacity_pass_1pct", "liquidity_capacity_reason", "known_volume_slots20",
               "factor_role", "fillable_size_certified"]
    output = pd.DataFrame(result, columns=columns)
    output["liquidity_capacity_pass_1pct"] = output["liquidity_capacity_pass_1pct"].astype("boolean")
    return output
