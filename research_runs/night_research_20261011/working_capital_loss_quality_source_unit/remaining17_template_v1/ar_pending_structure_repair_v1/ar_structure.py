"""Pending AR geometry overlay; the frozen f7b9 stock grammar stays unchanged."""
from __future__ import annotations
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_templates as base


# A complete heading may follow a completed prose sentence on the same line.
# A cross-reference without a sentence boundary, TOC suffix or ambiguous second
# heading is never accepted by this additional spelling.
INLINE_SCOPE = re.compile(r"(?m)(?:^|(?<=。))[ \t]*(?P<ordinal>[一二三四五六七八九十]+)[、.．][ \t]*合并财务报表项目注释[ \t]*$")
DETAIL_WITH_COLON = re.compile(r"(?m)^[ \t]*按(?:单项|组合)计提[^\n]*[:：][^\n]*$")
STANDALONE_DETAIL = re.compile(r"(?m)^[ \t]*按(?:单项|组合)计提[^\n]*$")


def ar_note_range(text):
    parents = list(INLINE_SCOPE.finditer(text))
    if len(parents) != 1:
        raise base.SourcePending("unique_consolidated_note_chapter_pending")
    parent = parents[0]
    if parent["ordinal"] not in base.ORDINALS[:-1]:
        raise base.SourcePending("chapter_ordinal_pending")
    ordinal = base.ORDINALS[base.ORDINALS.index(parent["ordinal"]) + 1]
    successors = [h for h in base.CHAPTER.finditer(text, parent.end()) if h["ordinal"] == ordinal]
    if not successors or successors[0]["title"].strip() not in base.CHAPTER_TITLES:
        raise base.SourcePending("first_next_semantic_chapter_ordinal_pending")
    # Later report/table lines with the same numeral are outside this complete
    # nearest known chapter boundary. An earlier unknown same numeral blocks.
    end_chapter = successors[0]
    headings = list(base.PARENT_NOTE.finditer(text, parent.end(), end_chapter.start()))
    hits = [h for h in headings if h["title"].strip() == "应收账款"]
    if len(hits) != 1:
        raise base.SourcePending("unique_bare_parent_AR_caption_pending")
    h = hits[0]
    following = [k for k in headings if k.start() > h.start()]
    if not following or int(following[0]["ordinal"]) != int(h["ordinal"]) + 1:
        raise base.SourcePending("nearest_next_bare_parent_ordinal_pending")
    nxt = following[0]
    if nxt["title"].strip() not in base.ACCOUNT_TITLES:
        raise base.SourcePending("nearest_next_bare_parent_semantic_caption_pending")
    note = {"status": "bounded_consolidated_asset_note", "start": h.start(), "end": nxt.start(),
            "parent": base.locator(text, h.start(), h.end()),
            "next_parent": base.locator(text, nxt.start(), nxt.end()),
            "range": base.locator(text, h.start(), nxt.start())}
    chapter = {"start": base.locator(text, parent.start(), parent.end()),
               "end": base.locator(text, end_chapter.start(), end_chapter.end()),
               "boundary_method": "unique_exact_complete_heading_and_first_expected_semantic_successor"}
    return note, chapter


def whole_ar_classification(text, note):
    children = list(base.CHILD_NOTE.finditer(text, note["start"], note["end"]))
    aliases = {"按坏账计提方法分类披露", "应收账款按坏账计提方法分类披露", "应收账款分类披露"}
    hits = [h for h in children if h["title"].strip().rstrip("：:") in aliases]
    if len(hits) != 1:
        raise base.SourcePending("whole_classification_bracket_caption_pending")
    h = hits[0]
    later = [k for k in children if k.start() > h.start()]
    end = later[0].start() if later else note["end"]
    header = base.TABLE_START.search(text, h.end(), end)
    before_header_end = header.start() if header else end
    if STANDALONE_DETAIL.search(text, h.end(), before_header_end):
        raise base.SourcePending("partial_population_subtitle_before_whole_header_pending")
    # A printed explicit single/combination detail caption cuts the whole
    # classification even when an amount/name follows the colon on that line.
    # It does not assign any role or inspect those amount/name tokens.
    detail = DETAIL_WITH_COLON.search(text, before_header_end, end)
    if detail:
        end = detail.start()
    # Preserve f7b9-v2: a no-colon category row inside the full table is legal,
    # but a separately titled new table cannot provide the whole opening block.
    for candidate in STANDALONE_DETAIL.finditer(text, before_header_end, end):
        new_header = base.TABLE_START.search(text, candidate.end(), end)
        if new_header and not base.TOTAL.search(text, candidate.end(), new_header.start()):
            end = candidate.start(); break
    other_detail = base.PARTIAL_CAPTION.search(text, h.end(), end)
    if other_detail:
        end = other_detail.start()
    return h.start(), end


def inherited_report(text, report, year):
    if report is None:
        # Only a previously uncompleted report-level binding may use this.
        # No separated first-page year/title grammar is introduced here.
        return base.report_contract(text, year)
    if report.get("year") != year:
        raise base.SourcePending("inherited_report_year_binding_changed")
    for key in ("annual_title", "annual_fiscal_policy", "presentation_CNY"):
        loc = report.get(key)
        if loc and base.locator(text, loc["start"], loc["end"])["span_sha256"] != loc["span_sha256"]:
            raise base.SourcePending("inherited_report_literal_binding_changed:" + key)
    return report


def consume_pending_ar(text, year=2025, report=None):
    """Only one unfinished AR: original amount/header/percent/precision logic."""
    report = inherited_report(text, report, year)
    note, chapter = ar_note_range(text)
    result = {"note_geometry": note, "stock": [], "pending_reasons": [],
              "movement": {"status": "unparsed_pending_independent_of_stock", "amounts_solved": 0},
              "complete_unique_closing_opening_stock": False,
              "comparability_policy_scope_version": "unreviewed_pending"}
    try:
        a, b = whole_ar_classification(text, note)
        result["classification"] = base.locator(text, a, b)
        heads = list(base.TABLE_START.finditer(text, a, b))
        if not heads:
            raise base.SourcePending("whole_stock_table_header_missing_pending")
        tables = []; unit = None
        for i, header in enumerate(heads):
            stop = heads[i + 1].start() if i + 1 < len(heads) else b
            t = base.table(text, a, b, header.start(), stop, "AR", unit, report)
            unit = t["unit"]; tables.append(t); result["stock"] = tables.copy()
        if sorted(period for table in tables for period in table["period_roles"]) != ["closing", "opening"]:
            raise base.SourcePending("complete_unique_closing_opening_blocks_pending")
        result["complete_unique_closing_opening_stock"] = True
    except base.SourcePending as error:
        result["pending_reasons"].append(str(error))
    return {"status": "source_only_pending_AR_structure_repair_facts", "report": report,
            "chapter": chapter, "AR": result, "INV_or_first3_or_old17_replayed": False,
            "financial_qualification": False, "Q_cash_release_or_loss_cause": "not_computed_or_inferred"}
