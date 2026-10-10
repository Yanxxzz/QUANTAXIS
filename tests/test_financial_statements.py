"""Small synthetic originals in the dated two-column issuer-report structure.

The figures are invented. This exercises the original text to annual admission
path without a private filing, price sample, source download or manual sidecar.
"""
from decimal import Decimal
import unittest

from panda_alpha.cash_buffer import parse_cash_buffer_report
from panda_alpha.gross_profitability import parse_gross_profit_report, select_gross_profit_asof
from panda_alpha.annual_gross_profitability import select_annual_gross_profit_asof
from panda_alpha.financial_statements import flow_spans


def original_report(period="2024-12-31", published="2025-04-01", ID="fy24", *,
                    assets="2000.00 1800.00", unit="元", explicit_balance=True,
                    income_header=None):
    year=int(period[:4]); half=period.endswith("06-30")
    title=f"{year}年半年度报告" if half else f"{year}年年度报告"
    balance=(f"项目 {period} {year-1}-12-31" if explicit_balance else
             f"{period}\n项目 期末余额 期初余额")
    income_header=income_header or (f"项目 {year}年半年度 {year-1}年半年度" if half else
                                    f"项目 {year}年度 {year-1}年度")
    text=f"""===SOURCE_PAGE:1===
公司{title}
1、合并资产负债表
单位：{unit}
{balance}
流动资产：
货币资金 300.00 280.00
资产总计 {assets}
短期借款 10.00 8.00
一年内到期的非流动负债 20.00 18.00
2、母公司资产负债表
单位：元
资产总计 1.00 1.00
===SOURCE_PAGE:2===
3、合并利润表
单位：{unit}
{income_header}
一、营业收入 400.00 350.00
二、营业成本 250.00 200.00
销售费用 60.00 50.00
4、母公司利润表
单位：元
营业收入 999.00 800.00
营业成本 777.00 700.00
===SOURCE_PAGE:3===
5、合并现金流量表
单位：{unit}
{income_header}
一、经营活动产生的现金流量：
经营活动产生的现金流量净额 80.00 70.00
六、期末现金及现金等价物余额 300.00 280.00
6、母公司现金流量表
单位：元
期末现金及现金等价物余额 1.00 1.00
"""
    metadata={"symbol":"600026","announcement_id":ID,"report_date":period,
              "published_at":published,"title":title,"edition":"ORIGINAL_FULL_REPORT"}
    provenance={"pdf_sha256":"a"*64,"text_sha256":"b"*64,
                "pdf_hash_reverified":True,"text_hash_reverified":True}
    asset=parse_cash_buffer_report(text,metadata,provenance)
    gross=parse_gross_profit_report(text,metadata,provenance,asset_record=asset)
    return asset,gross


