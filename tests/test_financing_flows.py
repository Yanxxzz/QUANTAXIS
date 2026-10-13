"""Synthetic retained-text boundaries; never reads real reports or prices."""
import hashlib
import unittest
from unittest.mock import patch

from panda_alpha.financing_flows import parse_current_flows

BORROW = "cash_received_from_financial_institution_borrows"
REPAY = "cash_paid_for_debt"
META = {"symbol": "SYNTH", "report_date": "2025-12-31"}


def report(financing, *, columns="项目2025年度2024年度", unit="元"):
    return ("公司2025年年度报告\n===SOURCE_PAGE:12===\n合并现金流量表\n"
            f"2025年1—12月\n单位：{unit} 币种：人民币\n{columns}\n"
            "一、经营活动产生的现金流量：\n销售商品、提供劳务收到的现金 71.00 62.00\n"
            "二、投资活动产生的现金流量：\n投资活动现金流出小计 21.00 18.00\n"
            "三、筹资活动产生的现金流量：\n" + financing +
            "\n筹资活动现金流出小计 52.00 41.00\n四、汇率变动的影响 1.00 2.00\n")


def provenance(text):
    return {"text_sha256": "a" * 64, "text_hash_reverified": True,
            "text_content_sha256": hashlib.sha256(text.encode()).hexdigest()}


def parse(text, **kwargs):
    return parse_current_flows(text, META, provenance(text), **kwargs)


