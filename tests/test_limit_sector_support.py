from copy import deepcopy

import pandas as pd
import pytest

from panda_alpha.limit_sector_support import measure_sector_support


def case():
    calendar = pd.bdate_range("2024-11-01", periods=50).strftime("%Y-%m-%d").tolist()
    event = calendar[-1]
    codes = [f"{i:06d}" for i in range(1, 33)]
    records = {code: {"category_code": "C", "major_code": "39" if i < 6 else "35"}
               for i, code in enumerate(codes)}
    life = {code: {"ipo_date": "1990-01-01", "delisted_date": None} for code in codes}
    rows = []
    for i, code in enumerate(codes):
        for offset, day in enumerate(calendar[-3:-1]):
            # Target twice; five same-industry peers each at least once;
            # other industry has no upper close. Both target and peers
            # remain in the same fully observable source domain.
            hit = i == 0 or (i < 6 and offset == 1)
            rows.append(dict(code=code, date=day, open=10.0, high=11.0,
                             low=9.0, close=11.0 if hit else 10.0,
                             pre_close=10.0, volume=1000, amount=10000,
                             is_st=False))
    return dict(rows=rows, event_date=event, calendar=calendar, universe=codes,
                target=codes[0], lifecycle=life,
                industry_edition=dict(vintage_id="2024H1", publication_date="2024-09-30",
                                      records=records))


def run(c):
    return measure_sector_support(**c)


def test_fixed_peer_denominators_exclude_target_and_or_across_two_days():
    result = run(case())
    assert result["common_supported"]
    assert result["peer_universe_count"] == result["market_observed_peers"] == 31
    assert result["sector_observed_peers"] == result["sector_upper_hit_peers"] == 5
    assert result["market_upper_hit_peers"] == 5
    assert result["target_score"] == pytest.approx(1 - 5 / 31)
    assert result["control_score"] == pytest.approx(5 / 31)
    assert result["positive_support_gate"] is True


def test_event_and_future_rows_do_not_change_scores_or_counts():
    c = case()
    before = run(c)
    for day in [c["event_date"], "2099-01-01"]:
        c["rows"].extend([dict(row, date=day, close=-999) for row in c["rows"][:2]])
    after = run(c)
    for key in ["target_score", "control_score", "market_observed_peers", "sector_observed_peers"]:
        assert before[key] == after[key]
    assert after["ignored_unneeded_rows"] == 4


@pytest.mark.parametrize("change,reason", [
    ({"is_st": None}, "ST_OR_UNKNOWN_ST"),
    ({"is_st": "False"}, "ST_OR_UNKNOWN_ST"),
    ({"is_st": 0}, "ST_OR_UNKNOWN_ST"),
    ({"is_st": True}, "ST_OR_UNKNOWN_ST"),
    ({"volume": 0}, "RAW_PRICE_OR_ACTIVITY_UNKNOWN"),
    ({"amount": float("nan")}, "RAW_PRICE_OR_ACTIVITY_UNKNOWN"),
    ({"low": 10.5}, "RAW_GEOMETRY_INVALID"),
    ({"high": 11.01}, "ORDINARY10_PERCENT_BAND_VIOLATION"),
])
def test_unknown_peer_is_not_a_zero_hit_and_shrinks_common_observed_domain(change, reason):
    c = case()
    # Use an outside-sector peer, retaining min5 and market30.
    c["rows"][-1].update(change)
    result = run(c)
    assert result["common_supported"]
    assert result["market_observed_peers"] == 30
    assert result["market_unknown_peers"] == 1
    assert result["peer_unknown_reasons"] == {reason: 1}
    assert result["control_score"] == pytest.approx(5 / 30)


def test_missing_peer_day_not_filled_and_sector_minimum_common_masks_both_scores():
    c = case()
    del c["rows"][3]  # One of exactly five sector peers lacks its second day.
    result = run(c)
    assert result["sector_observed_peers"] == 4
    assert result["sector_unknown_peers"] == 1
    assert result["target_score"] is result["control_score"] is None
    assert result["reason"] == "FEWER_THAN5_OBSERVED_SECTOR_PEERS"


def test_market_minimum_is30_not29():
    c = case()
    c["rows"] = [row for row in c["rows"] if row["code"] not in c["universe"][-2:]]
    result = run(c)
    assert result["market_observed_peers"] == 29
    assert result["target_score"] is result["control_score"] is None
    assert result["reason"] == "FEWER_THAN30_OBSERVED_MARKET_PEERS"


@pytest.mark.parametrize("publication", ["EVENT", "2099-01-01", None])
def test_publication_day_and_later_or_unknown_not_usable(publication):
    c = case()
    c["industry_edition"]["publication_date"] = c["event_date"] if publication == "EVENT" else publication
    result = run(c)
    assert result["reason"] == "INDUSTRY_NOT_PUBLIC_BEFORE_EVENT"
    assert not result["common_supported"]


