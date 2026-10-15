"""Pure exit intents for the explicitly accepted minute-IOC strategy migration.

This module does not submit orders or claim fills.  The caller supplies a bound
price basis, the independent reference-session index, and lagged features.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal, ROUND_FLOOR
import math


ADAPTER_METADATA = {
    "execution_role": "minute_reference_trigger_and_IOC_intent_not_daily_bar_fill_parity",
    "position_basis": "caller_supplied_current_same_price_basis_cost",
    "native_average_price_role": "native_avg_price_and_corporate_actions_are_not_old_total_return_equivalent_units",
    "price_basis_certified": False,
    "feature_clock": "caller_must_bind_financing_and_turnover_through_at_least_t_minus_2_reference_sessions",
    "state_role": "exit_flags_only_no_NAV_no_group_returns_no_network",
    "reference_clock": "entry_session_offset_zero_T_plus_1_then_reference_sessions_not_minutes_or_calendar_days",
    "partial_fill_role": "caller_keeps_position_and_pending_intent_until_actual_remaining_quantity_is_zero",
    "take_profit_role": "strict_gross_price_gain_over_10_percent_not_net_after_fees",
}


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _positive(value):
    return _finite(value) and value > 0


def _tick_price(basis, multiplier, tick, *, strictly_above=False):
    quantum = Decimal(str(tick))
    ticks = (Decimal(str(basis)) * Decimal(multiplier) / quantum).to_integral_value(
        rounding=ROUND_FLOOR)
    return float((ticks + int(strictly_above)) * quantum)


def _intent(reason, reference_price, *, trigger_price=None, sticky=False):
    return {"action": "sell", "reason": reason,
            "reference_price": reference_price, "trigger_price": trigger_price,
            "sticky": sticky,
            "order_timing": "current_or_next_available_quote_IOC_by_caller"}


def evaluate_exit(mode, *, session_index, position, previous_close,
                  reference_price=None, net_flow=None, turnover_relative=None,
                  tick=.01):
    """Update only ``position['flags']`` and return a sell intent or ``None``.

    ``position`` has integer ``entry_index``, positive ``basis`` and an optional
    mutable ``flags`` dictionary.  ``previous_close`` and ``reference_price``
    must use that same current price basis.  Missing prior close cannot confirm
    a scheduled loss; missing current quote cannot confirm an intraday touch.

    A scheduled exit can be confirmed without a current quote.  Its returned
    reference is then None: the callback must wait for a legal quote and obey
    actual exchange/session/capacity/T+1 guards.  An intent is never a fill.
    Non-TP exits remain sticky after partial/unfilled IOC attempts.  TP does not
    become a market exit below its threshold on a later callback.
    """
    if mode not in {"control", "late_confirmed"}:
        raise ValueError("Unknown frozen strategy mode")
    if (type(session_index) is not int or not isinstance(position, MutableMapping)
            or type(position.get("entry_index")) is not int
            or session_index < position["entry_index"]
            or not _positive(position.get("basis")) or not _positive(tick)):
        raise ValueError("Bound position basis and independent reference indices are required")
    if reference_price is not None and not _positive(reference_price):
        raise ValueError("Current reference quote must be positive and finite or unknown")
    flags = position.setdefault("flags", {})
    if not isinstance(flags, MutableMapping):
        raise ValueError("Exit flags must be a mutable mapping")
    offset = session_index - position["entry_index"]
    if offset < 1:
        return None  # T+1, including no signal confirmation from entry-day touches.
    pending = flags.get("pending_exit")
    if pending:
        return _intent(pending["reason"], reference_price,
                       trigger_price=pending.get("trigger_price"), sticky=True)
    basis = position["basis"]
    prior_loss = _positive(previous_close) and previous_close < basis
    known_flow = _finite(net_flow)

    def confirm(reason, trigger_price=None):
        flags["pending_exit"] = {"reason": reason, "confirmed_index": session_index,
                                 "trigger_price": trigger_price}
        return _intent(reason, reference_price, trigger_price=trigger_price, sticky=True)

    if mode == "late_confirmed":
        # Continuous checks precede a newly observed intraday first loss touch.
        if (offset >= 25 and offset % 5 == 0
                and flags.get("continuous_last_index") != session_index):
            consecutive = flags.get("continuous_last_index") == session_index - 5
            streak = flags.get("continuous_outflow_streak", 0) if consecutive else 0
            streak = streak + 1 if known_flow and net_flow < 0 else 0
            flags.update(continuous_last_index=session_index,
                         continuous_outflow_streak=streak)
            cooling = _finite(turnover_relative) and turnover_relative < 1
            flags["last_continuous_review"] = {
                "session_index": session_index, "known_flow": known_flow,
                "outflow_streak": streak, "cooling": cooling,
                "prior_loss": bool(prior_loss),
            }
            if prior_loss and known_flow and net_flow < 0 and (cooling or streak >= 2):
                return confirm("continuous_confirmed_loss")

        reason = None
        if offset >= 20 and not flags.get("conditional_time_reviewed", False):
            flags["conditional_time_reviewed"] = True
            if prior_loss:
                reason = "conditional_stale_loss"
        stop = _tick_price(basis, "0.92", tick)
        if (reference_price is not None and reference_price <= stop + 1e-9
                and not flags.get("conditional_first_loss_reviewed", False)):
            flags["conditional_first_loss_reviewed"] = True
            if reason is None:
                reason = "conditional_stop_loss"
        if reason and known_flow and net_flow < 0:
            return confirm(reason, stop if reason == "conditional_stop_loss" else None)

    target = _tick_price(basis, "1.10", tick, strictly_above=True)
    if reference_price is not None and reference_price >= target - 1e-9:
        return _intent("take_profit", reference_price, trigger_price=target)
    return None
