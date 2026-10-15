"""Fixed financing withdrawal reviews, not fitted recovery classifiers."""
from decimal import Decimal, ROUND_FLOOR
import math


class ConditionalFinanceExit:
    """Review first8% touch and t+20 stale loss; retain10%TP and outer t+60 cap.

    provider(code, day) must return only features ending at least two reference
    sessions before day. Unknown features waive the optional exit, never become
    zero. A waived first touch is not re-tested every day; t+20 is a separate
    review. Confirmed exits become sticky in the enclosing execution engine.
    """
    def __init__(self, provider, *, require_cooling=False):
        self.provider = provider
        self.require_cooling = require_cooling
        self.reviews = []

    def evaluate(self, *, day, index, code, position, ohlc, basis, previous_return, tick):
        offset = index - position["entry_index"]
        if offset < 1:
            return None
        reason = None
        if offset >= 20 and not position.get("conditional_time_reviewed", False):
            position["conditional_time_reviewed"] = True
            if previous_return < 0:
                reason = "conditional_stale_loss"
        stop = float((Decimal(str(basis)) * Decimal("0.92") / Decimal(str(tick)))
                     .to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(tick)))
        touched = (ohlc is not None and ohlc["low"] <= stop + 1e-9
                   and not position.get("conditional_first_loss_reviewed", False))
        if touched:
            position["conditional_first_loss_reviewed"] = True
            if reason is None:
                reason = "conditional_stop_loss"
        if reason is None:
            return None
        f = self.provider(code, day)
        flow, turnover = f.get("net_flow"), f.get("turnover_relative")
        finite = lambda x: isinstance(x, (float, int)) and math.isfinite(x)
        known = finite(flow) and (not self.require_cooling or finite(turnover))
        trigger = bool(known and flow < 0 and (not self.require_cooling or turnover < 1))
        self.reviews.append({"date": day, "code": code, "entry_date": position["entry_date"],
                             "reason": reason, "known": known, "trigger": trigger,
                             "net_flow": flow, "turnover_relative": turnover})
        if not trigger:
            return None
        price = None if ohlc is None else (ohlc["open"] if reason == "conditional_stale_loss"
                                         else min(ohlc["open"], stop))
        return {"reason": reason, "price": price}
