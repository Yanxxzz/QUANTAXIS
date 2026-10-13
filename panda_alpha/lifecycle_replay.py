"""An explicit, separately registered announcement-driven execution policy.

This module does not fetch notices or prove their public availability. Callers
freeze the complete event inventory and its evidence before looking at results.
No-event runs retain the original study engine exactly. An event prohibits new
buys from its supplied reference date and requests a flat exit each day until a
potential fill exists. It does not rebalance other securities between scheduled
entries, infer cash redemption, or provide prices for a delisted claim.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date

import pandas as pd

from . import study
from .attribution import Trade, account_day, summarize
from .evaluation import performance


POLICY_ID = "announced_termination_no_buy_daily_flat_exit_v1"
_DELISTED = {"delisted", "source_explicit_delisted", "source_explicit_delisting",
             "terminated_listing"}


def _events(events, days):
    """Validate supplied reference dates; never derive them from receipt dates."""
    normalized = []
    for event in events:
        if not isinstance(event, dict):
            raise study.StudyInputError("Lifecycle event must be an evidence-bound mapping")
        symbol, available = event.get("symbol"), event.get("available_reference_date")
        if not isinstance(symbol, str) or not symbol:
            raise study.StudyInputError("Lifecycle event symbol is missing")
        try:
            parsed = date.fromisoformat(available)
        except (TypeError, ValueError) as error:
            raise study.StudyInputError("Lifecycle available reference date is unknown") from error
        if parsed.isoformat() != available:
            raise study.StudyInputError("Lifecycle reference date must be canonical ISO")
        if not event.get("source"):
            raise study.StudyInputError("Lifecycle source evidence is missing")
        if days[0] <= available <= days[-1] and available not in days:
            raise study.StudyInputError("Lifecycle reference date is outside the frozen sessions")
        normalized.append(dict(event))
    return sorted(normalized, key=lambda item: (item["available_reference_date"], item["symbol"]))


def _price(quotes, code, day, field):
    # A source may explicitly distinguish delisting from a zero trading-status
    # flag. Never let the study's suspension fallback turn that into a price.
    row = quotes.rows.get((code, day))
    if row is not None and any(str(row.get(key)) in _DELISTED
                               for key in ("source_status", "listing_status", "lifecycle_status")):
        raise study.StudyExecutionError(f"delisted_valuation_pending:{code}:{day}:{field}")
    return quotes.price(code, day, field)


def replay_portfolio(name, schedule, days, quotes, cost, *, events=None):
    """Replay with frozen public-notice events, retaining blocked claim holdings.

    Each event requires ``symbol``, ``available_reference_date`` and ``source``
    (the caller's bound notice/availability evidence). Unknown dates are errors.
    All supplied events use one rule; duplicate notices retain the earliest
    available trigger, never a later receipt-date choice. Missing event inventory
    coverage remains the caller's disclosed limitation.

    At ordinary rebalance dates the original target-slot count remains the
    denominator. Prohibited slots stay cash rather than reallocating their
    budget to survivors. Off-grid exits also stay cash. Turnover includes actual
    lifecycle exit days and scheduled days, excluding ordinary terminal sales.
    Existing mark/fill/fee/risk semantics otherwise follow ``study``; explicit
    delisting cannot masquerade as suspension. This is a new trading policy,
    not a source-only correction to an old frozen economic experiment.
    """
    if events is None or len(events) == 0:
        return study.replay_portfolio(name, schedule, days, quotes, cost)
    if not days:
        raise study.StudyInputError("Lifecycle replay requires frozen sessions")
    inventory = _events(events, days)
    available_by_code = {}
    for event in inventory:
        available_by_code.setdefault(event["symbol"], event["available_reference_date"])

    cash, holdings, previous_nav, previous_marks = 1., {}, 1., {}
    daily, orders, executions, turnover, exposure_days = [], [], [], [], []
    blocked = Counter()
    for day in days:
        active = {code for code, available in available_by_code.items() if available <= day}
        before, cash0 = dict(holdings), cash
        opens = {code: _price(quotes, code, day, "open")[0] for code in holdings}
        equity_open = cash + sum(qty * opens[code] for code, qty in holdings.items())
        trades, traded = [], 0.
        lifecycle_traded = False
        # Only affected holdings are handled daily. No other target changes.
        for code in sorted(set(holdings) & active):
            state = quotes.state(code, day, "sell")
            order = {"date": day, "symbol": code, "side": "sell", "state": state,
                     "requested_value": holdings[code] * opens[code],
                     "lifecycle_exit": True, "available_reference_date": available_by_code[code]}
            orders.append(order)
            if state != "potential_fill":
                if not state.startswith("blocked_"):
                    raise study.StudyExecutionError(f"{state}:{code}:{day}:lifecycle_sell")
                blocked["lifecycle_sell_" + state] += 1
                continue
            quantity = holdings.pop(code)
            value = quantity * opens[code]
            cash += value * (1 - cost)
            trades.append(Trade(code, -quantity, opens[code], value * cost))
            traded += value
            lifecycle_traded = True
            order["filled_value"] = value

        if day in schedule:
            event = schedule[day]
            selected = set(event["codes"])
            # Keep the frozen target slots: excluded slots leave cash. This is
            # not financial-universe deletion or an extra surviving-name weight.
            goal = equity_open / len(selected) if selected else 0.
            for code in sorted(list(holdings)):
                if code in active:  # Already attempted one flat exit this day.
                    continue
                excess = max(0., holdings[code] * opens[code] - (goal if code in selected else 0.))
                if excess <= 1e-12:
                    continue
                state = quotes.state(code, day, "sell")
                orders.append({"date": day, "symbol": code, "side": "sell",
                               "state": state, "requested_value": excess})
                if state != "potential_fill":
                    if not state.startswith("blocked_"):
                        raise study.StudyExecutionError(f"{state}:{code}:{day}:sell")
                    blocked["sell_" + state] += 1
                    continue
                quantity = min(holdings[code], excess / opens[code])
                value = quantity * opens[code]
                holdings[code] -= quantity
                cash += value * (1 - cost)
                trades.append(Trade(code, -quantity, opens[code], value * cost))
                traded += value
                if holdings[code] <= 1e-14:
                    del holdings[code]
            deficits = {}
            for code in sorted(selected):
                if code in active:
                    orders.append({"date": day, "symbol": code, "side": "buy",
                                   "state": "blocked_announced_termination",
                                   "available_reference_date": available_by_code[code]})
                    blocked["buy_blocked_announced_termination"] += 1
                    continue
                deficit = max(0., goal - holdings.get(code, 0.) * opens.get(code, 0.))
                if deficit <= 1e-12:
                    continue
                state = quotes.state(code, day, "buy")
                if state != "potential_fill":
                    orders.append({"date": day, "symbol": code, "side": "buy",
                                   "state": state, "requested_value": deficit})
                    if not state.startswith("blocked_"):
                        raise study.StudyExecutionError(f"{state}:{code}:{day}:buy")
                    blocked["buy_" + state] += 1
                    continue
                opens[code] = _price(quotes, code, day, "open")[0]
                deficits[code] = deficit
            demand = sum(deficits.values())
            scale = min(1., max(0., cash) / ((1 + cost) * demand)) if demand else 0.
            for code, deficit in deficits.items():
                value = deficit * scale
                if value <= 1e-12:
                    continue
                quantity = value / opens[code]
                holdings[code] = holdings.get(code, 0.) + quantity
                cash -= value * (1 + cost)
                trades.append(Trade(code, quantity, opens[code], value * cost))
                traded += value
                orders.append({"date": day, "symbol": code, "side": "buy", "state": "potential_fill",
                               "requested_value": deficit, "filled_value": value})
        if day in schedule or lifecycle_traded:
            turnover.append({"date": day, "two_side_traded_fraction": traded / equity_open,
                             "one_side_turnover_proxy": traded / (2 * equity_open)})
        if day == days[-1]:
            for code in sorted(list(holdings)):
                if code in active:  # No second lifecycle attempt at terminal.
                    continue
                state = quotes.state(code, day, "sell")
                orders.append({"date": day, "symbol": code, "side": "sell", "state": state,
                               "final_liquidation": True})
                if state != "potential_fill":
                    if not state.startswith("blocked_"):
                        raise study.StudyExecutionError(f"{state}:{code}:{day}:final_sell")
                    blocked["final_sell_" + state] += 1
                    continue
                quantity = holdings.pop(code)
                value = quantity * opens[code]
                cash += value * (1 - cost)
                trades.append(Trade(code, -quantity, opens[code], value * cost))
        if cash < -1e-10:
            raise study.StudyExecutionError("negative_cash")
        cash = max(0., cash)
        codes = set(before) | set(holdings) | {trade.code for trade in trades}
        for code in codes:
            opens.setdefault(code, _price(quotes, code, day, "open")[0])
        closes = {code: _price(quotes, code, day, "close")[0] for code in codes}
        day_result = account_day(day=day, previous_nav=previous_nav, cash_before=cash0,
                                 quantities_before=before, previous_close=previous_marks,
                                 open_marks=opens, close_marks=closes, trades=trades, cash_after=cash)
        day_result["known_suspension_mark"] = any(_price(quotes, code, day, "close")[1]
                                                 for code in holdings)
        exposure_days.append(study._exposure_day(holdings, closes, cash, day_result["nav"], quotes, day))
        daily.append(day_result)
        executions.extend({"date": day, **asdict(trade)} for trade in trades)
        previous_nav = day_result["nav"]
        previous_marks = {code: closes[code] for code in holdings}
    summary = summarize(daily, folds=min(5, len(daily)))
    metrics = performance(pd.Series([row["net_return"] for row in daily]))
    risk_pending = any(row["status"] == "risk_metadata_pending" for row in exposure_days)
    exposure_summary = study._exposure_summary(exposure_days, summary, turnover)
    exposure_summary["market_projection_report"] = study._market_projection_report(daily, inputs_provided=False)
    return study._clean({"status": "potential_wealth_evaluated", "name": name, "cost": cost,
                         "metrics": metrics, "accounting": summary,
                         "daily": [{key: value for key, value in row.items() if key != "security_rows"}
                                   for row in daily],
                         "orders": orders, "trades": executions, "turnover": turnover,
                         "blocked_orders": dict(blocked), "final_open_positions": holdings,
                         "terminal_liquidation_pending": bool(holdings),
                         "exposures": {"status": "risk_metadata_pending" if risk_pending else "supplied_risk_metadata_available",
                                       "summary": exposure_summary, "daily": exposure_days},
                         "auction_fills_or_capacity_certified": False,
                         "wealth_unit_model_not_actual_account_nav": True,
                         "lifecycle_policy": {"id": POLICY_ID, "events": inventory,
                                              "scheduled_excluded_slots_retained_as_cash": True,
                                              "off_schedule_survivor_rebalance": False,
                                              "public_availability_certified_by_replay": False,
                                              "unknown_inventory_not_no_event_certified": True,
                                              "delisted_claim_valuation_provided": False}})
