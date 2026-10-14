"""Past two-upper episodes with an explicit five-reference-close maturity clock.

Inputs are supplied source roles, not an inferred native price-limit feed. The
caller validates exact raw OHLC, legal bands, actual activity and lifecycle roles.
``upper_close`` is True/False/unknown independently of entry eligibility. An
ordinary episode needs a known non-ST, legally priced, trading anchor and pair.
A legally observed later ST close can still support a historical outcome.

``count`` mode never evaluates giveback or a risk ratio. ``values`` is intended
only for a separately frozen/registered real-data unit; implementing it here
does not perform that unit. Missing or nontrading post-sessions are unknown,
not successful recovery, and unsupported/no prior history receives no score.
"""
from __future__ import annotations

from collections import Counter, deque
from datetime import date
from decimal import Decimal, localcontext
from numbers import Real
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


HORIZON = 5
FIELDS = (
    "raw_close", "upper_close", "trading", "is_st", "legal_price",
    "adjustment_factor", "factor_asof_date", "factor_source_id",
)


def _ordinal(value: Any) -> int:
    if not isinstance(value, str) or len(value) != 10:
        return -1
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return -1
    return parsed.toordinal() if parsed.isoformat() == value else -1


def _positive(value: Any) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        return np.nan
    number = float(value)
    return number if np.isfinite(number) and number > 0 else np.nan


