"""Three bounded annual stock-note templates; unfamiliar evidence stays pending.

No issuer offsets, arithmetic column inference, sparse-cell completion,
financial eligibility, loss attribution, cash inference or factor definition.
"""
from __future__ import annotations
from decimal import Decimal
import hashlib
import re


class SourcePending(ValueError):
    pass


def dense(text):
    return re.sub(r"\s+", "", text)


def locator(text, start, end):
    pages = re.findall(r"===SOURCE_PAGE:(\d+)===" , text[:start])
    return {"start": start, "end": end, "source_page": int(pages[-1]) if pages else None,
            "span_sha256": hashlib.sha256(text[start:end].encode()).hexdigest()}


ORDINALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二",
            "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]
CHAPTER_TITLES = {"研发支出", "合并范围的变更", "在其他主体中的权益", "政府补助", "与金融工具相关的风险",
                  "公允价值的披露", "关联方及关联交易", "股份支付", "承诺及或有事项", "资产负债表日后事项",
                  "其他重要事项", "母公司财务报表主要项目注释", "补充资料"}
SCOPE = re.compile(r"(?m)^[ \t]*(?P<ordinal>[一二三四五六七八九十]+)[、.．][ \t]*合并财务报表项目注释[ \t]*$")
CHAPTER = re.compile(r"(?m)^[ \t]*(?P<ordinal>[一二三四五六七八九十]+)[、.．][ \t]*(?P<title>[^\d\s][^\n]{0,65})$")
PARENT_NOTE = re.compile(r"(?m)^[ \t]*(?P<ordinal>\d+)[、.．][ \t]*(?P<title>[^\d\s][^\n]{0,65})$")
ACCOUNT_TITLES = {"货币资金", "交易性金融资产", "应收票据", "应收账款", "应收款项融资", "预付款项", "其他应收款",
                  "存货", "合同资产", "持有待售资产", "一年内到期的非流动资产", "其他流动资产", "债权投资", "其他债权投资",
                  "长期应收款", "长期股权投资", "其他权益工具投资", "其他非流动金融资产", "投资性房地产", "固定资产",
                  "在建工程", "生产性生物资产", "油气资产", "使用权资产", "无形资产", "开发支出", "商誉",
                  "长期待摊费用", "递延所得税资产", "其他非流动资产"}
CHILD_NOTE = re.compile(r"(?m)^[ \t]*[（(](?P<ordinal>\d+)[）)][ \t]*(?P<title>[^\n]+)$")
PARTIAL_CAPTION = re.compile(r"(?m)^[ \t]*(?:按(?:单项|组合)计提[^\n]*[:：]|[（(][a-zA-Z][）)][^\n]*)[ \t]*$")
TABLE_START = re.compile(r"(?m)^[ \t]*(?:类别|项目)[ \t]*$")
TOTAL = re.compile(r"(?m)^[ \t]*合\s*计(?=[ \t\n]|$)[^\n]*$")
AMOUNT = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}")
PERCENT = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}%")
INTEGER = re.compile(r"\d+|\d{1,3}(?:,\d{3})+")

# Whitespace variations are permitted; character/role order is immutable.
AR_BOTH = {
    "类别期末余额期初余额账面余额坏账准备账面价值账面余额坏账准备账面价金额比例金额计提比值例金额比例金额计提比例",
    "类别期末余额期初余额账面余额坏账准备账面价值账面余额坏账准备账面价值金额比例金额计提比例金额比例金额计提比例",
}
AR_ONE = {"类别" + period + "账面余额坏账准备账面价值金额比例金额计提比例": period
          for period in ("期末余额", "期初余额")}
INV_BOTH = "项目期末余额期初余额账面余额存货跌价准备或合同履约成本减值准备账面价值账面余额存货跌价准备或合同履约成本减值准备账面价值"


