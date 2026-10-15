"""Render the explicitly accepted minute-IOC account migration, without running it.

No prices, source API calls, account operations or registrations occur here.
The worker uses native cash/positions/corporate actions. Historical entry
eligibility is a bound external roster, never recomputed from future bars.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

from .limit_finance_strategy_inputs import _date, _symbol, _examples


_BODY = r'''
_LF_TERMINAL = {2, 3, 5, 7, -1}
_LF_ACTIVE = {0, 1, 4, 6}


def _lf_number(value, name, positive=False):
    if isinstance(value, bool):
        raise RuntimeError('Invalid native ' + name)
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise RuntimeError('Missing native ' + name) from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise RuntimeError('Invalid native ' + name)
    return result


def _lf_quantity(value, name):
    result = _lf_number(value, name)
    if result != int(result):
        raise RuntimeError('Noninteger native ' + name)
    return int(result)


def _lf_price(value, name):
    value = _lf_number(value, name, positive=True)
    # A raw legal cent tick may have crossed a float32 transport. Only remove
    # at most half its float32 ULP; genuine off-tick/qfq prices remain unknown.
    half_ulp = math.ldexp(1.0, math.frexp(value)[1] - 25)
    if half_ulp >= .005:
        raise RuntimeError('Native price precision cannot distinguish legal cent ticks')
    nearest = math.floor(value * 100 + .5) / 100
    if nearest <= 0 or abs(value - nearest) > max(1e-12, half_ulp + 1e-12):
        raise RuntimeError('Native price is not a legal cent tick within float32 representation error')
    return nearest


def _lf_order_price(raw_reference, economic_limit):
    # raw_reference is the SAME snapshot already accepted by _lf_bar, never a
    # new quote. An economic limit is not an avg_price and is never rounded here.
    economic_limit = _lf_number(economic_limit, 'economic limit', positive=True)
    raw_reference = _lf_number(raw_reference, 'raw reference', positive=True)
    if _lf_price(raw_reference, 'raw reference') == economic_limit:
        return raw_reference
    return economic_limit  # In particular, never chase a TP quote one tick higher.


def _lf_count(context, name):
    counters = context._lf['daily_gates']
    counters[name] = counters.get(name, 0) + 1


def _lf_source_reject(context, symbol, reason, raw=None):
    if context is not None:
        state = context._lf
        state['source_contract_valid'] = False
        counts = state['source_rejections']
        counts[reason] = counts.get(reason, 0) + 1
        _lf_count(context, 'rejected_bars')
        if reason not in state['source_rejection_samples']:
            sample = {'date': state['day'], 'minute': state['minute'], 'symbol': symbol,
                      'reason': reason, 'raw_repr': str(raw), 'no_price_or_band_imputation': True}
            state['source_rejection_samples'][reason] = sample
            print('LF_SOURCE_REJECT ' + json.dumps(sample, ensure_ascii=False, sort_keys=True))
    return None


def _lf_account(context):
    account = context.stock_account_dict.get(context.account)
    if account is None:
        raise RuntimeError('Named native stock account does not exist')
    return account


def _lf_positions(context):
    result = {}
    for symbol, position in _lf_account(context).positions.items():
        if _lf_quantity(position.quantity, 'position.quantity') > 0:
            if symbol not in FROZEN_SYMBOLS:
                raise RuntimeError('Unmanaged native holding: ' + str(symbol))
            result[symbol] = position
    if len(result) > 5:
        raise RuntimeError('Native account exceeded frozen five-position scope')
    return result


def _lf_minute(context):
    if not isinstance(context.trade_time, datetime):
        raise RuntimeError('Native trade_time must be a datetime')
    return context.trade_time.strftime('%Y-%m-%d %H:%M')


def _lf_day(context):
    return _date(context.trade_date)


def initialize(context):
    info = context.run_info
    observed_settings = json.dumps({
        'frequency': str(info.frequency), 'frequency_type': str(type(info.frequency)),
        'matching_type': str(info.matching_type), 'matching_value_type': str(type(info.matching_type)),
        'stock_starting_cash': str(info.stock_starting_cash),
        'commission_multiplier': str(info.commission_multiplier), 'slippage': str(info.slippage),
        'start_date': str(info.start_date), 'end_date': str(info.end_date),
    }, ensure_ascii=False, sort_keys=True)
    if info.frequency != '1M' or type(info.matching_type) is not int or info.matching_type != FROZEN_MATCHING_TYPE:
        raise RuntimeError('Frozen migration requires frequency1M and matching_type' + str(FROZEN_MATCHING_TYPE) + '; no orders permitted; '
                           'observed_settings=' + observed_settings)
    checks = ((info.stock_starting_cash, 100000.0, 'stock_starting_cash'),
              (info.commission_multiplier, FROZEN_COMMISSION_MULTIPLIER, 'commission_multiplier'),
              (info.slippage, FROZEN_SLIPPAGE, 'slippage'))
    for observed, required, name in checks:
        try:
            actual = _lf_number(observed, name)
        except RuntimeError as exc:
            raise RuntimeError(str(exc) + '; observed_settings=' + observed_settings) from exc
        if not math.isclose(actual, required, rel_tol=0, abs_tol=1e-9):
            raise RuntimeError('Native setting differs from frozen ' + name + '; observed_settings=' + observed_settings)
    if (_date(info.start_date) != FROZEN_WINDOW['start']
            or _date(info.end_date) != FROZEN_WINDOW['end']):
        raise RuntimeError('Native dates differ from frozen strategy window; observed_settings=' + observed_settings)
    context.account = info.stock_account
    account = _lf_account(context)
    if list(account.positions.keys()) or not math.isclose(
            _lf_number(account.cash, 'cash'), 100000.0, rel_tol=0, abs_tol=1e-7):
        raise RuntimeError('A fresh empty native100000CNY account is required')
    context._lf = {'ready': False, 'day': None, 'minute': None, 'position_state': {},
                   'orders': {}, 'tokens': [], 'by_remark': {}, 'submitting': {},
                   'active': {}, 'terminal_ids': set(), 'trade_ids': set(),
                   'liquidity': {}, 'traded_today': {}, 'last_submit': {},
                   'daily_buy_debits': 0.0, 'daily_trade_cost': 0.0,
                   'daily_trade_count': 0, 'order_sequence': 0}
    context._lf_inputs = load_inputs(panda_data, list(FROZEN_SYMBOLS),
        FROZEN_WINDOW['start'], FROZEN_WINDOW['end'], FROZEN_VALIDATION_EXAMPLES,
        reference_calendar=FROZEN_REFERENCE_CALENDAR)
    if context._lf_inputs.validation_count != 3:
        raise RuntimeError('Worker financing repayment examples did not pass; no orders permitted')
    context._lf_inputs.validate_run_calendar(context.trade_date_list,
        FROZEN_WINDOW['start'], FROZEN_WINDOW['end'])
    context._lf['ready'] = True
    print('LF_INITIALIZED ' + json.dumps(MIGRATION_METADATA, ensure_ascii=False, sort_keys=True))


def _lf_state(context, symbol, native_position):
    state = context._lf['position_state'].get(symbol)
    if state is None:
        raise RuntimeError('Native holding lacks an actual first-buy trade: ' + symbol)
    # Read cost and corporate-action quantities anew; never rewrite native assets.
    state['basis'] = _lf_number(native_position.avg_price, 'position.avg_price', positive=True)
    return state


def _lf_features(context, symbol):
    cache = context._lf['features']
    if symbol not in cache:
        cache[symbol] = context._lf_inputs.features_for(symbol, context._lf['session_index'])
    return cache[symbol]


def _lf_exit(context, symbol, position, reference_price=None):
    state = _lf_state(context, symbol, position)
    index = context._lf['session_index']
    prior_day = FROZEN_REFERENCE_CALENDAR[index - 1] if index > 0 else None
    previous = context._lf_inputs.daily_close.get((symbol, prior_day))
    features = _lf_features(context, symbol)
    return evaluate_exit(FROZEN_MODE, session_index=index, position=state,
        previous_close=previous, reference_price=reference_price,
        net_flow=features.get('net_flow'), turnover_relative=features.get('turnover_relative'), tick=.01)


def before_trading(context):
    state = context._lf
    if not state.get('ready'):
        raise RuntimeError('Worker input validation must complete before trading')
    if state['submitting'] or any(state['active'].values()):
        raise RuntimeError('Unexpected native outstanding IOC across sessions; reconcile before continuation')
    day = _lf_day(context)
    if state['day'] == day:
        raise RuntimeError('Duplicate before_trading would change frozen pre-open reservations')
    if day not in FROZEN_REFERENCE_CALENDAR or not FROZEN_WINDOW['start'] <= day <= FROZEN_WINDOW['end']:
        raise RuntimeError('Native day outside frozen reference window')
    account, positions = _lf_account(context), _lf_positions(context)
    for symbol in list(state['position_state']):
        if symbol not in positions:
            if state['position_state'][symbol].get('last_actual_sell_trade') is None:
                raise RuntimeError('Holding disappeared without an actual sell trade')
            del state['position_state'][symbol]
    nav, cash = (_lf_number(account.total_value, 'total_value', positive=True),
                 _lf_number(account.cash, 'cash'))
    state.update(day=day, minute=None, session_index=FROZEN_REFERENCE_CALENDAR.index(day),
                 preopen_nav=nav, preopen_cash=cash, preopen_position_count=len(positions),
                 features={}, liquidity={}, traded_today={}, last_submit={},
                 daily_buy_debits=0.0, daily_trade_cost=0.0, daily_trade_count=0)
    state.update(daily_gates={}, source_contract_valid=True,
                 source_rejections={}, source_rejection_samples={})
    # Freeze candidate identities and budgets before any same-day exit. A failed
    # earlier candidate never hands its reserved slot/cash to a later stock.
    candidates = [s for s in FROZEN_ENTRY_ROSTER.get(day, []) if s not in positions]
    selected = candidates[:max(0, 5 - len(positions))]
    reservations, available = {}, cash
    for symbol in selected:
        budget = min(.20 * nav, available)
        available -= budget
        reservations[symbol] = {'budget': budget, 'target_quantity': None,
                                'limit_down': None, 'actual_bought': 0,
                                'actual_buy_debits': 0.0}
    state['reservations'] = reservations
    for symbol, position in sorted(positions.items()):
        _lf_exit(context, symbol, position, reference_price=None)


def _lf_order_terminal(context, order):
    state = context._lf
    if str(order.account) != str(context.account):
        return
    symbol, order_id = str(order.order_book_id), str(order.order_id)
    if not order_id:
        raise RuntimeError('Native order callback lacks order_id')
    known = state['orders'].get(order_id)
    token = known['token'] if known else state['submitting'].get(symbol)
    if token is None:
        token = state['by_remark'].get(order.remark)
    if token is None:
        return  # Another account/strategy's order is not ours to reinterpret.
    _lf_count(context, 'owned_order_callback_events')
    if symbol != token['symbol']:
        raise RuntimeError('Order callback symbol differs from submission')
    status = order.status
    if type(status) is not int or status not in _LF_TERMINAL | _LF_ACTIVE:
        raise RuntimeError('Unrecognized native IOC status: ' + str(status))
    quantity = _lf_quantity(order.quantity, 'order.quantity')
    filled = _lf_quantity(order.filled_quantity, 'order.filled_quantity')
    if filled > quantity:
        raise RuntimeError('Order filled quantity exceeds submitted quantity')
    if status == 2:
        filled = quantity
    if known is None:
        known = {'token': token, 'quantity': quantity, 'reported_filled': filled,
                 'actual_traded': 0, 'terminal': False}
        state['orders'][order_id] = known
    else:
        known['quantity'] = quantity
        known['reported_filled'] = max(known['reported_filled'], filled)
    token['order_ids'].add(order_id)
    # Terminal callbacks may arrive before order_shares returns its list. A
    # later stale ACTIVE object must never resurrect that completed order.
    if order_id in state['terminal_ids']:
        return
    if status in _LF_TERMINAL:
        known['terminal'] = True
        state['terminal_ids'].add(order_id)
        state['active'].setdefault(symbol, set()).discard(order_id)
    else:
        state['active'].setdefault(symbol, set()).add(order_id)


def on_stock_order_rtn(context, order):
    _lf_order_terminal(context, order)


def stock_order_cancel(context, order):
    _lf_order_terminal(context, order)


def _lf_pending_buy_estimate(context, symbol=None):
    total = 0.0
    state = context._lf
    for token in state['tokens']:
        if token['side'] != 0 or (symbol is not None and token['symbol'] != symbol):
            continue
        records = [state['orders'][i] for i in token['order_ids']]
        if token['returning'] or any(not row['terminal'] for row in records):
            outstanding = max(0, token['quantity'] - token['actual_traded'])
        else:
            outstanding = sum(max(0, row['reported_filled'] - row['actual_traded']) for row in records)
        total += outstanding * token['price'] * 1.003
    return total


def _lf_submit(context, symbol, quantity, price, reason, economic_price=None):
    state = context._lf
    _lf_count(context, 'submit_attempts')
    if (symbol in state['submitting'] or state['active'].get(symbol)
            or state['last_submit'].get(symbol) == state['minute']):
        _lf_count(context, 'submit_blocked_reentrant_active_or_same_minute')
        return
    economic_price = price if economic_price is None else economic_price
    if _lf_price(price, 'IOC limit') != economic_price:
        raise RuntimeError('IOC limit representation differs from its original legal economic tick')
    if price != economic_price:
        _lf_count(context, 'same_tick_native_limit_representations')
    state['order_sequence'] += 1
    remark = 'LF:' + state['day'] + ':' + str(state['order_sequence']) + ':' + reason
    token = {'symbol': symbol, 'side': 0 if quantity > 0 else 1,
             'quantity': abs(quantity), 'price': price, 'economic_price': economic_price, 'actual_traded': 0,
             'order_ids': set(), 'returning': True, 'remark': remark, 'day': state['day']}
    state['tokens'].append(token)
    state['by_remark'][remark] = token
    state['submitting'][symbol] = token
    state['last_submit'][symbol] = state['minute']
    try:
        _lf_count(context, 'order_api_calls')
        orders = order_shares(context.account, symbol, int(quantity),
                              style=LimitOrderStyle(float(price)), remark=remark)
        returned = 0
        for order in orders or []:
            returned += 1
            _lf_count(context, 'returned_order_objects')
            _lf_order_terminal(context, order)
        if returned == 0:
            _lf_count(context, 'empty_order_api_returns')
    except Exception:
        _lf_count(context, 'order_api_exceptions')
        raise
    finally:
        token['returning'] = False
        state['submitting'].pop(symbol, None)


def _lf_trade_quantity(trade):
    return _lf_quantity(trade.volume, 'trade.volume')


def on_stock_trade_rtn(context, trade):
    state = context._lf
    if str(trade.account_id) != str(context.account):
        return
    trade_id, order_id = str(trade.trade_id), str(trade.order_id)
    if not trade_id or not order_id:
        raise RuntimeError('Actual native trade requires trade_id and order_id')
    identity = str(trade.account_id), trade_id
    if identity in state['trade_ids']:
        return
    symbol = str(trade.contract_code)
    known = state['orders'].get(order_id)
    token = known['token'] if known else state['submitting'].get(symbol)
    if token is None:
        token = state['by_remark'].get(trade.order_remark)
    if token is None:
        return
    if symbol != token['symbol'] or _date(trade.trade_date) != state['day']:
        raise RuntimeError('Actual trade outside its frozen submission symbol/day')
    side = trade.business
    if type(side) is not int or side not in (0, 1) or side != token['side']:
        raise RuntimeError('Actual native trade has an incompatible side')
    quantity = _lf_trade_quantity(trade)
    if quantity <= 0:
        raise RuntimeError('Actual native trade quantity must be positive')
    price, cost = (_lf_number(trade.price, 'trade.price', positive=True),
                   _lf_number(trade.cost, 'trade.cost'))
    if known is None:
        known = {'token': token, 'quantity': token['quantity'], 'reported_filled': 0,
                 'actual_traded': 0, 'terminal': False}
        state['orders'][order_id] = known
        token['order_ids'].add(order_id)
        state['active'].setdefault(symbol, set()).add(order_id)
    known['actual_traded'] += quantity
    token['actual_traded'] += quantity
    if token['actual_traded'] > token['quantity']:
        raise RuntimeError('Actual trades exceed the submitted request')
    state['trade_ids'].add(identity)
    state['traded_today'][symbol] = state['traded_today'].get(symbol, 0) + quantity
    state['daily_trade_cost'] += cost
    state['daily_trade_count'] += 1
    _lf_count(context, 'unique_actual_trade_callbacks')
    if side == 0:
        reservation = state['reservations'].get(symbol)
        if reservation is None:
            raise RuntimeError('Actual buy has no frozen pre-open reservation')
        debit = price * quantity + cost
        reservation['actual_bought'] += quantity
        reservation['actual_buy_debits'] += debit
        state['daily_buy_debits'] += debit
        state['position_state'].setdefault(symbol, {
            'entry_index': state['session_index'], 'entry_date': state['day'], 'flags': {}})
    else:
        position = state['position_state'].get(symbol)
        if position is None:
            raise RuntimeError('Actual sell lacks a known first-buy trade')
        position['last_actual_sell_trade'] = trade_id
        native = _lf_account(context).positions.get(symbol)
        if native is None or _lf_quantity(native.quantity, 'position.quantity') == 0:
            del state['position_state'][symbol]


def _lf_bar(data, symbol, context=None):
    # Documented data.get accepts ONE argument, never dict-style default.
    bar = data.get(symbol)
    if bar is None:
        return _lf_source_reject(context, symbol, 'missing_bar')
    try:
        reference = bar.open if FROZEN_MATCHING_TYPE == 1 else bar.close
        values = (reference, bar.limit_up, bar.limit_down)
    except AttributeError as exc:
        _lf_source_reject(context, symbol, 'missing_price_or_band_attribute')
        raise RuntimeError('Native Bar lacks documented ' + FROZEN_REFERENCE_PHASE + '/limit_up/limit_down fields') from exc
    try:
        reference, upper, lower = [_lf_number(v, 'bar price/limits', positive=True) for v in values]
    except RuntimeError:
        return _lf_source_reject(context, symbol, 'nonpositive_or_missing_price_or_band', values)
    raw_reference = reference
    try:
        reference, upper, lower = [_lf_price(v, 'bar price/limits') for v in (reference, upper, lower)]
    except RuntimeError:
        return _lf_source_reject(context, symbol, 'off_tick_or_ambiguous_price_precision', values)
    if lower > upper or not lower - 1e-9 <= reference <= upper + 1e-9:
        return _lf_source_reject(context, symbol, 'inconsistent_price_or_band_range', values)
    if context is not None:
        _lf_count(context, 'valid_price_and_band_bars')
    return bar, reference, upper, lower, raw_reference


def _lf_capacity(context, symbol):
    state = context._lf
    completed = state['liquidity'].get(symbol, {}).get('completed_volume', 0)
    return max(0, int(math.floor(.01 * completed + 1e-9)) - state['traded_today'].get(symbol, 0))


def handle_data(context, data):
    state = context._lf
    if not state.get('ready') or state['day'] != _lf_day(context):
        raise RuntimeError('Validated before_trading must precede every trading day')
    _lf_count(context, 'minute_callback_events')
    minute = _lf_minute(context)
    if state['minute'] is not None and minute < state['minute']:
        raise RuntimeError('Native minute clock moved backward')
    new_minute = minute != state['minute']
    state['minute'] = minute
    if new_minute:
        for record in state['liquidity'].values():
            if record.get('cached_minute') is not None and record.get('consumed_minute') != record['cached_minute']:
                record['completed_volume'] += record['cached_volume']
                record['consumed_minute'] = record['cached_minute']
    positions = _lf_positions(context)
    symbols = sorted(set(positions) | set(state['reservations']))
    observed = {}
    for symbol in symbols:
        _lf_count(context, 'observed_symbol_events')
        bar = data.get(symbol)
        if bar is not None:
            try:
                volume = _lf_number(bar.volume, 'bar.volume')
            except (RuntimeError, AttributeError):
                _lf_source_reject(context, symbol, 'invalid_or_missing_volume')
                raise
            record = state['liquidity'].setdefault(symbol, {'completed_volume': 0})
            if record.get('cached_minute') != minute:
                record.update(cached_minute=minute, cached_volume=volume)
        observed[symbol] = _lf_bar(data, symbol, context)
    # Current bar volume is cached, never part of this minute's sizing budget.
    # Price decisions use ONLY the explicitly frozen open/close phase that
    # matches the native engine. This does not add current-bar volume to sizing.
    for symbol in sorted(positions):
        available = observed[symbol]
        if available is None:
            continue
        _, reference, upper, lower, raw_reference = available
        position = _lf_account(context).positions.get(symbol)
        if position is None or _lf_quantity(position.quantity, 'position.quantity') == 0:
            continue
        intent = _lf_exit(context, symbol, position, reference_price=reference)
        _lf_count(context, 'held_position_evaluations')
        capacity = _lf_capacity(context, symbol)
        sellable = _lf_quantity(position.sellable, 'position.sellable')
        if intent and capacity > 0 and sellable > 0 and reference > lower + 1e-9:
            quantity = sellable if capacity >= sellable else capacity // 100 * 100
            if quantity <= 0:
                continue
            economic_limit = intent['trigger_price'] if intent['reason'] == 'take_profit' else reference
            limit = _lf_order_price(raw_reference, economic_limit)
            _lf_submit(context, symbol, -quantity, limit, intent['reason'], economic_price=economic_limit)
        elif intent:
            _lf_count(context, 'sell_intent_blocked_capacity_t1_or_down_limit')
    for symbol, reservation in state['reservations'].items():
        available = observed[symbol]
        if available is None:
            continue
        _, reference, upper, lower, raw_reference = available
        _lf_count(context, 'entry_valid_quote_evaluations')
        if reservation['limit_down'] is None:
            reservation['limit_down'] = lower
            reservation['target_quantity'] = int(reservation['budget'] / (lower * 1.003) / 100) * 100
        elif not math.isclose(reservation['limit_down'], lower, rel_tol=0, abs_tol=1e-9):
            raise RuntimeError('Native lower limit changed within the frozen trading day')
        remaining = reservation['target_quantity'] - reservation['actual_bought']
        if remaining < 100 or reference > lower + 1e-9:
            _lf_count(context, 'entry_target_complete' if remaining < 100 else 'entry_reference_above_down_limit')
            continue
        _lf_count(context, 'entry_at_down_limit')
        native_positions = _lf_positions(context)
        if symbol not in native_positions and len(native_positions) >= 5:
            _lf_count(context, 'entry_five_slots_full')
            continue
        cash = min(reservation['budget'] - reservation['actual_buy_debits'] - _lf_pending_buy_estimate(context, symbol),
                   state['preopen_cash'] - state['daily_buy_debits'] - _lf_pending_buy_estimate(context),
                   _lf_number(_lf_account(context).cash, 'cash'))
        limit = _lf_order_price(raw_reference, lower)
        # A downward transport residual must not increase the frozen quantity;
        # upward residuals still need to fit the actual native cash reservation.
        budget_price = max(lower, limit)
        budget_quantity = max(0, int(max(0, cash) / (budget_price * 1.003) / 100) * 100)
        capacity_quantity = _lf_capacity(context, symbol) // 100 * 100
        if budget_quantity < 100:
            _lf_count(context, 'entry_cash_below_one_lot')
        if capacity_quantity < 100:
            _lf_count(context, 'entry_prior_volume_capacity_below_one_lot')
        quantity = min(remaining, budget_quantity, capacity_quantity)
        if quantity >= 100:
            _lf_count(context, 'entry_all_intent_gates_pass')
            _lf_submit(context, symbol, quantity, limit, 'frozen_entry', economic_price=lower)


def after_trading(context):
    account, state = _lf_account(context), context._lf
    positions = _lf_positions(context)
    holdings = [{'symbol': symbol, 'quantity': _lf_quantity(p.quantity, 'position.quantity'),
                 'sellable': _lf_quantity(p.sellable, 'position.sellable'),
                 'avg_price': _lf_number(p.avg_price, 'position.avg_price', positive=True),
                 'entry_date': state['position_state'].get(symbol, {}).get('entry_date')}
                for symbol, p in sorted(positions.items())]
    print('LF_DAILY ' + json.dumps({'date': state['day'], 'mode': FROZEN_MODE,
        'matching_type': FROZEN_MATCHING_TYPE, 'reference_phase': FROZEN_REFERENCE_PHASE,
        'native_NAV': _lf_number(account.total_value, 'total_value', positive=True),
        'native_cash': _lf_number(account.cash, 'cash'),
        'native_cumulative_fees': _lf_number(account.transaction_cost, 'transaction_cost'),
        'actual_trade_callback_cost_today': state['daily_trade_cost'],
        'actual_trade_callbacks_today': state['daily_trade_count'],
        'source_contract_valid': state['source_contract_valid'],
        'source_rejection_counts': state['source_rejections'],
        'source_rejection_first_examples': state['source_rejection_samples'],
        'gate_and_order_counts': state['daily_gates'],
        'frozen_preopen_NAV': state['preopen_nav'], 'frozen_preopen_cash': state['preopen_cash'],
        'holdings': holdings}, ensure_ascii=False, sort_keys=True))
'''


def _inline(path):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    excluded = {i for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "__future__"
                for i in range(node.lineno, node.end_lineno + 1)}
    return "\n".join(line for i, line in enumerate(text.splitlines(), 1) if i not in excluded) + "\n"


def render_official_strategy(mode, entry_roster, reference_calendar, validation_examples,
                             window, commission_multiplier=3.75, slippage=0, matching_type=1):
    """Return one self-contained worker script; never load actual data or accounts."""
    if mode not in {"control", "late_confirmed"}:
        raise ValueError("Unknown frozen strategy mode")
    if type(matching_type) is not int or matching_type not in (0, 1):
        raise ValueError("Explicit matching_type integer0 close or integer1 open required")
    if (isinstance(commission_multiplier, bool) or not isinstance(commission_multiplier, (int, float))
            or not math.isfinite(commission_multiplier) or commission_multiplier != 3.75
            or isinstance(slippage, bool) or not isinstance(slippage, (int, float)) or slippage != 0):
        raise ValueError("This frozen migration requires commission multiplier3.75 and slippage0")
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ValueError("Exact start/end window required")
    window = {k: _date(window[k]) for k in ("start", "end")}
    calendar = [_date(d) for d in reference_calendar]
    if (calendar != sorted(set(calendar)) or window["start"] not in calendar
            or window["end"] not in calendar or window["start"] > window["end"]):
        raise ValueError("Frozen ordered reference calendar must cover the full window")
    roster, identities = {}, set()
    for row in entry_roster:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("Entry roster rows must be exact (date,code) pairs")
        day, symbol = _date(row[0]), _symbol(row[1])
        if not window["start"] <= day <= window["end"] or day not in calendar or (day, symbol) in identities:
            raise ValueError("Duplicate or out-of-window entry identity")
        identities.add((day, symbol))
        roster.setdefault(day, []).append(symbol)
    if not identities:
        raise ValueError("Nonempty frozen original entry roster required")
    roster = {day: sorted(symbols) for day, symbols in sorted(roster.items())}
    symbols = sorted({symbol for _, symbol in identities})
    expected = _examples(validation_examples)
    examples = [{"SCODE": symbol, "DATE": day, "RZYE": values[0], "RZMRE": values[1], "RZCHE": values[2]}
                for (symbol, day), values in sorted(expected.items())]
    folder = Path(__file__).resolve().parent
    modules = [folder / "limit_finance_strategy_rules.py", folder / "limit_finance_strategy_inputs.py"]
    bindings = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in modules}
    metadata = {
        "schema": "native-minute-IOC-account-migration-v1", "mode": mode,
        "migration_not_original_daily_potential_fill_reproduction": True,
        "external_entry_roster": True, "entry_identities": len(identities), "symbols": len(symbols),
        "position_basis": "native avg_price and native corporate actions; not old equivalent units",
        "basis_equivalence_or_PIT_certified": False,
        "capacity": "At most1%previous-completed-observed-bar volume less actual same-day buy+sell shares",
        "first_minute_no_orders": True,
        "no_current_bar_volume_for_sizing": True, "no_daily_future_volume": True,
        "matching_type": matching_type,
        "reference_phase": "open" if matching_type == 1 else "close",
        "minute_open_reference_IOC_not_touch_or_queue_proof": matching_type == 1,
        "minute_reference_IOC_not_touch_or_queue_proof": True,
        "close_phase_is_actual_decision_clock_change_not_dtype_alias": matching_type == 0,
        "same_explicit_reference_phase_for_both_comparison_roles": True,
        "no_same_day_sale_cash_or_slot_reuse": True, "native_assets_never_assigned": True,
        "native_financing_role_requires_three_examples_before_orders": True,
        "source_rejections_and_daily_gate_order_counts": True,
        "legal_cent_tick_transport_normalization": "At most half float32 ULP plus1e-12; strictly below half tick; true off-tick rejected",
        "backend_raw_price_comparison_equivalence_certified": False,
        "same_tick_raw_reference_limit_mapping": "Only canonical(raw snapshot)==original economic cent limit; never chase another tick",
        "native_weighted_average_cost_never_tick_normalized": True,
        "IOC_representation_cash_guard": "max(original economic limit,actual same-tick submitted representation); no quantity increase from residual",
        "raw_reference_limit_acceptance_by_current_service_certified": False,
        "minute_volume_delivery_unit_certified": False,
        "same_frozen_roster_source_contract_and_engine_settings_for_both_modes": True,
        "rule_input_module_sha256": bindings,
    }
    header = ("from panda_backtest.api.api import *\nimport panda_data\nimport json\n\n"
              + "\n".join(_inline(p) for p in modules))
    constants = {"FROZEN_MODE": mode, "FROZEN_WINDOW": window,
                 "FROZEN_MATCHING_TYPE": matching_type,
                 "FROZEN_REFERENCE_PHASE": "open" if matching_type == 1 else "close",
                 "FROZEN_REFERENCE_CALENDAR": calendar, "FROZEN_ENTRY_ROSTER": roster,
                 "FROZEN_SYMBOLS": symbols, "FROZEN_VALIDATION_EXAMPLES": examples,
                 "FROZEN_COMMISSION_MULTIPLIER": float(commission_multiplier),
                 "FROZEN_SLIPPAGE": float(slippage), "MIGRATION_METADATA": metadata}
    code = header + "\n" + "\n".join(k + " = " + repr(v) for k, v in constants.items()) + "\n" + _BODY
    ast.parse(code)
    return code
