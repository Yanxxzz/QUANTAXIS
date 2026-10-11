"""Fixed, sparse event diagnostics; overlapping events are not a portfolio NAV."""
from __future__ import annotations

import math
import pandas as pd


def evaluate_event_windows(quotes, events, calendar, peers, *, horizon=5,
                           costs=(0.003, 0.005)):
    """Use next-session open and complete fixed session paths, never drop gaps."""
    if horizon < 1 or len(set(peers)) != len(peers) or not peers:
        raise ValueError("Positive horizon and distinct fixed peers required")
    dates = sorted(set(str(x)[:10] for x in calendar))
    frame = quotes.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate quote identity prevents event evaluation")
    lookup = frame.set_index(["symbol", "date"])

    def path(symbol, formation):
        if formation not in dates:
            return {"status": "source_pending", "reason": "formation_not_on_bound_calendar"}
        i = dates.index(formation)
        held = dates[i+1:i+1+horizon]
        if len(held) != horizon:
            return {"status": "source_pending", "reason": "incomplete_bound_holding_calendar"}
        required = [formation, *held]
        missing = [d for d in required if (symbol, d) not in lookup.index]
        if missing:
            return {"status": "source_pending", "missing_dates": missing}
        values = lookup.loc[[(symbol, d) for d in required]]
        for column in ("open", "close"):
            if any(not math.isfinite(float(x)) or float(x) <= 0 for x in values[column]):
                return {"status": "source_pending", "reason": "invalid_positive_quote"}
        initial_close = float(values.iloc[0]["close"])
        entry_open = float(values.iloc[1]["open"])
        exit_close = float(values.iloc[-1]["close"])
        multiple = exit_close/entry_open
        return {"status": "complete", "entry_date": held[0], "exit_date": held[-1],
                "held_sessions": horizon, "information_return": exit_close/initial_close-1,
                "next_open_gross_return": multiple-1,
                "round_trip_cost_proxy": {str(c): multiple*(1-c)/(1+c)-1 for c in costs}}

    rows = []
    for event in events:
        formation = event["formation_day"]
        own = path(event["symbol"], formation)
        references = [path(p, formation) for p in peers]
        missing_peers = [p for p, r in zip(peers, references) if r["status"] != "complete"]
        row = dict(event, event_path=own, fixed_reference_count=len(peers),
                   missing_reference_peers=missing_peers, reference_status="source_pending")
        if not missing_peers:
            row.update(reference_status="complete",
                       reference_information_return=sum(r["information_return"] for r in references)/len(peers),
                       reference_next_open_gross_return=sum(r["next_open_gross_return"] for r in references)/len(peers))
            if own["status"] == "complete":
                row["relative_information_return"] = own["information_return"]-row["reference_information_return"]
        rows.append(row)
    complete = all(r["event_path"]["status"] == "complete" and
                   r["reference_status"] == "complete" for r in rows)
    result = {"status": "COMPLETE_FIXED_EVENT_DIAGNOSTIC" if complete else "SOURCE_PENDING_EVENT_DIAGNOSTIC",
              "events": rows, "planned_event_count": len(events),
              "complete_event_paths": sum(r["event_path"]["status"] == "complete" for r in rows),
              "complete_matched_references": sum(r["reference_status"] == "complete" for r in rows),
              "formation_clusters": len({r["formation_day"] for r in rows}),
              "source_limited_preliminary": True, "annual_Sharpe": None, "ICIR": None,
              "portfolio_compound_return": None, "eligible_for_review": False,
              "scope": "Sparse event paths and overlapping fixed five-session labels; no portfolio NAV, realized fills or significance claim"}
    if complete and rows:
        scores = pd.Series([-r["raw_supply_ratio"] for r in rows])
        relative = pd.Series([r["relative_information_return"] for r in rows])
        correlation = scores.corr(relative, method="spearman")
        result["direction_aligned_event_RankIC"] = float(correlation) if pd.notna(correlation) else None
        median = pd.Series([r["raw_supply_ratio"] for r in rows]).median()
        groups = {"lower_supply": [r for r in rows if r["raw_supply_ratio"] <= median],
                  "higher_supply": [r for r in rows if r["raw_supply_ratio"] > median]}
        result["fixed_median_groups"] = {}
        for name, group in groups.items():
            result["fixed_median_groups"][name] = {"count": len(group),
                "mean_relative_information_return": sum(r["relative_information_return"] for r in group)/len(group) if group else None,
                "mean_per_event_cost_proxy": {str(c): sum(r["event_path"]["round_trip_cost_proxy"][str(c)] for r in group)/len(group) if group else None for c in costs},
                "not_portfolio_return": True}
    return result
