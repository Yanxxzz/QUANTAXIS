"""Contest score formulas with explicit missing-data and version boundaries.

These functions calculate supplied evidence only. They do not certify complete
calendar coverage, an official ledger, or a pool-level B denominator.
"""

from __future__ import annotations

from datetime import date
import math
import statistics


def _number(value: object, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not bool or missing")
    return float(value)


def _date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date YYYY-MM-DD")
    return parsed


def _numbers(values: list[float], name: str) -> list[float]:
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a list")
    return [_number(value, f"{name}[{index}]") for index, value in enumerate(values)]


def _growth(returns: list[float]) -> float:
    growth = math.prod(1.0 + value for value in returns)
    if not math.isfinite(growth) or growth <= 0:
        raise ValueError("Compounded wealth must be finite and positive")
    return growth


def monthly_c(net_returns: list[float], benchmark_returns: list[float],
              monthly_turnover: float) -> dict:
    """Calculate one month's C; returns and turnover use decimal units.

    Daily net returns already include trading costs. Drawdown starts from
    wealth 1 at the beginning of this month. Undefined Sharpe is an error,
    rather than a fabricated zero. The caller verifies aligned complete dates.
    """
    net = _numbers(net_returns, "net_returns")
    benchmark = _numbers(benchmark_returns, "benchmark_returns")
    if len(net) < 2 or len(net) != len(benchmark):
        raise ValueError("At least two aligned net and benchmark returns are required")
    if any(value <= -1.0 for value in net + benchmark):
        raise ValueError("Daily returns must be greater than -100%")
    turnover = _number(monthly_turnover, "monthly_turnover")
    if turnover < 0:
        raise ValueError("monthly_turnover must be nonnegative")
    net_growth, benchmark_growth = _growth(net), _growth(benchmark)
    try:
        sd = statistics.stdev(net)
        if not math.isfinite(sd) or sd <= 0:
            raise ValueError("Net daily sample standard deviation must be positive")
        sharpe = statistics.fmean(net) / sd * math.sqrt(252.0)
        rex = math.expm1((math.log(net_growth) - math.log(benchmark_growth)) * 252.0 / len(net))
    except (OverflowError, statistics.StatisticsError) as exc:
        raise ValueError("Monthly score is not finite") from exc
    wealth, peak, drawdown = 1.0, 1.0, 0.0
    for value in net:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        drawdown = max(drawdown, 1.0 - wealth / peak)
    raw_c = max(rex, 0.0) / max(turnover, 0.3) * sharpe * (1.0 - 1.2 * drawdown)
    if not all(math.isfinite(value) for value in (rex, sharpe, drawdown, raw_c)):
        raise ValueError("Monthly score is not finite")
    return {"N": len(net), "Rp": net_growth - 1.0, "Rb": benchmark_growth - 1.0,
            "Rex_ann": rex, "SR_ann": sharpe, "Turnover_month": turnover,
            "MaxDD_month": drawdown, "rawC": raw_c,
            "NC": min(max(raw_c / 0.6, 0.0), 1.0)}


def normalized_a(single_scores: list[float], decay: float, cap: float) -> float:
    """Average single-factor raw A first; normalize, decay, then cap."""
    scores = _numbers(single_scores, "single_scores")
    if not scores or any(value < 0 for value in scores):
        raise ValueError("At least one nonnegative single-factor raw A score is required")
    decay_value, cap_value = _number(decay, "decay"), _number(cap, "cap")
    if not 0.0 <= decay_value <= 1.0 or not 0.0 <= cap_value <= 1.0:
        raise ValueError("decay and cap must be between 0 and 1")
    try:
        mean = statistics.fmean(scores)
    except OverflowError as exc:
        raise ValueError("Mean single-factor A score must be finite") from exc
    return min(min(max(mean / 0.08, 0.0), 1.0) * decay_value, cap_value)


def new_b_score(records: list[dict], version: str, effective_date: str,
                direction: int, *, as_of: str) -> dict:
    """Calculate one version's new single-factor B score, with no pool mean.

    Every supplied record must be a completed post-effective cycle of this
    exact version, known by as_of. Mixed old or future records are rejected.
    The official minimum sample size is not assumed: fewer than two or zero
    IC dispersion remains pending; a numeric result is not admission proof.
    """
    if not isinstance(version, str) or not version.strip():
        raise ValueError("version must be a nonempty string")
    if type(direction) is not int or direction not in (0, 1):
        raise ValueError("direction must be integer 0 or 1")
    effective, cutoff = _date(effective_date, "effective_date"), _date(as_of, "as_of")
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    if not records:
        return {"status": "cold_start", "rawB": None, "n": 0,
                "reason": "No completed post-effective IC records"}
    ic_values, rank_values = [], []
    previous_signal = previous_realized = None
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("version") != version:
            raise ValueError(f"records[{index}] does not match the requested version")
        signal = _date(record.get("signal_date"), f"records[{index}].signal_date")
        realized = _date(record.get("realized_date"), f"records[{index}].realized_date")
        if signal < effective or realized <= signal or realized > cutoff:
            raise ValueError("B records must be completed post-effective cycles known by as_of")
        if previous_signal is not None and (signal <= previous_signal or realized <= previous_realized):
            raise ValueError("B signal and realized dates must each be strictly increasing without duplicates")
        ic = _number(record.get("ic"), f"records[{index}].ic")
        rank_ic = _number(record.get("rank_ic"), f"records[{index}].rank_ic")
        if not -1.0 <= ic <= 1.0 or not -1.0 <= rank_ic <= 1.0:
            raise ValueError("IC and RankIC must lie between -1 and 1")
        ic_values.append(ic)
        rank_values.append(rank_ic)
        previous_signal, previous_realized = signal, realized
    n = len(records)
    if n < 2:
        return {"status": "pending", "rawB": None, "n": n,
                "reason": "At least two completed records are needed to estimate ICIR"}
    sd = statistics.stdev(ic_values)
    if sd <= 0:
        return {"status": "pending", "rawB": None, "n": n,
                "reason": "ICIR is undefined with zero IC sample dispersion"}
    icir, rank_mean = statistics.fmean(ic_values) / sd, statistics.fmean(rank_values)
    wins = sum(value > 0.02 if direction == 1 else value < -0.02 for value in ic_values)
    win_rate = wins / n
    return {"status": "scored", "rawB": abs(rank_mean) * abs(icir) * win_rate,
            "n": n, "rank_ic_mean": rank_mean, "icir": icir,
            "ic_win_rate": win_rate, "official_minimum_sample_verified": False}
