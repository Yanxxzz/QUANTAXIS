"""Formation-time F141 portfolio controls; no prices after formation are used.

These controls change portfolio decisions, not the factor definition. They must
be frozen and evaluated as research policies; they do not certify actual fills.
"""
from __future__ import annotations

from math import floor, isfinite
from collections import defaultdict
from typing import Mapping, Iterable


def buffered_low_tail(values: Mapping[str, float], held: Iterable[str], *,
                      entry_fraction: float = .10, exit_fraction: float = .15) -> list[str]:
    """Retain eligible incumbents within the exit band, then fill by low score.

    The target count remains the baseline low-decile count. Securities with
    unknown scores cannot be retained by pretending that their score is zero.
    A simulator must separately preserve held assets whose sale is blocked.
    """
    if not 0 < entry_fraction <= exit_fraction <= 1:
        raise ValueError("Ordered entry/exit fractions required")
    finite = {str(code): float(value) for code, value in values.items()
              if isfinite(float(value))}
    ordered = sorted(finite, key=lambda code: (finite[code], code))
    count = floor(len(ordered) * entry_fraction)
    if count < 1:
        raise ValueError("No full target position in supplied support")
    band = set(ordered[:max(count, floor(len(ordered) * exit_fraction))])
    incumbents = set(map(str, held)) & band
    selected = [code for code in ordered if code in incumbents][:count]
    chosen = set(selected)
    selected.extend(code for code in ordered if code not in chosen)
    return selected[:count]


def constrained_equal_weights(codes: Iterable[str], beta: Mapping[str, float],
                              industry: Mapping[str, str | None], *,
                              beta_budget: float = 1., industry_cap: float = .20) -> dict:
    """Reduce equal weights to sector and positive-beta budgets; retain cash.

    No sector redistribution or leverage is introduced. Missing industry is an
    explicit common UNKNOWN bucket; missing beta blocks this policy. The limits
    constrain desired weights only: blocked sells may leave realized violations.
    """
    selected = list(map(str, codes))
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Nonempty unique target codes required")
    if not isfinite(beta_budget) or beta_budget <= 0 or not 0 < industry_cap <= 1:
        raise ValueError("Invalid exposure budgets")
    if any(code not in beta or not isfinite(float(beta[code])) for code in selected):
        raise ValueError("Missing formation-time beta")
    groups = {code: (industry[code].strip() if isinstance(industry.get(code), str)
                    and industry[code].strip() else "UNKNOWN") for code in selected}
    weights = {code: 1. / len(selected) for code in selected}
    sector_weights = defaultdict(float)
    for code in selected:
        sector_weights[groups[code]] += weights[code]
    for code in selected:
        weights[code] *= min(1., industry_cap / sector_weights[groups[code]])
    positive_beta = sum(weight * max(0., float(beta[code])) for code, weight in weights.items())
    scale = min(1., beta_budget / positive_beta) if positive_beta else 1.
    weights = {code: weight * scale for code, weight in weights.items()}
    sectors = defaultdict(float)
    for code, weight in weights.items():
        sectors[groups[code]] += weight
    return {"weights": weights, "cash_weight": 1. - sum(weights.values()),
            "positive_beta_exposure": sum(weight * max(0., float(beta[code]))
                                          for code, weight in weights.items()),
            "industry_weights": dict(sectors),
            "unknown_industry_codes": [code for code in selected if groups[code] == "UNKNOWN"]}
