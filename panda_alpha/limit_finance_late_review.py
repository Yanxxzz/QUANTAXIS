"""Onset ablation: preserve baseline decisions through entry+20."""
from .limit_conditional_finance_exit import ConditionalFinanceExit
from .limit_finance_continuous_exit import ContinuousFinanceExit


class LateContinuousFinanceExit(ContinuousFinanceExit):
    """Continue the same five-session reviews from t+25, with no hard expiry.

    Streak counting starts at t+25. The t+20 baseline review is unchanged and
    is not retroactively turned into a new persistence observation.
    """

    def evaluate(self, **kwargs):
        offset = kwargs["index"] - kwargs["position"]["entry_index"]
        if offset < 25:
            return ConditionalFinanceExit.evaluate(self, **kwargs)
        return super().evaluate(**kwargs)
