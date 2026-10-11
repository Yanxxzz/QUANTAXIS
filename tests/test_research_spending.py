import hashlib
from decimal import Decimal
from panda_alpha.research_spending import parse_current_annual_research_spending


def parse(lines, mother="研发费用 999 888"):
    text = "合并利润表\n2025年度\n单位：人民币元\n项目 附注 2025年度 2024年度\n一、营业总收入 1000 900\n其中：营业收入 1000 900\n"+lines+"\n母公司利润表\n"+mother
    return parse_current_annual_research_spending(text, {"report_date": "2025-12-31"},
        {"text_hash_reverified": True, "text_content_sha256": hashlib.sha256(text.encode()).hexdigest()})


def test_zero_expense_is_explicit_and_parent_amount_not_used():
    out = parse("研发费用 0.00 30.00")
    assert out["status"] == "research_expense_and_revenue_current_annual_bound"
    assert Decimal(out["fields"]["research_expense"]["current_amount_yuan"]) == 0
    assert Decimal(out["fields"]["operating_revenue"]["current_amount_yuan"]) == 1000


def test_dash_expense_cannot_become_zero():
    out = parse("研发费用 — 30.00")
    assert out["status"] == "pending"
    assert out["fields"]["research_expense"]["current_amount_yuan"] is None


def test_duplicate_expense_rows_are_not_selected_by_amount():
    out = parse("研发费用 20.00 30.00\n研发费用 10.00 5.00")
    assert out["status"] == "pending"
    assert out["fields"]["research_expense"]["row_count"] == 2