class FinancingFlowTests(unittest.TestCase):
    def test_explicit_suo_aliases_keep_actual_label_and_current_year(self):
        text = report("取得借款所收到的现金 123.45 98.76\n偿还债务所支付的现金 45.67 32.10")
        result = parse(text)
        self.assertEqual("current_annual_amount_bound", result["fields"][BORROW]["status"])
        self.assertEqual("123.45", result["fields"][BORROW]["current_amount_yuan"])
        self.assertEqual("45.67", result["fields"][REPAY]["current_amount_yuan"])
        self.assertEqual("取得借款所收到的现金", result["fields"][BORROW]["literal_label"])
        self.assertEqual("偿还债务所支付的现金", result["field_roles"][REPAY]["literal_label"])
        self.assertEqual("2025-01-01", result["fields"][BORROW]["current_start"])
        self.assertEqual("2025-12-31", result["fields"][BORROW]["current_end"])

    def test_closed_wrapped_minor_investment_pair_does_not_contaminate_inline_loan(self):
        text = report("吸收投资收到的现金 9.99 8.88\n"
                      "其中：子公司吸收少数股东投资收\n到的现金\n7.77 6.66\n"
                      "取得借款收到的现金 123.45 98.76\n偿还债务支付的现金 45.67 32.10")
        result = parse(text)
        self.assertEqual("current_annual_amount_bound", result["fields"][BORROW]["status"])
        self.assertEqual("123.45", result["fields"][BORROW]["current_amount_yuan"])
        self.assertFalse(result["borrowing_vs_total_debt_repayment_scope_matched"])
        boundary = result["fields"][BORROW]["previous_fragment_boundary_evidence"]
        self.assertEqual(12, boundary["source_page"])
        self.assertFalse(boundary["previous_amounts_emitted"])

    def test_unowned_numeric_fragment_still_requires_geometry(self):
        text = report("无法归属的行\n7.77 6.66\n取得借款收到的现金 123.45 98.76\n"
                      "偿还债务支付的现金 45.67 32.10")
        field = parse(text)["fields"][BORROW]
        self.assertEqual("pending", field["status"])
        self.assertEqual("adjacent_numeric_fragment_requires_geometry", field["reason"])
        self.assertIsNone(field["current_amount_yuan"])

    def test_real_blank_is_unknown_not_zero(self):
        result = parse(report("取得借款收到的现金\n收到其他与筹资活动有关的现金\n"
                              "偿还债务支付的现金\n分配股利、利润或偿付利息支付的现金 8.00 9.00"))
        for field in result["fields"].values():
            self.assertEqual("pending", field["status"])
            self.assertIsNone(field["current_amount_yuan"])

    def test_partial_request_never_parses_or_emits_old_successful_amount(self):
        text = report("取得借款所收到的现金 123.45 98.76\n偿还债务支付的现金 999999.99 888888.88")
        from panda_alpha.financing_flows import money as original_money
        with patch("panda_alpha.financing_flows.money", wraps=original_money) as money_spy:
            result = parse(text, target_fields=[BORROW])
        self.assertEqual("requested_current_annual_flow_amounts_bound", result["status"])
        self.assertEqual([BORROW], list(result["fields"]))
        self.assertEqual([BORROW], list(result["field_roles"]))
        self.assertFalse(result["paired_current_amounts"])
        self.assertEqual(["123.45"], [call.args[0] for call in money_spy.call_args_list])

    def test_cross_page_minority_fragment_cannot_clear_geometry(self):
        text = report("其中：子公司吸收少数股东投资收\n到的现金\n7.77 6.66\n"
                      "===SOURCE_PAGE:13===\n取得借款收到的现金 123.45 98.76\n偿还债务支付的现金 45.67 32.10")
        # A page marker between prior data and target removes numeric adjacency.
        # Put the marker in the wrapped preceding label instead: its numeric tail
        # is still adjacent, but the label's physical page does not match target.
        text = text.replace("到的现金\n7.77", "到的现金\n===SOURCE_PAGE:13===\n7.77")
        text = text.replace("6.66\n===SOURCE_PAGE:13===\n", "6.66\n")
        field = parse(text)["fields"][BORROW]
        self.assertEqual("pending", field["status"])
        self.assertEqual("adjacent_numeric_fragment_requires_geometry", field["reason"])

    def test_unclosed_or_different_previous_item_is_not_exempt(self):
        for previous in ["其中：子公司吸收少数股东投资收\n到的现金\n7.77",
                         "其中：子公司吸收少数股东投资收\n到的现金\n7.77 6.66 5.55",
                         "其他与筹资活动有关的现金\n7.77 6.66"]:
            text = report(previous + "\n取得借款收到的现金 123.45 98.76\n偿还债务支付的现金 45.67 32.10")
            field = parse(text)["fields"][BORROW]
            self.assertEqual("pending", field["status"])
            self.assertEqual("adjacent_numeric_fragment_requires_geometry", field["reason"])

    def test_current_target_must_have_its_own_complete_inline_pair_for_exception(self):
        text = report("其中：子公司吸收少数股东投资收\n到的现金\n7.77 6.66\n"
                      "取得借款收到的现金\n123.45 98.76\n偿还债务支付的现金 45.67 32.10")
        field = parse(text)["fields"][BORROW]
        self.assertEqual("pending", field["status"])
        self.assertEqual("adjacent_numeric_fragment_requires_geometry", field["reason"])

    def test_duplicate_canonical_and_alias_rows_never_choose_a_favorable_one(self):
        text = report("取得借款收到的现金 123.45 98.76\n取得借款所收到的现金 222.22 111.11\n"
                      "偿还债务支付的现金 45.67 32.10")
        field = parse(text)["fields"][BORROW]
        self.assertEqual("pending", field["status"])
        self.assertEqual(2, field["row_count"])

    def test_explicit_zero_negative_unit_and_scope_boundaries(self):
        result = parse(report("取得借款收到的现金 0.00 1.00\n偿还债务支付的现金 -45.67 32.10", unit="万元"))
        self.assertEqual("0.00", result["fields"][BORROW]["current_amount_yuan"])
        self.assertTrue(result["fields"][BORROW]["explicit_numeric_zero"])
        self.assertEqual("-456700.00", result["fields"][REPAY]["current_amount_yuan"])
        self.assertTrue(result["fields"][REPAY]["negative_requires_domain_review"])
        self.assertFalse(result["financial_input_eligible"])
        self.assertFalse(result["original_PDF_freshly_verified"])
        wrong_unit = parse(report("取得借款收到的现金 123.45 98.76\n偿还债务支付的现金 45.67 32.10", unit="件"))
        self.assertEqual("pending", wrong_unit["status"])

    def test_annual_period_and_hash_binding_remain_required(self):
        text = report("取得借款收到的现金 123.45 98.76\n偿还债务支付的现金 45.67 32.10")
        for meta, prov in [({**META, "report_date": "2026-03-31"}, provenance(text)),
                           (META, {**provenance(text), "text_content_sha256": "b" * 64})]:
            self.assertEqual("pending", parse_current_flows(text, meta, prov)["status"])
        parent_only = text.replace("合并现金流量表", "母公司现金流量表")
        self.assertEqual("pending", parse(parent_only)["status"])


if __name__ == "__main__":
    unittest.main()
