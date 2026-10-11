# Unexecuted proposal; native period/value/availability remains unqualified.
# Expensed R&D is neither all innovation spending nor capitalized development.
class NativeResearchExpenseIntensityDraft(Factor):
    def calculate(self, factors):
        close = factors['close'].astype('float64')
        research = factors['r_n_d'].astype('float64')
        revenue = factors['operating_revenue'].astype('float64')
        value = (research/revenue).reindex(close.index)
        valid = ((research >= 0) & (revenue > 0)).reindex(close.index, fill_value=False)
        return value.where(valid & ((value-value) == 0) & (close > 0)).rename('value')
