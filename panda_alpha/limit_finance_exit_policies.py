"""One preregistered exit comparison on the unchanged financing entry domain.

The holding clock includes entry as session1. A21-session policy therefore
first attempts a time exit at t+20 open. Execution blocks can extend the hold.
"""
from .limit_reversal import Policy

FINANCE_EXIT_POLICIES = {
    "profit5": Policy("finance_profit5", take_profit=.05),
    "stop8": Policy("finance_stop8", stop_loss=.08),
    "time20": Policy("finance_time20", max_holding_sessions=21),
    "combined": Policy("finance_combined", take_profit=.05, stop_loss=.08,
                       max_holding_sessions=21),
}