def report_contract(text, year):
    """Native first-page title plus annual policy and report presentation currency."""
    markers = list(re.finditer(r"===SOURCE_PAGE:(\d+)===" , text))
    if not markers or markers[0][1] != "1":
        raise SourcePending("native_first_page_marker_pending")
    end = markers[1].start() if len(markers) > 1 else len(text)
    first = text[markers[0].end():end]
    if re.search(r"年度报告\s*(?:摘要|工作制度)|目录", first):
        raise SourcePending("native_first_page_summary_or_contents_pending")
    titles = list(re.finditer(r"(?m)^[ \t]*(?:[^\n\d]{0,70}[ \t]+)?(?P<year>20\d{2})\s*年?\s*年度报告(?:全文)?[ \t]*$", first))
    if not titles or {int(h["year"]) for h in titles} != {year}:
        raise SourcePending("native_first_page_current_annual_year_pending")
    # Repeated running/cover titles for the same year bind one identity; no
    # different-year title, table of contents or metadata-year substitution.
    title = titles[-1]
    title_loc = locator(text, markers[0].end() + title.start(), markers[0].end() + title.end())
    annuals = list(re.finditer(r"会计年度[^。\n]{0,100}(?:1\s*月\s*1\s*日)[^。\n]{0,50}(?:12\s*月\s*31\s*日)[^。\n]{0,15}[。.]?", text))
    # An explicitly wrapped '会计\n年度' policy is also a literal annual clause.
    annuals += list(re.finditer(r"会计\s*\n\s*年度[^。\n]{0,100}1\s*月\s*1\s*日[^。\n]{0,50}12\s*月\s*31\s*日[^。\n]{0,15}[。.]?", text))
    currency = list(re.finditer(r"(?:本集团编制合并财务报表采用的货币为人民币[。.]|本公司编制本财务报表时所采用的货币为人民币[。.]|[（(]除特别注明外[，,]金额单位为人民币元[）)])", text))
    # Calendar policy and currency are independent from a bound annual identity
    # and literal closing/opening roles. Missing wording retains printed facts.
    return {"year": year, "annual_title": title_loc,
            "annual_fiscal_policy": locator(text, annuals[0].start(), annuals[0].end()) if annuals else None,
            "annual_calendar_status": "literal_policy_bound" if annuals else "calendar_policy_pending",
            "presentation_CNY": locator(text, currency[0].start(), currency[0].end()) if currency else None,
            "date_method": "annual_FY_closing_and_fiscal_opening_roles_not_literal_printed_dates",
            "closing_fiscal_role": f"FY{year}_closing", "opening_fiscal_role": f"FY{year}_opening"}


def note_ranges(text):
    parents = list(SCOPE.finditer(text))
    if len(parents) != 1:
        raise SourcePending("unique_consolidated_note_chapter_pending")
    parent = parents[0]
    if parent["ordinal"] not in ORDINALS[:-1]:
        raise SourcePending("chapter_ordinal_pending")
    next_ordinal = ORDINALS[ORDINALS.index(parent["ordinal"]) + 1]
    candidates = [h for h in CHAPTER.finditer(text, parent.end()) if h["ordinal"] == next_ordinal]
    if len(candidates) != 1 or candidates[0]["title"].strip() not in CHAPTER_TITLES:
        raise SourcePending("unique_next_semantic_chapter_ordinal_pending")
    end = candidates[0].start()
    headings = list(PARENT_NOTE.finditer(text, parent.end(), end))
    result = {}
    for asset, title in (("AR", "应收账款"), ("INV", "存货")):
        hits = [h for h in headings if h["title"].strip() == title]
        if len(hits) != 1:
            result[asset] = {"status": "pending", "reason": "unique_bare_parent_asset_caption_pending"}; continue
        h = hits[0]
        following = [k for k in headings if k.start() > h.start() and int(k["ordinal"]) == int(h["ordinal"]) + 1]
        if len(following) != 1:
            result[asset] = {"status": "pending", "reason": "unique_next_bare_parent_ordinal_pending"}; continue
        if following[0]["title"].strip() not in ACCOUNT_TITLES:
            result[asset] = {"status": "pending", "reason": "next_bare_parent_semantic_caption_pending"}; continue
        # A competing bare parent-style heading before the chosen successor is
        # unknown, never silently swallowed as a bracket child or data row.
        if any(k.start() > h.start() and k.start() < following[0].start() for k in headings):
            result[asset] = {"status": "pending", "reason": "unclassified_bare_heading_inside_asset_note_pending"}; continue
        result[asset] = {"status": "bounded_consolidated_asset_note", "start": h.start(), "end": following[0].start(),
                         "parent": locator(text, h.start(), h.end()),
                         "next_parent": locator(text, following[0].start(), following[0].end()),
                         "range": locator(text, h.start(), following[0].start())}
    return result, {"start": locator(text, parent.start(), parent.end()),
                    "end": locator(text, candidates[0].start(), candidates[0].end())}


