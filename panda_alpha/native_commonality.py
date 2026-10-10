"""Self-contained native source preserving the platform input index order."""
from pathlib import Path


def native_commonality_code() -> str:
    core = Path(__file__).with_name('residual_commonality.py').read_text(encoding='utf-8')
    core = core.replace('from __future__ import annotations\n', '')
    return core + '''
class ResidualCommonality(Factor):
    def calculate(self, factors):
        original = factors['close']
        series = original.reorder_levels(['date', 'symbol']).sort_index()
        if series.index.has_duplicates:
            raise ValueError('Duplicate source date/symbol observations')
        panel = series.unstack('symbol').sort_index()
        self.print('RS01_INPUT', list(original.index.names), len(panel), len(panel.columns))
        value = residual_commonality(panel).rank(axis=1, pct=True, method='average')
        result = value.rename_axis(index='date', columns='symbol').stack(dropna=False)
        result = result.reorder_levels(original.index.names).reindex(original.index)
        result.name = 'value'
        self.print('RS01_OUTPUT_FINITE', int(result.notna().sum()))
        if not result.notna().any():
            raise ValueError('RS01 has no finite values; inspect input calendar/coverage/index diagnostic before analysis')
        return result
'''
