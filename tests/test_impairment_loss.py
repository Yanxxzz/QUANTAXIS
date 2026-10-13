"""Source contracts with semantic adversaries; synthetic, never alpha/PIT."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "苏州华兴源创科技股份有限公司"


def evaluate(text, issuer=None):
    if os.environ.get("IMPAIRMENT_TEST_BASELINE") == "1":
        spec = importlib.util.spec_from_file_location("frozen_old_impairment_runtime", ROOT / "research_runs/day_research_20261013/impairment_source4/run_source.py")
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        heads = list(old.TABLE.finditer(text))
        hit, stop = heads[0], heads[1].start()
        body = text[hit.end():stop]
        first = old.re.search(r"(?m)^\s*" + old.PREFIX + "营业收入", body)
        header = body[:first.start()]
        field = old.parse_row(text, hit, body, old._annual_columns(header, "2025-12-31"), old.amount_unit(header), "资产减值损失", caption=True)
        return {"status": field["status"], "field": field}
    from panda_alpha.impairment_loss import parse_current_asset_impairment
    return parse_current_asset_impairment(text, {"report_date": "2025-12-31"},
        {"text_hash_reverified": True, "text_content_sha256": hashlib.sha256(text.encode()).hexdigest()}, issuer_page_header=issuer)


def source(preceding="", target='资产减值损失（损失以“-”号填列） -3.00 -2.00', header_extra="", years="2025年度 2024年度"):
    return ("===SOURCE_PAGE:1===\n合并利润表\n单位：元\n项目 " + years + "\n" + header_extra +
            "营业收入 100.00 90.00\n" + preceding + target + "\n资产处置收益 1.00 1.00\n母公司利润表\n")


def test_complete_samepage_wrapped_credit_and_asset_rows_are_separate():
    text = source('信用减值损失（损失以“-”号\n填列）\n-8.00 -7.00\n', '资产减值损失（损失以“-”号\n填列）\n-3.00 -2.00')
    f = evaluate(text)["field"]
    assert f["status"] == "current_annual_amount_bound"
    assert f["current_amount_yuan"] == "-3.00"
    assert f["losses_with_minus_caption_bound"]


def test_exact_known_company_sourcepage_header_not_third_column():
    packet = f"===SOURCE_PAGE:2===\n{ISSUER}2025 年年度报告\n2 / 300\n"
    f = evaluate(source(header_extra=packet), issuer=ISSUER)["field"]
    assert f["status"] == "current_annual_amount_bound"
    assert f["current_start"] == "2025-01-01"


def test_unattributed_numeric_line_is_not_credit_geometry():
    f = evaluate(source("-8.00 -7.00\n"))["field"]
    assert f["status"] == "pending"
    assert f["current_amount_yuan"] is None


def test_credit_caption_across_physical_pages_does_not_clear_fragment():
    text = source('信用减值损失（损失以“-”号\n填列）\n===SOURCE_PAGE:2===\n-8.00 -7.00\n', '资产减值损失（损失以“-”号\n填列）\n-3.00 -2.00')
    assert evaluate(text)["field"]["status"] == "pending"


def test_real_third_fiscal_column_must_remain_pending():
    assert evaluate(source(years="2025年度 2024年度 2023年度"))["field"]["status"] == "pending"


@pytest.mark.parametrize("packet", [
    "===SOURCE_PAGE:2===\n其他公司2025年年度报告\n2 / 300\n",
    f"===SOURCE_PAGE:2===\n{ISSUER}2025年年度报告\n3 / 300\n",
])
def test_wrong_issuer_or_page_cannot_be_removed(packet):
    assert evaluate(source(header_extra=packet), issuer=ISSUER)["field"]["status"] == "pending"


def test_uncaptioned_negative_fact_never_gains_loss_sign_contract():
    f = evaluate(source(target="资产减值损失 -3.00 -2.00"))["field"]
    assert f["current_amount_yuan"] == "-3.00"
    assert not f["losses_with_minus_caption_bound"]
    assert f["loss_burden_sign_mapping"] == "unknown"


def test_positive_recovery_preserved_and_dash_not_zero():
    f = evaluate(source(target='资产减值损失（损失以“-”号填列） 3.00 -2.00'))["field"]
    assert f["current_amount_yuan"] == "3.00"
    f = evaluate(source(target='资产减值损失（损失以“-”号填列） — -2.00'))["field"]
    assert f["status"] == "pending"
    assert f["current_amount_yuan"] is None
