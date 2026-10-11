# OFFLINE SOURCE CONTRACT ONLY: no alpha or production certification.
import numpy as np
import pandas as pd

class AnnouncedUnlockSourceContractProbe(Factor):
    def calculate(self, factors):
        close = factors['close']
        index = close.index
        dates = pd.to_datetime(index.get_level_values('date'), errors='raise')
        symbols = pd.Index(index.get_level_values('symbol')).astype(str)
        symbols = symbols.str.split('.').str[0].str.zfill(6)
        result = np.full(len(index), np.nan)
        for symbol, available, listing, quantity in [('600548', '2026-09-23', '2026-09-28', 75930144), ('603459', '2026-09-23', '2026-10-08', 2813681), ('688531', '2026-09-23', '2026-09-30', 50815112)]:
            known = ((symbols == symbol) & (dates >= pd.Timestamp(available))
                     & (dates < pd.Timestamp(listing)))
            result[known] = quantity
        return pd.Series(result, index=index, name='value')
