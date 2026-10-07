from copy import deepcopy
from decimal import Decimal
import gzip
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from panda_alpha.cash_buffer import (cash_buffer_score, load_cash_buffer_report,
                                     parse_cash_buffer_report, select_cash_buffer_asof)


def source_text(unit="元", short="100.00 90.00", current="50.00 40.00", cash="300.00 200.00"):
    return f"""===SOURCE_PAGE:1===
公司2024年年度报告
1、合并资产负债表
编制单位：公司
2024年12月31日
单位：{unit}
项目 期末余额 期初余额
流动资产：
货币资金 999.00 888.00
资产总计 1,000.00 900.00
短期借款 {short}
一年内到期的非流动负债 {current}
2、母公司资产负债表
单位：元
项目 期末余额 期初余额
资产总计 123.00 100.00
短期借款 999.00 900.00
一年内到期的非流动负债 777.00 700.00
3、合并利润表
单位：元
项目2024年度2023年度
净利润 12.00 10.00
4、母公司利润表
单位：元
净利润 3.00 2.00
===SOURCE_PAGE:2===
5、合并现金流量表
单位：{unit}
项目2024年度2023年度
一、经营活动产生的现金流量：
经营活动产生的现金流量净额 80.00 70.00
六、期末现金及现金等价物余额 {cash}
6、母公司现金流量表
单位：元
期末现金及现金等价物余额 111.00 100.00
7、合并所有者权益变动表
单位：元
"""


def parse(text=None, **updates):
    metadata = {"symbol": "000008", "announcement_id": "original", "report_date": "2024-12-31",
                "published_at": "2025-04-19", "title": "2024年年度报告", **updates}
    provenance = {"pdf_sha256": "a" * 64, "pdf_hash_reverified": True, "text_hash_reverified": True}
    return parse_cash_buffer_report(source_text() if text is None else text, metadata, provenance)


