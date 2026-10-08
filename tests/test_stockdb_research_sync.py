import unittest
import pandas as pd

from panda_alpha.stockdb_sync import FIELDS, month_windows, normalize_frame, validate_factors, validate_identities


def native(**changes):
    row = dict(zip(FIELDS, [20250102, "000001", 10, 12, 9, 10.5, 1000, 10500, 10, 1, 5, 0, 1e9, 1, 10]))
    row.update(changes)
    return pd.DataFrame([row], columns=list(FIELDS))


class StockdbResearchSyncTests(unittest.TestCase):
    def test_units_and_original_row_hash_are_preserved(self):
        accepted, rejected = normalize_frame(native(), {"000001"}, "a" * 64)
        self.assertFalse(rejected)
        self.assertEqual(accepted[0]["vol"], 10)
        self.assertEqual(accepted[0]["volume_shares"], 1000)
        self.assertEqual(accepted[0]["amount"], 10500)
        self.assertEqual(accepted[0]["source"], "stockdb")
        self.assertEqual(len(accepted[0]["source_row_sha256"]), 64)

    def test_wrong_units_nonfinite_and_zero_activity_are_quarantined(self):
        for change in ({"amount": 1050000}, {"close": float("nan")}, {"volume": 0, "amount": 0}, {"volume": -1}):
            accepted, rejected = normalize_frame(native(**change), {"000001"}, "a" * 64)
            self.assertFalse(accepted)
            self.assertEqual(rejected[0]["research_eligibility"], "candidate_only")

    def test_unknown_optional_value_does_not_become_zero(self):
        accepted, _ = normalize_frame(native(is_st=float("nan")), {"000001"}, "a" * 64)
        self.assertIsNone(accepted[0]["is_st"])

    def test_identity_projection_rejects_stale_dates_and_duplicate_keys(self):
        for frame in (native(date=20260102), pd.concat([native(), native()])):
            with self.assertRaises(ValueError):
                validate_identities(frame, "20250101", "20250131")

    def test_complete_factor_key_census_is_required(self):
        rows = [["复权:000001:20250102", 1.2]]
        self.assertEqual(validate_factors(rows, [rows[0][0]])[0]["adj"], 1.2)
        for keys in ([], [rows[0][0], "复权:000001:20250103"]):
            with self.assertRaises(ValueError):
                validate_factors(rows, keys)
        with self.assertRaises(ValueError):
            validate_factors([[rows[0][0], -1]], [rows[0][0]])

    def test_month_partitions_have_no_overlap_or_unrequested_dates(self):
        self.assertEqual(list(month_windows("2025-01-20", "2025-03-05")),
                         [("2025-01-20", "2025-01-31"), ("2025-02-01", "2025-02-28"), ("2025-03-01", "2025-03-05")])


if __name__ == "__main__":
    unittest.main()
