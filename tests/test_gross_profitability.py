import unittest
from copy import deepcopy
from panda_alpha.gross_profitability import parse_gross_profit_report,select_gross_profit_asof


def make_report(period="2024-12-31",pub="2025-04-01",ID="fy24",revenue="400.00 350.00",cost="250.00 200.00",unit="元",change="",header=None):
    year=int(period[:4]);half=period.endswith("06-30")
    title=f"{year}年半年度报告" if half else f"{year}年年度报告"
    header=header or (f"项目 {year}年半年度 {year-1}年半年度" if half else f"项目 {year}年度 {year-1}年度")
    text=f"""===SOURCE_PAGE:1===
{title}
{change}
3、合并利润表
单位：{unit}
{header}
一、营业总收入 500.00 450.00
其中：营业收入 {revenue}
二、营业总成本 600.00 500.00
其中：营业成本 {cost}
销售费用 60.00 50.00
四、净利润 -100.00 -50.00
4、母公司利润表
单位：元
营业收入 999.00 800.00
营业成本 777.00 700.00
5、合并现金流量表
单位：元
"""
    metadata={"symbol":"000008","announcement_id":ID,"report_date":period,"published_at":pub,"title":title,"edition":"ORIGINAL_FULL_REPORT"}
    provenance={"pdf_sha256":"a"*64,"pdf_hash_reverified":True,"text_hash_reverified":True}
    asset={"code":"000008","announcement_id":ID,"report_date":period,"pdf_sha256":"a"*64,"record_sha256":"b"*64,"status":"source_values_pending","field_evidence":{"total_assets":{"amount_yuan":"2000.00","currency":"CNY","statement_scope":"consolidated","stock_asof":period}}}
    return parse_gross_profit_report(text,metadata,provenance,asset_record=asset)


class GrossProfitSourceTests(unittest.TestCase):
    def test_exact_operating_rows_are_not_total_rows_parent_or_net_profit(self):
        r=make_report()
        self.assertEqual("400.00",r["values"]["operating_revenue"])
        self.assertEqual("250.00",r["values"]["operating_cost"])
        self.assertEqual("source_current_values_verified",r["status"])
        self.assertEqual("2000.00",r["values"]["total_assets"])

    def test_footnote_unit_and_negative_real_cost_are_preserved(self):
        r=make_report(revenue="六、49 400.00 350.00",cost="六、49 -10.00 200.00",unit="万元")
        self.assertEqual("4000000.00",r["values"]["operating_revenue"])
        self.assertEqual("-100000.00",r["values"]["operating_cost"])

    def test_blank_not_zero_and_single_amount_column_not_guessed(self):
        for cost in ["", "— 200.00", "100.00"]:
            self.assertIsNone(make_report(cost=cost)["values"]["operating_cost"])

    def test_numeric_zero_is_allowed_without_fill(self):
        self.assertEqual("0.00",make_report(cost="0.00 200.00")["values"]["operating_cost"])

    def test_three_adjusted_amount_columns_cannot_be_misread_as_integer_note(self):
        r=make_report(revenue="5 400.00 350.00",cost="2 250.00 200.00",header="项目 附注 2024年度 2023年度 调整后 调整前")
        self.assertIsNone(r["values"]["operating_revenue"])
        self.assertIsNone(r["values"]["operating_cost"])

    def test_checked_no_and_unapplicable_are_not_actual_change(self):
        text="公司是否需追溯调整或重述以前年度会计数据\n□是 √否\n重要会计政策变更\n□适用 不适用"
        self.assertEqual([],make_report(change=text)["comparison_change_barriers"])

    def test_checked_positive_restatement_or_policy_requires_bridge(self):
        for text in ["公司是否需追溯调整或重述以前年度会计数据\n√是 □否", "重要会计政策变更\n适用 □不适用"]:
            self.assertTrue(make_report(change=text)["comparison_change_barriers"])

    def test_asset_from_same_original_pdf_and_period_only(self):
        r=make_report();r["field_evidence"]["total_assets"]["source_same_original_document_verified"]
        self.assertEqual("2000.00",r["values"]["total_assets"])


