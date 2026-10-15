"""Documented PandaData inputs for the separately frozen strategy migration.

No orders, NAV, account authentication, files, or registry operations belong here.
Root inlines this module in the remote strategy; pandas is provided by Worker.
"""
from datetime import datetime, timedelta
import math
import re

import pandas as pd


MARGIN_FIELDS = ("margin_balance", "buy_on_margin_value", "margin_repayment")


def _date(value):
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        fmt = "%Y%m%d"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", text):
        # Only the date role is consumed; malformed suffixes still fail parsing.
        try:
            stamp = pd.Timestamp(text)
        except (ValueError, TypeError) as exc:
            raise ValueError("Malformed PandaData date: " + text) from exc
        if pd.isna(stamp):
            raise ValueError("Missing PandaData date")
        return stamp.strftime("%Y-%m-%d")
    else:
        raise ValueError("Unverified PandaData date format: " + text)
    try:
        return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Malformed PandaData date: " + text) from exc


def _symbol(value):
    text = str(value).upper()
    if re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", text):
        return text
    if not re.fullmatch(r"\d{6}", text):
        raise ValueError("Unverified PandaData symbol: " + text)
    if text.startswith(("00", "30")):
        return text + ".SZ"
    if text.startswith(("60", "68")):
        return text + ".SH"
    if text.startswith(("4", "8", "92")):
        return text + ".BJ"
    raise ValueError("Unverified exchange for symbol: " + text)


