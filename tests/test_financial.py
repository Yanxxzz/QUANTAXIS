import unittest
from panda_alpha.financial import (availability, normalize_announcement, normalize_financial,
                                   normalize_baostock_financial, normalize_eastmoney_financial, select_financial_asof)

PROVENANCE={"source_path":"public.sqlite","source_sha256":"a"*64}


def value(announcement,published,amount,status="ok",**kwargs):
    row={"symbol":"600603","announcement_id":announcement,"report_date":"2023-09-30",
         "published_at":published,"amount_yuan":amount,"status":status,"pdf_sha256":"b"*64,**kwargs}
    return normalize_financial(row,PROVENANCE,source_index_verified=True)


class FinancialTests(unittest.TestCase):
    def test_publication_is_not_report_period_and_cannot_trade_same_day(self):
        row=value("a","2023-10-28","100")
        self.assertEqual("2023-10-29",row["available_date"])
        self.assertEqual("missing_disclosed_value",select_financial_asof([row],code="600603",report_date="2023-09-30",decision_date="2023-10-28")["status"])

    def test_missing_field_never_becomes_zero_and_new_failure_blocks_old(self):
        rows=[value("a","2023-10-28","100"),value("b","2024-04-30",None,"not_found")]
        self.assertIsNone(rows[1]["values"]["contract_liability"])
        self.assertEqual("blocked_by_unparsed_or_unverified_latest_document",select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2024-05-01")["status"])

    def test_correction_changes_value_only_after_own_disclosure(self):
        rows=[value("a","2023-10-28","100"),value("b","2024-04-30","150",previous_amount_yuan="100",document_kind="correction")]
        self.assertEqual("100",select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2024-04-30")["value"])
        self.assertEqual("150",select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2024-05-01")["value"])

    def test_unknown_correction_scope_blocks_value(self):
        rows=[value("a","2023-10-28","100")]
        corrections=[{"code":"600603","announcement_id":"c","available_date":"2024-05-01","unknown_scope_barrier":True,"period_links":[]}]
        result=select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2024-05-01",corrections=corrections)
        self.assertIsNone(result["value"])
        self.assertEqual("blocked_by_unresolved_correction_scope",result["status"])

    def test_unverified_original_hash_or_index_withholds_numeric_value(self):
        row={"symbol":"600603","announcement_id":"a","report_date":"2023-09-30","published_at":"2023-10-28","amount_yuan":"100","status":"ok","pdf_sha256":"bad"}
        self.assertIsNone(normalize_financial(row,PROVENANCE,source_index_verified=True)["values"]["contract_liability"])

    def test_baostock_pubdate_preserved_without_certifying_revisions(self):
        row=normalize_baostock_financial({"code":"sh.600603","statDate":"2023-09-30","pubDate":"2023-10-28","roeAvg":"","netProfit":"0"},"profit","now")
        self.assertEqual("2023-10-29",row["available_date"])
        self.assertIsNone(row["values"]["roeAvg"])
        self.assertEqual("0",row["values"]["netProfit"])
        self.assertFalse(row["full_pit_certified"])

    def test_announcement_generic_correction_does_not_invent_report_date(self):
        row=normalize_announcement({"symbol":"600603","announcement_id":"a","published_at":"2024-04-30","title":"关于前期会计差错更正的公告","category":"补充更正"},PROVENANCE)
        self.assertIsNone(row["report_date"])
        self.assertFalse(row["financial_values_available"])

    def test_eastmoney_current_numbers_cannot_be_pit_from_notice_date_alone(self):
        row=normalize_eastmoney_financial({"SECURITY_CODE":"600603","NOTICE_DATE":"2023-10-28 00:00:00",
                                          "REPORT_DATE":"2023-09-30 00:00:00","PARENT_NETPROFIT":123,
                                          "TOTAL_OPERATE_INCOME":None},"income",observed_at="2026-10-05",page_sha256="a"*64,source_path="private_page.json")
        self.assertEqual("2023-10-29",row["available_date"])
        self.assertFalse(row["pit_usable"])
        self.assertFalse(row["original_revision_history_verified"])
        self.assertIsNone(row["values"]["TOTAL_OPERATE_INCOME"])
        self.assertIn("TOTAL_OPERATE_INCOME",row["missing_fields"])

    def test_same_pdf_verified_reparse_can_resolve_older_diagnostic_copy(self):
        verified=value("a","2023-10-28","100")
        diagnostic={**verified,"value_status":"blocked_or_unverified","values":{"contract_liability":None}}
        result=select_financial_asof([verified,diagnostic],code="600603",report_date="2023-09-30",decision_date="2023-10-29")
        self.assertEqual("100",result["value"])

    def test_old_unknown_scope_notice_cannot_affect_a_future_accounting_period(self):
        rows=[value("a","2023-10-28","100")]
        correction={"code":"600603","announcement_id":"old","available_date":"2020-05-01","pub_date":"2020-04-30","unknown_scope_barrier":True,"period_links":[]}
        self.assertEqual("100",select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2023-10-29",corrections=[correction])["value"])

    def test_same_day_equal_amount_cannot_hide_a_mismatched_revision_chain(self):
        rows=[value("a","2023-10-28","100"),value("r","2024-04-30","150"),
              value("c","2024-04-30","150",previous_amount_yuan="999",chain_status="linked_correction")]
        self.assertEqual("revision_chain_mismatch",select_financial_asof(rows,code="600603",report_date="2023-09-30",decision_date="2024-05-01")["status"])


if __name__=="__main__":unittest.main()
