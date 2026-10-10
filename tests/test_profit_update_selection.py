"""Supplied-edition selection and revision barriers; no market data or labels."""
from copy import deepcopy
from decimal import Decimal
import unittest

from panda_alpha.profit_update_selection import select_profit_update_asof


def report(period="2025-06-30", annid="h125", published="2025-08-01", *,
           revenue="200.00", prior_revenue="100.00", cost="120.00", prior_cost="70.00"):
    year = int(period[:4])
    values = {"operating_revenue": revenue, "operating_revenue_comparative": prior_revenue,
              "operating_cost": cost, "operating_cost_comparative": prior_cost}
    evidence = {}
    for field in ("operating_revenue", "operating_cost"):
        evidence[field] = {
            "source_cells": [values[field], values[field + "_comparative"]], "printed_unit": "元",
            "flow_start": f"{year}-01-01", "flow_end": period,
            "comparative_flow_start": f"{year - 1}-01-01",
            "comparative_flow_end": f"{year - 1}" + period[4:],
        }
    r, p, c, pc = map(Decimal, (revenue, prior_revenue, cost, prior_cost))
    margin = (r - c) / r - (p - pc) / p if r > 0 and p > 0 else None
    return {"code": "600001", "announcement_id": annid, "report_date": period,
            "published_at": published, "available_date": published,
            "status": "text_source_pair_ready", "gross_margin_change": float(margin) if margin is not None else None,
            "pdf_sha256": "a" * 64, "text_sha256": "b" * 64,
            "values": values, "field_evidence": evidence, "comparison_change_barriers": []}


def notice(**changes):
    return {"code": "600001", "announcement_id": "notice", "published_at": "2025-08-03",
            "report_dates": ["2025-06-30"], "scope_verified": False,
            "scope_evidence": [{"kind": "original_body"}], "impact_status": "unknown", **changes}


