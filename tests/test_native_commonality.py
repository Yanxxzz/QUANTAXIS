import unittest
import numpy as np
import pandas as pd
from panda_alpha.native_commonality import native_commonality_code
from panda_alpha.residual_commonality import residual_commonality


class Base:
    def print(self, *args):
        pass


class FactorSeriesFixture:
    def __init__(self, series):
        self.series = series
    def __getattr__(self, key):
        return getattr(self.series, key)


class NativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(401)
        cls.panel = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, .012, (145, 501)), axis=0),
                                 index=pd.bdate_range('2023-01-02', periods=145))
        cls.panel.columns = [f'A{n:04d}' for n in cls.panel.columns]
        cls.series = cls.panel.rename_axis(index='date', columns='symbol').stack()
        expected = residual_commonality(cls.panel).rank(axis=1, pct=True)
        cls.expected = expected.rename_axis(index='date', columns='symbol').stack(dropna=False)
        namespace = {'Factor': Base}
        exec(compile(native_commonality_code(), 'native_commonality.py', 'exec'), namespace)
        cls.factor = namespace['ResidualCommonality']()

    def test_real_date_first_wrapper_preserves_values_and_dates(self):
        value = self.factor.calculate({'close': FactorSeriesFixture(self.series)})
        self.assertEqual(list(value.index.names), ['date', 'symbol'])
        self.assertEqual(value.name, 'value')
        self.assertGreater(value.notna().sum(), 0)
        np.testing.assert_allclose(value, self.expected, equal_nan=True, atol=1e-12)
        # Public process_result filters its date level; dates must remain dates.
        self.assertTrue((value.index.get_level_values('date') >= pd.Timestamp('2023-01-02')).all())

    def test_symbol_first_input_also_preserves_its_original_row_order(self):
        input_series = self.series.reorder_levels(['symbol', 'date']).sort_index()
        value = self.factor.calculate({'close': FactorSeriesFixture(input_series)})
        expected = self.expected.reorder_levels(['symbol', 'date']).reindex(input_series.index)
        self.assertTrue(value.index.equals(input_series.index))
        np.testing.assert_allclose(value, expected, equal_nan=True, atol=1e-12)

    def test_empty_native_output_has_an_explicit_diagnostic(self):
        with self.assertRaisesRegex(ValueError, 'no finite values'):
            self.factor.calculate({'close': FactorSeriesFixture(self.series.iloc[:100])})


if __name__ == '__main__':
    unittest.main()