def test_target_source_must_agree_with_both_frozen_upper_closes():
    c = case()
    c["rows"][0]["close"] = 10.0
    result = run(c)
    assert result["reason"] == "TARGET_NOT_TWO_UPPER_CLOSES"
    assert result["target_score"] is result["control_score"] is None


def test_decimal_exactness_and_half_up_tick_no_float_epsilon():
    c = case()
    c["rows"][0].update(pre_close="10.05", open="10.05", low="9.05", high="11.06", close="11.06")
    assert run(c)["common_supported"]
    c["rows"][0]["close"] = "11.059999999"
    assert run(c)["reason"] == "TARGET_NOT_TWO_UPPER_CLOSES"


def test_duplicate_needed_identity_unknown_not_first_or_last_selected():
    c = case()
    c["rows"].append(deepcopy(c["rows"][-1]))
    result = run(c)
    assert result["peer_unknown_reasons"] == {"DUPLICATE_RAW_IDENTITY": 1}
    assert result["market_observed_peers"] == 30
    c["rows"].append(deepcopy(c["rows"][0]))
    assert run(c)["reason"] == "TARGET_DUPLICATE_RAW_IDENTITY"


@pytest.mark.parametrize("life_change,reason", [
    ({"ipo_date": None, "delisted_date": None}, "IPO_DATE_UNKNOWN"),
    ({"ipo_date": "1990-01-01"}, "LIFECYCLE_UNKNOWN"),
    ({"ipo_date": "1990-01-01", "delisted_date": "2024-01-01"}, "DELISTED"),
    ({"ipo_date": "2099-01-01", "delisted_date": None}, "PRE_IPO"),
])
def test_lifecycle_unknown_delisted_preipo_do_not_enter_observed_peer_domain(life_change, reason):
    c = case()
    c["lifecycle"][c["universe"][-1]] = life_change
    result = run(c)
    assert result["market_observed_peers"] == 30
    assert result["peer_unknown_reasons"] == {reason: 1}


def test_ipo_excludes_first20_independent_reference_days():
    c = case()
    c["lifecycle"][c["universe"][-1]]["ipo_date"] = c["calendar"][-22]
    assert run(c)["market_observed_peers"] == 30  # t-2 is reference ordinal19.
    c["lifecycle"][c["universe"][-1]]["ipo_date"] = c["calendar"][-23]
    assert run(c)["market_observed_peers"] == 31  # t-2 ordinal20, t-1 ordinal21.


def test_unknown_industry_is_not_current_backfilled_and_can_only_join_market():
    c = case()
    del c["industry_edition"]["records"][c["universe"][-1]]
    result = run(c)
    assert result["industry_unknown_peers"] == 1
    assert result["market_observed_peers"] == 31
    del c["industry_edition"]["records"][c["target"]]
    assert run(c)["reason"] == "TARGET_INDUSTRY_UNKNOWN"


@pytest.mark.parametrize("clock_change", ["duplicate", "unsorted", "event_absent", "too_short"])
def test_reference_clock_invalid_not_compressed_or_reordered(clock_change):
    c = case()
    if clock_change == "duplicate": c["calendar"].append(c["calendar"][-1])
    elif clock_change == "unsorted": c["calendar"][0:2] = list(reversed(c["calendar"][0:2]))
    elif clock_change == "event_absent": c["event_date"] = "2099-01-01"
    else: c["event_date"] = c["calendar"][1]
    assert not run(c)["common_supported"]
    assert run(c)["target_score"] is run(c)["control_score"] is None


def test_dataframe_and_raw_integer_dates_same_measurement():
    c = case()
    expected = run(c)
    for row in c["rows"]: row["date"] = int(row["date"].replace("-", ""))
    c["rows"] = pd.DataFrame(c["rows"])
    assert run(c) == expected


def test_accepted_canonical_aliases_match_raw_source():
    c = case()
    expected = run(c)
    for row in c["rows"]:
        row["preclose"] = row.pop("pre_close")
        row["volume_shares"] = row.pop("volume")
    assert run(c) == expected


@pytest.mark.parametrize("field,value", [("preclose", 9.99), ("volume_shares", 1234)])
def test_conflicting_canonical_alias_never_hides_raw_source(field, value):
    c = case()
    c["rows"][-1][field] = value
    result = run(c)
    assert result["peer_unknown_reasons"] == {"CONFLICTING_RAW_CANONICAL_ALIAS": 1}
    assert result["market_observed_peers"] == 30


def test_duplicate_fixed_universe_rejected_and_qfq_not_accepted():
    c = case()
    c["universe"].append(c["universe"][-1])
    assert run(c)["reason"] == "INVALID_FIXED_UNIVERSE_OR_TARGET"
    c = case()
    c["price_basis"] = "qfq"
    assert run(c)["reason"] == "UNADJUSTED_RAW_PRICE_BASIS_REQUIRED"


def test_zero_support_difference_is_valid_not_missing_or_positive_gate():
    c = case()
    for row in c["rows"]:
        row["close"] = 11.0
    result = run(c)
    assert result["common_supported"]
    assert result["target_score"] == 0.0
    assert result["control_score"] == 1.0
    assert result["positive_support_gate"] is False
