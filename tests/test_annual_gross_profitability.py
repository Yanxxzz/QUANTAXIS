import unittest
from tests.test_gross_profitability import make_report
from panda_alpha.annual_gross_profitability import select_annual_gross_profit_asof


class AnnualGrossProfitVintageTests(unittest.TestCase):
    def setUp(self):
        self.fy=make_report()
        self.h1=make_report("2025-06-30","2025-08-27","h125",revenue="220.00 200.00",cost="100.00 100.00")

    def select(self,rows=None,day="2025-09-01",events=()):
        return select_annual_gross_profit_asof([self.fy,self.h1] if rows is None else rows,code="000008",decision_date=day,corrections=events)

    def event(self,**updates):
        return {"code":"000008","announcement_id":"c","published_at":"2025-08-30","report_dates":["2024-06-30"],"affected_fields":["operating_cost"],**updates}

    def test_h1_does_not_update_annual_value_or_become_score_input(self):
        r=self.select()
        self.assertEqual("fy24",r["announcement_id"])
        self.assertAlmostEqual(.075,r["value"])
        self.assertTrue(r["annual_only_frequency"])
        self.assertEqual(["2024-12-31"],r["dependent_report_periods"])
        self.assertEqual(1,r["later_reports_checked"])

    def test_new_fy_unparsed_or_ambiguous_never_falls_back_to_old_fy(self):
        new=make_report("2025-12-31","2026-04-01","fy25",cost="")
        self.assertEqual("blocked_by_latest_unparsed_report",self.select([self.fy,self.h1,new],"2026-04-02")["status"])
        good=make_report("2025-12-31","2026-04-01","fy25")
        duplicate={**good,"announcement_id":"duplicate"}
        self.assertEqual("ambiguous_same_day_versions",self.select([self.fy,good,duplicate],"2026-04-02")["status"])

    def test_future_new_fy_waits_until_after_publication_day(self):
        new=make_report("2025-12-31","2026-04-01","fy25",revenue="999.00 400.00")
        self.assertEqual("fy24",self.select([self.fy,new],"2026-04-01")["announcement_id"])
        self.assertEqual("fy25",self.select([self.fy,new],"2026-04-02")["announcement_id"])

    def test_later_same_year_h1_or_quarter_flow_correction_not_ignored(self):
        for period in ["2024-03-31","2024-06-30","2024-09-30"]:
            e=self.event(report_dates=[period])
            self.assertEqual("annual_same_year_flow_correction_unbridged",self.select(events=[e])["status"])
            self.assertTrue(self.select(day="2025-08-30",events=[e])["pit_usable"])

    def test_unknown_selected_fy_correction_stays_unknown(self):
        self.assertEqual("annual_selected_fy_correction_unresolved",self.select(events=[self.event(report_dates=[])])["status"])
        self.assertTrue(self.select(events=[self.event(report_dates=["2023-06-30"])])["pit_usable"])

    def test_only_source_proved_unchanged_or_absorbed_annual_releases_flow_barrier(self):
        e=self.event(scope_verified=True,scope_evidence="precise body",annual_effect_status="annual_current_unchanged",annual_source_ids=["fy24"],annual_effect_evidence="source statesFY2024income unchanged")
        self.assertTrue(self.select(events=[e])["pit_usable"])
        self.assertFalse(self.select(events=[{**e,"annual_source_ids":["differentFY"]}])["pit_usable"])

    def test_later_retro_or_unknown_accounting_basis_blocks_annual(self):
        for kind in ["restatement","accounting_policy","adjusted_comparative_columns"]:
            later={**self.h1,"comparison_change_barriers":[{"kind":kind,"source_evidence":"actual affirmative current report"}]}
            self.assertEqual("later_report_selected_fy_basis_uncertain",self.select([self.fy,later])["status"])

    def test_ordinary_group_change_diagnostic_but_common_control_retro_blocks(self):
        ordinary={**self.h1,"comparison_change_barriers":[{"kind":"consolidation_scope","source_evidence":"本期新设子公司、发生非同一控制下企业合并。"}]}
        self.assertTrue(self.select([self.fy,ordinary])["pit_usable"])
        for quote in ["本期同一控制下企业合并。","本期合并变更，比较报表需要追溯调整。"]:
            changed={**self.h1,"comparison_change_barriers":[{"kind":"consolidation_scope","source_evidence":quote}]}
            self.assertEqual("later_scope_selected_fy_restatement_unbridged",self.select([self.fy,changed])["status"])

    def asset_comparison_fixture(self,amount="2100.00",quantum="0.01"):
        fy={**self.fy,"field_evidence":{**self.fy["field_evidence"]}}
        fy["field_evidence"]["total_assets"]={**fy["field_evidence"]["total_assets"],"cells":["2000.00","1800.00"],"printed_unit":"元"}
        h1={**self.h1,"provenance":{**self.h1["provenance"],"text_sha256":"c"*64}}
        proof={"code":"000008","current_report_id":"h125","current_report_date":"2025-06-30","current_pdf_sha256":"a"*64,"current_text_sha256":"c"*64,"published_at":"2025-08-27","available_date":"2025-08-28","comparative_stock_asof":"2024-12-31","comparative_amount_yuan":amount,"comparative_printed_quantum_yuan":quantum,"currency":"CNY","printed_unit":"元","consolidated_scope_verified":True,"source_cells":["2200.00",amount],"balance_header_dates":[[2025,6,30],[2024,12,31]],"source_page":1}
        return [fy,h1],proof

    def test_explicit_later_previous_fy_asset_comparison_blocks_only_after_disclosure(self):
        rows,proof=self.asset_comparison_fixture()
        r=select_annual_gross_profit_asof(rows,code="000008",decision_date="2025-08-27",asset_comparisons=[proof])
        self.assertTrue(r["pit_usable"])
        r=select_annual_gross_profit_asof(rows,code="000008",decision_date="2025-08-28",asset_comparisons=[proof])
        self.assertEqual("later_report_selected_fy_asset_comparison_mismatch",r["status"])
        self.assertEqual("100.00",r["comparison_difference_yuan"])

    def test_ambiguous_date_currency_or_original_sha_does_not_invent_comparison(self):
        rows,proof=self.asset_comparison_fixture()
        for key,value in [("balance_header_dates",[[2025,6,30]]),("currency","USD"),("current_pdf_sha256","d"*64),("source_cells",["2200.00"])]:
            r=select_annual_gross_profit_asof(rows,code="000008",decision_date="2025-09-01",asset_comparisons=[{**proof,key:value}])
            self.assertTrue(r["pit_usable"])

    def test_source_print_precision_interval_is_not_actual_restatement(self):
        rows,proof=self.asset_comparison_fixture(amount="2000.001",quantum="0.001")
        self.assertTrue(select_annual_gross_profit_asof(rows,code="000008",decision_date="2025-09-01",asset_comparisons=[proof])["pit_usable"])


if __name__=="__main__":unittest.main()
