"""Fixed open-to-open source labels, separate from execution or cost returns.

The caller binds the quote basis and the independently accepted calendar. Every
original formation key remains present. Missing opens are unknown; this helper
never uses suspension valuation, forward fills or future quote selection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _keys(frame, *, label):
    if not {"date", "symbol"}.issubset(frame.columns):
        raise ValueError(f"{label} date/symbol fields required")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.strftime("%Y-%m-%d")
    result["symbol"] = result["symbol"].astype(str)
    if not result.symbol.str.fullmatch(r"\d{6}").all():
        raise ValueError(f"{label} six-digit symbol required")
    if result.duplicated(["date", "symbol"]).any():
        raise ValueError(f"{label} duplicate date/symbol keys")
    return result


def fixed_forward_open_labels(formations, quotes, calendar, *, entry_lag=1, exit_lag=6,
                              preserve_unavailable=None):
    """Keep each formation; label = open[t+6] / open[t+1] - 1 by default.

    ``preserve_unavailable`` accepts previously frozen label keys and their
    ``label_available`` flags. Its false flags remain false after source repair;
    its true labels are independently recomputed for caller equality checking.
    It never supplies missing numeric opens or filters the formation population.
    """
    if (type(entry_lag) is not int or type(exit_lag) is not int
            or entry_lag < 1 or exit_lag <= entry_lag):
        raise ValueError("Positive ordered fixed quote lags required")
    days = pd.to_datetime(pd.Series(list(calendar), dtype="object"), errors="raise").dt.strftime("%Y-%m-%d").tolist()
    if not days or days != sorted(set(days)):
        raise ValueError("Sorted unique independent calendar required")
    source = _keys(formations[["date", "symbol"]], label="Formation")
    if "open" not in quotes.columns:
        raise ValueError("Quote adjusted open field required")
    quote = _keys(quotes[["date", "symbol", "open"]], label="Quote")
    quote["open"] = pd.to_numeric(quote.open, errors="coerce")
    positions = {d: n for n, d in enumerate(days)}

    def shifted(day, lag):
        position = positions.get(day)
        return days[position + lag] if position is not None and position + lag < len(days) else None

    result = source.copy()
    result["entry_date"] = result.date.map(lambda d: shifted(d, entry_lag))
    result["exit_date"] = result.date.map(lambda d: shifted(d, exit_lag))
    for role in ["entry", "exit"]:
        field = role + "_date"
        joined = result[[field, "symbol"]].merge(
            quote.rename(columns={"date": field, "open": role + "_open"}),
            on=[field, "symbol"], how="left", validate="many_to_one", sort=False)
        result[role + "_open"] = joined[role + "_open"].to_numpy()
    entry = np.isfinite(result.entry_open) & result.entry_open.gt(0)
    exit = np.isfinite(result.exit_open) & result.exit_open.gt(0)
    horizon = result.entry_date.notna() & result.exit_date.notna()
    result["label_available"] = horizon & entry & exit
    result["availability_reason"] = np.select(
        [~horizon, ~entry, ~exit],
        ["calendar_horizon_pending", "entry_open_missing_or_nonpositive", "exit_open_missing_or_nonpositive"],
        default="fixed_original_quote_label_available")
    if preserve_unavailable is not None:
        if "label_available" not in preserve_unavailable.columns:
            raise ValueError("Prior frozen label availability field required")
        prior = _keys(preserve_unavailable[["date", "symbol", "label_available"]], label="Prior label")
        blocked = prior.loc[prior.label_available.eq(False), ["date", "symbol"]]
        blocked_keys = pd.MultiIndex.from_frame(blocked)
        blocked_here = pd.MultiIndex.from_frame(result[["date", "symbol"]]).isin(blocked_keys)
        result.loc[blocked_here, "label_available"] = False
        result.loc[blocked_here, "availability_reason"] = "old_unavailable_label_flag_preserved"
    # Work only on finite positive pairs. No division is attempted for a gap.
    result["forward_return"] = np.nan
    available = result.label_available
    result.loc[available, "forward_return"] = (
        result.loc[available, "exit_open"] / result.loc[available, "entry_open"] - 1)
    return result.drop(columns=["entry_open", "exit_open"])
