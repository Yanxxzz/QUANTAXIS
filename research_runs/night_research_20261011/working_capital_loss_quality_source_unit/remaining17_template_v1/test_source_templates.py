"""Synthetic contracts only; no captures, actual report text or market inputs."""
import importlib.util
from pathlib import Path
import sys
import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("remaining17_templates_under_test", HERE / "source_templates.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)

AR = """类别
期末余额 期初余额
账面余额 坏账准备 账面价值
账面余额 坏账准备 账面价值
金额 比例 金额 计提比例 金额 比例 金额 计提比例
"""
INV = """项目
期末余额 期初余额
账面余额 存货跌价准备或合同履约成本减值准备 账面价值
账面余额 存货跌价准备或合同履约成本减值准备 账面价值
"""
AR_C = "类别\n期末余额\n账面余额 坏账准备\n账面价值\n金额 比例 金额 计提比例\n"
AR_O = AR_C.replace("期末余额", "期初余额")
AR_ROW = "合计 1,200.00 100.00% 200.00 16.67% 1,000.00 900.00 100.00% 100.00 11.11% 800.00\n"
INV_ROW = "合计 700.00 50.00 650.00 600.00 40.00 560.00\n"


def frame(ar=None, inv=None, currency=True, calendar=True, unit="单位：元", title="2025 年年度报告"):
    ar = ar if ar is not None else unit + "\n" + AR + "按组合计提坏账准备\n" + AR_ROW
    inv = inv if inv is not None else unit + "\n" + INV + INV_ROW
    return ("===SOURCE_PAGE:1===\n合成股份有限公司\n" + title + "\n"
            + "===SOURCE_PAGE:2===\n"
            + ("会计年度自公历1月1日至12月31日止。\n" if calendar else "")
            + ("本集团编制合并财务报表采用的货币为人民币。\n" if currency else "")
            + "五、合并财务报表项目注释\n4、应收账款\n（1）按账龄披露\n一、子项\n七.30）\n"
            + "（2）按坏账计提方法分类披露\n" + ar
            + "（3）本期计提、收回或转回的坏账准备情况\n未解析滚动列。\n"
            + "5、合同资产\n8、存货\n（1）存货分类\n" + inv
            + "（2）存货跌价准备和合同履约成本减值准备\n滚动列未知。\n"
            + "9、其他流动资产\n六、研发支出\n七、合并范围的变更\n")


def ar_stock(doc):
    return t.consume(doc)["assets"]["AR"]


def test_both_templates_keep_percent_slots_and_independent_movement_pending():
    got = t.consume(frame())
    ar = got["assets"]["AR"]; inv = got["assets"]["INV"]
    assert ar["complete_unique_closing_opening_stock"] and inv["complete_unique_closing_opening_stock"]
    assert ar["stock"][0]["percentage_indices_preserved"] == [1, 3, 6, 8]
    assert len(ar["stock"][0]["cells"]) == 10
    assert inv["stock"][0]["allowance_population"].endswith("combined")
    assert ar["movement"]["status"] == "unparsed_pending_independent_of_stock"
    assert ar["comparability_policy_scope_version"] == "unreviewed_pending"


def test_parent_chapter_ignores_child_chinese_heading_and_inline_reference():
    got = t.consume(frame())
    assert got["chapter"]["end"]["start"] == frame().index("六、研发支出")
    assert got["assets"]["AR"]["note_geometry"]["end"] == frame().index("5、合同资产")


def test_unknown_actual_next_chapter_is_pending_not_skipped():
    with pytest.raises(t.SourcePending, match="next_semantic_chapter"):
        t.consume(frame().replace("六、研发支出", "六、未解释章节"))


def test_unknown_bare_heading_before_asset_successor_is_pending():
    got = ar_stock(frame().replace("（1）按账龄披露", "1、未知裸子层\n（1）按账龄披露"))
    assert got["stock"] == []
    assert "unclassified_bare_heading" in got["pending_reasons"][0]


def test_same_ordinal_unrecognized_child_cannot_masquerade_as_asset_successor():
    got = ar_stock(frame().replace("5、合同资产", "5、未解释子层"))
    assert got["stock"] == []
    assert "next_bare_parent_semantic_caption_pending" in got["pending_reasons"]


def test_group_caption_cannot_substitute_whole_asset_classification():
    ar = "按组合计提坏账准备：\n单位：元\n" + AR + AR_ROW
    got = ar_stock(frame(ar=ar))
    assert not got["complete_unique_closing_opening_stock"]
    assert got["stock"] == []


def test_unpunctuated_group_subtitle_before_header_cannot_supply_whole_total():
    ar = "按组合计提坏账准备\n单位：元\n" + AR + AR_ROW
    got = ar_stock(frame(ar=ar))
    assert got["stock"] == []
    assert "partial_population_subtitle_before_whole_header_pending" in got["pending_reasons"]


def test_group_opening_subtable_cannot_complete_independent_whole_closing_block():
    ar = ("单位：元\n" + AR_C + "合计 1,200.00 100.00% 200.00 16.67% 1,000.00\n"
          + "按组合计提坏账准备\n单位：元\n" + AR_O
          + "合计 900.00 100.00% 100.00 11.11% 800.00\n")
    got = ar_stock(frame(ar=ar))
    assert len(got["stock"]) == 1  # Keep valid whole closing facts.
    assert not got["complete_unique_closing_opening_stock"]
    assert got["stock"][0]["period_roles"] == ["closing"]


def test_legitimate_group_category_before_whole_total_does_not_cut_next_opening_block():
    ar = ("单位：元\n" + AR_C + "按组合计提坏账准备\n"
          + "合计 1,200.00 100.00% 200.00 16.67% 1,000.00\n"
          + AR_O + "合计 900.00 100.00% 100.00 11.11% 800.00\n")
    assert ar_stock(frame(ar=ar))["complete_unique_closing_opening_stock"]


def test_data_category_group_words_are_not_subgroup_caption():
    assert ar_stock(frame())["complete_unique_closing_opening_stock"]


def test_duplicate_total_pending_even_if_both_reconcile():
    got = ar_stock(frame(ar="单位：元\n" + AR + AR_ROW + AR_ROW))
    assert "unique_whole_classification_total_pending" in got["pending_reasons"]


def test_percentage_missing_or_reordered_never_stripped_to_fit():
    for row in [AR_ROW.replace("16.67% ", ""), AR_ROW.replace("200.00 16.67%", "16.67% 200.00")]:
        got = ar_stock(frame(ar="单位：元\n" + AR + row))
        assert got["stock"] == []
        assert "sparse_or_misaligned_stock_cells_pending" in got["pending_reasons"]


def test_reordered_money_header_not_inferred_from_numbers():
    wrong = AR.replace("账面余额 坏账准备", "坏账准备 账面余额")
    got = ar_stock(frame(ar="单位：元\n" + wrong + AR_ROW))
    assert "stock_header_shape_roles_pending" in got["pending_reasons"]


def test_inv_sparse_unknown_does_not_discard_independent_ar_stock():
    row = "合计 700.00 50.00 650.00 600.00 600.00\n"
    got = t.consume(frame(inv="单位：元\n" + INV + row))
    assert got["assets"]["AR"]["complete_unique_closing_opening_stock"]
    assert got["assets"]["INV"]["stock"] == []
    assert "sparse_or_misaligned_stock_cells_pending" in got["assets"]["INV"]["pending_reasons"]


def test_unique_cell_wrap_preserves_bound_span_without_joining_adjacent_cells():
    row = AR_ROW.replace("1,200.00", "1,\n200.00")
    doc = frame(ar="单位：元\n" + AR + row)
    cell = ar_stock(doc)["stock"][0]["cells"][0]
    assert cell["printed"] == "1,200.00"
    assert doc[cell["cell"]["start"]:cell["cell"]["end"]] == "1,\n200.00"


def test_proved_multilevel_interleaved_value_header_and_required_decimal_continuation():
    header = ("类别\n期末余额 期初余额\n账面余额 坏账准备 账面价\n值\n"
              "账面余额 坏账准备 账面价\n金额 比例 金额 计提比 值 例 金额 比例 金额 计提比\n例\n")
    row = AR_ROW.replace("1,200.00", "1,2\n00.\n00")
    got = ar_stock(frame(ar="单位：元\n" + header + row))
    assert got["complete_unique_closing_opening_stock"]
    assert got["stock"][0]["cells"][0]["printed"] == "1,200.00"


def test_ambiguous_integer_wrap_stays_pending_without_using_reconciliation():
    row = AR_ROW.replace("1,200.00", "1\n200.00")
    got = ar_stock(frame(ar="单位：元\n" + AR + row))
    assert "integer_or_wrapped_cell_boundary_unknown" in got["pending_reasons"]


def test_split_current_opening_tables_each_have_period_header_and_preserve_page():
    ar = ("单位：元\n" + AR_C + "合计 1,200.00 100.00% 200.00 16.67% 1,000.00\n2\n"
          + "===SOURCE_PAGE:3===\n合成股份有限公司2025年年度报告全文\n3\n" + AR_O
          + "合计 900.00 100.00% 100.00 11.11% 800.00\n")
    got = ar_stock(frame(ar=ar))
    assert got["complete_unique_closing_opening_stock"]
    assert [s["period_roles"] for s in got["stock"]] == [["closing"], ["opening"]]
    assert got["stock"][1]["header"]["source_page"] == 3
    assert all(s["percentage_indices_preserved"] == [1, 3] for s in got["stock"])


def test_duplicate_period_retains_bound_printed_blocks_but_not_complete_stock():
    ar = "单位：元\n" + AR_C + "合计 1,200.00 100.00% 200.00 16.67% 1,000.00\n" + AR_C + "合计 900.00 100.00% 100.00 11.11% 800.00\n"
    got = ar_stock(frame(ar=ar))
    assert len(got["stock"]) == 2
    assert not got["complete_unique_closing_opening_stock"]


def test_global_presentation_missing_keeps_printed_stock_and_currency_pending():
    got = ar_stock(frame(currency=False))
    assert got["complete_unique_closing_opening_stock"]
    stock = got["stock"][0]
    assert stock["status"] == "printed_stock_roles_bound_normalization_pending"
    assert stock["cells"][0]["amount_yuan"] is None


@pytest.mark.parametrize("unit", ["单位：人民币元", "单位：元 币种：人民币"])
def test_explicit_local_CNY_does_not_need_specific_global_presentation_wording(unit):
    stock = ar_stock(frame(currency=False, unit=unit))["stock"][0]
    assert stock["CNY_normalization_status"] == "bound_from_local_declaration"
    assert stock["cells"][0]["amount_yuan"] == "1200.00"


def test_mother_functional_currency_not_group_presentation():
    doc = frame(currency=False).replace("五、合并", "母公司的记账本位币为人民币。\n五、合并")
    assert ar_stock(doc)["stock"][0]["CNY_normalization_status"] == "presentation_currency_pending"


def test_calendar_missing_keeps_literal_period_roles_without_claimed_printed_dates():
    got = t.consume(frame(calendar=False))
    assert got["report"]["annual_calendar_status"] == "calendar_policy_pending"
    assert got["assets"]["AR"]["stock"][0]["literal_calendar_dates_printed"] is False


def test_arithmetic_is_diagnostic_does_not_reassign_or_correct_values():
    row = AR_ROW.replace("1,000.00", "990.00")
    stock = ar_stock(frame(ar="单位：元\n" + AR + row))["stock"][0]
    check = stock["stock_display_reconciliations"][0]
    assert check["status"] == "source_arithmetic_conflict_pending"
    assert check["values_reassigned_or_corrected"] is False
    assert stock["cells"][4]["printed"] == "990.00"


def test_source_year_title_optional_year_marker_and_linewrap_are_literal():
    assert t.consume(frame(title="2025\n年度报告"))["report"]["year"] == 2025


@pytest.mark.parametrize("title", ["2024年年度报告", "2025年年度报告摘要", "2025年年度报告目录", "2025年年度报告\n2024年年度报告"])
def test_wrong_conflicting_summary_contents_firstpage_never_metadata_year_fallback(title):
    with pytest.raises(t.SourcePending):
        t.consume(frame(title=title))
