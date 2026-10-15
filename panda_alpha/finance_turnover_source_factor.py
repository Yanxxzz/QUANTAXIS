"""Render a public-source finance/turnover replay, never acquire or score labels.

The supplied date is the frozen feature's usable date. This renderer does not
infer a publication lag, fill missing dates, model a position or certify PIT.
Only the received ``close`` index is used; received prices are not signal inputs.
"""
from __future__ import annotations

from collections.abc import Mapping
import keyword
import math
from numbers import Real
import re

import pandas as pd


_ALIASES = {"SH": "sh", "XSHG": "sh", "SZ": "sz", "XSHE": "sz",
            "BJ": "bj", "XBSE": "bj"}


def _plain_code(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{6}", value) is not None


def _code(value, markets):
    # Exact aliases observed in native_gf_transfer, with explicit market binding.
    if _plain_code(value):
        return value
    if isinstance(value, str) and value.count(".") == 1:
        code, suffix = value.split(".")
        if (_plain_code(code) and suffix in _ALIASES
                and markets.get(code) == _ALIASES[suffix]):
            return code
    raise ValueError("Source requires a six-digit code or explicitly bound market alias")


def _day(value):
    if not isinstance(value, (str, pd.Timestamp)):
        raise ValueError("Whole naive source date required")
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Whole naive source date required") from error
    if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
        raise ValueError("Whole naive source date required")
    return day.strftime("%Y-%m-%d")


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Source fields must be numeric, never bool or coerced strings")
    value = float(value)
    return value if math.isfinite(value) else None


def renderer_metadata(mode="target"):
    if mode not in {"target", "control"}:
        raise ValueError("Unknown finance/turnover mode")
    return {
        "scope": "public_source_replay_only", "mode": mode, "direction": 1,
        "formula": ("0.5*(rank_pct(net_flow_5)+rank_pct(turnover_ratio_5_20))"
                    if mode == "target" else "rank_pct(turnover_ratio_5_20)"),
        "rank": "same-date average percentile on the identical common valid source domain",
        "close_role": "received original index carrier only; no close values consumed",
        "missing": "NaN; no zero-fill, carry, decay, jitter or artificial tie splitting",
        "native_finance_certified": False, "PIT_certified": False,
        "continuous_production_certified": False, "admission_qualified": False,
        "holding_exit_strategy_included": False,
        "clock": "supplied source date is the frozen usable date; no lag inferred here",
    }


_BODY = r'''
    def _source_code(self, symbol):
        if len(symbol) == 6 and symbol.isascii() and symbol.isdigit():
            return symbol
        if symbol.count('.') == 1:
            code, suffix = symbol.split('.')
            aliases = {'SH': 'sh', 'XSHG': 'sh', 'SZ': 'sz', 'XSHE': 'sz',
                       'BJ': 'bj', 'XBSE': 'bj'}
            if (len(code) == 6 and code.isascii() and code.isdigit()
                    and suffix in aliases and self.MARKETS.get(code) == aliases[suffix]):
                return code
        return None

    def calculate(self, factors):
        supplied = factors['close']
        close = supplied.series if hasattr(supplied, 'series') else supplied
        if not isinstance(close, pd.Series):
            raise ValueError('close must be a pandas Series or FactorSeries.series')
        original = close.index
        if (not isinstance(original, pd.MultiIndex) or original.nlevels != 2
                or set(original.names) != {'date', 'symbol'} or not original.is_unique):
            raise ValueError('Unique named date/symbol input index required')
        days = pd.to_datetime(original.get_level_values('date').astype(str), errors='raise')
        if days.isna().any() or days.tz is not None or not days.equals(days.normalize()):
            raise ValueError('Whole naive input reference dates required')
        if original.get_level_values('symbol').isna().any():
            raise ValueError('Missing input symbol')
        day_strings = days.strftime('%Y-%m-%d').to_numpy()
        symbols = original.get_level_values('symbol').astype(str).to_numpy()
        codes = [self._source_code(symbol) for symbol in symbols]
        identities = pd.DataFrame({'date': day_strings, 'symbol': symbols, 'code': codes})
        if identities.duplicated(['date', 'symbol']).any():
            raise ValueError('Duplicate daily input identity')
        if identities.dropna(subset=['code']).duplicated(['date', 'code']).any():
            raise ValueError('Duplicate canonical stock/date aliases')
        projected = ((day_strings >= self.EVALUATION_START)
                     & (day_strings <= self.EVALUATION_END))
        flow = np.full(len(original), np.nan)
        turnover = np.full(len(original), np.nan)
        for i in np.flatnonzero(projected):
            pair = self.SOURCE.get(codes[i], {}).get(day_strings[i])
            if pair is not None:
                flow[i] = np.nan if pair[0] is None else pair[0]
                turnover[i] = np.nan if pair[1] is None else pair[1]
        common = projected & np.isfinite(flow) & np.isfinite(turnover) & (turnover >= 0)
        output = np.full(len(original), np.nan)
        selected = pd.DataFrame({'date': day_strings[common], 'flow': flow[common],
                                 'turnover': turnover[common]}, index=np.flatnonzero(common))
        for _, rows in selected.groupby('date', sort=False):
            turnover_rank = rows.turnover.rank(method='average', pct=True)
            values = turnover_rank
            if self.MODE == 'target':
                values = 0.5 * (rows.flow.rank(method='average', pct=True) + turnover_rank)
            output[rows.index.to_numpy()] = values.to_numpy()
        result = pd.Series(output[projected], index=original[projected], name='value')
        self.diagnostics = {'input_rows': len(original), 'projected_rows': len(result),
                            'common_valid_rows': int(common.sum()),
                            'finite_output_rows': int(np.isfinite(result.to_numpy()).sum()),
                            'external_public_source_replay_only': True,
                            'native_finance_certified': False, 'PIT_certified': False}
        return result
'''


def render_finance_turnover_source_factor(external_source, evaluation_window, *,
                                           mode="target", markets=None,
                                           class_name="FinanceTurnoverSourceFactor"):
    """Return deterministic UTF-8/LF-compatible source with one injected Factor subclass.

    ``external_source[code][usable_date]`` is exactly ``[net_flow_5,
    turnover_ratio_5_20]``. Nonfinite values remain unknown, and a negative
    turnover ratio is outside the common valid domain for BOTH modes. ``markets``
    binds only the previously observed SH/XSHG, SZ/XSHE and BJ/XBSE aliases.
    """
    metadata = renderer_metadata(mode)
    if (not isinstance(class_name, str) or keyword.iskeyword(class_name)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", class_name) is None):
        raise ValueError("Invalid generated class identity")
    if not isinstance(evaluation_window, Mapping) or set(evaluation_window) != {"start", "end"}:
        raise ValueError("Exact start/end evaluation window required")
    first, last = _day(evaluation_window["start"]), _day(evaluation_window["end"])
    if first > last:
        raise ValueError("Ordered evaluation window required")
    markets = {} if markets is None else markets
    if not isinstance(markets, Mapping) or any(
            not _plain_code(code) or market not in {"sh", "sz", "bj"}
            for code, market in markets.items()):
        raise ValueError("Explicit six-digit market identities required")
    markets = dict(sorted(markets.items()))
    if not isinstance(external_source, Mapping):
        raise ValueError("Source must be a code/date mapping")
    source = {}
    for raw_code, records in external_source.items():
        code = _code(raw_code, markets)
        if code in source:
            raise ValueError("Duplicate canonical source code aliases")
        if not isinstance(records, Mapping):
            raise ValueError("Source records must be a dated mapping")
        rows = {}
        for raw_day, pair in records.items():
            day = _day(raw_day)
            if not first <= day <= last or day in rows:
                raise ValueError("Duplicate or out-of-window source date")
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("Exactly two source fields required")
            rows[day] = [_number(pair[0]), _number(pair[1])]
        source[code] = dict(sorted(rows.items()))
    source = dict(sorted(source.items()))
    header = "import numpy as np\nimport pandas as pd\n\n\nclass " + class_name + "(Factor):\n"
    for name, value in (("SOURCE", source), ("MARKETS", markets), ("MODE", mode),
                        ("EVALUATION_START", first), ("EVALUATION_END", last),
                        ("REPLAY_METADATA", metadata)):
        header += "    " + name + " = " + repr(value) + "\n"
    return header + _BODY
