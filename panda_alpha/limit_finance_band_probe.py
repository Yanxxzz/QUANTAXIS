"""Render one bounded native-band/matching diagnostic; never run a query here."""
from __future__ import annotations

ROSTER = {
    "2026-07-01": ["000566.SZ", "002407.SZ", "002628.SZ", "600113.SH", "600363.SH"],
    "2026-07-02": ["000566.SZ", "000603.SZ", "002053.SZ", "002273.SZ", "002396.SZ"],
}
WINDOW = {"start": "2026-07-01", "end": "2026-07-02"}

_BODY = r'''
from panda_backtest.api.api import *
import panda_data
import pandas as pd
import json
import math
import struct
import re
from datetime import datetime, date
from numbers import Real, Integral


def _error_message(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    text = text.split('Traceback (most recent call last)', 1)[0]
    text = re.sub(r'https?://[^\s,;]+', '<url omitted>', text)
    text = re.sub(r'(?i)bearer\s+[^\s,;]+', 'Bearer <redacted>', text)
    text = re.sub(r"(?i)([\"']?(?:token|authorization|api[_-]?key|password|secret|phone|(?:user|account|owner)[_-]?id)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
                  r'\1<redacted>', text)
    text = re.sub(r'\b(?:[A-Fa-f0-9]{20,}|\d{9,})\b', '<id omitted>', text)
    text = re.sub(r'(?i)\b(?:request|headers)\s*[:=].*', '<request omitted>', text)
    return {'type': type(exc).__name__, 'message': text[:240]}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _day(value):
    if isinstance(value, bool):
        raise ValueError('Boolean date')
    if isinstance(value, (datetime, pd.Timestamp)):
        if pd.isna(value) or value.tzinfo is not None or value.time() != datetime.min.time():
            raise ValueError('Non-date timestamp')
        return value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    fmt = '%Y%m%d' if len(text) == 8 and text.isdigit() else '%Y-%m-%d'
    return datetime.strptime(text, fmt).strftime('%Y-%m-%d')


def _symbol(value):
    if not isinstance(value, str):
        raise ValueError('Symbol must be a string')
    if len(value) == 9 and value[:6].isdigit() and value[6:] in ('.SZ', '.SH'):
        return value
    if len(value) == 6 and value.isdigit():
        if value.startswith(('00', '30')):
            return value + '.SZ'
        if value.startswith(('60', '68')):
            return value + '.SH'
    raise ValueError('Undocumented symbol format')


def _tick(value):
    raw = _number(value)
    if raw is None or raw <= 0:
        return None
    cents = int(round(raw * 100))
    canonical = cents / 100.0
    if raw == canonical:
        return cents
    try:
        alias = struct.unpack('>f', struct.pack('>f', canonical))[0]
    except (OverflowError, struct.error):
        return None
    return cents if raw == alias else None


def _provider_frame(frame, fields, symbols, day):
    result = {'status': 'schema_failed', 'rows': {}, 'reason': None,
              'missing': list(symbols), 'columns': []}
    if not isinstance(frame, pd.DataFrame):
        result['reason'] = 'not_dataframe'
        return result
    if frame.columns.has_duplicates:
        result['reason'] = 'duplicate_columns'
        return result
    missing_keys = {'symbol', 'date'} - set(frame.columns)
    if missing_keys:
        names = list(frame.index.names)
        if (not missing_keys.issubset(set(names)) or len(names) != len(set(names))
                or any(name in frame.columns for name in names)):
            result['reason'] = 'undeclared_identity_index'
            return result
        frame = frame.reset_index()
    result['columns'] = [str(c) for c in frame.columns[:20]]
    if not {'symbol', 'date', *fields}.issubset(frame.columns):
        result['reason'] = 'missing_requested_columns'
        return result
    if len(frame) > len(symbols):
        result['reason'] = 'extra_or_duplicate_rows'
        return result
    try:
        for row in frame.loc[:, ['symbol', 'date', *fields]].to_dict('records'):
            symbol, row_day = _symbol(row['symbol']), _day(row['date'])
            if symbol not in symbols or row_day != day:
                raise ValueError('Unexpected identity')
            if symbol in result['rows']:
                raise ValueError('Duplicate identity')
            values = {field: _number(row[field]) for field in fields}
            # Preserve unknown/zero values as pending; do not infer aliases/0s.
            result['rows'][symbol] = values
    except (ValueError, TypeError, OverflowError) as exc:
        result.update(rows={}, reason='invalid_or_ambiguous_identity')
        return result
    result['missing'] = [s for s in symbols if s not in result['rows']]
    result['status'] = 'partial' if result['missing'] else 'complete'
    result['reason'] = None
    return result


def _providers():
    all_days, summaries = {}, []
    for day, symbols in FROZEN_ROSTER.items():
        request_day = day.replace('-', '')
        observed = {}
        for provider in ('daily', 'factor'):
            try:
                if provider == 'daily':
                    fields = ['open', 'limit_up', 'limit_down']
                    frame = panda_data.get_stock_daily(
                        symbol=list(symbols), start_date=request_day, end_date=request_day,
                        fields=['symbol', 'date', *fields], indicator='', st=True)
                else:
                    fields = ['cal_limit_up', 'cal_limit_down']
                    frame = panda_data.get_factor(
                        symbol=list(symbols), start_date=request_day, end_date=request_day,
                        type='stock', factors=list(fields))
                observed[provider] = _provider_frame(frame, fields, symbols, day)
            except Exception as exc:
                observed[provider] = {'status': 'query_failed', 'rows': {},
                    'reason': type(exc).__name__, 'error': _error_message(exc),
                    'missing': list(symbols), 'columns': []}
        pairs = {}
        for symbol in symbols:
            daily = observed['daily']['rows'].get(symbol)
            factor = observed['factor']['rows'].get(symbol)
            pair = {'status': 'pending', 'reason': 'provider_identity_missing'}
            if daily is not None and factor is not None:
                upper, lower, opening = (_tick(daily['limit_up']),
                    _tick(daily['limit_down']), _tick(daily['open']))
                fu, fl = _tick(factor['cal_limit_up']), _tick(factor['cal_limit_down'])
                if None in (upper, lower, opening, fu, fl):
                    pair['reason'] = 'missing_nonpositive_or_off_tick_price'
                elif lower > upper or fl > fu or not lower <= opening <= upper:
                    pair['reason'] = 'invalid_band_range'
                elif (upper, lower) != (fu, fl):
                    pair['reason'] = 'providers_disagree'
                else:
                    pair = {'status': 'band_concordant', 'upper_tick': upper,
                            'lower_tick': lower, 'daily_open_tick': opening,
                            'daily': daily, 'factor': factor}
            pairs[symbol] = pair
        all_days[day] = pairs
        summaries.append({'date': day, 'providers': {
            key: {k: v for k, v in info.items() if k != 'rows'}
            for key, info in observed.items()}, 'pair_status': {
            key: {'status': value['status'], 'reason': value.get('reason')}
            for key, value in pairs.items()},
            'first_pair': dict(symbol=symbols[0], **pairs[symbols[0]])})
    return all_days, summaries


def _account(context):
    account = context.stock_account_dict.get(context.account)
    if account is None:
        raise RuntimeError('Documented native stock account unavailable')
    return account


def _fault(context, reason):
    state = context._band_probe
    if reason not in state['faults']:
        state['faults'].append(reason)


def initialize(context):
    info = context.run_info
    if (info.frequency != '1M' or type(info.matching_type) is not int or info.matching_type != 0
            or _day(info.start_date) != FROZEN_WINDOW['start']
            or _day(info.end_date) != FROZEN_WINDOW['end']
            or _number(info.stock_starting_cash) != 100000.0
            or _number(info.commission_multiplier) != 3.75 or _number(info.slippage) != 0.0):
        raise RuntimeError('Frozen band/matching settings differ')
    context.account = info.stock_account
    account = _account(context)
    if (_number(account.cash) != 100000.0 or _number(account.total_value) != 100000.0
            or any(p.quantity != 0 for _, p in account.positions.items())):
        raise RuntimeError('Diagnostic requires empty native100000 account')
    pairs, summaries = _providers()  # Exactly four declared calls, no retry.
    context._band_probe = {'pairs': pairs, 'current': None, 'attempt': None,
        'orders': {}, 'trade_ids': set(), 'trades': [], 'faults': [],
        'invalid_trade_samples': [], 'provider_summary': summaries}
    print('LF_BAND_INITIALIZED ' + json.dumps({'window': FROZEN_WINDOW,
        'provider_calls': 4, 'max_order_attempts': 1, 'no_economic_pass': True,
        'provider_summary': summaries}, sort_keys=True))


def before_trading(context):
    state = context._band_probe
    day = _day(context.trade_date)
    if day not in FROZEN_ROSTER:
        raise RuntimeError('Day outside frozen ten identities')
    if state['current'] is not None and state['current']['date'] == day:
        raise RuntimeError('Duplicate daily initialization')
    state['current'] = {'date': day, 'minute': None, 'liquidity': {}, 'last': {},
        'first_bar_checked': {}, 'open_alignment': {}, 'samples': [],
        'callbacks': 0, 'get_calls': 0, 'none_bars': 0, 'missing_attributes': 0,
        'bar_date_match': 0, 'bar_trade_date_match': 0, 'bar_time_match': 0,
        'bar_stamp_match': 0, 'valid_close': 0, 'minute_band_zero': 0,
        'close_changes': 0, 'volume_equal': 0, 'volume_increase': 0,
        'volume_decrease': 0, 'invalid_volume': 0,
        'provider_eligible': 0, 'close_at_provider_DL': 0,
        'previous_volume_capacity_ge100': 0, 'all_gates_pass': 0}


def _observe(context, symbol, bar, minute):
    state, root = context._band_probe['current'], context._band_probe
    if bar is None:
        state['none_bars'] += 1
        if context.trade_time.strftime('%H%M%S') == '093100':
            state['first_bar_checked'][symbol] = True
            state['open_alignment'][symbol] = 'missing_first_0931'
        return None
    try:
        raw = {'symbol': symbol, 'context_minute': minute,
               'date': bar.date, 'trade_date': bar.trade_date, 'time': bar.time,
               'open': _number(bar.open), 'close': _number(bar.close),
               'volume': _number(bar.volume), 'limit_up': _number(bar.limit_up),
               'limit_down': _number(bar.limit_down)}
    except AttributeError:
        state['missing_attributes'] += 1
        if context.trade_time.strftime('%H%M%S') == '093100':
            state['first_bar_checked'][symbol] = True
            state['open_alignment'][symbol] = 'first_0931_attributes_missing'
        return None
    if (symbol == FROZEN_ROSTER[state['date']][0] and len(state['samples']) < 3
            and not any(s['context_minute'] == minute for s in state['samples'])):
        sample = dict(raw)
        for key in ('date', 'trade_date', 'time'):
            sample[key + '_type'] = type(raw[key]).__name__
            sample[key] = str(raw[key])
        state['samples'].append(sample)
    expected_date = int(state['date'].replace('-', ''))
    expected_hms = int(context.trade_time.strftime('%H%M%S'))
    # Exact documented roles; no guess that integer trade_date/string time is an alias.
    date_ok = isinstance(raw['date'], Integral) and not isinstance(raw['date'], bool) and raw['date'] == expected_date
    trade_ok = isinstance(raw['trade_date'], str) and raw['trade_date'] == str(expected_date)
    time_ok = isinstance(raw['time'], Integral) and not isinstance(raw['time'], bool) and raw['time'] == expected_hms
    state['bar_date_match'] += int(date_ok)
    state['bar_trade_date_match'] += int(trade_ok)
    state['bar_time_match'] += int(time_ok)
    if raw['close'] is not None and raw['close'] > 0:
        state['valid_close'] += 1
    if raw['limit_up'] == 0 or raw['limit_down'] == 0:
        state['minute_band_zero'] += 1
    if raw['volume'] is None or raw['volume'] < 0:
        state['invalid_volume'] += 1
        return None
    if not (date_ok and trade_ok and time_ok):
        if expected_hms == 93100:
            state['first_bar_checked'][symbol] = True
            state['open_alignment'][symbol] = 'first_0931_stamp_invalid'
        return None
    state['bar_stamp_match'] += 1
    previous = state['last'].get(symbol)
    if previous is not None:
        if raw['close'] is not None and previous['close'] is not None:
            state['close_changes'] += int(raw['close'] != previous['close'])
        state['volume_equal'] += int(raw['volume'] == previous['volume'])
        state['volume_increase'] += int(raw['volume'] > previous['volume'])
        state['volume_decrease'] += int(raw['volume'] < previous['volume'])
    state['last'][symbol] = raw
    pair = root['pairs'][state['date']][symbol]
    if symbol not in state['first_bar_checked']:
        state['first_bar_checked'][symbol] = True
        if expected_hms != 93100:
            state['open_alignment'][symbol] = 'first_available_not_0931'
        elif pair['status'] != 'band_concordant':
            state['open_alignment'][symbol] = 'provider_pending'
        else:
            state['open_alignment'][symbol] = ('aligned' if _tick(raw['open']) == pair['daily_open_tick']
                                                else 'first_0931_open_disagrees')
    record = state['liquidity'].setdefault(symbol, {'completed_volume': 0.0})
    if record.get('cached_minute') != minute:
        record.update(cached_minute=minute, cached_volume=raw['volume'])
    capacity = int(math.floor(.01 * record['completed_volume'] + 1e-9))
    state['previous_volume_capacity_ge100'] += int(capacity >= 100)
    if pair['status'] != 'band_concordant' or state['open_alignment'].get(symbol) != 'aligned':
        return None
    upper, lower, price = pair['upper_tick'], pair['lower_tick'], _tick(raw['close'])
    if price is None or not lower <= price <= upper:
        return None
    for name, expected in [('limit_up', upper), ('limit_down', lower)]:
        if raw[name] not in (None, 0.0) and _tick(raw[name]) != expected:
            _fault(context, 'nonzero_minute_band_disagrees')
            return None
    state['provider_eligible'] += 1
    if price == lower:
        state['close_at_provider_DL'] += 1
        if capacity >= 100 and int(20000 / ((lower / 100) * 1.003) / 100) >= 1:
            state['all_gates_pass'] += 1
            return raw, pair
    return None


def _record_order(context, order):
    root = context._band_probe
    attempt = root['attempt']
    if attempt is None:
        _fault(context, 'order_without_attempt')
        return
    try:
        order_id = str(order.order_id)
        if (str(order.account) != str(context.account) or str(order.order_book_id) != attempt['symbol']
                or order.remark != attempt['remark'] or not order_id
                or order.quantity != 100 or order.filled_quantity < 0 or order.filled_quantity > 100
                or type(order.status) is not int or order.status not in (-1, 0, 1, 2, 3, 4, 5, 6, 7)):
            _fault(context, 'invalid_native_order_contract')
            return
        info = root['orders'].setdefault(order_id, {'statuses': [], 'terminal': False,
                                                   'quantity': order.quantity,
                                                   'reported_filled': 0})
        info['statuses'].append(order.status)
        info['terminal'] = info['terminal'] or order.status in (-1, 2, 3, 5, 7)
        info['reported_filled'] = max(info['reported_filled'], order.filled_quantity)
    except AttributeError:
        _fault(context, 'missing_native_order_field')


def on_stock_order_rtn(context, order):
    _record_order(context, order)


def stock_order_cancel(context, order):
    _record_order(context, order)


def on_stock_trade_rtn(context, trade):
    root, attempt = context._band_probe, context._band_probe['attempt']
    if attempt is None:
        _fault(context, 'trade_without_attempt')
        return
    try:
        identity = (str(trade.account_id), str(trade.trade_id))
        if identity in root['trade_ids']:
            return
        quantity, price, cost = _number(trade.volume), _number(trade.price), _number(trade.cost)
        if (identity[0] != str(context.account) or not identity[1] or not str(trade.order_id)
                or str(trade.contract_code) != attempt['symbol'] or trade.order_remark != attempt['remark']
                or _day(trade.trade_date) != attempt['date'] or type(trade.business) is not int
                or trade.business != 0 or quantity is None or quantity != int(quantity)
                or not 0 < quantity <= 100 or price is None or _tick(price) != attempt['lower_tick']
                or cost is None or cost < 0):
            if not root['invalid_trade_samples']:
                root['invalid_trade_samples'].append({'quantity': quantity, 'price': price,
                    'cost': cost, 'business': _number(trade.business),
                    'business_type': type(trade.business).__name__})
            _fault(context, 'invalid_or_zero_price_native_trade')
            return
        if sum(t['quantity'] for t in root['trades']) + quantity > 100:
            _fault(context, 'overfilled_native_trade')
            return
        root['trade_ids'].add(identity)
        root['trades'].append({'order_id': str(trade.order_id), 'trade_id': identity[1],
            'business': trade.business, 'quantity': int(quantity), 'price': price, 'cost': cost})
    except (AttributeError, ValueError, TypeError):
        _fault(context, 'missing_or_invalid_native_trade_field')


def handle_data(context, data):
    root, state = context._band_probe, context._band_probe['current']
    if state is None or state['date'] != _day(context.trade_date):
        raise RuntimeError('before_trading missing')
    minute = context.trade_time.strftime('%Y-%m-%d %H:%M')
    if state['minute'] is not None and minute < state['minute']:
        raise RuntimeError('Native minute moved backward')
    if minute != state['minute']:
        for item in state['liquidity'].values():
            if item.get('cached_minute') is not None and item.get('consumed_minute') != item['cached_minute']:
                item['completed_volume'] += item['cached_volume']
                item['consumed_minute'] = item['cached_minute']
    state['minute'] = minute
    state['callbacks'] += 1
    for symbol in FROZEN_ROSTER[state['date']]:
        state['get_calls'] += 1
        eligible = _observe(context, symbol, data.get(symbol), minute)
        if eligible is None or root['attempt'] is not None or root['faults']:
            continue
        raw, pair = eligible
        price = raw['close']  # Same legal cent, exact native close representation.
        cash = _number(_account(context).cash)
        if cash is None or cash < 100 * price + max(5.0, .003 * 100 * price):
            continue
        attempt = {'date': state['date'], 'symbol': symbol, 'quantity': 100,
                   'lower_tick': pair['lower_tick'], 'limit_price': price,
                   'minute': minute, 'remark': 'LF_BAND:' + state['date'] + ':' + symbol,
                   'returned': False, 'exception': None}
        root['attempt'] = attempt  # Before API/callback; never clear or retry.
        try:
            returned = order_shares(context.account, symbol, 100,
                style=LimitOrderStyle(price), remark=attempt['remark'])
            for order in returned or []:
                _record_order(context, order)
        except Exception as exc:
            attempt['exception'] = _error_message(exc)
        finally:
            attempt['returned'] = True


def after_trading(context):
    root, state, account = context._band_probe, context._band_probe['current'], _account(context)
    quantity = sum(t['quantity'] for t in root['trades'])
    expected_cash = 100000.0 - sum(t['quantity'] * t['price'] + t['cost'] for t in root['trades'])
    cash, fees, nav = _number(account.cash), _number(account.transaction_cost), _number(account.total_value)
    actual_positions = {str(s): p.quantity for s, p in account.positions.items() if p.quantity != 0}
    expected_positions = {root['attempt']['symbol']: quantity} if quantity else {}
    trade_quantities_by_order = {}
    for trade in root['trades']:
        order_id = trade['order_id']
        trade_quantities_by_order[order_id] = trade_quantities_by_order.get(order_id, 0) + trade['quantity']
    # Defer linkage until day end: actual trade callbacks may precede orders.
    order_trade_reconciled = (set(trade_quantities_by_order).issubset(root['orders'])
        and all(trade_quantities_by_order.get(order_id, 0) == info['reported_filled']
                and trade_quantities_by_order.get(order_id, 0) <= info['quantity']
                for order_id, info in root['orders'].items()))
    reconciled = (cash is not None and fees is not None and nav is not None
        and abs(cash - expected_cash) <= .01 and actual_positions == expected_positions
        and abs(fees - sum(t['cost'] for t in root['trades'])) <= .01
        and order_trade_reconciled)
    if root['faults']:
        result = 'EXPLICIT_CONTRACT_FAILURE'
    elif quantity and reconciled:
        result = 'MATCHING_VALIDATED_SINGLE_ATTEMPT_ONLY'
    elif quantity:
        result = 'FILL_OBSERVED_NATIVE_RECONCILIATION_PENDING'
    elif root['attempt'] is not None:
        result = 'MATCHING_ATTEMPTED_NOT_VALIDATED_NO_FILL'
    elif state['provider_eligible'] == 0:
        result = 'BAND_CLOCK_OR_FIRST_OPEN_PENDING_MATCHING_UNOBSERVED'
    elif state['close_at_provider_DL'] == 0:
        result = 'MATCHING_UNOBSERVED_NO_TOUCH'
    else:
        result = 'MATCHING_UNOBSERVED_EXECUTION_GATE_NOT_MET'
    output = {k: v for k, v in state.items() if k not in ('minute', 'liquidity', 'last', 'first_bar_checked')}
    output.update(result=result, attempt=root['attempt'], orders=root['orders'],
        actual_trades=root['trades'], invalid_trade_samples=root['invalid_trade_samples'],
        faults=root['faults'], native_cash=cash,
        native_NAV=nav, native_fees=fees, native_positions=actual_positions,
        native_reconciled=reconciled, economic_pass=False,
        order_trade_reconciled=order_trade_reconciled,
        trade_quantities_by_order=trade_quantities_by_order,
        volume_interval_and_units_verified=False, engine_daily_source_equivalence_verified=False,
        positive_quotes_do_not_certify_minute_granularity=True)
    print('LF_BAND_DAY ' + json.dumps(output, sort_keys=True))
'''


def render_band_matching_probe() -> str:
    """Return self-contained fixed diagnostic code; this never calls the SDK."""
    prefix = f"FROZEN_ROSTER = {ROSTER!r}\nFROZEN_WINDOW = {WINDOW!r}\n"
    return prefix + _BODY.lstrip()