class ProfitUpdateSelectionTests(unittest.TestCase):
    def select(self, rows, day="2025-08-10", corrections=()):
        return select_profit_update_asof(rows, code="600001", decision_date=day, corrections=corrections)

    def test_date_only_publication_is_not_usable_until_next_day(self):
        r = report()
        self.assertFalse(self.select([r], "2025-08-01")["pit_usable"])
        result = self.select([r], "2025-08-02")
        self.assertTrue(result["pit_usable"])
        self.assertIs(r, result["selected"])
        self.assertEqual("2025-08-02", result["available_date"])
        self.assertFalse(result["full_pit_certified"])

    def test_not_before_date_is_respected(self):
        r = report(); r["available_date"] = "2025-08-20"
        self.assertFalse(self.select([r])["pit_usable"])

    def test_newer_period_is_selected_before_latest_publication_date(self):
        fy = report("2024-12-31", "fy24", "2025-08-05")
        h1 = report()
        self.assertEqual("h125", self.select([fy, h1])["announcement_id"])

    def test_known_quarter_cannot_replace_fy_h1(self):
        q = report("2025-09-30", "q3", "2025-10-01")
        self.assertEqual("h125", self.select([report(), q], "2025-10-02")["announcement_id"])

    def test_future_fy_cannot_replace_current(self):
        future = report("2025-12-31", "future", "2025-08-05")
        self.assertEqual("h125", self.select([report(), future])["announcement_id"])

    def test_latest_unparsed_edition_does_not_fall_back(self):
        missing = report(annid="updated", published="2025-08-05"); missing["status"] = "pending"
        result = self.select([report(), missing])
        self.assertEqual("latest_fy_h1_source_pending", result["status"])
        self.assertEqual("updated", result["announcement_id"])
        self.assertIsNone(result["value"])
        self.assertFalse(result["economic_rejection"])

    def test_latest_metadata_identity_conflict_is_pending(self):
        r = report(); r["status"] = "metadata_identity_conflict"
        self.assertEqual("latest_fy_h1_source_pending", self.select([r])["status"])

    def test_multiple_ids_same_latest_day_are_ambiguous(self):
        self.assertEqual("ambiguous_same_day_fy_h1_editions",
                         self.select([report(), report(annid="other")])["status"])

    def test_same_id_conflicting_source_hash_is_pending(self):
        a = report(); b = deepcopy(a); b["text_sha256"] = "c" * 64
        self.assertEqual("conflicting_latest_fy_h1_identity", self.select([a, b])["status"])

    def test_same_identity_duplicate_transport_reference_is_allowed(self):
        a = report(); b = deepcopy(a); b["source_path"] = "other-reference.json"
        self.assertTrue(self.select([a, b])["pit_usable"])

    def test_named_latest_missing_publication_is_pending(self):
        h1 = report(); h1["published_at"] = None
        fy = report("2024-12-31", "fy24", "2025-04-01")
        self.assertEqual("named_fy_h1_publication_time_pending", self.select([fy, h1])["status"])

    def test_different_code_is_ignored(self):
        other = report(annid="other", published="2025-08-05"); other["code"] = "600002"
        self.assertEqual("h125", self.select([report(), other])["announcement_id"])

    def test_bound_annual_pair_is_usable(self):
        r = report("2024-12-31", "fy24", "2025-04-01"); r["status"] = "bound_annual_pair_ready"
        self.assertTrue(self.select([r])["pit_usable"])

    def test_mixed_fy_and_h1_comparative_spans_are_not_guessed(self):
        r = report(); r["field_evidence"]["operating_cost"]["comparative_flow_end"] = "2024-12-31"
        self.assertEqual("current_comparative_same_span_proof_pending", self.select([r])["status"])

    def test_embedded_period_evidence_spans_are_supported(self):
        r = report()
        for ev in r["field_evidence"].values():
            spans = {"current_start": ev.pop("flow_start"), "current_end": ev.pop("flow_end"),
                     "comparative_start": ev.pop("comparative_flow_start"), "comparative_end": ev.pop("comparative_flow_end")}
            ev["period_evidence"] = {"spans": spans}
        self.assertTrue(self.select([r])["pit_usable"])

    def test_explicit_change_barrier_blocks_even_if_status_was_ready(self):
        r = report(); r["comparison_change_barriers"] = [{"kind": "accounting_policy"}]
        self.assertEqual("explicit_accounting_or_scope_change_unbridged", self.select([r])["status"])

    def test_margin_does_not_silently_disagree_with_bound_amounts(self):
        r = report(); r["gross_margin_change"] = 0.9
        self.assertEqual("gross_margin_change_amount_binding_pending", self.select([r])["status"])

    def test_missing_values_never_become_zero(self):
        r = report(); r["values"]["operating_cost"] = None
        result = self.select([r])
        self.assertEqual("income_amount_pending", result["status"])
        self.assertIsNone(result["value"])

    def test_invalid_original_identity_is_pending(self):
        r = report(); r["pdf_sha256"] = None
        self.assertEqual("original_source_identity_pending", self.select([r])["status"])

    def test_unresolved_income_correction_blocks_when_public(self):
        result = self.select([report()], corrections=[notice()])
        self.assertEqual("known_income_revision_scope_pending", result["status"])
        self.assertEqual("notice", result["correction_announcement_id"])

    def test_future_correction_does_not_backdate_its_effect(self):
        self.assertTrue(self.select([report()], "2025-08-03", [notice()])["pit_usable"])

    def test_income_irrelevance_requires_verified_scope(self):
        n = notice(scope_verified=True, impact_status="unrelated_fields", affected_fields=["total_assets"])
        self.assertTrue(self.select([report()], corrections=[n])["pit_usable"])
        n["scope_verified"] = False
        self.assertFalse(self.select([report()], corrections=[n])["pit_usable"])

    def test_stock_only_proof_does_not_exempt_income_revision(self):
        n = notice(scope_verified=True, impact_status="unrelated_fields",
                   affected_fields=["operating_revenue", "inventory"])
        self.assertFalse(self.select([report()], corrections=[n])["pit_usable"])

    def test_financial_unrelated_requires_body_scope_proof(self):
        n = notice(scope_verified=True, impact_status="financial_unrelated")
        self.assertTrue(self.select([report()], corrections=[n])["pit_usable"])
        n["scope_evidence"] = []
        self.assertFalse(self.select([report()], corrections=[n])["pit_usable"])

    def test_unknown_relevant_correction_publication_is_pending(self):
        n = notice(published_at=None)
        self.assertEqual("known_correction_publication_time_pending", self.select([report()], corrections=[n])["status"])

    def test_verified_outside_fiscal_years_correction_is_ignored(self):
        n = notice(scope_verified=True, report_dates=["2021-09-30"])
        self.assertTrue(self.select([report()], corrections=[n])["pit_usable"])

    def test_comparative_year_quarter_correction_is_relevant(self):
        n = notice(scope_verified=True, report_dates=["2024-03-31"])
        self.assertFalse(self.select([report()], corrections=[n])["pit_usable"])

    def test_revised_report_requires_exact_id_and_pdf_text_binding(self):
        r = report()
        n = notice(scope_verified=True, impact_status="revised_report", resolved_by_report_ids=["h125"],
                   resolved_report_source_bindings=[{key: r[key] for key in ("code", "announcement_id", "pdf_sha256", "text_sha256")}])
        self.assertTrue(self.select([r], corrections=[n])["pit_usable"])
        n["resolved_report_source_bindings"][0]["text_sha256"] = "c" * 64
        self.assertFalse(self.select([r], corrections=[n])["pit_usable"])

    def test_prior_missing_or_pending_is_a_visible_limitation(self):
        r = report(); prior = report("2024-06-30", "h124", "2024-08-01"); prior["status"] = "pending"
        self.assertTrue(self.select([r, prior])["pit_usable"])
        self.assertIn("earlier_same_span_source_not_usable_for_revision_comparison", self.select([r, prior])["limitations"])
        self.assertIn("earlier_same_span_original_source_missing", self.select([r])["limitations"])

    def test_prior_available_original_mismatch_blocks(self):
        prior = report("2024-06-30", "h124", "2024-08-01", revenue="101.00", cost="70.00")
        result = self.select([prior, report()])
        self.assertEqual("unbridged_comparative_restatement_mismatch", result["status"])
        self.assertEqual("operating_revenue", result["mismatch_field"])

    def test_matching_prior_original_is_compared(self):
        prior = report("2024-06-30", "h124", "2024-08-01", revenue="100.00", cost="70.00")
        result = self.select([prior, report()])
        self.assertTrue(result["pit_usable"])
        self.assertEqual("h124", result["earlier_same_span_compared_id"])

    def test_source_display_rounding_intervals_overlap(self):
        prior = report("2024-06-30", "h124", "2024-08-01", revenue="100.004", cost="70.00")
        self.assertTrue(self.select([prior, report()])["pit_usable"])

    def test_unknown_prior_printed_precision_stays_pending(self):
        prior = report("2024-06-30", "h124", "2024-08-01", revenue="100.00", cost="70.00")
        prior["field_evidence"]["operating_revenue"].pop("printed_unit")
        self.assertEqual("earlier_same_span_printed_precision_pending", self.select([prior, report()])["status"])

    def test_unit_evidence_supplies_printed_precision(self):
        prior = report("2024-06-30", "h124", "2024-08-01", revenue="100.00", cost="70.00")
        for ev in prior["field_evidence"].values():
            ev["unit_evidence"] = {"printed_unit": ev.pop("printed_unit")}
        self.assertTrue(self.select([prior, report()])["pit_usable"])

    def test_invalid_decision_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self.select([report()], "invalid")


if __name__ == "__main__":
    unittest.main()
