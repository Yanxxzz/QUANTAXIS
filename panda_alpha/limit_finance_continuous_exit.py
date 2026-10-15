"""Pre-open, five-session financing reviews layered over the fixed baseline."""
import math

from .limit_conditional_finance_exit import ConditionalFinanceExit


class ContinuousFinanceExit(ConditionalFinanceExit):
    """Keep first-touch/t+20 rules and TP; never impose a hard holding cap.

    Starting at entry+5, review every five reference sessions. The provider
    contract requires features ending at least two sessions before execution.
    ``confirmed`` requires outflow plus cooling turnover, or outflow at two
    consecutive scheduled reviews (nonoverlapping five-session flow windows).
    Unknown/nonnegative flow breaks persistence. A profitable position is not
    sold by the added rule. Scheduled exits take pre-open priority over a new
    intraday first-touch signal; the enclosing engine keeps blocked exits sticky.
    """

    def __init__(self, provider, *, confirmed=False):
        super().__init__(provider)
        self.confirmed = confirmed

    def evaluate(self, *, day, index, code, position, ohlc, basis,
                 previous_return, tick):
        offset = index - position["entry_index"]
        if offset >= 5 and offset % 5 == 0 and position.get("continuous_last_index") != index:
            f = self.provider(code, day)
            flow, turnover = f.get("net_flow"), f.get("turnover_relative")
            finite = lambda x: isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)
            known = finite(flow)
            previous = position.get("continuous_last_index")
            streak = position.get("continuous_outflow_streak", 0) if previous == index - 5 else 0
            streak = streak + 1 if known and flow < 0 else 0
            position.update(continuous_last_index=index, continuous_outflow_streak=streak)
            cooling = finite(turnover) and turnover < 1
            withdrawal = known and flow < 0
            confirmed = bool(withdrawal and (cooling or streak >= 2))
            trigger = bool(previous_return < 0 and (confirmed if self.confirmed else withdrawal))
            reason = "continuous_confirmed_loss" if self.confirmed else "continuous_finance_loss"
            self.reviews.append({"date": day, "code": code, "entry_date": position["entry_date"],
                                 "reason": reason, "known": known, "trigger": trigger,
                                 "net_flow": flow, "turnover_relative": turnover,
                                 "turnover_known": finite(turnover), "outflow_streak": streak,
                                 "previous_return": previous_return})
            if trigger:
                return {"reason": reason, "price": None if ohlc is None else ohlc["open"]}
        return super().evaluate(day=day, index=index, code=code, position=position,
                                ohlc=ohlc, basis=basis, previous_return=previous_return, tick=tick)
