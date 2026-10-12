"""Synthetic fixed-label checks; no quote-source or economic certification."""
import numpy as np
import pandas as pd
import pytest

from panda_alpha.quote_labels import fixed_forward_open_labels


CALENDAR = ["2032-01-05", "2032-01-06", "2032-01-07", "2032-01-08", "2032-01-09", "2032-01-12", "2032-01-13"]


def formation(symbol="000001", date=CALENDAR[0]):
    return pd.DataFrame({"date": [date], "symbol": [symbol]})


def quote_frame(entry=10., exit=12.):
    return pd.DataFrame({"date": [CALENDAR[1], CALENDAR[5], CALENDAR[6]],
                         "symbol": ["000001"] * 3, "open": [entry, 99., exit]})


def test_fixed_reference_t_plus_one_to_t_plus_six_not_fifth_close():
    label = fixed_forward_open_labels(formation(), quote_frame(), CALENDAR).iloc[0]
    assert label.entry_date == CALENDAR[1]
    assert label.exit_date == CALENDAR[6]
    assert label.label_available
    assert label.forward_return == pytest.approx(.2)


@pytest.mark.parametrize("role,bad", [("entry", None), ("entry", 0.), ("entry", -1.),
                                       ("exit", None), ("exit", np.inf), ("exit", 0.)])
def test_missing_nonpositive_open_retains_unavailable_formation(role, bad):
    quotes = quote_frame(**{role: bad})
    labels = fixed_forward_open_labels(formation(), quotes, CALENDAR)
    assert len(labels) == 1
    assert not labels.iloc[0].label_available
    assert np.isnan(labels.iloc[0].forward_return)


def test_future_horizon_and_absent_quote_never_drop_stocks_or_fill_price():
    forms = pd.concat([formation(), formation("000002"), formation("000003", CALENDAR[-1])], ignore_index=True)
    labels = fixed_forward_open_labels(forms, quote_frame(), CALENDAR)
    assert labels[["date", "symbol"]].equals(forms)
    assert labels.label_available.tolist() == [True, False, False]
    assert labels.iloc[1].availability_reason == "entry_open_missing_or_nonpositive"
    assert labels.iloc[2].availability_reason == "calendar_horizon_pending"


def test_no_suspension_preclose_book_valuation_or_close_substitute():
    quotes = quote_frame(entry=None)
    quotes["preclose"] = 10.
    quotes["close"] = 10.
    quotes["trade_status"] = "0"
    result = fixed_forward_open_labels(formation(), quotes, CALENDAR)
    assert not result.iloc[0].label_available


@pytest.mark.parametrize("which", ["formations", "quotes", "prior"])
def test_duplicate_identity_rejected(which):
    forms, quotes = formation(), quote_frame()
    prior = forms.assign(label_available=False)
    if which == "formations": forms = pd.concat([forms, forms])
    if which == "quotes": quotes = pd.concat([quotes, quotes.iloc[:1]])
    if which == "prior": prior = pd.concat([prior, prior])
    with pytest.raises(ValueError, match="duplicate"):
        fixed_forward_open_labels(forms, quotes, CALENDAR, preserve_unavailable=prior)


def test_old_false_flag_remains_false_when_numeric_quote_pair_now_exists():
    prior = formation().assign(label_available=False)
    result = fixed_forward_open_labels(formation(), quote_frame(), CALENDAR, preserve_unavailable=prior)
    assert not result.iloc[0].label_available
    assert np.isnan(result.iloc[0].forward_return)
    assert result.iloc[0].availability_reason == "old_unavailable_label_flag_preserved"


def test_extra_and_reordered_quotes_do_not_change_formation_identity_or_label():
    quotes = pd.concat([quote_frame().iloc[::-1], pd.DataFrame({"date": [CALENDAR[-1]], "symbol": ["000002"], "open": [1.]})])
    actual = fixed_forward_open_labels(formation(), quotes, CALENDAR)
    expected = fixed_forward_open_labels(formation(), quote_frame(), CALENDAR)
    pd.testing.assert_frame_equal(actual, expected)


def test_calendar_duplicates_or_unsorted_rejected():
    for bad in [CALENDAR[::-1], CALENDAR + [CALENDAR[-1]]]:
        with pytest.raises(ValueError, match="calendar"):
            fixed_forward_open_labels(formation(), quote_frame(), bad)
