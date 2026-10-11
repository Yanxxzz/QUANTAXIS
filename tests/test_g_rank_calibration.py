from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from panda_alpha.g_rank_calibration import midpoint_industry_g


def cohort():
    return pd.DataFrame({"date": ["2026-08-26"] * 6,
                         "symbol": ["a", "b", "c", "d", "e", "f"],
                         "sector": ["solo", "pair", "pair", "tied", "tied", "tied"],
                         "GP_delta": [-100.0, -2.0, 4.0, 7.0, 7.0, 7.0],
                         "G_pct": [1.0, 0.5, 1.0, 2/3, 2/3, 2/3],
                         "source_record_sha256": ["bound"] * 6})


def test_midpoint_removes_size_shift_and_preserves_real_ties_and_source():
    source = cohort()
    old = deepcopy(source)
    result, receipt = midpoint_industry_g(source)
    assert result.G_pct.tolist() == [0.5, 0.25, 0.75, 0.5, 0.5, 0.5]
    assert result.groupby("sector").G_pct.mean().eq(0.5).all()
    assert receipt["singleton_rows"] == 1
    assert receipt["jitter_or_tie_breaking"] is False
    pd.testing.assert_frame_equal(source, old)
    pd.testing.assert_frame_equal(result.drop(columns="G_pct"), old.drop(columns="G_pct"))


def test_counts_use_financial_peers_before_later_F_missingness():
    source = cohort().iloc[1:3].copy()
    source["F_finite"] = [True, False]
    result, _ = midpoint_industry_g(source)
    assert result.loc[result.F_finite, "G_pct"].tolist() == [0.25]


def test_dates_are_separate_cohorts_even_when_sector_name_is_same():
    source = cohort().iloc[1:3].copy()
    source.loc[source.index[1], "date"] = "2026-08-27"
    result, _ = midpoint_industry_g(source)
    assert result.G_pct.eq(0.5).all()


@pytest.mark.parametrize("bad", ["missing", "empty", "duplicate", "nonfinite", "unknown_sector"])
def test_incomplete_financial_sources_cannot_be_ranked(bad):
    source = cohort()
    if bad == "missing":
        source = source.drop(columns="GP_delta")
    elif bad == "empty":
        source = source.iloc[:0]
    elif bad == "duplicate":
        source = pd.concat([source, source.iloc[:1]])
    elif bad == "nonfinite":
        source.loc[0, "GP_delta"] = np.nan
    else:
        source.loc[0, "sector"] = None
    with pytest.raises(ValueError):
        midpoint_industry_g(source)
