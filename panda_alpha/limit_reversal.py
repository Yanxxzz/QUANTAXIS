"""Two consecutive limit-up closes followed by a pre-open lower-limit bid.

This is a daily-bar *potential execution* model. A low touching the posted bid
does not prove queue priority, intraday sequence or executable size. Supplied
price limits must be raw, same-session and known before the opening auction;
this module neither derives exchange rules nor fetches or repairs prices.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
import math

import numpy as np
import pandas as pd

from .evaluation import performance


class LimitStrategyInputError(ValueError):
    """The frozen calendar or normalized raw-bar contract is invalid."""


class _SourcePending(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    name: str
    take_profit: float = .10
    stop_loss: float | None = None
    max_holding_sessions: int | None = None


BASELINE = Policy("baseline")
RISK_GUARD = Policy("risk_guard", stop_loss=.08, max_holding_sessions=10)


def _number(value, *, positive=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _day(value):
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise LimitStrategyInputError("Invalid reference date") from error
    if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
        raise LimitStrategyInputError("Dates must be timezone-free reference days")
    return day.strftime("%Y-%m-%d")


def _status(row):
    if row is None:
        return "unknown"
    status = row.get("trade_status")
    status = str(status).lower()
    source = str(row.get("source_status", "")).lower()
    if status in {"delisted", "terminated_listing"} or source in {
            "delisted", "source_explicit_delisted", "terminated_listing"}:
        return "delisted"
    if status in {"suspended", "suspension", "source_explicit_suspension"} or (
            status in {"0", "0.0"} and (
                source == "source_explicit_suspension" or row.get("suspension_confirmed") is True)):
        return "suspended"
    if status in {"1", "1.0", "trading", "tradable", "normal"}:
        return "trading"
    if status in {"0", "0.0", "no_trade"}:
        return "no_trade"
    return "unknown"


def _raw(row, field):
    return _number(row.get("raw_" + field, row.get(field)), positive=True)


def _limits(row):
    if row is None:
        return None
    up, down = (_number(row.get(name), positive=True) for name in ("up_limit", "down_limit"))
    return (up, down) if up is not None and down is not None and up >= down else None


def _ohlc(row):
    if row is None:
        return None
    values = {name: _raw(row, name) for name in ("open", "high", "low", "close")}
    if any(value is None for value in values.values()):
        return None
    if not (values["low"] <= min(values["open"], values["close"]) <=
            max(values["open"], values["close"]) <= values["high"]):
        return None
    limits = _limits(row)
    if limits and (values["low"] < limits[1] - 1e-7 or values["high"] > limits[0] + 1e-7):
        return None
    return values


def _volume(row):
    # Normalization contract: shares traded. No participation/capacity inference.
    return _number(row.get("volume_shares", row.get("volume"))) if row is not None else None


def _at(left, right):
    return abs(left - right) <= 1e-7


def _up_close(row):
    values, limits = _ohlc(row), _limits(row)
    volume = _volume(row)
    return bool(_status(row) == "trading" and values and limits and volume is not None
                and volume > 0 and _at(values["close"], limits[0]))


def _strict_profit_tick(entry, take_profit, tick):
    # Exact decimal comparison: an 11.00 price is not > 10% on a 10.00 entry.
    threshold = Decimal(str(entry)) * (Decimal("1") + Decimal(str(take_profit)))
    quantum = Decimal(str(tick))
    return float((threshold / quantum).to_integral_value(rounding=ROUND_FLOOR) * quantum + quantum)


class _RowBook:
    """Keep column arrays and an index, not millions of Python quote dicts."""
    def __init__(self, frame):
        self.index = pd.MultiIndex.from_frame(frame[["date", "symbol"]])
        self.columns = {name: frame[name].to_numpy() for name in frame}
        self.up_by_date = {}
        close_name = "raw_close" if "raw_close" in frame else "close"
        close = pd.to_numeric(frame[close_name], errors="coerce")
        upper = pd.to_numeric(frame["up_limit"], errors="coerce")
        possible = frame.loc[(close - upper).abs().le(1e-7), ["date", "symbol"]]
        for day, symbol in possible.itertuples(index=False, name=None):
            if _up_close(self.get((day, symbol))):
                self.up_by_date.setdefault(day, set()).add(symbol)

    def get(self, key, default=None):
        try:
            index = self.index.get_loc(key)
        except KeyError:
            return default
        return {name: (value[index].item() if isinstance(value[index], np.generic) else value[index])
                for name, value in self.columns.items()}


def _normalize(bars, calendar):
    frame = pd.DataFrame(bars).copy()
    if not {"date", "symbol", "trade_status", "up_limit", "down_limit"}.issubset(frame) or not ({"volume", "volume_shares"} & set(frame)):
        raise LimitStrategyInputError("Raw bar identity/status/volume/limit columns are required")
    days = [_day(day) for day in calendar]
    if not days or days != sorted(set(days)):
        raise LimitStrategyInputError("Independent reference calendar must be sorted and unique")
    date_map = {day: _day(day) for day in frame["date"].unique()}
    frame["date"] = frame["date"].map(date_map)
    if frame["symbol"].isna().any():
        raise LimitStrategyInputError("Symbol is missing")
    frame["symbol"] = frame["symbol"].astype(str)
    if frame.symbol.eq("").any() or frame.duplicated(["date", "symbol"]).any():
        raise LimitStrategyInputError("Empty symbols or duplicate stock-days")
    rows = _RowBook(frame)
    return rows, days, sorted(frame.symbol.unique())


def simulate(bars, calendar, *, policy="baseline", cost=.003, initial_cash=100000.,
             max_slots=5, slot_fraction=.20, lot_size=100, tick=.01,
             evaluation_start=None, adjustment_factor_role=None, max_daily_participation=.01, resume_state=None):
    """Return frozen signals, potential executions, daily cash/NAV and terminal claims.

    Orders are chosen before the open from the preceding TWO independent
    reference days (missing/suspended rows break the pattern). Code order is
    predetermined. Slots and funds available at the previous close are reserved
    before same-day exits: no intraday sale proceeds fund new bids, no recycling
    an unfilled slot into a later-code candidate. Fees are single-side fractions.

    The 10-session guard counts entry as session 1 and exits at session 10 open.
    T+1 forbids entry-day exits. Stop gaps use the worse opening price, and a bar
    hitting both stop and profit is assigned to the stop. A one-price lower-limit
    bar blocks selling even with reported volume. Stop/time exits retry daily.
    Baseline profit bids remain contingent on prices strictly > 10%; failed profit
    attempts do not authorize later selling below the threshold.

    Raw-price cash accounting permits only EXPLICIT supplied corporate actions:
    ``share_multiplier`` and ``cash_dividend_per_share`` (per previous-day share),
    with ``corporate_action_confirmed=True``. A held adjustment-factor change
    without that evidence is SOURCE_PENDING; a factor alone cannot invent shares
    or dividend cash. An EXPLICIT ``adjustment_factor_role='total_return_factor'``
    instead selects a total-return unit proxy: initial raw round-lot shares/factor
    become equivalent units, marked/sold as units*current factor*RAW price; profit
    triggers use entry raw price*entry factor/current factor. This does NOT prove
    actual post-action shares, dividends or executable sale size. Explicit action
    cash/share entries cannot be combined with this proxy. A split changes the
    entry basis proportionally in actual-share mode. Missing or
    delisted held marks stop the run before that day's accounting; prefix metrics
    are suppressed. Explicit suspension may use bound ``prevclose`` (or the last
    legal mark), and is separately disclosed. No terminal liquidation is forced.
    """
    if isinstance(policy, str):
        try:
            policy = {"baseline": BASELINE, "risk_guard": RISK_GUARD}[policy]
        except KeyError as error:
            raise LimitStrategyInputError("Unknown fixed policy") from error
    if not isinstance(policy, Policy) or not 0 < policy.take_profit < 1:
        raise LimitStrategyInputError("Invalid policy")
    if policy.stop_loss is not None and not 0 < policy.stop_loss < 1:
        raise LimitStrategyInputError("Invalid stop loss")
    if policy.max_holding_sessions is not None and policy.max_holding_sessions < 2:
        raise LimitStrategyInputError("Holding guard must respect T+1")
    if not (math.isfinite(cost) and 0 <= cost < 1 and math.isfinite(initial_cash) and initial_cash > 0
            and isinstance(max_slots, int) and max_slots > 0 and 0 < slot_fraction <= 1
            and isinstance(lot_size, int) and lot_size > 0 and math.isfinite(tick) and tick > 0):
        raise LimitStrategyInputError("Invalid cash, cost, slot or tick contract")
    if adjustment_factor_role not in {None, "total_return_factor"}:
        raise LimitStrategyInputError("Unknown adjustment factor role")
    if not math.isfinite(max_daily_participation) or not 0 < max_daily_participation <= 1:
        raise LimitStrategyInputError("Invalid daily volume participation bound")
    equivalent_units = adjustment_factor_role == "total_return_factor"
    rows, days, symbols = _normalize(bars, calendar)
    first = _day(evaluation_start) if evaluation_start is not None else days[0]
    if first not in days:
        raise LimitStrategyInputError("Evaluation start is outside the reference calendar")
    cash, previous_nav, holdings = float(initial_cash), float(initial_cash), {}
    daily, trades, orders, signals, pending = [], [], [], [], []
    blocked, fees = Counter(), 0.
    if resume_state is not None:
        if evaluation_start is None:
            raise LimitStrategyInputError("Resume needs exact next reference session")
        cash = float(resume_state["cash"])
        previous_nav = float(resume_state["nav"])
        holdings = deepcopy(resume_state["holdings"])
        if cash < 0 or previous_nav <= 0 or len(holdings) > max_slots:
            raise LimitStrategyInputError("Invalid bound account checkpoint")
        for position in holdings.values():
            if position["entry_index"] >= days.index(first):
                raise LimitStrategyInputError("Checkpoint entry index is not before resume date")

    def order(day, code, side, state, **details):
        item = {"date": day, "symbol": code, "side": side, "state": state, **details}
        orders.append(item)
        if state.startswith("blocked_") or state == "source_pending":
            blocked[f"{side}_{state}"] += 1
        return item

    for index, day in enumerate(days):
        if day < first:
            continue
        # Validate every claim's current legal mark before changing any account.
        marks, prices, suspension_marks, actions = {}, {}, [], []
        try:
            for code, position in holdings.items():
                row = rows.get((day, code))
                status = _status(row)
                if status in {"unknown", "delisted"}:
                    raise _SourcePending(f"held_{status}_status:{code}:{day}")
                current_factor = _number(row.get("adjustment_factor", position["adjustment_factor"]), positive=True)
                if current_factor is None:
                    raise _SourcePending(f"adjustment_factor_unknown:{code}:{day}")
                multiplier = _number(row.get("share_multiplier", 1.), positive=True)
                dividend = _number(row.get("cash_dividend_per_share", 0.))
                if multiplier is None or dividend is None or dividend < 0:
                    raise _SourcePending(f"corporate_action_unknown:{code}:{day}")
                changed = not _at(current_factor, position["adjustment_factor"])
                if equivalent_units and (multiplier != 1. or dividend != 0.):
                    raise _SourcePending(f"double_corporate_action_model_pending:{code}:{day}")
                if not equivalent_units and (changed or multiplier != 1. or dividend != 0.) and row.get("corporate_action_confirmed") is not True:
                    raise _SourcePending(f"corporate_action_quantity_or_cash_pending:{code}:{day}")
                quantity = position["equivalent_units"] * current_factor if equivalent_units else position["quantity"] * multiplier
                if not equivalent_units and not _at(quantity, round(quantity)):
                    raise _SourcePending(f"corporate_action_fractional_claim_pending:{code}:{day}")
                value = _ohlc(row)
                if status == "suspended":
                    mark = _number(row.get("prevclose"), positive=True) or position["last_close"] / multiplier
                    if changed and row.get("prevclose") is None:
                        raise _SourcePending(f"suspension_corporate_mark_pending:{code}:{day}")
                    suspension_marks.append(code)
                elif value is not None:
                    mark = value["close"]
                    if _limits(row) is None or _volume(row) is None:
                        raise _SourcePending(f"held_execution_contract_unknown:{code}:{day}")
                else:
                    raise _SourcePending(f"held_price_unknown:{code}:{day}")
                marks[code], prices[code] = mark, value
                actions.append((code, quantity if equivalent_units else int(round(quantity)), multiplier, dividend, current_factor))
        except _SourcePending as error:
            pending.append({"date": day, "reason": str(error)})
            break

        # Reserve candidates/budgets before seeing current OHLC or trade volume.
        prior_up = set() if index < 2 else rows.up_by_date.get(days[index - 2], set()) & rows.up_by_date.get(days[index - 1], set())
        candidates = [] if index < 2 else [code for code in sorted(prior_up) if code not in holdings
            and _up_close(rows.get((days[index - 2], code))) and _up_close(rows.get((days[index - 1], code)))
            and all(rows.get((prior, code), {}).get("entry_eligible", True) is True for prior in days[index - 2:index + 1])]
        signals.extend({"date": day, "symbol": code, "signal": "two_reference_limit_up_closes",
                        "evidence_dates": days[index - 2:index], "formed_before_open": True} for code in candidates)
        selected = candidates[:max(0, max_slots - len(holdings))]
        reservations, available_cash = [], cash
        for code in selected:
            budget = min(previous_nav * slot_fraction, max(0., available_cash))
            reservations.append((code, budget))
            available_cash -= budget
        for code in candidates[len(selected):]:
            order(day, code, "buy", "blocked_no_preopen_slot")

        day_fee, dividend_cash, day_traded = 0., 0., 0.
        for code, quantity, multiplier, dividend, factor in actions:
            position = holdings[code]
            dividend_cash += position["quantity"] * dividend
            position["quantity"] = quantity
            if not equivalent_units:
                position["entry_price"] /= multiplier
            position["adjustment_factor"] = factor
        cash += dividend_cash

        for code in sorted(list(holdings)):
            position, row = holdings[code], rows.get((day, code))
            if index <= position["entry_index"]:
                continue
            value, limits = prices[code], _limits(row)
            if _status(row) == "suspended":
                reason = position.get("pending_exit")
                age = index - position["entry_index"] + 1
                if reason is None and policy.max_holding_sessions is not None and age >= policy.max_holding_sessions:
                    reason = "max_holding_sessions"
                    position["pending_exit"] = reason
                if reason is not None:
                    order(day, code, "sell", "blocked_suspension", reason=reason)
                continue
            basis = position["entry_economic_price"] / position["adjustment_factor"] if equivalent_units else position["entry_price"]
            target = _strict_profit_tick(basis, policy.take_profit, tick)
            stop = (float((Decimal(str(basis)) * (Decimal(1) - Decimal(str(policy.stop_loss))) /
                           Decimal(str(tick))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(tick)))
                    if policy.stop_loss is not None else None)
            age = index - position["entry_index"] + 1
            reason, price = position.get("pending_exit"), value["open"]
            if reason is None and policy.max_holding_sessions is not None and age >= policy.max_holding_sessions:
                reason = "max_holding_sessions"
            if reason is None and stop is not None and value["low"] <= stop + 1e-9:
                reason, price = "stop_loss", min(value["open"], stop)
            if reason is None and value["high"] >= target - 1e-9:
                reason, price = "take_profit", max(value["open"], target)
            if reason is None:
                continue
            one_down = all(_at(value[name], limits[1]) for name in value)
            if one_down or _volume(row) <= 0:
                state = "blocked_one_price_limit_down" if one_down else "blocked_no_trade"
                order(day, code, "sell", state, reason=reason, limit_price=target if reason == "take_profit" else None)
                if reason != "take_profit":
                    position["pending_exit"] = reason
                continue
            quantity, notional = position["quantity"], position["quantity"] * price
            if quantity > _volume(row) * max_daily_participation + 1e-7:
                order(day, code, "sell", "blocked_daily_volume_bound", reason=reason,
                      requested_quantity=quantity, participation_bound=max_daily_participation)
                if reason != "take_profit":
                    position["pending_exit"] = reason
                continue
            fee = notional * cost
            cash += notional - fee
            entry_cost = position["entry_notional"] + position["entry_fee"]
            trade = {"date": day, "symbol": code, "side": "sell", "quantity": quantity,
                     "price": price, "notional": notional, "fee": fee, "reason": reason,
                     "holding_sessions": age, "entry_date": position["entry_date"],
                     "realized_net_pnl": notional - fee - entry_cost,
                     "state": "potential_sell_fill", "fill_certified": False}
            trades.append(trade)
            order(day, code, "sell", "potential_sell_fill", quantity=quantity, price=price, reason=reason)
            day_fee += fee
            day_traded += notional
            del holdings[code]
        for code, budget in reservations:
            row, limits = rows.get((day, code)), _limits(rows.get((day, code)))
            if limits is None:
                order(day, code, "buy", "source_pending", reason="preopen_limit_unknown", reserved_budget=budget)
                continue
            bid = limits[1]
            quantity = int(math.floor((budget + 1e-9) / (bid * (1 + cost) * lot_size))) * lot_size
            if quantity <= 0:
                order(day, code, "buy", "blocked_cash_or_lot", limit_price=bid, reserved_budget=budget)
                continue
            status, value, volume = _status(row), _ohlc(row), _volume(row)
            if status != "trading" or volume is None or volume <= 0:
                state = "source_pending" if status in {"unknown", "delisted"} or volume is None else "blocked_no_trade"
                order(day, code, "buy", state, limit_price=bid, quantity=quantity, reserved_budget=budget)
                continue
            if value is None:
                order(day, code, "buy", "source_pending", reason="raw_ohlc_unknown", limit_price=bid, quantity=quantity)
                continue
            if value["low"] > bid + 1e-7:
                order(day, code, "buy", "not_touched", limit_price=bid, quantity=quantity, reserved_budget=budget)
                continue
            if quantity > volume * max_daily_participation + 1e-7:
                order(day, code, "buy", "blocked_daily_volume_bound", limit_price=bid,
                      requested_quantity=quantity, reserved_budget=budget,
                      participation_bound=max_daily_participation)
                continue
            factor = _number(row.get("adjustment_factor", 1.), positive=True)
            if factor is None:
                order(day, code, "buy", "source_pending", reason="adjustment_factor_unknown", limit_price=bid)
                continue
            notional, fee = quantity * bid, quantity * bid * cost
            if notional + fee > cash + 1e-7:
                raise LimitStrategyInputError("Reserved pre-open cash does not cover the order")
            cash -= notional + fee
            holdings[code] = {"quantity": quantity, "entry_price": bid, "entry_index": index,
                              "entry_date": day, "entry_notional": notional, "entry_fee": fee,
                              "last_close": value["close"], "adjustment_factor": factor,
                              "equivalent_units": quantity / factor, "entry_economic_price": bid * factor,
                              "entry_raw_quantity": quantity}
            marks[code] = value["close"]
            trade = {"date": day, "symbol": code, "side": "buy", "quantity": quantity,
                     "price": bid, "notional": notional, "fee": fee,
                     "state": "potential_limit_fill", "fill_certified": False}
            trades.append(trade)
            order(day, code, "buy", "potential_limit_fill", quantity=quantity, price=bid,
                  reserved_budget=budget, fill_certified=False)
            day_fee += fee
            day_traded += notional
        if cash < -1e-7:
            raise LimitStrategyInputError("Negative cash")
        cash = max(0., cash)
        market_value = sum(position["quantity"] * marks[code] for code, position in holdings.items())
        nav = cash + market_value
        if not math.isfinite(nav) or nav <= 0:
            raise LimitStrategyInputError("Invalid account NAV")
        positions = []
        for code, position in sorted(holdings.items()):
            position["last_close"] = marks[code]
            positions.append({"symbol": code, "quantity": position["quantity"], "close": marks[code],
                              "value": position["quantity"] * marks[code], "entry_date": position["entry_date"]})
        daily.append({"date": day, "nav": nav, "cash": cash, "market_value": market_value,
                      "net_return": nav / previous_nav - 1, "fees": day_fee,
                      "dividend_cash": dividend_cash, "traded_notional": day_traded,
                      "exposure": market_value / nav, "known_suspension_marks": suspension_marks,
                      "positions": positions})
        fees += day_fee
        previous_nav = nav

    completed = not pending
    metrics = performance(pd.Series([row["net_return"] for row in daily], dtype=float)) if completed and daily else None
    summary = {"status": "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" if completed else "SOURCE_PENDING",
               "policy": asdict(policy), "cost_single_side": cost, "initial_cash": initial_cash,
               "metrics": metrics, "total_fees": fees, "completed_sessions": len(daily),
               "requested_sessions": sum(day >= first for day in days), "terminal_cash": cash,
               "terminal_nav": previous_nav if completed else None,
               "last_completed_nav": previous_nav,
               "terminal_market_value": daily[-1]["market_value"] if daily else 0.,
               "terminal_exposure": daily[-1]["exposure"] if daily else 0.,
               "terminal_holdings": deepcopy(holdings), "terminal_liquidation_forced": False,
               "quantity_model": "total_return_equivalent_units_not_actual_post_action_shares" if equivalent_units else "explicit_raw_shares",
               "adjustment_factor_role": adjustment_factor_role,
               "auction_fills_or_capacity_certified": False,
               "daily_intraday_order_sequence_certified": False,
               "single_side_cost_includes_all_fee_slippage_proxy": True,
               "volume_role": "ex_post_daily_total_volume_upper_bound_only; not limit-price volume/queue proof or preopen selection",
               "max_daily_participation": max_daily_participation,
               "partial_fills_inferred": False,
               "preopen_limit_availability_certified_by_engine": False,
               "corporate_action_inventory_complete_certified": False,
               "source_pending": pending, "blocked_orders": dict(blocked),
               "completed_round_trips": sum(trade["side"] == "sell" for trade in trades)}
    return {"summary": summary, "daily": daily, "signals": signals, "orders": orders,
            "trades": trades, "blocked": dict(blocked), "terminal": deepcopy(holdings)}
