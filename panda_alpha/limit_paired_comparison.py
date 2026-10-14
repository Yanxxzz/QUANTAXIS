"""Fixed paired month-block inference from complete, already-net portfolio NAVs.

The effect is an arithmetic annualized daily-return difference, NOT a CAGR,
Sharpe difference, independent-alpha test or formal admission statistic. Both
legs of each calendar-month block are sampled together. Month lengths retain
their actual weights when a block is repeated. No market or account access.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


INITIAL_CASH = 100000.0
BOOTSTRAP_SEED = 20261014
BOOTSTRAP_SAMPLES = 2000
ANNUAL_REFERENCE_DAYS = 252


class PairedNavInputError(ValueError):
    """Incomplete dates or invalid NAVs cannot be silently selected or filled."""


def _dates(values, role):
    dates = []
    for raw in values:
        try:
            day = pd.Timestamp(raw)
        except (TypeError, ValueError, OverflowError) as error:
            raise PairedNavInputError(f"Invalid {role} date") from error
        if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
            raise PairedNavInputError(f"{role} dates must be timezone-free whole days")
        dates.append(day)
    result = pd.DatetimeIndex(dates, name="date")
    if not len(result) or not result.is_unique or not result.is_monotonic_increasing:
        raise PairedNavInputError(f"{role} dates must be nonempty, sorted and unique")
    return result


def _nav(table, role):
    frame = pd.DataFrame(table)
    if not frame.columns.is_unique or not {"date", "nav"}.issubset(frame):
        raise PairedNavInputError(f"{role} requires unique date/nav columns")
    dates = _dates(frame["date"], role)
    # A bool is not a numeric monetary NAV even though Python treats it as1/0.
    if frame["nav"].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise PairedNavInputError(f"{role} NAV must be finite positive money")
    try:
        nav = pd.to_numeric(frame["nav"], errors="raise").to_numpy(dtype=float)
    except (TypeError, ValueError, OverflowError) as error:
        raise PairedNavInputError(f"Invalid {role} NAV") from error
    if not np.isfinite(nav).all() or not (nav > 0).all():
        raise PairedNavInputError(f"{role} NAV must be finite and positive")
    previous = np.r_[INITIAL_CASH, nav[:-1]]
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        returns = nav / previous - 1.0
    if not np.isfinite(returns).all():
        raise PairedNavInputError(f"{role} implied daily returns are invalid")
    return dates, returns


def compare_complete_nav(candidate_nav, control_nav, *, expected_dates=None):
    """Compare complete paired NAVs using fixed 2000 calendar-month bootstrap.

    Each input must contain sorted, unique whole-day ``date`` and positive
    finite ``nav`` columns. Dates must match EXACTLY; there is no inner join,
    first-finite trimming, cash backfill or missing-return zero. Optional
    ``expected_dates`` additionally catches calendar days absent in BOTH legs.
    Without that independent calendar, matching dates alone cannot certify
    jointly missing sessions. The first return uses the fixed100000 initial cash.

    For each resample, draw M months with replacement and divide the sum of
    their paired daily differences by the sum of their actual daily lengths.
    Repeating a month repeats its entire paired block. The percentile95% CI is
    expressed as decimal annualized arithmetic mean differences.
    """
    dates, candidate = _nav(candidate_nav, "candidate")
    control_dates, control = _nav(control_nav, "control")
    if not dates.equals(control_dates):
        raise PairedNavInputError("Complete candidate/control dates must match exactly")
    calendar_verified = expected_dates is not None
    if calendar_verified and not dates.equals(_dates(expected_dates, "expected")):
        raise PairedNavInputError("Both NAVs must cover the entire expected date sequence")
    delta = candidate - control
    if not np.isfinite(delta).all():
        raise PairedNavInputError("Paired daily differences must be finite")
    months = dates.to_period("M")
    labels = months.unique()
    month_sums = np.asarray([delta[months == month].sum() for month in labels])
    month_days = np.asarray([(months == month).sum() for month in labels], dtype=int)
    count = len(labels)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    chosen = rng.integers(0, count, size=(BOOTSTRAP_SAMPLES, count))
    estimates = (ANNUAL_REFERENCE_DAYS * month_sums[chosen].sum(axis=1)
                 / month_days[chosen].sum(axis=1))
    if not np.isfinite(estimates).all():
        raise PairedNavInputError("Bootstrap annualized differences are invalid")
    low, high = np.quantile(estimates, [.025, .975])
    correlation = None
    if len(dates) > 1 and np.std(candidate) > 0 and np.std(control) > 0:
        correlation = float(np.corrcoef(candidate, control)[0, 1])
    return {
        "schema": "complete-nav-paired-month-block-bootstrap-v1",
        "annualized_mean_difference": float(ANNUAL_REFERENCE_DAYS * delta.mean()),
        "confidence_interval_95": [float(low), float(high)],
        "ci_contains_zero": bool(low <= 0 <= high),
        "mean_daily_return_difference": float(delta.mean()),
        "day_count": len(dates),
        "block_count": count,
        "month_blocks": [{"month": str(month), "days": int(days)}
                         for month, days in zip(labels, month_days)],
        "first_date": dates[0].strftime("%Y-%m-%d"),
        "last_date": dates[-1].strftime("%Y-%m-%d"),
        "paired_daily_return_correlation": correlation,
        "expected_calendar_verified": calendar_verified,
        "fewer_than_two_month_blocks": count < 2,
        "initial_cash_per_leg": INITIAL_CASH,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "annual_reference_days": ANNUAL_REFERENCE_DAYS,
        "effect_unit": "decimal annualized arithmetic mean of candidate-minus-control net daily returns",
        "sampling": "Paired entire calendar months, replacement; repeated blocks weighted by actual day counts",
        "net_input_role": "Caller-supplied already-after-cost NAV; no extra fee deduction or fee inference",
        "economic_upgrade_decision": None,
        "limitations": [
            "The arithmetic annualized mean difference is not a compounded return/CAGR or Sharpe difference.",
            "Highly correlated strategies share daily shocks; paired blocks preserve this dependence but do not establish independent alpha.",
            "One calendar-month block method does not capture every longer cross-month dependence or regime change; partial boundary months remain partial blocks.",
            "An already-exposed or strategy-selected sample remains exposed; bootstrap does not create untouched OOS evidence.",
            "The95% interval is not adjusted for multiple strategy/parameter comparisons or selection.",
            "A CI containing0 means this procedure does not resolve the effect's sign, not proof that the strategies are equivalent.",
            "Statistical return differences are separate from cost, Sharpe, drawdown and risk-budget tradeoffs; this helper does not decide economic upgrading.",
            "A single-month sample cannot estimate between-month variation; calendar completeness beyond exact paired dates requires expected_dates."
        ],
    }
