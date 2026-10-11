"""One explicit G rank hypothesis; unchanged source operands and qualification.

Midpoint empirical CDF removes the 1/(2*n) mean shift of average-rank/n.
Call this on the complete qualified financial cohort before native F filtering.
It does not certify sources, resolve revisions, or change portfolio weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def midpoint_industry_g(source: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = {"date", "symbol", "sector", "GP_delta"}
    if not required.issubset(source.columns) or source.empty:
        raise ValueError("Complete financial G cohort required")
    if source.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate financial cohort identity")
    if (source[["date", "symbol", "sector"]].isna().any().any()
            or source.sector.astype(str).str.strip().eq("").any()):
        raise ValueError("Named dated industry source required")
    values = pd.to_numeric(source.GP_delta, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("Finite source GP_delta required")
    result = source.copy()
    grouped = values.groupby([source.date, source.sector], sort=False)
    counts = grouped.transform("size")
    ranks = grouped.rank(method="average")
    result["G_pct"] = (ranks - 0.5) / counts
    means = result.groupby(["date", "sector"], sort=False).G_pct.mean()
    return result, {
        "rank_definition": "(average_rank-0.5)/financial_industry_n",
        "financial_cohort_rows": len(result),
        "industry_date_groups": len(means),
        "singleton_rows": int(counts.eq(1).sum()),
        "max_industry_mean_error_from_half": float((means - 0.5).abs().max()),
        "before_native_F_missingness": True,
        "source_operands_and_qualification_unchanged": True,
        "jitter_or_tie_breaking": False,
    }