class CashBufferSourceTests(unittest.TestCase):
    def test_current_consolidated_stock_columns_are_not_parent_or_cfo(self):
        row = parse()
        self.assertEqual("source_values_verified", row["status"])
        self.assertEqual({"closing_cash_equivalents": "300.00", "short_term_borrowings": "100.00",
                          "current_noncurrent_liabilities": "50.00", "total_assets": "1000.00"}, row["values"])
        self.assertEqual(2, row["field_evidence"]["closing_cash_equivalents"]["source_page"])
        self.assertFalse(row["full_pit_certified"])

    def test_integer_note_reference_is_not_monetary_current_column(self):
        text = source_text(short="六、25 100.00 90.00", cash="六、67 300.00 200.00")
        self.assertEqual("100.00", parse(text)["values"]["short_term_borrowings"])
        self.assertEqual("300.00", parse(text)["values"]["closing_cash_equivalents"])

    def test_unit_conversion_and_numeric_zero_are_exact(self):
        row = parse(source_text(unit="万元", short="0.00 9.00", current="0 0"))
        self.assertEqual("source_values_verified", row["status"])
        self.assertEqual(Decimal(0), Decimal(row["values"]["short_term_borrowings"]))
        self.assertEqual(Decimal(3000000), Decimal(row["values"]["closing_cash_equivalents"]))
        self.assertEqual("explicit_numeric_zero", row["field_evidence"]["short_term_borrowings"]["zero_method"])

    def test_current_dash_and_single_amount_never_become_zero_or_prior_column(self):
        for short in ["— 900.00", "900.00", "", "-- --"]:
            row = parse(source_text(short=short))
            self.assertIsNone(row["values"]["short_term_borrowings"])
            self.assertEqual("source_values_pending", row["status"])

    def test_missing_comparison_does_not_hide_a_clear_current_value(self):
        row = parse(source_text(short="123.00 —"))
        self.assertEqual("123.00", row["values"]["short_term_borrowings"])
        self.assertIsNone(row["field_evidence"]["short_term_borrowings"]["comparative_amount_yuan"])

    def test_source_current_column_whitespace_is_not_concatenated(self):
        self.assertEqual("300.00", parse(source_text(cash="300.00     200.00"))["values"]["closing_cash_equivalents"])

    def test_wrapped_value_row_can_be_read_but_next_field_cannot(self):
        row = parse(source_text(short="\n100.00 90.00"))
        self.assertEqual("100.00", row["values"]["short_term_borrowings"])
        row = parse(source_text(short=""))
        self.assertIsNone(row["values"]["short_term_borrowings"])

    def test_missing_unit_currency_or_wrong_report_period_withhold_values(self):
        texts = [source_text().replace("单位：元", "单位：未知"),
                 source_text().replace("单位：元", "单位：元币种：美元"),
                 source_text().replace("2024年12月31日", "2023年12月31日"),
                 source_text().replace("项目2024年度2023年度", "项目2023年度2022年度")]
        for text in texts:
            self.assertEqual("source_values_pending", parse(text)["status"])

    def test_halfyear_columns_bind_flow_end_stock_to_same_report_period(self):
        text = source_text().replace("2024年12月31日", "2025年6月30日").replace("项目2024年度2023年度", "项目2025年1-6月2024年1-6月")
        row = parse(text, report_date="2025-06-30", published_at="2025-08-29")
        self.assertEqual("source_values_verified", row["status"])
        self.assertEqual("2025-06-30", row["field_evidence"]["closing_cash_equivalents"]["stock_asof"])

    def test_multiple_consolidated_tables_and_missing_end_boundary_are_pending(self):
        self.assertEqual("source_values_pending", parse(source_text() + source_text())["status"])
        self.assertEqual("source_values_pending", parse(source_text().split("6、母公司现金流量表")[0])["status"])

    def test_continuation_heading_stays_inside_consolidated_scope(self):
        text = source_text().replace("短期借款 100.00", "合并资产负债表（续）\n短期借款 100.00")
        self.assertEqual("source_values_verified", parse(text)["status"])

    def test_semantic_zero_needs_exact_original_period_scope_page_and_text(self):
        text = source_text(short="— —") + "\n===SOURCE_PAGE:3===\n合并报表短期借款期末无余额。\n"
        metadata = {"symbol": "000008", "announcement_id": "a", "report_date": "2024-12-31", "published_at": "2025-04-19"}
        provenance = {"pdf_sha256": "a" * 64, "pdf_hash_reverified": True, "text_hash_reverified": True}
        proof = {"method": "explicit_semantic_absence", "statement_scope": "consolidated", "report_date": "2024-12-31",
                 "pdf_sha256": "a" * 64, "source_page": 3, "evidence": "合并报表短期借款期末无余额。"}
        accepted = parse_cash_buffer_report(text, metadata, provenance, zero_evidence={"short_term_borrowings": proof})
        self.assertEqual("0", accepted["values"]["short_term_borrowings"])
        for field, value in [("statement_scope", "parent"), ("report_date", "2025-06-30"),
                             ("pdf_sha256", "b" * 64), ("source_page", 2), ("evidence", "无债务")]:
            changed = {**proof, field: value}
            row = parse_cash_buffer_report(text, metadata, provenance, zero_evidence={"short_term_borrowings": changed})
            self.assertIsNone(row["values"]["short_term_borrowings"])

    def test_direct_parse_does_not_certify_unchecked_source_bytes(self):
        metadata = {"symbol": "000008", "announcement_id": "a", "report_date": "2024-12-31", "pub_date": "2025-04-19"}
        self.assertEqual("source_hash_verification_pending", parse_cash_buffer_report(source_text(), metadata)["status"])

    def test_loader_rejects_wrong_original_or_text_hash(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            pdf = Path(temp) / "source.pdf"; pdf.write_bytes(b"original public bytes")
            text = Path(temp) / "source.txt.gz"; text.write_bytes(gzip.compress(source_text().encode()))
            doc = {"symbol": "000008", "announcement_id": "a", "report_date": "2024-12-31", "pub_date": "2025-04-19",
                   "path": str(pdf), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                   "text_path": str(text), "text_sha256": hashlib.sha256(text.read_bytes()).hexdigest()}
            self.assertEqual("source_values_verified", load_cash_buffer_report(doc)["status"])
            for key in ["pdf_sha256", "text_sha256"]:
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    load_cash_buffer_report({**doc, key: "a" * 64})

    def test_score_uses_only_frozen_four_field_formula(self):
        self.assertAlmostEqual(.15, cash_buffer_score(parse()["values"]))
        with self.assertRaises(ValueError):
            cash_buffer_score({**parse()["values"], "total_assets": "0"})

    def liability_text(self, short="", current="", other="200.00 180.00", total="200.00 190.00", extra=""):
        text = source_text(short=short, current=current)
        text = text.replace("短期借款 " + short, "流动负债：\n短期借款 " + short, 1)
        text = text.replace("2、母公司资产负债表", f"其他应付款 {other}\n其中：应付利息 10.00 8.00\n应付股利 20.00 15.00\n{extra}\n流动负债合计 {total}\n非流动负债：\n2、母公司资产负债表", 1)
        return text

    def test_blank_debt_zero_requires_complete_nonnegative_printed_identity(self):
        row = parse(self.liability_text())
        self.assertEqual("source_values_verified", row["status"])
        for field in ["short_term_borrowings", "current_noncurrent_liabilities"]:
            self.assertEqual("0", row["values"][field])
            self.assertEqual("balance_identity_printed_zero", row["field_evidence"][field]["status"])
        proof = row["current_liability_identity"]
        self.assertEqual("200.00", proof["known_sum"])
        self.assertEqual(3, len(proof["components"]))

    def test_identity_does_not_double_count_children_or_ignore_unknowns(self):
        cases = [self.liability_text(total="230.00 190.00"),
                 self.liability_text(extra="未知融资义务 100.00 80.00", total="300.00 190.00"),
                 self.liability_text(other="-200.00 180.00", total="-200.00 190.00"),
                 self.liability_text(short="78.00"),
                 self.liability_text(short="\n1\n9")]
        for text in cases:
            row = parse(text)
            self.assertIsNone(row["values"]["short_term_borrowings"])
            self.assertNotEqual("closed_at_printed_precision", row["current_liability_identity"]["status"])

    def test_explicit_current_dash_can_use_identity_but_never_standalone(self):
        row = parse(self.liability_text(short="— 10.00", current="— 0.00"))
        self.assertEqual("0", row["values"]["short_term_borrowings"])
        self.assertIsNone(parse(source_text(short="— 10.00"))["values"]["short_term_borrowings"])

    def test_repeated_predata_title_and_explicit_common_unit_are_one_table(self):
        text = source_text().replace("1、合并资产负债表\n", "1、合并资产负债表\n2024年12月31日\n合并资产负债表\n")
        text = text.replace("单位：元", "（除特别注明外，金额单位均为人民币元）")
        self.assertEqual("source_values_verified", parse(text)["status"])

    def test_current_column_needs_original_source_period_when_date_not_printed(self):
        text = source_text().replace("2024年12月31日\n", "")
        self.assertEqual("source_values_verified", parse(text)["status"])
        text = text.replace("公司2024年年度报告", "公司报告")
        self.assertEqual("source_values_pending", parse(text)["status"])

    def test_collapsed_same_line_asset_and_liability_rows_are_separated(self):
        text = source_text().replace("资产总计 1,000.00 900.00\n短期借款 100.00 90.00\n一年内到期的非流动负债 50.00 40.00", "资产总计 1,000.00 900.00 短期借款 100.00 90.00 一年内到期的非流动负债 50.00 40.00")
        row = parse(text)
        self.assertEqual("source_values_verified", row["status"])
        self.assertEqual("50.00", row["values"]["current_noncurrent_liabilities"])

    def test_yearend_cash_label_is_same_closing_stock_inside_verified_flow_table(self):
        row = parse(source_text(cash="六、67 300.00 200.00").replace("期末现金及现金等价物余额", "年末现金及现金等价物余额"))
        self.assertEqual("300.00", row["values"]["closing_cash_equivalents"])

    def coordinate_proof(self, column):
        return {"method": "original_pdf_header_and_row_coordinates", "statement_scope": "consolidated",
                "pdf_sha256": "a" * 64, "report_date": "2024-12-31", "label": "短期借款", "source_page": 1,
                "column_headers": {"current": {"x": 300, "y": 600}, "comparative": {"x": 450, "y": 600}},
                "label_box": [20, 100, 100, 110],
                "current_printed_amount": "90" if column == "current" else None,
                "comparative_printed_amount": "90" if column == "comparative" else None,
                "numeric_cells": [{"printed": "90", "column": column, "box": [290, 100, 310, 110] if column == "current" else [440, 100, 460, 110]}]}

    def test_unique_amount_coordinates_resolve_current_or_prior_without_guessing(self):
        meta = {"symbol": "000008", "announcement_id": "a", "report_date": "2024-12-31", "pub_date": "2025-04-19"}
        prov = {"pdf_sha256": "a" * 64, "pdf_hash_reverified": True, "text_hash_reverified": True}
        current = parse_cash_buffer_report(self.liability_text(short="90", total="290.00 190.00"), meta, prov,
                                          column_evidence={"short_term_borrowings": self.coordinate_proof("current")})
        self.assertEqual("90", current["values"]["short_term_borrowings"])
        prior = parse_cash_buffer_report(self.liability_text(short="90"), meta, prov,
                                        column_evidence={"short_term_borrowings": self.coordinate_proof("comparative")})
        self.assertEqual("0", prior["values"]["short_term_borrowings"])
        self.assertEqual("balance_identity_printed_zero", prior["field_evidence"]["short_term_borrowings"]["status"])
        no_identity = parse_cash_buffer_report(source_text(short="90"), meta, prov,
                                               column_evidence={"short_term_borrowings": self.coordinate_proof("comparative")})
        self.assertIsNone(no_identity["values"]["short_term_borrowings"])

    def test_wrong_hash_period_column_or_row_coordinate_is_not_accepted(self):
        meta = {"symbol": "000008", "announcement_id": "a", "report_date": "2024-12-31", "pub_date": "2025-04-19"}
        prov = {"pdf_sha256": "a" * 64, "pdf_hash_reverified": True, "text_hash_reverified": True}
        for key, value in [("pdf_sha256", "b" * 64), ("report_date", "2025-06-30"), ("source_page", 2)]:
            proof = {**self.coordinate_proof("current"), key: value}
            row = parse_cash_buffer_report(source_text(short="90"), meta, prov, column_evidence={"short_term_borrowings": proof})
            self.assertIsNone(row["values"]["short_term_borrowings"])
        proof = self.coordinate_proof("current")
        proof["numeric_cells"][0]["box"] = [290, 200, 310, 210]
        row = parse_cash_buffer_report(source_text(short="90"), meta, prov, column_evidence={"short_term_borrowings": proof})
        self.assertIsNone(row["values"]["short_term_borrowings"])


class CashBufferVintageTests(unittest.TestCase):
    def setUp(self):
        self.original = parse()

    def select(self, rows=None, decision="2025-05-01", corrections=()):
        return select_cash_buffer_asof([self.original] if rows is None else rows,
                                      code="000008", decision_date=decision, corrections=corrections)

    def correction(self, **updates):
        return {"code": "000008", "announcement_id": "notice", "published_at": "2025-04-30",
                "report_date": "2024-12-31", "scope_verified": False, **updates}

    def test_publication_day_is_not_available_even_if_caller_backdates(self):
        row = parse(available_date="2025-04-19")
        self.assertEqual("2025-04-20", row["available_date"])
        self.assertEqual("missing_disclosed_report", self.select([row], "2025-04-19")["status"])
        self.assertTrue(self.select([row], "2025-04-20")["pit_usable"])

    def test_future_correction_does_not_change_earlier_selection(self):
        c = self.correction()
        self.assertTrue(self.select(decision="2025-04-30", corrections=[c])["pit_usable"])
        self.assertEqual("blocked_by_unresolved_correction", self.select(corrections=[c])["status"])

    def test_future_parsed_version_is_used_only_after_own_disclosure(self):
        revised = parse(source_text(cash="400.00 200.00"), announcement_id="revised", published_at="2025-04-30")
        self.assertEqual("300.00", self.select([self.original, revised], "2025-04-30")["values"]["closing_cash_equivalents"])
        c = self.correction(scope_verified=True, scope_evidence="linked body and revised original", impact_status="revised_report", resolved_by_report_ids=["revised"])
        self.assertEqual("400.00", self.select([self.original, revised], corrections=[c])["values"]["closing_cash_equivalents"])

    def test_latest_unparsed_revision_or_later_period_blocks_old_fallback(self):
        revised = parse(source_text(short=""), announcement_id="revised", published_at="2025-04-30")
        self.assertEqual("blocked_by_latest_unparsed_report", self.select([self.original, revised])["status"])
        newer = {**revised, "report_date": "2025-06-30", "available_date": "2025-08-30"}
        self.assertEqual("blocked_by_latest_unparsed_report", self.select([self.original, newer], "2025-08-30")["status"])

    def test_generic_unknown_scope_correction_blocks_but_prior_period_date_does_not(self):
        c = self.correction(report_date=None)
        self.assertEqual("blocked_by_unresolved_correction", self.select(corrections=[c])["status"])
        old = self.correction(report_date=None, published_at="2023-05-01")
        self.assertTrue(self.select(corrections=[old])["pit_usable"])

    def test_only_verified_scope_can_release_unrelated_or_resolved_barrier(self):
        c = self.correction(impact_status="financial_unrelated", scope_evidence="explicit source statement")
        self.assertFalse(self.select(corrections=[c])["pit_usable"])
        self.assertTrue(self.select(corrections=[{**c, "scope_verified": True}])["pit_usable"])
        other = self.correction(scope_verified=True, scope_evidence="linked different field", impact_status="unrelated_fields", affected_fields=["share_based_payment"])
        self.assertTrue(self.select(corrections=[other])["pit_usable"])
        same = {**other, "affected_fields": ["short_term_borrowings"]}
        self.assertFalse(self.select(corrections=[same])["pit_usable"])

    def test_ambiguous_same_day_editions_do_not_merge_equal_values(self):
        duplicate = {**self.original, "announcement_id": "other"}
        self.assertEqual("ambiguous_same_day_versions", self.select([self.original, duplicate])["status"])
        self.assertTrue(self.select([self.original, deepcopy(self.original)])["pit_usable"])

    def test_correction_of_different_issuer_or_period_does_not_block(self):
        self.assertTrue(self.select(corrections=[self.correction(code="000009")])["pit_usable"])
        self.assertTrue(self.select(corrections=[self.correction(report_date="2023-12-31")])["pit_usable"])


if __name__ == "__main__":
    unittest.main()
