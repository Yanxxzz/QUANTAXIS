"""Outcome-blind, same-session matching for a financing-report-domain audit.

The opposite group has unconfirmed report support, not zero financing and not
certified non-margin eligibility. Matches are diagnostics, not causal estimates.
"""
from collections import defaultdict
import math

import numpy as np
from scipy.optimize import linear_sum_assignment


def match_financing_domain(rows):
    """Maximum-cardinality, minimum-distance matching without replacement.

    Exact date and two-digit code-board prefix; both market-cap ratio and
    second/first-board turnover ratio must differ by at most a factor of two.
    Distance is squared log2-ratio distance, with equal fixed dimension weights.
    The two-times tolerances are frozen diagnostic matching tolerances, not
    strategy selection cutoffs optimized on returns. No cross-date statistics.
    Extra columns (including outcomes) are never accessed.
    """
    cells = defaultdict(list)
    seen = set()
    for row in rows:
        date, code = str(row['date']), str(row['code'])
        if len(code) != 6 or not code.isdigit():
            raise ValueError('Need an exact six-digit code')
        if (date, code) in seen:
            raise ValueError('Duplicate opportunity identity')
        seen.add((date, code))
        if not isinstance(row['finance_supported'], bool):
            raise ValueError('Report support must be explicit boolean, not amount')
        values = [row['size_ratio'], row['turnover_ratio']]
        if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) <= 0 for v in values):
            raise ValueError('Positive finite pre-entry covariates required')
        cells[date, code[:2]].append({
            'date': date, 'code': code, 'finance_supported': row['finance_supported'],
            'log2_size': math.log2(float(values[0])),
            'log2_turnover': math.log2(float(values[1])),
        })
    pairs = []
    for (date, board), cell in sorted(cells.items()):
        a = sorted((r for r in cell if r['finance_supported']), key=lambda r: r['code'])
        b = sorted((r for r in cell if not r['finance_supported']), key=lambda r: r['code'])
        if not a or not b:
            continue
        xa = np.array([[r['log2_size'], r['log2_turnover']] for r in a])
        xb = np.array([[r['log2_size'], r['log2_turnover']] for r in b])
        difference = xa[:, None, :] - xb[None, :, :]
        allowed = np.all(np.abs(difference) <= 1.0, axis=2)
        distance = np.sum(difference ** 2, axis=2)
        # One additional valid pair always wins over any total distance change.
        dummy = 2.0 * len(a) + 1.0
        costs = np.full((len(a), len(b) + len(a)), dummy)
        costs[:, :len(b)] = np.where(allowed, distance, dummy * (len(a) + 2))
        ai, bi = linear_sum_assignment(costs)
        for i, j in zip(ai, bi):
            if j >= len(b):
                continue
            if not allowed[i, j]:
                raise AssertionError('Invalid match passed the dummy option')
            pairs.append({'date': date, 'board': board, 'reported_code': a[i]['code'],
                          'unconfirmed_code': b[j]['code'], 'distance': float(distance[i, j]),
                          'log2_size_difference': float(difference[i, j, 0]),
                          'log2_turnover_difference': float(difference[i, j, 1])})
    return sorted(pairs, key=lambda r: (r['date'], r['reported_code'], r['unconfirmed_code']))
