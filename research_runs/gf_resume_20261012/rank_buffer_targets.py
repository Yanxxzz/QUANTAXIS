"""Fixed GF top-decile targets with a top-two-decile retention buffer.

This is proposed-target hysteresis, not a simulation of actual holdings or
fills. The previous state is the previous proposed target set, initially empty.
Only the current bound GF cohort determines quantiles and retained identities;
prices, returns and future observations are neither accepted nor consulted.

The caller supplies the already qualified, finite date/symbol/GF cohort plus
explicit formation anchors and trading sessions. No source rows are filtered,
imputed or added here. Optional source_qualified/pit_usable flags must certify
every supplied row, otherwise construction is pending rather than changing the
denominator. A code absent from the current cohort cannot be retained.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from panda_alpha import study


class RankBufferPendingError(ValueError):
    """An unresolved source, schedule or exact-identity check stops the unit."""

    def __init__(self, reason, *, date=None, details=None):
        self.status = "RANK_BUFFER_PENDING"
        self.reason = reason
        self.date = date
        self.details = details or {}
        location = ":" + date if date is not None else ""
        super().__init__(self.status + location + ":" + reason)


def _dates(values, label):
    if isinstance(values, (str, bytes)):
        raise ValueError(label + " requires an explicit ordered date sequence")
    try:
        parsed = pd.to_datetime(list(values), errors="raise")
        if parsed.isna().any():
            raise ValueError("missing date")
        days = parsed.strftime("%Y-%m-%d").tolist()
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid " + label + " dates") from error
    if not days or days != sorted(set(days)):
        raise ValueError(label + " dates must be nonempty, unique and ordered")
    return days


def _bound_signals(signals, anchors):
    if not isinstance(signals, pd.DataFrame):
        raise ValueError("Bound signals must be a date/symbol/GF DataFrame")
    frame = signals.copy(deep=True)
    if not {"date", "symbol", "GF"}.issubset(frame.columns):
        if (isinstance(frame.index, pd.MultiIndex)
                and list(frame.index.names) == ["date", "symbol"]
                and "GF" in frame.columns):
            frame = frame.reset_index()
        else:
            raise ValueError("Explicit bound date/symbol/GF identities required")
    if frame.columns.duplicated().any():
        raise ValueError("Duplicate bound signal columns")
    parsed = pd.to_datetime(frame["date"], errors="raise")
    if parsed.isna().any():
        raise ValueError("Missing bound formation date")
    frame["date"] = parsed.dt.strftime("%Y-%m-%d")
    if not frame["symbol"].map(lambda value: isinstance(value, str)
                               and bool(value.strip())).all():
        raise ValueError("Explicit nonempty string stock codes required")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate bound formation identity")
    if sorted(frame["date"].unique().tolist()) != anchors:
        raise ValueError("Bound signal dates must match the supplied anchors exactly")
    frame["GF"] = pd.to_numeric(frame["GF"], errors="raise")
    if not np.isfinite(frame["GF"]).all():
        raise RankBufferPendingError("nonfinite_bound_GF_would_change_source_cohort")
    for field in ("source_qualified", "pit_usable"):
        if field in frame.columns and not frame[field].map(
                lambda value: isinstance(value, (bool, np.bool_)) and bool(value)).all():
            raise RankBufferPendingError(
                "unqualified_or_unknown_bound_source", details={"field": field})
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def _groups(rows, value_column, day):
    try:
        groups, status = study.quantile_groups(
            rows[["symbol", value_column]].rename(columns={value_column: "value"}),
            10, 10)
    except ValueError as error:
        raise RankBufferPendingError(
            "quantile_construction_failed", date=day,
            details={"value_column": value_column, "error": str(error)}) from error
    if status != "groups_available":
        raise RankBufferPendingError(
            status, date=day, details={"value_column": value_column})
    return groups


def build_rank_buffer_targets(signals, anchors, calendar):
    """Return exact identities, next-open schedule, encoding and diagnostics.

    The rule is fixed: original GF group 10 supplies the target count and all
    entrants; original groups 9 and 10 supply the retention buffer. Retain the
    previous proposed targets still in that current buffer, cap them if needed
    by current GF descending/code ascending, then fill remaining slots from
    original group 10 with the same ordering. There is no parameter search.

    ``encoded_signals`` contains every supplied identity, with
    ``encoded_signal = 2 * selected_binary + current_GF_average_percentile``.
    Reapplying study.quantile_groups(..., 10, 10) must recover the exact proposed
    group-10 identities. A tie/count mismatch raises RankBufferPendingError;
    it never triggers a cohort change, jitter, tie split or fallback selection.

    ``schedule`` has the shape accepted by study.replay_portfolio: entry date
    -> {decision_date, codes}. The calendar must cover each next-open entry and
    its fixed five-session holding interval. Inputs are copied and not mutated;
    the function has no I/O, registration, network or persistent state.
    """
    anchors = _dates(anchors, "Formation anchor")
    calendar = _dates(calendar, "Trading calendar")
    positions = {day: index for index, day in enumerate(calendar)}
    missing = [day for day in anchors if day not in positions]
    if missing:
        raise RankBufferPendingError("anchor_missing_from_calendar",
                                     details={"dates": missing})
    if any(positions[right] - positions[left] != 5
           for left, right in zip(anchors, anchors[1:])):
        raise ValueError("Formation anchors must preserve the fixed five-session phase")
    for day in anchors:
        if positions[day] + 6 >= len(calendar):
            raise RankBufferPendingError("full_next_open_five_session_window_missing",
                                         date=day)
    frame = _bound_signals(signals, anchors)
    previous = set()
    targets, diagnostics, encoded_parts = [], [], []
    schedule = {}
    for day in anchors:
        rows = frame.loc[frame["date"].eq(day)].copy()
        groups = _groups(rows, "GF", day)
        original_top = set(groups["10"])
        buffer = set(groups["9"]) | original_top
        target_count = len(original_top)
        ranked = rows.sort_values(["GF", "symbol"], ascending=[False, True])
        retainable = previous & buffer
        retained = ranked.loc[ranked["symbol"].isin(retainable), "symbol"].tolist()
        retained = retained[:target_count]
        slots = target_count - len(retained)
        fillable = original_top - set(retained)
        filled = ranked.loc[ranked["symbol"].isin(fillable), "symbol"].tolist()[:slots]
        selected = set(retained) | set(filled)
        if len(selected) != target_count:
            raise RankBufferPendingError("original_top10_cannot_fill_fixed_target_count",
                                         date=day)
        rows["selected_binary"] = rows["symbol"].isin(selected).astype(int)
        rows["current_GF_average_percentile"] = rows["GF"].rank(method="average", pct=True)
        rows["encoded_signal"] = (2 * rows["selected_binary"]
                                  + rows["current_GF_average_percentile"])
        encoded_groups = _groups(rows, "encoded_signal", day)
        encoded_top = set(encoded_groups["10"])
        if encoded_top != selected:
            raise RankBufferPendingError(
                "encoded_top10_identity_mismatch", date=day,
                details={"source_cohort_count": len(rows),
                         "proposed_target_count": target_count,
                         "encoded_top10_count": len(encoded_top),
                         "proposed_only": sorted(selected - encoded_top),
                         "encoded_only": sorted(encoded_top - selected),
                         "ties_or_quantile_boundary_not_repaired": True})
        entry = calendar[positions[day] + 1]
        codes = sorted(selected)
        targets.append({"decision_date": day, "entry_date": entry,
                        "planned_exit_date": calendar[positions[day] + 6],
                        "codes": codes, "common_codes": sorted(rows["symbol"].tolist()),
                        "original_top10_codes": sorted(original_top),
                        "buffer_codes": sorted(buffer), "retained_codes": sorted(retained),
                        "filled_from_top10_codes": sorted(filled)})
        schedule[entry] = {"decision_date": day, "codes": codes.copy()}
        diagnostics.append({"date": day, "source_cohort_count": len(rows),
                            "original_top10_count": target_count,
                            "buffer_count": len(buffer), "previous_target_count": len(previous),
                            "retainable_count_before_cap": len(retainable),
                            "retained_count": len(retained), "filled_from_top10_count": len(filled),
                            "selected_count": len(selected),
                            "entrants_count": len(selected - previous),
                            "exits_count": len(previous - selected),
                            "original_top10_overlap_count": len(selected & original_top),
                            "encoded_top10_identity_exact": True})
        encoded_parts.append(rows)
        previous = selected
    return {"status": "RANK_BUFFER_TARGETS_READY", "anchors": anchors,
            "calendar": calendar, "targets": targets, "schedule": schedule,
            "encoded_signals": pd.concat(encoded_parts, ignore_index=True),
            "diagnostics": diagnostics,
            "fixed_rule": {"cycle_sessions": 5, "groups": 10, "minimum_assets": 10,
                           "held_group": 10, "buffer_groups": [9, 10], "direction": 1,
                           "encoding": "2*selected_binary+current_GF_average_percentile"},
            "state_semantics": "previous_proposed_targets_not_actual_filled_holdings",
            "no_forward_outcomes_used_to_select_targets": True,
            "source_cohort_filtered_or_expanded": False,
            "actual_fill_or_holdings_certified": False}
