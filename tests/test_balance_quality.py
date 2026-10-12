import hashlib
import unittest

from panda_alpha.balance_quality import MATURITY_FIELDS, parse_current_balance_fields


class CurrentBalanceFieldsTests(unittest.TestCase):
    def fixture(self, goodwill="商誉 0.00 —", *, dates="2025年12月31日 2024年12月31日"):
        return ("2025年年度报告\n===SOURCE_PAGE:8===\n合并资产负债表\n"
                "单位：人民币万元\n项目 " + dates + "\n流动资产：\n"
                "货币资金 10.00 9.00\n" + goodwill
                + "\n资产总计 100.00 90.00\n===SOURCE_PAGE:9===\n母公司资产负债表\n"
                "商誉 999.00 888.00\n资产总计 200.00 100.00\n")

    def parse(self, text):
        return parse_current_balance_fields(text, {
            "report_date": "2025-12-31", "announcement_id": "a1",
            "published_at": "2026-04-28", "available_date": "2026-04-29"}, {
            "text_hash_reverified": True,
            "text_content_sha256": hashlib.sha256(text.encode()).hexdigest()})

    def test_current_zero_does_not_require_numeric_comparative(self):
        out = self.parse(self.fixture())
        self.assertEqual(out["status"], "current_balance_source_amounts_bound")
        self.assertEqual(out["fields"]["goodwill"]["current_amount_yuan"], "0.00")
        self.assertEqual(out["fields"]["total_assets"]["current_amount_yuan"], "1000000.00")
        self.assertEqual(out["fields"]["goodwill"]["zero_method"], "explicit_numeric_zero")
        self.assertEqual(out["fields"]["goodwill"]["source_page"], 8)

    def test_current_dash_is_not_zero(self):
        out = self.parse(self.fixture("商誉 — 0.00"))
        self.assertEqual(out["status"], "pending")
        self.assertIsNone(out["fields"]["goodwill"]["current_amount_yuan"])

    def test_duplicate_current_row_stays_pending(self):
        out = self.parse(self.fixture("商誉 1.00 0.00\n商誉 2.00 0.00"))
        self.assertEqual(out["fields"]["goodwill"]["status"], "multiple_rows_pending")

    def test_wrong_current_period_is_not_accepted(self):
        out = self.parse(self.fixture(dates="2024年12月31日 2023年12月31日"))
        self.assertEqual(out["status"], "pending")

    def test_single_cell_and_wrapped_numeric_cell_are_ambiguous(self):
        out = self.parse(self.fixture("商誉 1.00"))
        self.assertIsNone(out["fields"]["goodwill"]["current_amount_yuan"])
        out = self.parse(self.fixture("123,\n商誉 456.00 0.00"))
        self.assertIsNone(out["fields"]["goodwill"]["current_amount_yuan"])

    def test_text_tampering_rejected_before_amounts(self):
        with self.assertRaises(ValueError):
            parse_current_balance_fields(self.fixture(), {
                "report_date": "2025-12-31", "announcement_id": "a1", "published_at": "2026-04-28"},
                {"text_hash_reverified": True, "text_content_sha256": "0" * 64})

    def test_numeric_note_requires_explicit_three_column_header(self):
        text = self.fixture("商誉 16 7,568 7,568")
        self.assertIsNone(self.parse(text)["fields"]["goodwill"]["current_amount_yuan"])
        text = text.replace("项目 2025", "附注四 2025")
        out = self.parse(text)
        self.assertEqual(out["fields"]["goodwill"]["current_amount_yuan"], "75680000")
        self.assertEqual(out["fields"]["goodwill"]["numeric_note_column_evidence"]["note_reference"], "16")

    def test_page_footer_not_swallowed_into_source_row(self):
        text = self.fixture().replace("资产总计", "===SOURCE_PAGE:9===\n9\n资产总计", 1)
        out = self.parse(text)
        row = out["fields"]["total_assets"]
        self.assertTrue(row["source_label"].startswith("资产总计"))
        self.assertEqual(row["source_page"], 9)
        self.assertEqual(row["current_amount_yuan"], "1000000.00")

    def parse_maturity(self, rows):
        text = self.fixture().replace("商誉 0.00 —", rows)
        return parse_current_balance_fields(text, {
            "report_date": "2025-12-31", "announcement_id": "a1", "published_at": "2026-04-28"}, {
            "text_hash_reverified": True,
            "text_content_sha256": hashlib.sha256(text.encode()).hexdigest()}, MATURITY_FIELDS)

    def test_maturity_current_amounts_need_no_comparative_amounts(self):
        out = self.parse_maturity("一年内到期的非流动负债 0.00 —\n非流动负债合计 20.00 —")
        self.assertEqual(out["status"], "current_balance_source_amounts_bound")
        self.assertEqual(out["fields"]["non_current_liability_due_one_year"]["current_amount_yuan"], "0.00")
        self.assertEqual(out["fields"]["non_current_liabilities"]["current_amount_yuan"], "200000.00")

    def test_total_and_current_liabilities_do_not_replace_noncurrent_total(self):
        out = self.parse_maturity("一年内到期的非流动负债 10.00 20.00\n流动负债合计 100.00 90.00\n负债合计 120.00 100.00")
        self.assertIsNone(out["fields"]["non_current_liabilities"]["current_amount_yuan"])
        self.assertEqual(out["fields"]["non_current_liabilities"]["status"], "field_missing_pending")

    def test_maturity_aliases_are_one_field_not_an_addition(self):
        out = self.parse_maturity("一年内到期的非流动负债 10.00 20.00\n一年内到期非流动负债 10.00 20.00\n非流动负债合计 40.00 50.00")
        row = out["fields"]["non_current_liability_due_one_year"]
        self.assertEqual(row["status"], "multiple_rows_pending")
        self.assertIsNone(row["current_amount_yuan"])

    def test_maturity_dash_current_stays_unknown(self):
        out = self.parse_maturity("一年内到期非流动负债 — 20.00\n非流动负债合计 40.00 50.00")
        self.assertEqual(out["status"], "pending")
        self.assertIsNone(out["fields"]["non_current_liability_due_one_year"]["current_amount_yuan"])

    def test_known_liability_text_join_can_bind_maturity_not_arbitrary_prose(self):
        out = self.parse_maturity("应付股利 1.00 一年内到期的非流动负债 10.00 20.00\n非流动负债合计 40.00 50.00")
        self.assertEqual(out["fields"]["non_current_liability_due_one_year"]["current_amount_yuan"], "100000.00")
        out = self.parse_maturity("政策说明 一年内到期的非流动负债 10.00 20.00\n非流动负债合计 40.00 50.00")
        self.assertIsNone(out["fields"]["non_current_liability_due_one_year"]["current_amount_yuan"])

    def test_generic_column_role_needs_matching_actual_report_title(self):
        text = self.fixture(dates="期末余额 期初余额").replace("2025年年度报告", "2024年年度报告")
        self.assertEqual(self.parse(text)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