def _amount(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _rows(frame, fields, symbols, start, end, label):
    """Require declared columns, reject ambiguous duplicate identities."""
    if not isinstance(frame, pd.DataFrame):
        raise RuntimeError(label + " did not return a DataFrame; check Worker permission and API contract")
    if "symbol" not in frame.columns or "date" not in frame.columns:
        if isinstance(frame.index, pd.MultiIndex) and {"symbol", "date"}.issubset(frame.index.names):
            frame = frame.reset_index()
        elif frame.index.name in ("date", "symbol"):
            frame = frame.reset_index()
    required = {"symbol", "date", *fields}
    if not required.issubset(frame.columns):
        raise RuntimeError(label + " missing required columns: " + ",".join(sorted(required - set(frame.columns))))
    result = {}
    allowed = set(symbols)
    for raw in frame.loc[:, ["symbol", "date", *fields]].to_dict("records"):
        symbol, day = _symbol(raw["symbol"]), _date(raw["date"])
        if symbol not in allowed or not start <= day <= end:
            raise RuntimeError(label + " returned an out-of-scope symbol/date")
        key = symbol, day
        value = {field: _amount(raw[field]) for field in fields}
        if key in result and result[key] != value:
            raise RuntimeError(label + " contains conflicting duplicate symbol/date rows")
        result[key] = value
    return result


def _examples(validation_examples):
    # Accept the retained receipt.selected_facts object or a list of its 3 rows.
    if isinstance(validation_examples, dict):
        if "selected_facts" in validation_examples:
            validation_examples = validation_examples["selected_facts"]
        rows = list(validation_examples.values())
    else:
        rows = list(validation_examples)
    if len(rows) != 3:
        raise ValueError("Exactly three frozen public repayment-role examples are required")
    expected = {}
    for row in rows:
        symbol = _symbol(row["SCODE"])
        day = _date(row["DATE"])
        values = (_amount(row["RZYE"]), _amount(row["RZMRE"]), _amount(row["RZCHE"]))
        if any(v is None for v in values) or (symbol, day) in expected:
            raise ValueError("Invalid or duplicate frozen validation example")
        expected[symbol, day] = values
    if {symbol for symbol, _ in expected} != {"000001.SZ"}:
        raise ValueError("Frozen validation examples must be the independently checked 000001 cases")
    return expected


def _validate_repayment_role(panda_data, validation_examples):
    expected = _examples(validation_examples)
    dates = sorted(day for _, day in expected)
    result = _rows(
        panda_data.get_margin(symbol="000001.SZ", start_date=dates[0].replace("-", ""),
                              end_date=dates[-1].replace("-", ""), fields=["symbol", "date", *MARGIN_FIELDS], margin_type="cash"),
        MARGIN_FIELDS, ["000001.SZ"], dates[0], dates[-1], "get_margin validation",
    )
    for key, amounts in expected.items():
        observed = result.get(key)
        if observed is None or any(observed[field] != expected_value for field, expected_value in zip(MARGIN_FIELDS, amounts)):
            raise RuntimeError("Worker financing validation failed for " + key[0] + " " + key[1] + "; no orders permitted")
    return result


def _segments(start, end):
    day, final = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    while day <= final:
        last = min(day + timedelta(days=91), final)
        yield day.strftime("%Y%m%d"), last.strftime("%Y%m%d"), day.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")
        day = last + timedelta(days=1)


class StrategyInputs:
    """Raw histories plus causal, cached features on the frozen reference clock."""

    def __init__(self, daily_close, financing, turnover, calendar, symbols, validation_count):
        self.daily_close = daily_close
        self.financing = financing
        self.turnover = turnover
        self.calendar = list(calendar)
        self.symbols = tuple(symbols)
        self.validation_count = validation_count
        self._features = {}

    def validate_run_calendar(self, actual, start, end):
        received = [_date(d) for d in actual]
        expected = [d for d in self.calendar if _date(start) <= d <= _date(end)]
        if received != expected:
            raise RuntimeError("Native strategy trading calendar differs from the frozen reference sessions; no orders permitted")
        return True

    def features_for(self, symbol, session_index):
        symbol = _symbol(symbol)
        if symbol not in self.symbols:
            raise KeyError("Feature request outside frozen strategy universe")
        if not isinstance(session_index, int) or isinstance(session_index, bool) or not 0 <= session_index < len(self.calendar):
            raise IndexError("Invalid reference session index")
        key = symbol, session_index
        if key in self._features:
            return dict(self._features[key])
        result = {"net_flow": None, "balance_change": None, "turnover_relative": None,
                  "latest_report_date": self.calendar[session_index - 2] if session_index >= 2 else None,
                  "execution_date": self.calendar[session_index]}
        if session_index >= 21:
            fd = self.calendar[session_index - 7:session_index - 1]
            td = self.calendar[session_index - 21:session_index - 1]
            financing = [self.financing.get((symbol, day)) for day in fd]
            if all(row is not None and all(row.get(field) is not None for field in ("RZYE", "RZMRE", "RZCHE")) for row in financing):
                gross = sum(row["RZMRE"] + row["RZCHE"] for row in financing[1:])
                if gross > 0:
                    result["net_flow"] = sum(row["RZMRE"] - row["RZCHE"] for row in financing[1:]) / gross
                if financing[0]["RZYE"] > 0:
                    result["balance_change"] = financing[-1]["RZYE"] / financing[0]["RZYE"] - 1
            tv = [self.turnover.get((symbol, day)) for day in td]
            if len(tv) == 20 and all(v is not None for v in tv) and sum(tv) > 0:
                result["turnover_relative"] = (sum(tv[-5:]) / 5) / (sum(tv) / 20)
        self._features[key] = result
        return dict(result)

    def asoffeatures(self, session_index, symbol=None):
        if symbol is not None:
            return self.features_for(symbol, session_index)
        return {s: self.features_for(s, session_index) for s in self.symbols}


def load_inputs(panda_data, symbols, start, end, validation_examples, *, reference_calendar):
    """Preload documented APIs; fail before first order on schema/role mismatch.

    The caller supplies the retained reference calendar by value. Input warm-up
    starts 25 reference sessions before start, preserving the original two-board
    and prior-20-session entry lookback as well as the t-2 feature clock.
    The task's native context.trade_date_list must subsequently pass
    result.validate_run_calendar before any order is sent.
    """
    symbols = [_symbol(s) for s in symbols]
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("Frozen strategy symbols must be nonempty and unique")
    cal = [_date(d) for d in reference_calendar]
    if cal != sorted(set(cal)):
        raise ValueError("Frozen reference calendar must be unique and ordered")
    first, last = _date(start), _date(end)
    if first not in cal or last not in cal or first > last:
        raise ValueError("Strategy boundaries must be retained reference sessions")
    validation = _validate_repayment_role(panda_data, validation_examples)
    warm = cal[max(0, cal.index(first) - 25)]
    daily_close, financing, turnover = {}, {}, {}
    for segment_start, segment_end, iso_start, iso_end in _segments(warm, last):
        for offset in range(0, len(symbols), 100):
            batch = symbols[offset:offset + 100]
            margin = _rows(panda_data.get_margin(symbol=batch, start_date=segment_start, end_date=segment_end,
                                                fields=["symbol", "date", *MARGIN_FIELDS], margin_type="cash"),
                           MARGIN_FIELDS, batch, iso_start, iso_end, "get_margin")
            for key, row in margin.items():
                if key in validation and row != validation[key]:
                    raise RuntimeError("Bulk financing values conflict with validated Worker examples")
                financing[key] = {"RZYE": row["margin_balance"], "RZMRE": row["buy_on_margin_value"], "RZCHE": row["margin_repayment"]}
            factor = _rows(panda_data.get_factor(factors=["turnover"], symbol=batch,
                                                start_date=segment_start, end_date=segment_end),
                           ["turnover"], batch, iso_start, iso_end, "get_factor turnover")
            turnover.update({key: row["turnover"] for key, row in factor.items()})
            daily = _rows(panda_data.get_stock_daily(symbol=batch, start_date=segment_start, end_date=segment_end,
                                                    fields=["symbol", "date", "close"], indicator="", st=True),
                          ["close"], batch, iso_start, iso_end, "get_stock_daily")
            daily_close.update({key: row["close"] if row["close"] is not None and row["close"] > 0 else None for key, row in daily.items()})
    if not any(v is not None for v in daily_close.values()):
        raise RuntimeError("No valid daily close input; empty strategy result is not an economic result")
    if not any(v is not None for v in turnover.values()):
        raise RuntimeError("No valid turnover input; check Worker field/permission")
    if not any(all(v is not None for v in row.values()) for row in financing.values()):
        raise RuntimeError("No complete financing input in the frozen universe; check Worker field/permission")
    return StrategyInputs(daily_close, financing, turnover, cal, symbols, 3)
