"""Point-in-time company guidance news, distinct from year-on-year growth.

Amounts/precision are supplied by source-certified extractors. This module does
not infer a market consensus or turn a missing disclosure into a zero signal.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import math
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ProfitInterval:
    lower: Decimal
    upper: Decimal
    nominal_bounds: tuple[Decimal, ...]
    precision_units: tuple[Decimal, ...]

    def __post_init__(self):
        values = (self.lower, self.upper, *self.nominal_bounds, *self.precision_units)
        if (not self.nominal_bounds or not self.precision_units
                or any(not x.is_finite() for x in values)
                or self.lower > self.upper
                or any(x <= 0 for x in self.precision_units)
                or any(not self.lower <= x <= self.upper for x in self.nominal_bounds)):
            raise ValueError("Invalid source amount/precision interval")

    @classmethod
    def from_source(cls, row: Mapping) -> "ProfitInterval":
        # These bounds must already include the source's printed precision.
        return cls(Decimal(row["lower_yuan"]), Decimal(row["upper_yuan"]),
                   tuple(Decimal(x) for x in row["printed_values_yuan"]),
                   tuple(Decimal(x) for x in row["precision_units_yuan"]))


def range_news(previous: ProfitInterval, current: ProfitInterval) -> dict:
    """Certify the sign of information outside the prior *public* range.

    Previously loss-making issuers can carry positive news. A known overlap is
    neutral; callers must keep unknown amounts out rather than invoking this.
    """
    scale = max(*(abs(x) for x in previous.nominal_bounds),
                *previous.precision_units, *current.precision_units)
    if current.lower > previous.upper:
        gap = current.lower - previous.upper
        sign = "positive"
    elif current.upper < previous.lower:
        gap = current.upper - previous.lower
        sign = "negative"
    else:
        gap = Decimal(0)
        sign = "neutral_overlap"
    return {"sign": sign, "score": float(gap / scale),
            "certified_gap_yuan": str(gap), "scale_yuan": str(scale),
            "comparator": "earlier_public_company_guidance_not_analyst_consensus"}


def guidance_midpoint_realization(previous: ProfitInterval, actual: ProfitInterval) -> dict:
    """An explicit midpoint proxy, never a certified market expectation.

    Realization within a published range can resolve uncertainty. This is a
    separate hypothesis from strictly outside-range news and must be registered
    separately; the midpoint assumption needs its own economic falsification.
    """
    midpoint = (min(previous.nominal_bounds) + max(previous.nominal_bounds)) / 2
    scale = max(*(abs(x) for x in previous.nominal_bounds),
                *previous.precision_units, *actual.precision_units)
    realized = (min(actual.nominal_bounds) + max(actual.nominal_bounds)) / 2
    precision = max(previous.precision_units) + max(actual.precision_units)
    delta = realized - midpoint
    if abs(delta) <= precision:
        sign, score = "neutral_precision_overlap", 0.0
    else:
        sign, score = ("positive" if delta > 0 else "negative"), float(delta / scale)
    return {"sign": sign, "score": score, "midpoint_yuan": str(midpoint),
            "realized_nominal_yuan": str(realized), "scale_yuan": str(scale),
            "expectation_proxy": "company_guidance_midpoint_assumption_not_market_consensus"}


def event_timing(calendar: Iterable[str], publication: str, *, holding: int = 5,
                 extension: int = 10, market_end: str | None = None) -> dict:
    days = list(calendar)
    if days != sorted(set(days)) or not days:
        raise ValueError("Calendar must be unique ascending source sessions")
    pub = date.fromisoformat(publication[:10]).isoformat()
    decision = bisect_right(days, pub)
    before = bisect_left(days, pub) - 1
    exit_position = decision + 1 + holding
    stop = exit_position + extension
    if before < 0 or stop >= len(days):
        raise ValueError("Complete frozen source/reaction/exit calendar is unavailable")
    if market_end is not None and days[stop] > market_end:
        raise ValueError("Exit source window exceeds the frozen market end")
    return {"publication_date": pub, "response_reference_date": days[before],
            "decision_date": days[decision], "entry_date": days[decision + 1],
            "planned_exit_date": days[exit_position],
            "exit_candidates": days[exit_position:stop + 1],
            "needed_dates": days[before:stop + 1]}


def latest_public_guidance(records: Iterable[Mapping], *, code: str, period: str,
                           publication: str) -> dict:
    """Never use same-day ambiguous versions, later revisions or old fallbacks."""
    rows = [r for r in records if r["code"] == code and r["fiscal_period"] == period
            and r["pub_date"] < publication]
    if not rows:
        return {"status": "no_earlier_guidance_in_verified_search"}
    latest_day = max(r["pub_date"] for r in rows)
    latest = [r for r in rows if r["pub_date"] == latest_day]
    if any(r.get("status") != "SOURCE_VERIFIED" for r in latest):
        return {"status": "pending_latest_guidance", "announcement_ids": [r["announcement_id"] for r in latest]}
    amounts = {tuple(str(r["profit"][k]) for k in ["lower_yuan", "upper_yuan"]) for r in latest}
    if len(amounts) != 1:
        return {"status": "pending_same_day_competing_guidance", "announcement_ids": [r["announcement_id"] for r in latest]}
    return {"status": "SOURCE_VERIFIED", "records": latest,
            "profit": latest[0]["profit"], "pub_date": latest_day}


def observed_response(reference_close: float, decision_close: float) -> float:
    if not all(math.isfinite(x) and x > 0 for x in [reference_close, decision_close]):
        raise ValueError("Both observed same-source adjusted closes are required")
    return decision_close / reference_close - 1


def unabsorbed_positive_signal(news_score: float, response: float) -> bool:
    if not all(math.isfinite(x) for x in [news_score, response]):
        raise ValueError("Unknown news/response is not a neutral signal")
    return news_score > 0 and response <= 0
