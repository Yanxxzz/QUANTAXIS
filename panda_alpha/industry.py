"""Published industry classifications with a conservative as-of boundary.

An edition's reference half-year is not its public availability date. The latest
already-public edition is selected globally; an absent code remains unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class IndustryVintage:
    vintage_id: str
    publication_date: str
    source_sha256: str
    records: dict[str, dict]

    def __post_init__(self):
        date.fromisoformat(self.publication_date)
        if not self.vintage_id or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("A named source edition and SHA256 are required")
        for code, row in self.records.items():
            if not re.fullmatch(r"\d{6}", code) or code != row.get("symbol"):
                raise ValueError("Six-digit edition code identity required")
            if not re.fullmatch(r"[A-S]", row.get("category_code", "")):
                raise ValueError("Unknown source industry category")
            if not re.fullmatch(r"\d{2}", row.get("major_code", "")):
                raise ValueError("Two-digit source major industry code required")


def parse_code_sorted_page(text: str, page: int) -> list[dict]:
    """Parse CAPCO's code-sorted native-text table without imputing wrapped cells."""
    starts = list(re.finditer(r"(?m)^(\d{6})(?=\s|[^\d])", text))
    records = []
    for position, start in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(text)
        raw = text[start.start():end].strip()
        # A company name can itself end in A/B; the single-letter category must
        # be a separate token followed by its Chinese category cell.
        head = re.match(r"^(\d{6})\s*(.*?)\s+([A-S])\s+", raw, re.S)
        if not head:
            raise ValueError(f"Ambiguous industry row on page {page}")
        remainder = raw[head.end():]
        major = re.search(r"(?<!\w)(\d{2})(?!\w)\s+", remainder)
        if not major:
            raise ValueError(f"Missing major industry row on page {page}")
        category_cell = remainder[:major.start()].strip()
        secondary = re.search(r"\s+(C[A-Z])\s+", category_cell)
        category_name = category_cell[:secondary.start()] if secondary else category_cell
        records.append({
            "symbol": head.group(1), "company_name": re.sub(r"\s+", "", head.group(2)),
            "category_code": head.group(3), "category_name": re.sub(r"\s+", "", category_name),
            "secondary_code": secondary.group(1) if secondary else None,
            "major_code": major.group(1), "source_page": page, "source_row_text": raw,
        })
    return records


def asof_industry(vintages: list[IndustryVintage], symbol: str, decision_date: str) -> dict:
    """Date-only publication is usable on a strictly later trading decision day."""
    decision = date.fromisoformat(decision_date)
    eligible = [edition for edition in vintages if date.fromisoformat(edition.publication_date) < decision]
    if not eligible:
        return {"symbol": symbol, "decision_date": decision_date, "status": "UNKNOWN_NO_PUBLIC_EDITION"}
    latest_date = max(edition.publication_date for edition in eligible)
    latest = [edition for edition in eligible if edition.publication_date == latest_date]
    if len(latest) != 1:
        raise ValueError("Ambiguous same-day classification editions require source revision resolution")
    edition = latest[0]
    common = {"symbol": symbol, "decision_date": decision_date, "vintage_id": edition.vintage_id,
              "publication_date": edition.publication_date, "source_sha256": edition.source_sha256}
    row = edition.records.get(symbol)
    if row is None:
        return {**common, "status": "UNKNOWN_ABSENT_IN_LATEST_PUBLIC_EDITION"}
    return {**row, **common, "status": "VERIFIED_PUBLISHED_CLASSIFICATION"}


def query_industry_asof(db, *, code: str, decision_date: str) -> dict:
    """Read the latest accepted global edition, never an issuer's old row.

    Scoped imports must distinguish a source omission from a security outside
    their independently checked universe. Date-only publications remain unusable
    on their own publication day, matching ``asof_industry``.
    """
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("Six-digit query code required")
    decision = date.fromisoformat(decision_date)
    editions = list(db["stock_industry_editions"].find({"status": "verified_scoped"}, {"_id": 0}))
    eligible = [edition for edition in editions if date.fromisoformat(edition["publication_date"]) < decision]
    basic = {"symbol": code, "decision_date": decision_date}
    if not eligible:
        return {**basic, "status": "UNKNOWN_NO_PUBLIC_EDITION"}
    latest_date = max(edition["publication_date"] for edition in eligible)
    latest = [edition for edition in eligible if edition["publication_date"] == latest_date]
    if len(latest) != 1:
        raise ValueError("Ambiguous same-day classification editions require source revision resolution")
    edition = latest[0]
    scope = edition["source_scope_codes"]
    common = {**basic, "vintage_id": edition["vintage_id"], "publication_date": edition["publication_date"],
              "source_sha256": edition["source_sha256"], "source_scope_count": len(scope),
              "scope_sha256": edition["scope_sha256"], "parse_receipt_sha256": edition["parse_receipt_sha256"],
              "full_a_industry_verified": False}
    if code not in scope:
        return {**common, "status": "UNKNOWN_OUTSIDE_VERIFIED_SOURCE_SCOPE", "out_of_verified_scope": True}
    rows = list(db["stock_industry_pit"].find({"code": code, "vintage_id": edition["vintage_id"],
        "source_sha256": edition["source_sha256"]}, {"_id": 0}))
    if not rows:
        return {**common, "status": "UNKNOWN_ABSENT_IN_LATEST_PUBLIC_EDITION", "out_of_verified_scope": False}
    if len(rows) != 1:
        raise ValueError("Duplicate scoped industry identity")
    row = rows[0]
    if row.get("publication_date") != edition["publication_date"] or row.get("parse_receipt_sha256") != edition["parse_receipt_sha256"]:
        raise ValueError("Industry row/edition provenance mismatch")
    if not re.fullmatch(r"[A-S]", row.get("category_code", "")) or not re.fullmatch(r"\d{2}", row.get("major_code", "")):
        raise ValueError("Invalid stored classification")
    return {**row, **common, "sector": row["category_code"] + row["major_code"],
            "status": "VERIFIED_PUBLISHED_CLASSIFICATION", "out_of_verified_scope": False}
