"""Sparse close-formed factors for the two-board/lower-limit event.

An observed lower-limit touch on t is only known after that session. Values
formed here become usable on t+1; they cannot be used to choose a t lower-limit
bid. A touch is not a queue fill. Non-events and unknown features remain NaN,
including outside event dates. No temporal extension, jitter or dense-group
construction is performed.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from .limit_event_features import FEATURES, FIELDS, LimitEventInputError, build_event_features


native_adapter_notes = {
    "up_limit": {"public_mapping": "cal_limit_up", "implemented": False},
    "down_limit": {"public_mapping": "cal_limit_down", "implemented": False},
    "status": "documentation_only_no_native_adapter_or_production_certification",
    "required_checks": [
        "Raw quote and price-limit values must share the same unadjusted price basis.",
        "Historical IPO, ST, exchange rule and no-limit roles require exact supplied metadata.",
        "Do not infer same-session limits from an adjusted yesterday close.",
        "Same-source factor role and observed-before-formation availability require binding.",
        "Use the independent reference calendar; missing sessions must not be compressed.",
    ],
    "formation": "after_close_t",
    "earliest_use": "next_reference_session_t_plus_1",
    "fill_certified": False,
}


def _canonical_index(series):
    if not isinstance(series, pd.Series) or not isinstance(series.index, pd.MultiIndex):
        raise LimitEventInputError("Local factors must be Series with (date, symbol) MultiIndex")
    if series.index.nlevels != 2 or list(series.index.names) != ["date", "symbol"]:
        raise LimitEventInputError("Local factor index names/order must be date, symbol")
    date_values = series.index.get_level_values("date")
    date_map = {}
    for value in date_values.unique():
        try:
            date = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise LimitEventInputError("Invalid factor reference date") from error
        if pd.isna(date) or date.tzinfo is not None or date != date.normalize():
            raise LimitEventInputError("Factor reference dates must be timezone-free whole days")
        date_map[value] = date
    symbols = series.index.get_level_values("symbol")
    if symbols.isna().any():
        raise LimitEventInputError("Missing local factor symbol")
    symbols = symbols.astype(str)
    if (symbols == "").any():
        raise LimitEventInputError("Empty local factor symbol")
    index = pd.MultiIndex.from_arrays([date_values.map(date_map), symbols], names=["date", "symbol"])
    if not index.is_unique:
        raise LimitEventInputError("Duplicate local factor identities")
    return index


class LimitEventFactor:
    """Score one fixed descriptor negatively on confirmed touch events only.

    ``feature`` must be one of FEATURES. Negative scoring is a supplied
    fragility hypothesis, not a fitted threshold or evidence of alpha.
    ``factors`` is a mapping of the exact FIELDS to pandas Series. raw_close's
    full supplied identity grid, ordering and date representations are retained
    in the output. Other fields may omit identities, which are aligned as NaN;
    extra identities are rejected rather than silently changing the universe.
    """
    def __init__(self, feature):
        if feature not in FEATURES:
            raise LimitEventInputError("Unsupported event feature")
        self.feature = feature
        self._coverage = {}

    def calculate(self, factors, calendar, adjustment_factor_role=None):
        if not isinstance(factors, Mapping) or not set(FIELDS).issubset(factors):
            raise LimitEventInputError("All exact local event factor fields are required")
        raw_close = factors["raw_close"]
        identity = _canonical_index(raw_close)
        columns = {}
        for field in FIELDS:
            source = factors[field]
            source_index = _canonical_index(source)
            if not source_index.isin(identity).all():
                raise LimitEventInputError("A field contains identities outside raw_close's grid")
            aligned = source.copy(deep=False)
            aligned.index = source_index
            columns[field] = aligned.reindex(identity)
        bars = pd.DataFrame(columns, index=identity).reset_index()
        events = build_event_features(bars, calendar,
                                      adjustment_factor_role=adjustment_factor_role)
        scores = np.full(len(identity), np.nan, dtype=float)
        touch = events["touch_event"].eq(True).fillna(False).to_numpy(dtype=bool)
        feature = pd.to_numeric(events[self.feature], errors="coerce").to_numpy(dtype=float)
        valid = touch & np.isfinite(feature)
        if valid.any():
            selected = events.loc[valid, ["date", "symbol"]]
            selected_index = pd.MultiIndex.from_arrays(
                [pd.to_datetime(selected["date"], errors="raise"), selected["symbol"]],
                names=["date", "symbol"])
            locations = identity.get_indexer(selected_index)
            if (locations < 0).any():
                raise LimitEventInputError("Event identity missing from supplied raw_close grid")
            scores[locations] = -feature[valid]
        values = pd.Series(scores, index=raw_close.index.copy(), name="value")
        self._coverage = {
            "output_identities": len(values),
            "preopen_two_board_candidates": len(events),
            "candidate_touch_unknown": int(events["touch_event"].isna().sum()),
            "known_touch_events": int(touch.sum()),
            "touch_events_feature_unknown": int((touch & ~np.isfinite(feature)).sum()),
            "finite_factor_values": int(valid.sum()),
            "structural_non_candidates": int(len(values) - len(events)),
            "nan_factor_values": int(np.isnan(scores).sum()),
        }
        return values

    def coverage_counts(self):
        """Copy of the most recent calculation's coverage, never synthetic fills."""
        return dict(self._coverage)