def classification(text, note, asset):
    children = list(CHILD_NOTE.finditer(text, note["start"], note["end"]))
    aliases = ({"按坏账计提方法分类披露", "应收账款按坏账计提方法分类披露", "应收账款分类披露"}
               if asset == "AR" else {"存货分类"})
    hits = [h for h in children if h["title"].strip().rstrip("：:") in aliases]
    if len(hits) != 1:
        raise SourcePending("whole_classification_bracket_caption_pending")
    h = hits[0]
    following = [k for k in children if k.start() > h.start()]
    end = following[0].start() if following else note["end"]
    first_header = TABLE_START.search(text, h.end(), end)
    before_header_end = first_header.start() if first_header else end
    # A standalone single/combination subtitle before any whole-table header
    # remains a subgroup even when the publisher omits its colon. Identical
    # words after a recognized whole header may be legitimate category rows.
    subtitle = re.compile(r"(?m)^[ \t]*按(?:单项|组合)计提[^\n]*[ \t]*$")
    if subtitle.search(text, h.end(), before_header_end):
        raise SourcePending("partial_population_subtitle_before_whole_header_pending")
    for candidate in subtitle.finditer(text, before_header_end, end):
        new_header = TABLE_START.search(text, candidate.end(), end)
        if new_header and not TOTAL.search(text, candidate.end(), new_header.start()):
            # A new header before any intervening whole-table total makes this
            # a separately titled detail table. A valid category row before
            # its own whole total remains inside the whole table.
            end = candidate.start(); break
    subgroup = PARTIAL_CAPTION.search(text, h.end(), end)
    if subgroup:
        end = subgroup.start()
    return h.start(), end


def header_kind(raw, asset):
    d = dense(raw)
    if asset == "AR":
        if d in AR_BOTH:
            return "AR_both", ["closing", "opening"]
        if d in AR_ONE:
            return "AR_one", ["closing" if AR_ONE[d] == "期末余额" else "opening"]
    elif d == INV_BOTH:
        return "INV_both", ["closing", "opening"]
    raise SourcePending("unrecognized_or_reordered_stock_header_pending")


def tokens(text, start, end):
    """Unique lexical fragments only. Never choose a split to fit a slot count."""
    parts = list(re.finditer(r"\S+", text[start:end]))
    result = []; i = 0
    while i < len(parts):
        first = i; joined = parts[i].group().replace("，", ",")
        while not AMOUNT.fullmatch(joined) and not PERCENT.fullmatch(joined):
            if i + 1 >= len(parts):
                raise SourcePending("unfinished_printed_cell_pending")
            next_piece = parts[i + 1].group().replace("，", ",")
            # An unpunctuated whole integer can be a separate monetary cell.
            # Only a syntactically incomplete comma/decimal fragment or a next
            # fragment that cannot stand alone gives a unique continuation.
            if (INTEGER.fullmatch(joined) and not next_piece.startswith((".", ","))):
                raise SourcePending("integer_or_wrapped_cell_boundary_unknown")
            if not re.fullmatch(r"[\d,.%]+", joined + next_piece):
                raise SourcePending("blank_dash_or_nonmonetary_cell_pending")
            if (AMOUNT.fullmatch(next_piece) or PERCENT.fullmatch(next_piece)) and not (
                    joined.endswith((",", ".")) or not INTEGER.fullmatch(joined)):
                raise SourcePending("adjacent_cell_join_ambiguous")
            i += 1; joined += next_piece
        # A separately wrapped '%' is necessarily this same cell, not a value.
        if i + 1 < len(parts) and parts[i + 1].group() == "%" and AMOUNT.fullmatch(joined):
            i += 1; joined += "%"
        a, b = start + parts[first].start(), start + parts[i].end()
        if dense(text[a:b]).replace("，", ",") != joined:
            raise SourcePending("cell_literal_binding_pending")
        result.append({"printed": joined, "kind": "percentage" if joined.endswith("%") else "money",
                       "cell": locator(text, a, b), "boundary_method": "whole_token_or_required_lexical_continuation"})
        i += 1
    return result


