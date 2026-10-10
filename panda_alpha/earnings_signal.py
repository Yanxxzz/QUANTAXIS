"""Sparse, source-time event factor values for local research only."""
from bisect import bisect_left
import math

import pandas as pd


def sparse_asof_values(events, calendar, complete_codes, *, score_key="score",
                       lifetime=5, pending_events=()):
    """Return source-bounded [symbol,date] values with explicit unknown barriers.

    No eligible recent news in a verified search is a known zero. An unparsed
    recent event is NaN, not a fallback to an older announcement. Only an event
    already available at the decision close can update the state.
    """
    days = list(calendar)
    if days != sorted(set(days)) or lifetime < 1:
        raise ValueError("Unique ordered calendar and positive lifetime required")
    codes = sorted(set(complete_codes))
    updates = {}
    for row in events:
        code, day = row["code"], row["decision_date"]
        if code not in codes or day not in days:
            continue
        if row["pub_date"] >= day:
            raise ValueError("Event is not yet available at the decision close")
        value = float(row[score_key])
        if not math.isfinite(value):
            raise ValueError("Verified score must be finite; unknowns need barriers")
        key = (code, day)
        if key in updates and updates[key] != value:
            raise ValueError("Same-day competing news needs source adjudication")
        updates[key] = value
    for row in pending_events:
        code, day = row["code"], row.get("decision_date")
        if code in codes and day in days:
            updates[(code, day)] = float("nan")
    rows = []
    for code in codes:
        value, expires = 0.0, -1
        for i, day in enumerate(days):
            if (code, day) in updates:
                value, expires = updates[(code, day)], i + lifetime
            if i >= expires:
                value = 0.0
            rows.append((code, pd.Timestamp(day), value))
    frame = pd.DataFrame(rows, columns=["symbol", "date", "value"])
    return frame.set_index(["symbol", "date"])["value"].rename("value")
