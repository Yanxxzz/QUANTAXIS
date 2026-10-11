"""Synthetic only; no original report/native/capture consumption."""
import importlib.util
from pathlib import Path
import sys
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ar_structure as a


HEADER = "类别\n期末余额 期初余额\n账面余额 坏账准备 账面价值\n账面余额 坏账准备 账面价值\n金额 比例 金额 计提比例 金额 比例 金额 计提比例\n"
ROW = "合计 1,200.00 100.00% 200.00 16.67% 1,000.00 1,000.00 100.00% 100.00 10.00% 900.00\n"
CAPTION = "（2）按坏账计提方法分类披露\n单位：人民币元\n"


def text(body=HEADER+ROW, chapter="七、合并财务报表项目注释", suffix=""):
    return ("===SOURCE_PAGE:1===\n合成公司 2025年年度报告\n===SOURCE_PAGE:2===\n" + chapter + "\n4、应收账款\n" + CAPTION + body +
            "（3）本期坏账准备情况\n5、应收款项融资\n6、预付款项\n" + suffix + "八、研发支出\n")


def test_colon_with_trailing_label_cuts_detail_totals_not_whole_total():
    t = text(HEADER+ROW+"按单项计提坏账准备：单项计提\n名称\n合计 999.00\n按组合计提坏账准备：组合名称\n合计 888.00\n")
    got = a.consume_pending_ar(t)
    assert got["AR"]["complete_unique_closing_opening_stock"]
    assert got["AR"]["classification"]["end"] == t.index("按单项计提坏账准备：")
    assert len(got["AR"]["stock"][0]["cells"]) == 10


def test_colon_with_trailing_amount_does_not_read_or_include_detail_total():
    t = text(HEADER+ROW+"按组合计提坏账准备：999.00元\n类别\n合计 999.00\n")
    got = a.consume_pending_ar(t)
    assert got["AR"]["complete_unique_closing_opening_stock"]
    assert got["AR"]["classification"]["end"] == t.index("按组合计提坏账准备：")


def test_legal_no_colon_category_data_preserved():
    got = a.consume_pending_ar(text(HEADER+"按组合计提坏账准备\n其中某组\n"+ROW))
    assert got["AR"]["complete_unique_closing_opening_stock"]


def test_no_colon_subtitle_before_header_still_rejected():
    got = a.consume_pending_ar(text("按组合计提坏账准备\n"+HEADER+ROW))
    assert got["AR"]["pending_reasons"] == ["partial_population_subtitle_before_whole_header_pending"]


def test_no_colon_new_detail_table_cannot_supply_missing_whole_opening():
    closing = "类别\n期末余额\n账面余额 坏账准备 账面价值\n金额 比例 金额 计提比例\n合计 1200.00 100.00% 200.00 16.67% 1000.00\n"
    opening = closing.replace("期末余额", "期初余额")
    got = a.consume_pending_ar(text(closing+"按组合计提坏账准备\n"+opening))
    assert got["AR"]["complete_unique_closing_opening_stock"] is False
    assert [p for table in got["AR"]["stock"] for p in table["period_roles"]] == ["closing"]


def test_inline_complete_chapter_after_full_stop_supported_not_reference():
    got = a.consume_pending_ar(text(chapter="适用上述优惠。 七、合并财务报表项目注释"))
    assert got["AR"]["complete_unique_closing_opening_stock"]
    with pytest.raises(a.base.SourcePending, match="unique_consolidated"):
        a.consume_pending_ar(text(chapter="详见七、合并财务报表项目注释"))


def test_nearest_semantic_chapter_ignores_later_same_numeral_table_line():
    t = text()+"现金流量表\n八、净利润 123.00 456.00\n"
    note, chapter = a.ar_note_range(t)
    assert chapter["end"]["start"] == t.index("八、研发支出")
    assert note["end"] == t.index("5、应收款项融资")


def test_unknown_same_numeral_before_known_chapter_is_not_skipped():
    with pytest.raises(a.base.SourcePending, match="first_next_semantic"):
        a.ar_note_range(text(suffix="八、未知项目\n"))


def test_nearest_account_successor_not_later_repeated_same_number():
    t = text(suffix="20、其他流动资产\n5、另一个段落\n")
    note, _ = a.ar_note_range(t)
    assert note["end"] == t.index("5、应收款项融资")


def test_unknown_bare_heading_before_successor_not_swallowed():
    t = text().replace("5、应收款项融资", "9、未知子标题\n5、应收款项融资")
    with pytest.raises(a.base.SourcePending, match="nearest_next_bare_parent_ordinal"):
        a.ar_note_range(t)


def test_no_duplicate_chapter_or_missing_title_year_fallback():
    with pytest.raises(a.base.SourcePending, match="unique_consolidated"):
        a.ar_note_range(text()+"七、合并财务报表项目注释\n")
    with pytest.raises(a.base.SourcePending, match="current_annual_year_pending"):
        a.consume_pending_ar(text().replace("合成公司 2025年年度报告", "合成公司\n年度报告全文"))


def test_stock_percent_and_sparse_roles_unchanged_no_arithmetic_completion():
    broken = ROW.replace("200.00 16.67% ", "")
    got = a.consume_pending_ar(text(HEADER+broken))
    assert got["AR"]["complete_unique_closing_opening_stock"] is False
    assert got["AR"]["pending_reasons"] == ["sparse_or_misaligned_stock_cells_pending"]


def test_wrapped_total_valid_fragment_and_ambiguous_cell_stay_distinct():
    good = a.consume_pending_ar(text(HEADER+ROW.replace("1,200.00", "1,\n200.00")))
    assert good["AR"]["complete_unique_closing_opening_stock"]
    bad = a.consume_pending_ar(text(HEADER+ROW.replace("1,200.00", "1\n200.00")))
    assert bad["AR"]["pending_reasons"] == ["integer_or_wrapped_cell_boundary_unknown"]


def test_unbound_inherited_report_literal_stays_pending():
    t = text(); report = a.base.report_contract(t, 2025)
    report["annual_title"]["span_sha256"] = "0" * 64
    with pytest.raises(a.base.SourcePending, match="inherited_report_literal"):
        a.consume_pending_ar(t, report=report)