def table(text, start, end, header_start, next_header_start, asset, inherited_unit=None, report=None):
    rows = list(re.finditer(r"(?m)^.*$", text[header_start:next_header_start]))
    nonempty = [m for m in rows if m.group().strip()]
    # Read the contiguous header until the first data/category line; exact
    # supported header text, not a bag of keywords, assigns all role order.
    recognized = None
    for m in nonempty[:18]:
        b = header_start + m.end()
        try:
            kind, periods = header_kind(text[header_start:b], asset)
            recognized = (kind, periods, b); break
        except SourcePending:
            pass
    if recognized is None:
        raise SourcePending("stock_header_shape_roles_pending")
    kind, periods, hb = recognized
    totals = list(TOTAL.finditer(text, hb, next_header_start))
    if len(totals) != 1:
        raise SourcePending("unique_whole_classification_total_pending")
    total = totals[0]
    following = list(re.finditer(r"(?m)^.*$", text[total.end():next_header_start]))
    row_end = total.end()
    for m in following:
        line = m.group().strip()
        if not line:
            continue
        absolute_start = total.end() + m.start()
        current_pages = re.findall(r"===SOURCE_PAGE:(\d+)===" , text[:absolute_start])
        after = text[total.end() + m.end():next_header_start].lstrip()
        if current_pages and line == current_pages[-1] and after.startswith("===SOURCE_PAGE:"):
            break  # Explicit physical-page footer, not an amount or slot guess.
        if not re.fullmatch(r"[\d,，.%％+()（）−－\- \t]+", line):
            break
        row_end = total.end() + m.end()
    prefix = re.match(r"[ \t]*合\s*计[ \t]*", text[total.start():row_end])
    cells = tokens(text, total.start() + prefix.end(), row_end)
    expected_pct = ([1, 3, 6, 8] if kind == "AR_both" else [1, 3] if kind == "AR_one" else [])
    count = 10 if kind == "AR_both" else 5 if kind == "AR_one" else 6
    if len(cells) != count or [i for i, c in enumerate(cells) if c["kind"] == "percentage"] != expected_pct:
        raise SourcePending("sparse_or_misaligned_stock_cells_pending")
    units = list(re.finditer(r"(?m)^[ \t]*单位[：:][ \t]*(?:人民币)?元[ \t]*(?:[，,]?[ \t]*币种[：:][ \t]*人民币)?[ \t]*$", text[start:header_start]))
    unit = locator(text, start + units[-1].start(), start + units[-1].end()) if units else inherited_unit
    unit_literal = text[unit["start"]:unit["end"]] if unit else ""
    local_CNY = bool(re.search(r"人民币", unit_literal))
    currency_bound = local_CNY or bool(report and report.get("presentation_CNY"))
    if re.search(r"美元|港元|欧元|日元|(?:单位|币种)[：:](?!\s*(?:人民币)?元|\s*人民币)", text[start:next_header_start]):
        raise SourcePending("table_currency_or_unit_override_pending")
    money_indices = [i for i in range(count) if i not in expected_pct]
    role_names = [period + "_" + role for period in periods for role in ("gross", "allowance", "net")]
    for i, role in zip(money_indices, role_names):
        cells[i].update(role=role, printed_decimal=str(Decimal(cells[i]["printed"].replace(",", ""))),
                        amount_yuan=str(Decimal(cells[i]["printed"].replace(",", ""))) if unit and currency_bound else None,
                        printed_quantum="0.01", printed_quantum_yuan="0.01" if unit and currency_bound else None,
                        printed_unit="元" if unit else None, currency="CNY" if currency_bound else None)
    for i in expected_pct:
        cells[i]["role"] = "excluded_percentage"
    checks = []
    for period in periods:
        roles = {c.get("role"): c for c in cells if c["kind"] == "money"}
        gross, allowance, net = [Decimal(roles[period + "_" + r]["printed_decimal"]) for r in ("gross", "allowance", "net")]
        residual = gross - allowance - net
        checks.append({"period_role": period, "residual_at_printed_unit": str(residual),
                       "display_interval_tolerance": "0.015",
                       "status": "source_display_intervals_overlap" if abs(residual) <= Decimal("0.015") else "source_arithmetic_conflict_pending",
                       "values_reassigned_or_corrected": False})
    return {"status": "printed_stock_amount_facts_bound" if unit and currency_bound else "printed_stock_roles_bound_normalization_pending", "asset": asset, "template": kind, "period_roles": periods,
            "header": locator(text, header_start, hb), "row": locator(text, total.start(), row_end),
            "unit": unit, "percentage_indices_preserved": expected_pct, "cells": cells,
            "CNY_normalization_status": "bound_from_local_declaration" if local_CNY else "bound_from_report_presentation" if currency_bound else "presentation_currency_pending",
            "period_policy_status": report.get("annual_calendar_status") if report else "calendar_policy_pending",
            "stock_display_reconciliations": checks,
            "population": locator(text, start, end), "population_kind": "whole_asset_classification_total",
            "allowance_population": "disclosed_inventory_or_contract_fulfillment_cost_combined" if asset == "INV" else "AR_bad_debt_allowance",
            "literal_calendar_dates_printed": False, "cash_or_loss_cause": "not_inferred",
            "financial_eligibility": False, "rollforward_arithmetic_not_used": True}


