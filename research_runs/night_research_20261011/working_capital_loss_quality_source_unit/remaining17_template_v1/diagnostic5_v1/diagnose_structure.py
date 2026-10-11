"""Five frozen source-format observations, never a stock/money parser replay."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT.parent))
import source_templates as templates


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def bound(item):
    path = Path(item["path"])
    if sha(path) != item["sha256"]:
        raise ValueError("frozen_source_diagnostic_binding_changed:" + str(path))
    return path


def observation(text, start, end, kind):
    """Raw span hash with a short structural caption; money digits are omitted."""
    loc = templates.locator(text, start, end)
    brief = re.sub(r"[-−－]?(?:\d[\d,，]*\.\d+)(?:[%％])?", "<number omitted>", text[start:end].strip())
    if kind in {"TOTAL_locator_numbers_omitted", "header_or_category_line_not_role_mapped"}:
        # These patterns can include complete printed data rows. Retain only
        # their lexical structure, including when monetary tokens are integers.
        # Separate title/chapter/ordinal observations preserve their numbering.
        brief = re.sub(r"[-−－]?\d[\d,，]*(?:[%％])?", "<number omitted>", brief)
    return {**loc, "kind": kind, "short_caption": brief[:180],
            "amounts_mapped_or_computed": False, "CNY_or_source_eligibility_created": False}


def matches(text, expression, start=0, end=None, kind="caption", limit=16):
    found = list(expression.finditer(text, start, len(text) if end is None else end))
    return {"count": len(found), "observations": [observation(text, h.start(), h.end(), kind) for h in found[:limit]],
            "truncated_observations": len(found) > limit}


def observe(text, prior):
    """Only headings/regions/counts. Never call consume, tokens or table math."""
    pages = list(re.finditer(r"===SOURCE_PAGE:(\d+)===" , text))
    first_end = pages[1].start() if len(pages) > 1 else len(text)
    first_start = pages[0].end() if pages else 0
    native_titles = re.compile(r"(?m)^.{0,80}(?:年度报告|年度報告|年报|年報).{0,80}$")
    scope_broad = re.compile(r"(?m)^.{0,60}(?:合并|合併|财务|財務).{0,25}(?:项目注释|項目注釋|报表附注|報表附註).{0,60}$")
    ar_captions = re.compile(r"(?m)^[ \t]*(?:\d+[、.．]|[（(]\d+[）)]?[、.．]?)[ \t]*应收账款[ \t]*[:：]?[ \t]*$")
    root_matches = list(templates.SCOPE.finditer(text))
    result = {"native_first_page": {"source_marker": pages[0][1] if pages else None,
                                   "title_candidates": matches(text, native_titles, first_start, first_end, "first_page_title", 8)},
              "exact_current_chapter_matches": matches(text, templates.SCOPE, kind="exact_chapter"),
              "broad_chapter_caption_observations": matches(text, scope_broad, kind="unqualified_broad_caption", limit=12),
              "AR_caption_candidates": matches(text, ar_captions, kind="AR_parent_or_numbering_candidate", limit=8),
              "existing_source17_status": prior["status"],
              "existing_source17_pending": prior.get("pending_reasons", []),
              "existing_AR_pending": prior.get("assets", {}).get("AR", {}).get("pending_reasons", [])}
    if len(root_matches) == 1:
        h = root_matches[0]
        result["post_parent_chapter_candidates"] = matches(text, templates.CHAPTER, h.end(), kind="chapter_or_nested_candidate", limit=20)
        if h["ordinal"] in templates.ORDINALS[:-1]:
            ordinal = templates.ORDINALS[templates.ORDINALS.index(h["ordinal"]) + 1]
            same = [m for m in templates.CHAPTER.finditer(text, h.end()) if m["ordinal"] == ordinal]
            result["expected_successor_ordinal"] = ordinal
            result["same_ordinal_observations"] = [observation(text, m.start(), m.end(), "not_yet_qualified_successor") for m in same[:16]]
    inherited = prior.get("assets", {}).get("AR", {}).get("note_geometry", {})
    ranges = []
    if inherited.get("status") == "bounded_consolidated_asset_note":
        a, b = inherited["start"], inherited["end"]
        if templates.locator(text, a, b)["span_sha256"] != inherited["range"]["span_sha256"]:
            raise ValueError("completed_AR_geometry_span_binding_changed")
        ranges.append((a, b, "existing_bounded_AR_note"))
    else:
        # A diagnostic window around a candidate is explicitly NOT an accepted
        # note boundary, statement scope, table population or source clear.
        for h in ar_captions.finditer(text):
            successor = templates.PARENT_NOTE.search(text, h.end())
            limit = min(len(text), h.start() + 8000, successor.start() if successor else len(text))
            ranges.append((h.start(), limit, "unqualified_AR_diagnostic_window"))
            if successor:
                result.setdefault("first_bare_successor_after_AR_candidate", []).append(
                    observation(text, successor.start(), successor.end(), "not_accepted_sibling_candidate"))
            if len(ranges) == 2: break
    region_observations = []
    header = re.compile(r"(?m)^.*(?:期末余额|期初余额|账面余额|账面价值|坏账准备|类别|项目).*$")
    detail = re.compile(r"(?m)^[ \t]*(?:[（(]\d+[）)]|按(?:单项|组合)计提)[^\n]*$")
    for a, b, authority in ranges:
        region_observations.append({"range": templates.locator(text, a, b), "authority": authority,
            "bare_numbered_candidates": matches(text, templates.PARENT_NOTE, a, b, "bare_note_or_nested_candidate", 16),
            "bracket_or_detail_captions": matches(text, detail, a, b, "whole_or_detail_caption", 20),
            "header_line_observations": matches(text, header, a, b, "header_or_category_line_not_role_mapped", 24),
            "table_starts": matches(text, templates.TABLE_START, a, b, "table_start", 16),
            "total_row_observations": matches(text, templates.TOTAL, a, b, "TOTAL_locator_numbers_omitted", 16)})
    result["AR_regions"] = region_observations
    result.update(stock_parser_replayed=False, successful_INV_or_first3_replayed=False,
                  money_roles_Q_currency_or_financial_mask_computed=False)
    return result


def collect_cases(cases, identities, prior, read_case, deadline_reached):
    """Keep completed observations on a runtime stop; never consume later cases."""
    records = []
    for index, (case, identity) in enumerate(zip(cases, identities)):
        failure = None
        if deadline_reached():
            failure = {"error_type": "DeadlineReached", "error": "human_deadline_reached"}
        else:
            try:
                document, binding, text = read_case(case)
                records.append({"source_identity": identity,
                                "status": "STRUCTURE_OBSERVED_NO_NEW_SOURCE_QUALIFICATION",
                                "observations": observe(text, prior[case["code"]])})
                del document, binding, text
            except ValueError as error:
                # A known binding/format rejection remains a completed pending
                # observation, as before; it does not certify report structure.
                records.append({"source_identity": identity,
                                "status": "SOURCE_BINDING_PENDING_NO_STRUCTURE_CLAIM",
                                "reason": str(error)})
            except Exception as error:
                failure = {"error_type": type(error).__name__, "error": str(error)[:512]}
        if failure is not None:
            return {"status": "SOURCE_EXECUTION_PENDING_FIXED5_STRUCTURE_DIAGNOSTIC",
                    "records": records,
                    "execution_stop": {**failure, "failed_index_zero_based": index,
                                       "failed_source_identity": identity},
                    "resume_position": {"first_uncompleted_index_zero_based": index,
                                        "first_uncompleted_source_identity": identity,
                                        "completed_records": len(records),
                                        "remaining_identity_rows": identities[index:],
                                        "automatic_resume_allowed": False,
                                        "instruction": "Root must bind this partial receipt and register a specific repair; do not replay completed records."}}
    return {"status": "COMPLETED_FIXED5_SOURCE_STRUCTURE_DIAGNOSTIC", "records": records,
            "execution_stop": None,
            "resume_position": {"completed_records": len(records), "automatic_resume_allowed": False,
                                "instruction": "All frozen cases processed; no implicit replay."}}


def save_receipt(output, result):
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--execute", action="store_true")
    parser.add_argument("--protocol", type=Path, default=OUT / "protocol.json")
    parser.add_argument("--protocol-sha"); args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_FIXED5_STRUCTURE_ONLY_NO_BODY_READ"})); return
    if not args.protocol_sha or sha(args.protocol) != args.protocol_sha:
        raise ValueError("exact_root_frozen_diagnostic_protocol_required")
    p = read(args.protocol)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]): raise ValueError("human_deadline_reached")
    output = Path(p["output_path"])
    if output.exists(): raise ValueError("fixed5_diagnostic_receipt_exists_no_implicit_replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        event = db.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?",
                           (p["event_prefix"] + args.protocol_sha, p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts") != 0:
            raise ValueError("registered_source_only_zero_context_freeze_required")
    for item in [*p["runtime_bindings"].values(), *p["bindings"].values()]: bound(item)
    plan = read(bound(p["bindings"]["source_plan"])); original = read(bound(p["bindings"]["completed17_receipt"]))
    bycode = {c["code"]: c for c in plan["selected_cases"]}; prior = {r["source_identity"]["code"]: r for r in original["records"]}
    identities = [{k: bycode[code][k] for k in ("code", "announcement_id", "report_date", "source_record_sha256")} for code in p["selected_codes"]]
    if identities != p["selected_identity_rows"] or len(identities) != 5: raise ValueError("fixed5_exactKEY_or_order_changed")
    spec = importlib.util.spec_from_file_location("bound_read_case_for_diagnostic5", p["bindings"]["shared_reader"]["path"])
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    indexmap = plan["bindings"]["source_indices"]
    if any(indexmap[k] != p["bindings"][k] for k in indexmap): raise ValueError("source_index_map_changed")
    indexes = {item["path"]: read(bound(item)) for item in indexmap.values()}
    cases = []
    for code in p["selected_codes"]:
        c = dict(bycode[code]); c["source_index_ref"] = indexmap[c["source_index_binding_key"]]
        cases.append(c)
    with sqlite3.connect(bound(p["bindings"]["capture_db"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        result = collect_cases(cases, identities, prior, lambda c: reader.read_case(c, db, indexes),
                               lambda: datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]))
    result.update({"protocol_sha256": args.protocol_sha,
              "new_contexts": 0, "money_Q_prices_labels_or_new_eligibility_computed": False,
              "original17_or_first3_replayed": False, "full_text_copies": 0, "old_inputs_receipts_DB_state_registry_changed": False})
    save_receipt(output, result)
    print(json.dumps({"status": result["status"], "records": len(result["records"]), "output_sha256": sha(output)}))


if __name__ == "__main__": main()
