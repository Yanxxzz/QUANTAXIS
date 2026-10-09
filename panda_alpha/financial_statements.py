"""Shared original-statement monetary columns and narrow field contracts.

Amounts are source-display values in CNY, not latest vendor snapshots. Explicit
column dates/spans bind comparisons; unknown periods remain pending. Parsers
still own table boundaries, zero proofs, corrections and factor admission.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re

FIELD_CONTRACT_VERSION = 1
AMOUNT_PARSER_VERSION = 2

UNIT_MULTIPLIERS = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000),
                    "百万元": Decimal(1000000), "亿元": Decimal(100000000)}
TABLE_NAMES = {"资产负债表": "balance", "现金流量表": "cashflow", "利润表": "income",
               "所有者权益变动表": "equity"}
PREFIX = r"(?:[（(]?[一二三四五六七八九十\d]+[）)]?\s*[、.．]?\s*)?"
TABLE = re.compile(r"(?m)^\s*" + PREFIX + r"(?P<scope>合并|母公司|本公司|公司|银行)\s*"
                   r"(?P<name>资产负债表|现金流量表|利润表|所有者权益变动表)"
                   r"(?P<continued>[（(]续[）)])?\s*$")
NUMBER = r"[+\-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?"
CELL = re.compile(r"[（(]" + NUMBER + r"[）)]|" + NUMBER + r"|[—–－-]{1,2}|/")
HEX = re.compile(r"[0-9a-f]{64}")


def iso_day(value: str) -> str:
    return date.fromisoformat(str(value)[:10]).isoformat()


def public_availability(published_at: str, not_before_date: str | None = None) -> str:
    result = (date.fromisoformat(iso_day(published_at)) + timedelta(days=1)).isoformat()
    return max(result, iso_day(not_before_date)) if not_before_date else result


def compact_text(text: str) -> str:
    return re.sub(r"[^\S\n]+", "", text.replace("\r\n", "\n"))


def money(value: str) -> Decimal | None:
    value = value.replace(",", "").replace("，", "").replace("−", "-").replace("－", "-")
    if value.startswith(("(", "（")) and value.endswith((")", "）")):
        value = "-" + value[1:-1]
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def amount_text(value: Decimal) -> str:
    return format(value, "f")


def source_page(text: str, offset: int) -> int | None:
    pages = re.findall(r"===SOURCE_PAGE:(\d+)===", text[:offset])
    return int(pages[-1]) if pages else None


def header_period(header: str, kind: str, period: str, source_period_proof: str = "") -> dict:
    # Only table preamble, not arbitrary later prose or notes, binds the column.
    header = re.sub(r"\s+", "", header)
    target = date.fromisoformat(period)
    dates = [(int(a), int(b), int(c)) for a, b, c in re.findall(
        r"(20\d{2})(?:年|-|/)(\d{1,2})(?:月|-|/)(\d{1,2})日?", header)]
    if kind == "balance":
        if dates and dates[0] == (target.year, target.month, target.day):
            comparison = None
            if len(dates) == 2:
                try:
                    candidate = date(*dates[1])
                    if candidate < target:
                        comparison = candidate.isoformat()
                except ValueError:
                    pass
            return {"status": "current_period_verified", "method": "first_table_date", "dates": dates,
                    "comparative_stock_asof": comparison,
                    "comparative_status": "explicit_second_table_date" if comparison else "comparative_period_unbound"}
        if not dates and re.search(r"(?:期末余额|本年年末余额).*(?:期初余额|上年年末余额)", header) and source_period_proof:
            return {"status": "current_period_verified", "method": "current_column_and_original_report_period",
                    "source_period_evidence": source_period_proof, "dates": dates}
        return {"status": "current_period_unbound_or_mismatch", "dates": dates}
    if target.month == 12 and target.day == 31:
        periods = re.findall(r"(20\d{2})年?(?:年度|1[-－—~至]12月|1月1日至12月31日)", header)
    elif target.month == 6 and target.day == 30:
        periods = re.findall(r"(20\d{2})年?(?:半年度|1[-－—~至]6月|1月1日至6月30日)", header)
    else:
        return {"status": "unsupported_nonsemiannual_report_period"}
    if periods and int(periods[0]) == target.year:
        return {"status": "current_period_verified", "method": "first_flow_period", "years": periods}
    if dates and dates[0] == (target.year, target.month, target.day):
        return {"status": "current_period_verified", "method": "explicit_end_date", "dates": dates}
    if len(dates) >= 2 and dates[:2] == [(target.year, 1, 1), (target.year, target.month, target.day)]:
        return {"status": "current_period_verified", "method": "explicit_current_flow_span", "dates": dates}
    if not dates and source_period_proof and re.search(r"(?:本期发生额|本年发生额).*(?:上期发生额|上年发生额)", header):
        return {"status": "current_period_verified", "method": "current_column_and_original_report_period", "source_period_evidence": source_period_proof}
    return {"status": "current_period_unbound_or_mismatch", "years": periods, "dates": dates}


def flow_spans(header: str, period: str, source_period_proof: str = "") -> dict:
    """Bind both flow columns to explicit FY/H1 spans, never assumed prior FY.

    Report-title evidence may bind a generic current column only. It cannot
    supply the comparative column's missing dates or turn a full year into H1.
    """
    current = header_period(header, "income", period, source_period_proof)
    dense = re.sub(r"\s+", "", header)
    target = date.fromisoformat(period)
    if (target.month, target.day) == (12, 31):
        years = re.findall(r"(20\d{2})年?(?:年度|1[-－—~至]12月|1月1日至12月31日)", dense)
    elif (target.month, target.day) == (6, 30):
        years = re.findall(r"(20\d{2})年?(?:半年度|1[-－—~至]6月|1月1日至6月30日)", dense)
    else:
        years = []
    expected_dates = [(target.year, 1, 1), (target.year, target.month, target.day),
                      (target.year - 1, 1, 1), (target.year - 1, target.month, target.day)]
    explicit_dates = current.get("dates", [])
    same_span = ((len(years) == 2 and list(map(int, years)) == [target.year, target.year - 1])
                 or explicit_dates == expected_dates)
    current_verified = current["status"] == "current_period_verified"
    comparison_verified = current_verified and same_span
    return {"current_status": current["status"], "current_evidence": current,
            "current_start": f"{target.year}-01-01" if current_verified else None,
            "current_end": period if current_verified else None,
            "comparative_status": "explicit_prior_same_span" if comparison_verified else "comparative_span_unbound",
            "comparative_start": f"{target.year - 1}-01-01" if comparison_verified else None,
            "comparative_end": f"{target.year - 1}-{target.month:02}-{target.day:02}" if comparison_verified else None,
            "explicit_column_years": years, "explicit_column_dates": explicit_dates}


def printed_quantum_yuan(printed: str | None, printed_unit: str | None) -> str | None:
    """Resolution of the printed decimal, not a financial rounding assertion."""
    value = money(printed) if printed is not None else None
    if value is None or printed_unit not in UNIT_MULTIPLIERS:
        return None
    return amount_text(Decimal(1).scaleb(value.as_tuple().exponent) * UNIT_MULTIPLIERS[printed_unit])


def display_precision_comparison(current_yuan: str, comparative_yuan: str,
                                 current_quantum_yuan: str | None,
                                 comparative_quantum_yuan: str | None) -> dict:
    """Compare source intervals conservatively; unknown precision stays pending."""
    if not current_quantum_yuan or not comparative_quantum_yuan:
        return {"status": "printed_precision_pending", "difference_yuan": None, "tolerance_yuan": None}
    old, new = money(current_yuan), money(comparative_yuan)
    oq, nq = money(current_quantum_yuan), money(comparative_quantum_yuan)
    if any(v is None for v in (old, new, oq, nq)) or oq <= 0 or nq <= 0:
        return {"status": "printed_precision_pending", "difference_yuan": None, "tolerance_yuan": None}
    difference, tolerance = abs(old - new), (oq + nq) / 2
    return {"status": "different_beyond_printed_precision" if difference > tolerance else "overlapping_source_display_intervals",
            "difference_yuan": amount_text(difference), "tolerance_yuan": amount_text(tolerance)}


def normalize_statement_field(evidence: dict, *, measure: str, publication: dict,
                              provenance: dict) -> dict:
    """Expose the small stock/flow field interface consumed by new parsers.

    This is a projection of accepted source evidence, not independent proof of
    original-byte integrity or a complete publication/revision census.
    """
    if measure not in {"stock", "flow"}:
        raise ValueError("Statement measure must be stock or flow")
    cells = evidence.get("cells", evidence.get("source_cells", []))
    coordinate = evidence.get("source_coordinate_proof", {})
    current_printed = coordinate.get("current_printed_amount") if coordinate else (cells[0] if len(cells) == 2 else None)
    comparative_printed = coordinate.get("comparative_printed_amount") if coordinate else (cells[1] if len(cells) == 2 else None)
    unit = evidence.get("printed_unit") or evidence.get("identity_proof", {}).get("printed_unit")
    current_amount = evidence.get("amount_yuan") if measure == "stock" else evidence.get("current_yuan")
    comparison_amount = evidence.get("comparative_amount_yuan") if measure == "stock" else evidence.get("comparative_yuan")
    if measure == "stock":
        current_period = {"stock_asof": evidence.get("stock_asof")}
        comparison_period = {"stock_asof": evidence.get("comparative_stock_asof")}
        comparison_bound = comparison_period["stock_asof"] is not None
    else:
        current_period = {"flow_start": evidence.get("flow_start"), "flow_end": evidence.get("flow_end")}
        comparison_period = {"flow_start": evidence.get("comparative_flow_start"), "flow_end": evidence.get("comparative_flow_end")}
        comparison_bound = bool(evidence.get("comparative_span_verified"))
    current_bound = all(current_period.values())
    column = lambda amount, bound, period, printed: {
        "status": "source_amount_and_period_bound" if amount is not None and bound else "amount_or_period_pending",
        "amount_yuan": amount, **period, "printed_amount": printed,
        "printed_quantum_yuan": printed_quantum_yuan(printed, unit),
    }
    return {"field_contract_version": FIELD_CONTRACT_VERSION, "amount_parser_version": AMOUNT_PARSER_VERSION, "measure": measure,
            "currency": evidence.get("currency"), "printed_unit": unit,
            "source_unit_multiplier": amount_text(UNIT_MULTIPLIERS[unit]) if unit in UNIT_MULTIPLIERS else None,
            "zero_method": evidence.get("zero_method"),
            "statement_scope": evidence.get("statement_scope"), "source_page": evidence.get("source_page"),
            "source_label": evidence.get("source_label"),
            "current": column(current_amount, current_bound, current_period, current_printed),
            "comparative": column(comparison_amount, comparison_bound, comparison_period, comparative_printed),
            "publication": {"published_at": publication.get("published_at") or publication.get("pub_date"),
                            "available_date": public_availability(publication.get("published_at") or publication["pub_date"], publication.get("available_date")),
                            "announcement_id": str(publication["announcement_id"])},
            "source": {"pdf_sha256": provenance.get("pdf_sha256"), "text_sha256": provenance.get("text_sha256"),
                       "pdf_hash_reverified": bool(provenance.get("pdf_hash_reverified")),
                       "text_hash_reverified": bool(provenance.get("text_hash_reverified")),
                       "asset_record_sha256": evidence.get("source_asset_record_sha256")},
            "full_pit_certified": False, "source_coverage": "within_supplied_original_editions_only"}


def amount_unit(header: str) -> dict:
    # "编制单位" is the issuer, not the amount unit.
    raw_header = header
    header = re.sub(r"\s+", "", header)
    units = re.findall(r"(?<!编制)单位(?::|：|均为|为)(?:人民币)?(百万元|千元|万元|亿元|元)", header)
    if not units:
        units = re.findall(r"(?m)^\s*(?:20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*)?"
                           r"人民币\s*(百万元|千元|万元|亿元|元)\s*$", raw_header)
    foreign = re.findall(r"(?:币种|单位)[:：]?(?:港币|美元|欧元|日元|HKD|USD|EUR|JPY)", header, re.I)
    if foreign or len(set(units)) != 1:
        return {"status": "currency_or_unit_unverified", "printed_units": units, "foreign_currency": foreign}
    return {"status": "unit_verified", "currency": "CNY", "printed_unit": units[0],
            "multiplier": amount_text(UNIT_MULTIPLIERS[units[0]]),
            "currency_basis": "explicit_yuan_monetary_header"}


def wrapped_money_requires_geometry(text: str) -> bool:
    """A decimal split across lines is one cell, not two fiscal columns.

    Plain text has lost the column geometry, so it cannot safely join the
    fragments or certify the other year. Complete small amounts stay valid.
    """
    return bool(re.search(r"\d[.,，](?:\s*\n|\s*$)|\d\s*\n\s*\.\d", text))


def strip_note_reference(rest: str) -> str:
    """Remove a complete explicit Chinese note marker, including its brackets.

    Mixed full-width opening and ASCII closing brackets occur in originals.
    Numeric currency cells, including parenthesized negatives, stay intact.
    """
    rest = re.sub(r"^\s*[:：]\s*", "", rest).strip()
    outer = r"^[（(]\s*[一二三四五六七八九十]+[、.．]\s*\d+(?:[（(]\d+[）)])?\s*[）)]"
    if re.match(outer, rest):
        return re.sub(outer, "", rest).strip()
    return re.sub(r"^[（(]?[一二三四五六七八九十]+[）)]?[、.．]?\s*"
                  r"(?:\d+|[（(][一二三四五六七八九十\d]+[）)])(?:[（(]\d+[）)])?", "", rest).strip()


def column_pair(rest: str, following: list[str]) -> tuple[list[str], str]:
    rest = strip_note_reference(rest)
    snippet = rest
    for line in following[:3]:
        if wrapped_money_requires_geometry(snippet):
            return [], snippet
        cells = CELL.findall(snippet)
        if len(cells) >= 2 or (snippet.strip() and re.search(r"[^\d,，.\s+\-−－—–/()（）]", snippet)):
            break
        if not line.strip():
            continue
        # Only isolated monetary cells continue a wrapped row. A following field,
        # report caption/page marker must never become this row's value.
        if not re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", line):
            break
        if re.fullmatch(r"\s*\d\s*", line):
            # Vertically extracted digits are not standalone currency cells.
            snippet += "\n" + line
            break
        snippet += "\n" + line
    if wrapped_money_requires_geometry(snippet):
        return [], snippet
    # Partial punctuation, malformed grouping, and residual prose cannot be
    # discarded by findall() and certified as a monetary column pair.
    if CELL.sub("", snippet).strip():
        return [], snippet
    matches = list(CELL.finditer(snippet))
    if any(a.end() == b.start() for a, b in zip(matches, matches[1:])):
        return [], snippet
    return [m.group() for m in matches], snippet


def resolve_statement_columns(rest, following, *, text, provenance, label,
                              table_kind, period, source_page_hint=None):
    """Try strict text columns, then a hash-bound original PDF grid.

    The third return item records the recovery method/pending reason. It does
    not change the caller's publication, period, sign or revision requirements.
    """
    cells, snippet = column_pair(rest, following)
    # Native extraction may put the major part above the label and the decimal
    # tail below it. The row's own snippet then looks like two complete amounts.
    pattern = re.compile(r"(?m)^[^\S\n]*" + PREFIX + r"(?:(?:其中|减)\s*[:：]\s*)?" +
                         r"\s*".join(map(re.escape, label)) + r"(?=[\s:：+\-−－—–/()（）\d]|$)")
    adjacent_fragment = False
    for hit in pattern.finditer(text):
        if source_page_hint is not None and source_page(text, hit.start()) != source_page_hint:
            continue
        previous = text[:hit.start()].rstrip().splitlines()
        line = previous[-1].strip() if previous else ""
        if line and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", line):
            adjacent_fragment = True
    if len(cells) == 2 and not adjacent_fragment:
        return cells, snippet, {"status": "complete_text_column_pair", "amount_parser_version": AMOUNT_PARSER_VERSION}
    from panda_alpha.statement_layout import original_pdf_pair
    proof = original_pdf_pair(text, provenance, label=label, table_kind=table_kind,
                              period=period, source_page_hint=source_page_hint)
    proof["amount_parser_version"] = AMOUNT_PARSER_VERSION
    if proof["status"] == "physical_grid_columns_bound":
        return [proof["current_printed"], proof["comparative_printed"]], snippet, proof
    return [] if adjacent_fragment else cells, snippet, proof
