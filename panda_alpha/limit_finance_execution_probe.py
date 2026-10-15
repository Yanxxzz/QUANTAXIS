"""No-order observability for the already frozen minute entry gates."""
from __future__ import annotations

import json


_BODY = '''
def _probe_number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _probe_day(context):
    value = str(context.trade_date)
    if len(value) == 8 and value.isdigit():
        return value[:4] + '-' + value[4:6] + '-' + value[6:]
    return value[:10]


def initialize(context):
    info = context.run_info
    if info.frequency != '1M' or type(info.matching_type) is not int or info.matching_type != 0:
        raise RuntimeError('LF_ZERO_GATES incompatible minute reference configuration')
    context._zero_probe = {'current': None}
    print('LF_ZERO_GATES_INITIALIZED ' + json.dumps({'window': FROZEN_PROBE_WINDOW,
          'no_orders': True, 'no_finance_queries': True, 'purpose': 'execution_reachability_only'}, sort_keys=True))


def before_trading(context):
    day = _probe_day(context)
    if day not in FROZEN_PROBE_ROSTER:
        raise RuntimeError('LF_ZERO_GATES day outside frozen diagnostic scope')
    selected = list(FROZEN_PROBE_ROSTER[day])
    context._zero_probe['current'] = {
        'date': day, 'selected': selected, 'minute': None, 'liquidity': {},
        'callbacks': 0, 'get_calls': 0, 'none_bars': 0, 'bar_objects': 0,
        'missing_attributes': 0, 'invalid_volume': 0, 'valid_close': 0,
        'nonpositive_or_missing_limits': 0, 'inconsistent_limits_or_range': 0,
        'valid_price_and_limits': 0, 'close_at_lower_limit': 0,
        'previous_volume_capacity_ge100': 0, 'entry_budget_ge100': 0,
        'all_entry_gates_pass': 0, 'samples': {},
    }


def handle_data(context, data):
    state = context._zero_probe['current']
    if state is None or state['date'] != _probe_day(context):
        raise RuntimeError('LF_ZERO_GATES before_trading must precede minute callback')
    minute = context.trade_time.strftime('%Y-%m-%d %H:%M')
    if minute != state['minute']:
        for record in state['liquidity'].values():
            if record.get('cached_minute') is not None and record.get('consumed_minute') != record['cached_minute']:
                record['completed_volume'] += record['cached_volume']
                record['consumed_minute'] = record['cached_minute']
    state['minute'] = minute
    state['callbacks'] += 1
    for symbol in state['selected']:
        state['get_calls'] += 1
        bar = data.get(symbol)
        if bar is None:
            state['none_bars'] += 1
            continue
        state['bar_objects'] += 1
        try:
            close, upper, lower, volume = [_probe_number(v) for v in
                (bar.close, bar.limit_up, bar.limit_down, bar.volume)]
        except AttributeError:
            state['missing_attributes'] += 1
            continue
        sample = {'symbol': symbol, 'minute': minute, 'close': close,
                  'limit_up': upper, 'limit_down': lower, 'volume': volume}
        if close is not None and close > 0:
            state['valid_close'] += 1
        if volume is None or volume < 0:
            state['invalid_volume'] += 1
            state['samples'].setdefault('invalid_volume', sample)
            continue
        record = state['liquidity'].setdefault(symbol, {'completed_volume': 0})
        if record.get('cached_minute') != minute:
            record.update(cached_minute=minute, cached_volume=volume)
        capacity = max(0, int(math.floor(.01 * record['completed_volume'] + 1e-9)))
        if capacity >= 100:
            state['previous_volume_capacity_ge100'] += 1
        state['samples'].setdefault('first_observed', sample)
        if (close is None or close <= 0 or upper is None or upper <= 0
                or lower is None or lower <= 0):
            state['nonpositive_or_missing_limits'] += 1
            if close is not None and close > 0 and (upper is None or upper <= 0 or lower is None or lower <= 0):
                state['samples'].setdefault('positive_close_missing_limits', sample)
            continue
        if lower > upper or not lower - 1e-9 <= close <= upper + 1e-9:
            state['inconsistent_limits_or_range'] += 1
            continue
        state['valid_price_and_limits'] += 1
        state['samples'].setdefault('valid_quote', sample)
        # The completed original account held only cash. These are the original
        # first-candidate intent gates, not fabricated orders, fills or returns.
        budget_quantity = max(0, int(20000.0 / (lower * 1.003) / 100) * 100)
        if budget_quantity >= 100:
            state['entry_budget_ge100'] += 1
        if close <= lower + 1e-9:
            state['close_at_lower_limit'] += 1
            state['samples'].setdefault('at_lower_limit', sample)
            if min(capacity // 100 * 100, budget_quantity) >= 100:
                state['all_entry_gates_pass'] += 1
                state['samples'].setdefault('first_would_submit', dict(sample,
                    previous_completed_volume=record['completed_volume'], capacity=capacity,
                    budget_quantity=budget_quantity))


def after_trading(context):
    state = context._zero_probe['current']
    output = {k: v for k, v in state.items() if k not in ('liquidity', 'minute')}
    output['no_orders'] = True
    print('LF_ZERO_GATES ' + json.dumps(output, ensure_ascii=False, sort_keys=True))
'''


def render_zero_gate_probe(roster: dict[str, list[str]]) -> str:
    if list(sorted(roster)) != ['2026-07-01', '2026-07-02']:
        raise ValueError('Only the two earliest frozen account sessions are permitted')
    if any(len(symbols) > 5 or symbols != sorted(set(symbols))
           or any(len(s) != 9 or s[-3:] not in ('.SH', '.SZ') or not s[:6].isdigit() for s in symbols)
           for symbols in roster.values()):
        raise ValueError('Keep at most the five original code-ordered candidates per day')
    return ('from panda_backtest.api.api import *\nimport json\nimport math\n'
            + 'FROZEN_PROBE_ROSTER = ' + repr(roster) + '\n'
            + "FROZEN_PROBE_WINDOW = {'start': '2026-07-01', 'end': '2026-07-02'}\n"
            + _BODY)