class StatementFieldContractTests(unittest.TestCase):
    def setUp(self):
        self.fy_asset,self.fy=original_report()

    def test_original_columns_publish_units_periods_scope_and_record_link(self):
        asset=self.fy_asset["field_evidence"]["total_assets"]
        self.assertEqual(2,self.fy_asset["schema_version"])
        self.assertEqual(2,self.fy["schema_version"])
        self.assertEqual("2023-12-31",asset["comparative_stock_asof"])
        contract=self.fy["field_evidence"]["total_assets"]["field_contract"]
        self.assertEqual("stock",contract["measure"])
        self.assertEqual("consolidated",contract["statement_scope"])
        self.assertEqual("2000.00",contract["current"]["amount_yuan"])
        self.assertEqual("0.01",contract["comparative"]["printed_quantum_yuan"])
        self.assertEqual(self.fy_asset["record_sha256"],contract["source"]["asset_record_sha256"])
        self.assertEqual("2025-04-02",contract["publication"]["available_date"])
        self.assertFalse(contract["full_pit_certified"])
        flow=self.fy["field_evidence"]["operating_revenue"]["field_contract"]
        self.assertEqual("flow",flow["measure"])
        self.assertEqual("2024-01-01",flow["current"]["flow_start"])
        self.assertEqual("2023-12-31",flow["comparative"]["flow_end"])
        self.assertEqual(2,flow["source_page"])

    def test_later_original_asset_restatement_blocks_without_manual_sidecar(self):
        _,h1=original_report("2025-06-30","2025-08-27","h125",assets="2200.00 2100.00")
        before=select_annual_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-27")
        after=select_annual_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-28")
        self.assertTrue(before["pit_usable"])
        self.assertEqual("later_report_selected_fy_asset_comparison_mismatch",after["status"])
        self.assertEqual(Decimal("100"),Decimal(after["comparison_difference_yuan"]))
        self.assertEqual(Decimal("0.01"),Decimal(after["printed_precision_tolerance_yuan"]))

    def test_different_printed_unit_resolution_does_not_invent_restatement(self):
        _,h1=original_report("2025-06-30","2025-08-27","h125",assets="0.22 0.2000",unit="万元")
        asset=h1["field_evidence"]["total_assets"]["field_contract"]
        self.assertEqual("1.0000",asset["comparative"]["printed_quantum_yuan"])
        self.assertTrue(select_annual_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-28")["pit_usable"])
        _,fine=original_report("2025-06-30","2025-08-27","h125",assets="2200.00 2000.001")
        self.assertTrue(select_annual_gross_profit_asof([self.fy,fine],code="600026",decision_date="2025-08-28")["pit_usable"])

    def test_unknown_comparative_stock_date_is_pending_not_previous_year_guess(self):
        asset,h1=original_report("2025-06-30","2025-08-27","h125",assets="2200.00 2100.00",explicit_balance=False)
        field=asset["field_evidence"]["total_assets"]
        self.assertEqual("2200.00",field["amount_yuan"])
        self.assertIsNone(field["comparative_stock_asof"])
        self.assertEqual("amount_or_period_pending",field["field_contract"]["comparative"]["status"])
        self.assertEqual("later_report_asset_comparison_period_pending",
                         select_annual_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-28")["status"])

    def test_full_year_comparison_is_never_subtracted_as_halfyear(self):
        _,h1=original_report("2025-06-30","2025-08-27","h125",
                             income_header="项目 2025年半年度 2024年度")
        evidence=h1["field_evidence"]["operating_revenue"]
        self.assertEqual("400.00",evidence["current_yuan"])
        self.assertFalse(evidence["comparative_span_verified"])
        self.assertIsNone(evidence["comparative_flow_end"])
        self.assertEqual("comparative_h1_span_or_amount_pending",
                         select_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-28")["status"])

    def test_explicit_two_flow_spans_preserve_comparative_start_and_end(self):
        header="项目 2025年1月1日至2025年6月30日 2024年1月1日至2024年6月30日"
        _,h1=original_report("2025-06-30","2025-08-27","h125",income_header=header)
        flow=h1["field_evidence"]["operating_revenue"]["field_contract"]
        self.assertEqual("source_amount_and_period_bound",flow["comparative"]["status"])
        self.assertEqual("2024-01-01",flow["comparative"]["flow_start"])
        self.assertEqual("2024-06-30",flow["comparative"]["flow_end"])
        result=select_gross_profit_asof([self.fy,h1],code="600026",decision_date="2025-08-28")
        self.assertTrue(result["pit_usable"])
        self.assertAlmostEqual(.075,result["value"])

    def test_generic_current_flow_header_does_not_generate_comparative_span(self):
        spans=flow_spans("项目 本期发生额 上期发生额","2025-06-30","2025年半年度报告")
        self.assertEqual("current_period_verified",spans["current_status"])
        self.assertEqual("comparative_span_unbound",spans["comparative_status"])
        self.assertIsNone(spans["comparative_start"])
        self.assertIsNone(spans["comparative_end"])

    def test_quarter_or_nine_month_flow_is_not_current_h1(self):
        for end in ("3月31日", "9月30日"):
            spans=flow_spans(f"项目 2025年1月1日至{end} 2024年1月1日至{end}",
                             "2025-06-30","2025年半年度报告")
            self.assertEqual("current_period_unbound_or_mismatch",spans["current_status"])
            self.assertIsNone(spans["current_start"])
            self.assertIsNone(spans["comparative_end"])


if __name__=="__main__":unittest.main()
