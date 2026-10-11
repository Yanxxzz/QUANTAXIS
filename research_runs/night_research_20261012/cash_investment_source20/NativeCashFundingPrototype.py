# Unregistered research draft. No score, native input fetch or official run yet.
# Input assumptions: the three native cashflow fields share report period/unit.
# Their live period/availability/value parity remains to be checked.
class NativeCashFundingPrototype(Factor):
    def calculate(self, factors):
        close = factors['close'].astype('float64')
        operating_net = factors['cash_flow_from_operating_activities'].astype('float64')
        operating_out = factors['cash_paid_for_operation_activities'].astype('float64')
        capex = factors['cash_paid_for_asset'].astype('float64')
        spending = operating_out + capex
        value = ((operating_net-capex)/spending).reindex(close.index)
        valid = ((operating_out >= 0) & (capex >= 0) & (spending > 0)).reindex(close.index, fill_value=False)
        return value.where(valid & ((value-value) == 0) & (close > 0)).rename('value')
