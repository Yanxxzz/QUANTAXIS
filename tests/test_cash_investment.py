import hashlib
from decimal import Decimal
import unittest
from panda_alpha.cash_investment import parse_current_annual_investment_cash


class InvestmentCashTests(unittest.TestCase):
    def parse(self, lines):
        text = "合并现金流量表\n2025年度\n单位：人民币元\n项目 附注 2025年度 2024年度\n一、经营活动产生的现金流量：\n"+lines+"\n母公司现金流量表\n"
        return parse_current_annual_investment_cash(text, {"report_date":"2025-12-31"},
             {"text_hash_reverified":True,"text_content_sha256":hashlib.sha256(text.encode()).hexdigest()})

    def fixture(self):
        return "经营活动现金流出小计 1,000.00 900.00\n经营活动产生的现金流量净额 -80.00 100.00\n购建固定资产、无形资产和其他长期资产所支付的现金 0.00 20.00"

    def test_signed_operating_cash_and_explicit_zero(self):
        out = self.parse(self.fixture())
        self.assertEqual(out["status"],"three_current_annual_cash_amounts_bound")
        self.assertEqual(Decimal(out["fields"]["operating_cash_net"]["current_amount_yuan"]),Decimal("-80"))
        self.assertEqual(Decimal(out["fields"]["capital_expenditure_cash"]["current_amount_yuan"]),Decimal("0"))

    def test_missing_current_is_not_zero(self):
        out = self.parse(self.fixture().replace("0.00 20.00","— 20.00"))
        self.assertEqual(out["status"],"pending")
        self.assertIsNone(out["fields"]["capital_expenditure_cash"]["current_amount_yuan"])

    def test_duplicate_capex_row_pending(self):
        out = self.parse(self.fixture()+"\n购建固定资产、无形资产和其他长期资产支付的现金 1.00 2.00")
        self.assertEqual(out["fields"]["capital_expenditure_cash"]["row_count"],2)


if __name__ == "__main__":unittest.main()
