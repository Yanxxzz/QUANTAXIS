"""Current maturity share of a recognized long-duration liability pool.

This is a native input prototype, not evidence of actual account field values,
same-report timing, publication/version metadata, PIT coverage or admission.
Both financial inputs must already represent compatible current balance stocks.
No report lag, flow aggregation, source constants or missing-value filling is
performed. Lower raw fraction is the proposed direction for future registration.
"""
import inspect

import numpy as np
import pandas as pd


class CurrentMaturityFraction:
    def calculate(self, factors):
        original = factors['close'].index
        maturity_input = factors['non_current_liability_due_one_year']
        noncurrent_input = factors['non_current_liabilities']
        if (not isinstance(original, pd.MultiIndex)
                or original.nlevels != 2
                or set(original.names) != {'date', 'symbol'}
                or not original.is_unique
                or not maturity_input.index.equals(original)
                or not noncurrent_input.index.equals(original)):
            raise ValueError('Aligned unique date/symbol native inputs required')

        # to_numpy/index are delegated by the observed FactorSeries contract.
        # Construct pandas Series explicitly rather than applying pandas coercion
        # to the wrapper itself or mixing its unverified operator semantics.
        maturity = pd.to_numeric(pd.Series(maturity_input.to_numpy(copy=True),
                                          index=original), errors='coerce')
        noncurrent = pd.to_numeric(pd.Series(noncurrent_input.to_numpy(copy=True),
                                            index=original), errors='coerce')
        valid = (np.isfinite(maturity) & np.isfinite(noncurrent)
                 & maturity.ge(0) & noncurrent.ge(0))
        denominator = maturity + noncurrent
        valid &= np.isfinite(denominator) & denominator.gt(0)
        result = maturity.where(valid) / denominator.where(valid)
        result = result.where(np.isfinite(result))
        result.name = 'value'
        return result


def native_code() -> str:
    """Generate the same calculation with the competition's injected Factor base."""
    source = inspect.getsource(CurrentMaturityFraction)
    source = source.replace('class CurrentMaturityFraction:',
                            'class CurrentMaturityFraction(Factor):', 1)
    return 'import numpy as np\nimport pandas as pd\n\n' + source
