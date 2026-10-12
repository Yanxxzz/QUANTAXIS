"""Recognized goodwill share of current consolidated total assets.

The proposed lower-share direction describes acquisition asset concentration,
not an already realized impairment or all intangible assets. This native input
prototype does not certify received account values, same-report metadata, PIT
coverage or continuous production updates. No flow aggregation or filling of
absent goodwill is performed.
"""
import inspect

import numpy as np
import pandas as pd


class GoodwillAssetShare:
    def calculate(self, factors):
        original = factors['close'].index
        goodwill_input = factors['goodwill']
        assets_input = factors['total_assets']
        if (not isinstance(original, pd.MultiIndex)
                or original.nlevels != 2
                or set(original.names) != {'date', 'symbol'}
                or not original.is_unique
                or not goodwill_input.index.equals(original)
                or not assets_input.index.equals(original)):
            raise ValueError('Aligned unique date/symbol native inputs required')

        # Explicit pandas Series construction uses observed FactorSeries
        # to_numpy/index delegation, without relying on wrapper arithmetic.
        goodwill = pd.to_numeric(pd.Series(goodwill_input.to_numpy(copy=True),
                                          index=original), errors='coerce')
        assets = pd.to_numeric(pd.Series(assets_input.to_numpy(copy=True),
                                        index=original), errors='coerce')
        valid = (np.isfinite(goodwill) & np.isfinite(assets)
                 & goodwill.ge(0) & assets.gt(0) & goodwill.le(assets))
        result = goodwill.where(valid) / assets.where(valid)
        result = result.where(np.isfinite(result))
        result.name = 'value'
        return result


def native_code() -> str:
    """Generate the same calculation with the competition's injected Factor base."""
    source = inspect.getsource(GoodwillAssetShare)
    source = source.replace('class GoodwillAssetShare:',
                            'class GoodwillAssetShare(Factor):', 1)
    return 'import numpy as np\nimport pandas as pd\n\n' + source