def consume(text, year=2025):
    report = report_contract(text, year)
    notes, chapter = note_ranges(text)
    assets = {}
    for asset, note in notes.items():
        result = {"note_geometry": note, "stock": [], "pending_reasons": [],
                  "movement": {"status": "unparsed_pending_independent_of_stock", "amounts_solved": 0},
                  "complete_unique_closing_opening_stock": False,
                  "comparability_policy_scope_version": "unreviewed_pending"}
        if note["status"] != "bounded_consolidated_asset_note":
            result["pending_reasons"].append(note["reason"]); assets[asset] = result; continue
        try:
            a, b = classification(text, note, asset)
            heads = list(TABLE_START.finditer(text, a, b))
            if not heads:
                raise SourcePending("whole_stock_table_header_missing_pending")
            bound_tables = []; unit = None
            for i, h in enumerate(heads):
                following = heads[i + 1].start() if i + 1 < len(heads) else b
                t = table(text, a, b, h.start(), following, asset, unit, report)
                unit = t["unit"]; bound_tables.append(t)
                result["stock"] = bound_tables.copy()
            periods = [period for t in bound_tables for period in t["period_roles"]]
            if sorted(periods) != ["closing", "opening"]:
                raise SourcePending("complete_unique_closing_opening_blocks_pending")
            result["stock"] = bound_tables
            result["complete_unique_closing_opening_stock"] = True
        except SourcePending as error:
            result["pending_reasons"].append(str(error))
        assets[asset] = result
    return {"status": "source_only_stock_template_facts", "report": report, "chapter": chapter, "assets": assets,
            "new_factor_or_financial_mask_defined": False, "net_decline_or_cash_release_cause": "not_inferred"}