def _bool_roles(array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if array.dtype.kind == "b":
        return array.astype(bool, copy=False), np.ones(array.shape, dtype=bool)
    known = np.frompyfunc(lambda x: isinstance(x, (bool, np.bool_)), 1, 1)(array).astype(bool)
    truth = np.frompyfunc(
        lambda x: bool(x) if isinstance(x, (bool, np.bool_)) else False, 1, 1,
    )(array).astype(bool)
    return truth, known


def _numbers(array: np.ndarray) -> np.ndarray:
    if array.dtype.kind in "iuf":
        numbers = array.astype(float, copy=False)
        return np.where(np.isfinite(numbers) & (numbers > 0), numbers, np.nan)
    return np.frompyfunc(_positive, 1, 1)(array).astype(float)


def economic_close(
    raw_close: Any, adjustment_factor: Any, *, session: str,
    factor_asof_date: Any, factor_source_id: Any,
    expected_factor_source_id: str, factor_role: str,
) -> tuple[float | None, str | None]:
    """Pure unit/clock check; effective-date binding does not certify full PIT."""
    if not isinstance(factor_role, str) or factor_role != "total_return_factor":
        return None, "factor_role_unknown"
    close, factor = _positive(raw_close), _positive(adjustment_factor)
    if not np.isfinite(close):
        return None, "raw_close_missing_or_invalid"
    if not np.isfinite(factor):
        return None, "factor_missing_or_invalid"
    if (not isinstance(expected_factor_source_id, str) or not expected_factor_source_id
            or not isinstance(factor_source_id, str)
            or factor_source_id != expected_factor_source_id):
        return None, "factor_source_unknown_or_mismatched"
    current, effective = _ordinal(session), _ordinal(factor_asof_date)
    if current < 0 or effective < 0:
        return None, "factor_effective_date_unknown"
    if effective > current:
        return None, "factor_effective_after_session"
    product = close * factor
    if not np.isfinite(product) or product <= 0:
        return None, "economic_close_invalid"
    return product, None


def build_episode_history(
    panel: Mapping[str, pd.DataFrame], calendar: Sequence[str], *,
    formation_end: str, factor_source_id: str, factor_role: str,
    mode: str = "count",
) -> dict[str, Any]:
    """Return compact episodes, date-by-symbol daily frames and a scalar receipt.

    ``daily.count`` is supported mature episode count, not an alpha score.
    ``unknown`` counts mature unsupported identities; ``pending`` counts known
    identities not yet five reference closes old. In values mode, ``risk`` and
    ``episode_frequency_count`` share supported-N>0 with no mature unknown
    outcome. Partial known history is not presented as a complete risk. No scalar
    represents the current episode's own not-yet-completed future outcome.

    Work is O(T*N + HORIZON*E); no date-by-episode quadratic matrix is built.
    Extra future input rows are sliced out before inspecting any role/value.
    """
    if mode not in {"count", "values"}:
        raise ValueError("mode must be count or values")
    if (not isinstance(factor_role, str) or factor_role != "total_return_factor"
            or not isinstance(factor_source_id, str) or not factor_source_id):
        raise ValueError("Explicit same-source total_return_factor contract required")
    sessions = list(calendar)
    ordinals = [_ordinal(d) for d in sessions]
    if not sessions or any(x < 0 for x in ordinals) or any(a >= b for a, b in zip(ordinals, ordinals[1:])):
        raise ValueError("Calendar must contain unique increasing ISO reference sessions")
    if formation_end not in sessions:
        raise ValueError("formation_end must be a received reference session")
    dates = sessions[:sessions.index(formation_end) + 1]
    missing = [k for k in FIELDS if k not in panel]
    if missing:
        raise ValueError(f"Missing explicit panel roles: {missing}")
    base = panel["raw_close"]
    if not isinstance(base, pd.DataFrame) or base.columns.has_duplicates or len(base.columns) == 0:
        raise ValueError("raw_close must have fixed unique nonempty symbols")
    symbols = list(base.columns)
    if any(not isinstance(code, str) or not code for code in symbols):
        raise ValueError("Symbol identities must be nonempty strings")
    arrays: dict[str, np.ndarray] = {}
    for name in FIELDS:
        frame = panel[name]
        if not isinstance(frame, pd.DataFrame) or frame.index.has_duplicates or frame.columns.has_duplicates:
            raise ValueError(f"{name} must have unique date/symbol axes")
        if set(frame.columns) != set(symbols):
            raise ValueError(f"{name} must use the fixed complete symbol roster")
        arrays[name] = frame.reindex(index=dates, columns=symbols).to_numpy()
    shape = (len(dates), len(symbols))
    close, factors = _numbers(arrays["raw_close"]), _numbers(arrays["adjustment_factor"])
    upper, upper_known = _bool_roles(arrays["upper_close"])
    trading, trading_known = _bool_roles(arrays["trading"])
    st, st_known = _bool_roles(arrays["is_st"])
    legal, legal_known = _bool_roles(arrays["legal_price"])
    raw_support = trading_known & trading & st_known & legal_known & legal & np.isfinite(close)
    normal = raw_support & ~st
    board_known = normal & upper_known
    up = board_known & upper
    non_up = board_known & ~upper

    date_cache: dict[Any, int] = {}
    def asof_ordinal(x: Any) -> int:
        # Source dates are strings; avoid ambiguous pd.NA equality/hashing.
        if not isinstance(x, str):
            return -1
        if x not in date_cache:
            date_cache[x] = _ordinal(x)
        return date_cache[x]
    effective = np.frompyfunc(asof_ordinal, 1, 1)(arrays["factor_asof_date"]).astype(np.int64)
    source_known = np.frompyfunc(
        lambda x: isinstance(x, str) and x == factor_source_id, 1, 1,
    )(arrays["factor_source_id"]).astype(bool)
    factor_valid = np.isfinite(factors) & source_known & (effective > 0)
    factor_valid &= effective <= np.asarray([_ordinal(d) for d in dates], dtype=np.int64)[:, None]
    with np.errstate(over="ignore", invalid="ignore"):
        econ = close * factors
    economic_support = raw_support & factor_valid & np.isfinite(econ) & (econ > 0)

    first = np.zeros(shape, dtype=bool)
    continued = np.zeros(shape, dtype=bool)
    start_unknown = np.zeros(shape, dtype=bool)
    start_outside_normal = np.zeros(shape, dtype=bool)
    if len(dates) >= 2:
        start_unknown[1] = up[0] & up[1]  # Anchor is outside retained history.
    if len(dates) >= 3:
        pair = up[1:-1] & up[2:]
        first[2:] = pair & non_up[:-2]
        continued[2:] = pair & up[:-2]
        anchor_missing = ~board_known[:-2]
        start_unknown[2:] = pair & anchor_missing & ~st[:-2]
        start_outside_normal[2:] = pair & st_known[:-2] & st[:-2]

    mature_known = np.zeros(shape, dtype=np.int64)
    mature_unknown = np.zeros(shape, dtype=np.int64)
    failure_events = np.zeros(shape, dtype=np.int64) if mode == "values" else None
    episodes: list[dict[str, Any]] = []
    support_reason_counts: Counter[str] = Counter()
    overlap_examples: list[dict[str, str]] = []
    overlap_pairs = 0
    active: dict[int, deque[tuple[int, str]]] = {}

    def reasons_for(i: int, j: int) -> list[str]:
        reasons = []
        if not trading_known[i, j]: reasons.append("trading_unknown")
        elif not trading[i, j]: reasons.append("known_nontrading_session")
        if not st_known[i, j]: reasons.append("ST_role_unknown")
        if not legal_known[i, j]: reasons.append("legal_price_role_unknown")
        elif not legal[i, j]: reasons.append("raw_price_not_legal")
        if not np.isfinite(close[i, j]): reasons.append("raw_close_missing_or_invalid")
        if not np.isfinite(factors[i, j]): reasons.append("factor_missing_or_invalid")
        if not source_known[i, j]: reasons.append("factor_source_unknown_or_mismatched")
        if effective[i, j] <= 0: reasons.append("factor_effective_date_unknown")
        elif effective[i, j] > _ordinal(dates[i]): reasons.append("factor_effective_after_session")
        if np.isfinite(close[i, j]) and np.isfinite(factors[i, j]) and not (np.isfinite(econ[i, j]) and econ[i, j] > 0):
            reasons.append("economic_close_invalid")
        return reasons

    # argwhere scans the T*N boolean grid once, ordered by end date then symbol.
    for e, j in np.argwhere(first):
        e, j = int(e), int(j)
        maturity = e + HORIZON
        identifier = f"{symbols[j]}:{dates[e]}"
        item: dict[str, Any] = {
            "episode_id": identifier, "symbol": symbols[j],
            "pre_rally_date": dates[e - 2], "first_upper_date": dates[e - 1],
            "second_upper_date": dates[e], "episode_end_index": e,
            "maturity_index": maturity,
            "maturity_date": dates[maturity] if maturity < len(dates) else None,
        }
        if maturity >= len(dates):
            item.update(support_status="not_yet_mature", unknown_roles=[], overlapping_prior_mature_episodes=0)
            if mode == "values": item["giveback"] = None
            episodes.append(item)
            continue
        required = [e - 2, *range(e + 1, maturity + 1)]
        unknown = [
            {"date": dates[i], "roles": reasons_for(i, j)}
            for i in required if not economic_support[i, j]
        ]
        item["unknown_roles"] = unknown
        if unknown:
            item["support_status"] = "mature_unknown"
            mature_unknown[maturity, j] += 1
            for gap in unknown: support_reason_counts.update(gap["roles"])
            if mode == "values": item["giveback"] = None
        else:
            item["support_status"] = "mature_supported"
            mature_known[maturity, j] += 1
            if mode == "values":
                # Printed raw/factor decimal equality must remain equality;
                # no epsilon can replace the frozen strictly-below condition.
                with localcontext() as context:
                    context.prec = 64  # Float-normalized inputs have <=17 significant digits.
                    anchor = Decimal(str(close[e - 2, j])) * Decimal(str(factors[e - 2, j]))
                    giveback = any(
                        Decimal(str(close[i, j])) * Decimal(str(factors[i, j])) < anchor
                        for i in range(e + 1, maturity + 1)
                    )
                item["giveback"] = giveback
                failure_events[maturity, j] += int(giveback)
        queue = active.setdefault(j, deque())
        while queue and queue[0][0] + HORIZON < e + 1:
            queue.popleft()
        item["overlapping_prior_mature_episodes"] = len(queue)
        overlap_pairs += len(queue)
        for _, prior in queue:
            if len(overlap_examples) < 20:
                overlap_examples.append({"earlier_episode": prior, "later_episode": identifier})
        queue.append((e, identifier))
        episodes.append(item)

    detected = np.cumsum(first.astype(np.int64), axis=0)
    counts, unknowns = np.cumsum(mature_known, axis=0), np.cumsum(mature_unknown, axis=0)
    pending = detected - counts - unknowns
    daily = {
        "detected": pd.DataFrame(detected, index=dates, columns=symbols),
        "count": pd.DataFrame(counts, index=dates, columns=symbols),
        "unknown": pd.DataFrame(unknowns, index=dates, columns=symbols),
        "pending": pd.DataFrame(pending, index=dates, columns=symbols),
    }
    if mode == "values":
        failures = np.cumsum(failure_events, axis=0)
        risk = np.full(shape, np.nan)
        supported_domain = (counts > 0) & (unknowns == 0)
        np.divide(failures, counts, out=risk, where=supported_domain)
        frequency_count = np.where(supported_domain, counts.astype(float), np.nan)
        daily.update(
            failure_count=pd.DataFrame(failures, index=dates, columns=symbols),
            risk=pd.DataFrame(risk, index=dates, columns=symbols),
            episode_frequency_count=pd.DataFrame(frequency_count, index=dates, columns=symbols),
        )
    receipt = {
        "mode": mode, "formation_end_completed_close": formation_end,
        "prefix_reference_sessions": len(dates), "fixed_symbol_count": len(symbols),
        "horizon_reference_sessions": HORIZON,
        "episode_identity_count": len(episodes),
        "mature_supported_count": int(counts[-1].sum()),
        "mature_unknown_count": int(unknowns[-1].sum()),
        "not_yet_mature_count": int(pending[-1].sum()),
        "known_continued_upper_pairs_not_new_episodes": int(continued.sum()),
        "possible_pair_start_unknown_count": int(start_unknown.sum()),
        "pair_start_known_ST_outside_normal_domain_count": int(start_outside_normal.sum()),
        "raw_normal_board_role_known_sessions": int(board_known.sum()),
        "ordinary_board_role_unusable_sessions": int((~board_known).sum()),
        "known_nontrading_sessions": int((trading_known & ~trading).sum()),
        "known_ST_sessions": int((st_known & st).sum()),
        "mature_support_unknown_role_counts": dict(sorted(support_reason_counts.items())),
        "overlapping_mature_post_window_pairs": overlap_pairs,
        "overlap_examples_at_most20": overlap_examples,
        "failure_comparison_computed": mode == "values",
        "risk_ratio_computed": mode == "values",
        "giveback_comparison": "strict Decimal(str(rawclose))*Decimal(str(asof_factor)); no epsilon",
        "frequency_control_role": "same_support_mature_episode_count_not_a_rate_or_fitted_score",
        "no_history_risk_or_frequency_zero_imputation": True,
        "any_mature_unknown_outcome_blocks_risk_and_frequency_scores": True,
        "history_effective_date_check_is_not_full_publication_PIT_certification": True,
        "native_price_limit_input_or_production_update_certified": False,
        "market_or_account_source_calls": 0,
        "algorithm": "O(reference_sessions*symbols+5*episodes); maturity-index cumulative events",
    }
    return {"episodes": episodes, "daily": daily, "receipt": receipt}