class GrossProfitVintageTests(unittest.TestCase):
    def setUp(self):
        self.fy=make_report()
        self.h1=make_report("2025-06-30","2025-08-27","h125",revenue="220.00 200.00",cost="100.00 100.00")

    def select(self,rows=None,day="2025-09-01",corrections=()):
        return select_gross_profit_asof([self.fy,self.h1] if rows is None else rows,code="000008",decision_date=day,corrections=corrections)

    def test_h1_ttm_uses_public_previous_fy_and_explicit_same_half_comparison(self):
        r=self.select()
        self.assertTrue(r["pit_usable"])
        self.assertAlmostEqual(.085,r["value"])
        self.assertEqual(["2024-06-30","2024-12-31","2025-06-30"],r["dependent_report_periods"])
        self.assertTrue(r["unseen_earlier_h1_revision_risk"])

    def test_h1_cannot_use_future_fy_or_h1_double(self):
        future=make_report("2025-12-31","2026-04-01","fy25",revenue="9999.00 400.00",cost="1.00 250.00")
        self.assertAlmostEqual(.085,self.select([self.fy,self.h1,future])["value"])
        self.assertFalse(self.select([self.h1,future])["pit_usable"])

    def test_publication_day_does_not_use_new_h1_source(self):
        self.assertEqual("fy24",self.select(day="2025-08-27")["announcement_id"])
        self.assertEqual("h125",self.select(day="2025-08-28")["announcement_id"])

    def test_unclear_comparative_half_span_blocks_ttm(self):
        unknown=make_report("2025-06-30","2025-08-27","h125",header="项目 本期发生额 上期发生额")
        self.assertEqual("comparative_h1_span_or_amount_pending",self.select([self.fy,unknown])["status"])

    def test_known_latest_unparsed_prior_fy_blocks_old_fy_fallback(self):
        revised=make_report(pub="2025-08-29",ID="newFY",cost="")
        self.assertEqual("prior_fy_blocked_by_latest_unparsed_report",self.select([self.fy,self.h1,revised])["status"])

    def test_known_prior_h1_new_unparsed_or_ambiguous_is_not_unsupplied(self):
        old=make_report("2024-06-30","2024-08-27","oldH1",revenue="200.00 190.00",cost="100.00 90.00")
        revised=make_report("2024-06-30","2025-08-29","newOldH1",cost="")
        self.assertEqual("prior_h1_blocked_by_latest_unparsed_report",self.select([self.fy,self.h1,old,revised])["status"])
        duplicate={**old,"announcement_id":"otherOldH1"}
        self.assertEqual("prior_h1_ambiguous_same_day_versions",self.select([self.fy,self.h1,old,duplicate])["status"])

    def test_known_old_h1_comparative_mismatch_requires_bridge(self):
        old=make_report("2024-06-30","2024-08-27","oldH1",revenue="201.00 190.00",cost="100.00 90.00")
        self.assertEqual("unbridged_comparative_restatement_mismatch",self.select([self.fy,self.h1,old])["status"])

    def test_prior_h1_comparison_uses_the_original_printed_precision(self):
        old=make_report("2024-06-30","2024-08-27","oldH1",revenue="200.00 190.00",cost="100.00 90.00")
        rounded=make_report("2025-06-30","2025-08-27","h125",revenue="220.00 200.001",cost="100.00 100.001")
        self.assertTrue(self.select([self.fy,rounded,old])["pit_usable"])
        changed=make_report("2025-06-30","2025-08-27","h125",revenue="220.00 200.020",cost="100.00 100.00")
        self.assertEqual("unbridged_comparative_restatement_mismatch",self.select([self.fy,changed,old])["status"])

    def test_correction_to_comparative_h1_dependency_blocks_not_future_notice(self):
        notice={"code":"000008","announcement_id":"c","published_at":"2025-08-30","report_dates":["2024-06-30"],"affected_fields":["operating_cost"]}
        self.assertTrue(self.select(day="2025-08-30",corrections=[notice])["pit_usable"])
        self.assertEqual("blocked_by_unresolved_dependency_correction",self.select(corrections=[notice])["status"])

    def test_explicit_change_not_rescued_and_negative_gp_not_flipped(self):
        changed=make_report(change="重要会计政策变更\n适用 □不适用")
        standalone=self.select([changed],day="2025-04-02")
        self.assertTrue(standalone["pit_usable"])
        self.assertTrue(standalone["source_change_diagnostics"])
        self.assertEqual("explicit_accounting_or_scope_change_unbridged",self.select([changed,self.h1])["status"])
        loss=make_report(cost="450.00 200.00")
        self.assertLess(self.select([loss],day="2025-04-02")["value"],0)


if __name__=="__main__":unittest.main()
