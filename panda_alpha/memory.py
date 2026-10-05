"""Build a portable, sanitized research memory from an existing PandaAI workspace.

This is context compaction, not evidence deletion. Every retained evidence item points
to a source file and its SHA256; large panels, account receipts, and credentials stay
in the private legacy workspace. The module uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

SCHEMA_VERSION = 1
_FACTOR = re.compile(r"(?<![A-Za-z0-9])([FC])(\d{1,4})(?![A-Za-z0-9])", re.I)
_FULL_FACTOR = re.compile(r"[FC]\d{1,4}", re.I)
_DENOMINATOR_KEYS = {"named_hypotheses", "multiple_testing_denominator",
                     "cumulative_hypotheses", "denominator_after", "hypotheses"}
_IDENTITY_KEYS = ("candidate", "name", "local_id", "factor", "factor_name")
_EXCLUDED_PARTS = {".git", "__pycache__", "broken_tools", ".venv", "node_modules",
                   "filing_market_index", "filing_samples", "cache", "research_bootstrap"}
_PRIVATE_NAMES = re.compile(
    r"account|balance|billing|authorization|credential|password|token|cookie|session|"
    r"create_response|info_response|factor_info|run_response|receipt|ledger", re.I)
_SOURCE_NAMES = re.compile(
    r"knowledge|research_db|decision_packet|classification|summary|preflight|formal_result|"
    r"validation|feedback|reflection|checkpoint|plan|freeze|coverage|contract|source.*(?:review|result|"
    r"decision|evidence)|capability|current|sealed_windows|erratum|audit|result", re.I)
_METRIC_KEYS = {
    "direction_rank_ic", "rank_ic", "rankIC", "rank_ic_p_value", "rank_ic_p",
    "rank_ic_bh_q", "ic_mean", "ic_ir", "ic_p_value", "ic_win_rate", "monotonicity",
    "coverage", "periods", "folds_positive", "mean_turnover", "turnover", "long_excess",
    "long_excess_annual", "net_excess_annual", "net_excess_annual_50bps", "net30_proxy",
    "net50_proxy", "net30_excess_proxy_pct", "net50_excess_proxy_pct", "long_sharpe",
    "long_max_drawdown", "p25_fold_net_excess", "pool_ic_delta", "pool_mdd_reduction",
    "max_abs_corr_final4", "max_abs_pool_rho", "max_abs_failed_neighbour_rho",
    "max_abs_corr_active_pool", "size_direction_rank_ic", "size_net30", "size_net50",
    "size_net30_proxy", "size_net50_proxy", "size_net_excess_annual_30bps",
    "size_net_excess_annual_50bps", "net30_relative_wealth_annual_proxy",
    "net50_relative_wealth_annual_proxy", "direction", "period_sharpe_proxy",
    "max_drawdown_after_cost", "rebalance_observations", "rank_ic_mean_unrounded",
}
_TEXT_KEYS = ("mechanism", "economic_information", "question", "lesson", "reason",
              "failure_reasons", "failure", "error", "error_type", "runtime_cause")
_VERIFICATION_KEYS = {"native_parity_verified", "native_or_vintage_verified",
                      "original_historical_vintage_verified", "native_parity",
                      "native_market_universe_and_asof_parity_verified",
                      "original_historical_vintage_verified", "pit_verified",
                      "full_A_coverage_verified", "five_year_quotes_absence_proven"}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_text(value: Any, limit: int = 700) -> str:
    """Redact incidental secrets in allowlisted explanatory text, never raw payloads."""
    if not isinstance(value, (str, int, float, bool)):
        return ""
    text = str(value)
    text = re.sub(r"(?i)(?:bearer\s+)[A-Za-z0-9_.~-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(?:api[_ -]?key|password|token|secret|cookie|account[_ -]?id)"
                  r"\s*[:=]\s*[^\s,;]+", "[REDACTED]", text)
    text = re.sub(r"https?://[^\s<>\"]+", "[URL_REDACTED]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]", text)
    text = re.sub(r"(?<!\w)[A-Za-z]:[\\/][^\s\"<>]+", "[LOCAL_PATH_REDACTED]", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[PHONE_REDACTED]", text)
    text = re.sub(r"(?i)\b(?:sk-|panda_)[A-Za-z0-9_-]{16,}\b", "[REDACTED]", text)
    return " ".join(text.split())[:limit]


def _factor_id(value: Any) -> str | None:
    if not isinstance(value, str) or not _FULL_FACTOR.fullmatch(value.strip()):
        return None
    return value[0].upper() + str(int(value[1:])).zfill(2)


def _ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted({item for raw in value for item in _ids(raw)})
    if not isinstance(value, str):
        return []
    found = {letter.upper() + str(int(number)).zfill(2)
             for letter, number in _FACTOR.findall(value)}
    for match in re.finditer(r"([FC])(\d+)\s*[-–]\s*\1?(\d+)", value, re.I):
        first, last = int(match[2]), int(match[3])
        if 0 <= last - first < 1000:
            found.update(match[1].upper() + str(number).zfill(2)
                         for number in range(first, last + 1))
    return sorted(found)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(numeric):
        return None
    return int(numeric) if numeric.is_integer() else numeric


def _binary_direction(value: Any, path: str) -> int | None:
    # Engine-v2 exports use signed -1/+1; their +1 must not masquerade as a
    # platform direction declaration. Only explicit 0/1 research declarations
    # from the legacy formula/spec surface are eligible as seed directions.
    if "cross_section_summary" in path or "v2_sensitivity" in path or "v2_cost" in path:
        return None
    number = _number(value)
    return int(number) if number in (0, 1) else None


def _source_window(node: dict, inherited: dict) -> dict:
    result = dict(inherited)
    settings = node.get("settings")
    if isinstance(settings, dict):
        for key in ("start", "end", "warmup_start", "cycle", "groups", "group_number",
                    "one_way_cost"):
            if isinstance(settings.get(key), (str, int, float)):
                result[key] = _safe_text(settings[key], 40)
    source = node.get("source")
    if isinstance(source, dict):
        for key in ("min_date", "max_date", "date_bounds"):
            value = source.get(key)
            if isinstance(value, int) or isinstance(value, str) and re.fullmatch(r"[0-9-]{8,10}", value):
                result[key] = value
            elif isinstance(value, list) and all(isinstance(item, int) for item in value):
                result[key] = value[:2]
    return result


def _failures(node: dict) -> list[str]:
    values = []
    for key in ("failure_reasons", "failure_buckets", "failed_checks", "failures",
                "unverified_official_checks"):
        value = node.get(key)
        if isinstance(value, str):
            values.extend(part for part in value.split(";") if part.strip())
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    for key in ("checks", "quality_checks", "available_official_checks"):
        checks = node.get(key)
        if isinstance(checks, dict):
            values.extend(str(name) for name, passed in checks.items() if passed is False)
    return sorted({_safe_text(value, 200) for value in values if value})


def _evidence_kind(path: str, node: dict, failures: list[str]) -> tuple[str, str]:
    lower = path.lower()
    status = str(node.get("status", node.get("classification", "recorded"))).lower()
    if node.get("official_metrics") or "official_validation" in lower:
        return "official_validation", "official_checks_partial" if (
            node.get("official_common_gate_fully_verified") is False) else "reported"
    if ("panda" in lower or "official_confirmations" in lower) and (
            node.get("factor_analysis") or node.get("success") is True):
        return "official_runtime", "reported"
    if node.get("error_type") or status in {"error", "failed", "execution_failed"}:
        return "execution_failure", "failed"
    coverage_failed = any(re.search(r"coverage|missing|source|pit|runtime|field", item, re.I)
                          for item in failures)
    if coverage_failed and ("preflight" in lower or "source" in lower or "coverage" in lower):
        return "source_coverage_failure", "failed_or_unverified"
    if "source" in lower or "coverage" in lower or "capability" in lower:
        return "source_audit", "reported"
    if any(word in status for word in ("reject", "fail", "stop")) or failures:
        return "legacy_gate_failure", "legacy_policy_result"
    if "plan" in lower or "freeze" in lower:
        return "registered_hypothesis", "unvalidated"
    return "legacy_proxy_backtest", "reported"


def _extract_json(payload: Any, path: str, digest: str) -> tuple[list[dict], list[int]]:
    records: list[dict] = []
    denominators: list[int] = []
    fallback = _ids(Path(path).parent.name + " " + Path(path).stem)
    fallback_id = fallback[0] if len(fallback) == 1 else None

    def walk(node: Any, pointer: str = "", inherited_id: str | None = fallback_id,
             window: dict | None = None, depth: int = 0,
             inherited_fields: list[str] | None = None) -> None:
        if depth > 15:
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{pointer}/{index}", inherited_id, window, depth + 1, inherited_fields)
            return
        if not isinstance(node, dict):
            return
        for key in _DENOMINATOR_KEYS:
            number = _number(node.get(key))
            if isinstance(number, int) and 0 <= number <= 100000:
                denominators.append(number)
        current_id = next((_factor_id(node.get(key)) for key in _IDENTITY_KEYS
                           if _factor_id(node.get(key))), inherited_id)
        current_window = _source_window(node, window or {})
        source_info = node.get("source") if isinstance(node.get("source"), dict) else {}
        declared_fields = source_info.get("fields", source_info.get("projected_fields",
                                            node.get("fields", node.get("input_fields"))))
        current_fields = [_safe_text(field, 100) for field in declared_fields if isinstance(field, str)] if isinstance(declared_fields, list) else (inherited_fields or [])
        has_evidence = any(key in node for key in _METRIC_KEYS | set(_TEXT_KEYS) |
                           {"classification", "failed_checks", "quality_checks",
                            "official_metrics", "error_type", "decision", "factor_analysis",
                            "checks", "status"})
        if current_id and has_evidence:
            failures = _failures(node)
            kind, evidence_status = _evidence_kind(path, node, failures)
            metric_nodes = [node]
            if isinstance(node.get("official_metrics"), dict):
                metric_nodes.append(node["official_metrics"])
            metrics = {key: number for item in metric_nodes for key in sorted(_METRIC_KEYS)
                       if (number := _number(item.get(key))) is not None}
            analysis = node.get("factor_analysis")
            if isinstance(analysis, dict):
                for item in analysis.get("query_factor_analysis_data", []):
                    if isinstance(item, dict):
                        indicator = str(item.get("indicator", "")).lower()
                        if indicator in {"rank_ic", "ic_mean", "ic_ir", "ic_std", "p-value"}:
                            numeric = _number(item.get("factor1"))
                            if numeric is not None:
                                metrics[indicator] = numeric
            mechanism = next((_safe_text(node[key]) for key in
                              ("mechanism", "economic_information", "question", "lesson")
                              if isinstance(node.get(key), str)), "")
            formula = next((_safe_text(node[key], 1000) for key in
                            ("formula", "local_definition", "definition")
                            if isinstance(node.get(key), str)), "")
            fields = current_fields
            data_source = "pandaai" if kind.startswith("official") else (
                "legacy_stockdb_proxy" if "stockdb" in path.lower() else "legacy_research_artifact")
            original_decision = _safe_text(node.get("decision", node.get("classification",
                                                node.get("status", "recorded"))), 150)
            if kind == "legacy_gate_failure":
                decision = "reassess_under_new_data_and_policy"
            elif kind in {"source_coverage_failure", "execution_failure"}:
                decision = "repair_source_or_runtime_before_retry"
            elif kind.startswith("official"):
                decision = "retain_official_evidence_verify_pool_increment"
            else:
                decision = "retain_as_research_prior"
            error = next((_safe_text(node[key], 300) for key in
                          ("error_type", "error", "failure", "runtime_cause")
                          if isinstance(node.get(key), str)), "")
            records.append({"hypothesis_id": current_id, "mechanism": mechanism,
                            "family": _safe_text(node.get("family", ""), 120),
                            "formula": formula, "fields": fields, "data_source": data_source,
                            "direction": _binary_direction(node.get("direction"), path),
                            "source_window": current_window, "evidence_kind": kind,
                            "evidence_status": evidence_status, "status": original_decision,
                            "error": error, "failure_reasons": failures,
                            "decision": decision, "metrics": metrics,
                            "source_path": path, "source_sha256": digest,
                            "source_pointer": pointer or "/"})
        for key, value in node.items():
            # Raw serialized node output, account data, and unstructured payloads
            # are deliberately never imported into the portable memory.
            if re.search(r"token|password|secret|account|balance|billing|cookie|credential|"
                         r"nodes|result_json|raw", key, re.I) or key in {
                             "official_metrics", "checks", "quality_checks", "available_official_checks"}:
                continue
            child_id = _factor_id(key) or current_id
            walk(value, f"{pointer}/{key}", child_id, current_window, depth + 1, current_fields)

    walk(payload)
    return records, denominators


def _source_findings(payload: Any, path: str, digest: str) -> list[dict]:
    """Preserve source blockers that did not create a numbered hypothesis."""
    findings = []
    def walk(node: Any, pointer: str = "", depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{pointer}/{index}", depth + 1)
        elif isinstance(node, dict):
            verification = {key: value for key, value in node.items()
                            if key in _VERIFICATION_KEYS and isinstance(value, bool)}
            coverage = node.get("checks", {}).get("coverage") if isinstance(node.get("checks"), dict) else None
            error = _safe_text(node.get("error_type", ""), 120)
            no_records = node.get("source_records_accepted") == 0
            if error or no_records or coverage is False or any(value is False for value in verification.values()):
                findings.append({"source_path": path, "source_sha256": digest,
                                 "source_pointer": pointer or "/",
                                 "status": _safe_text(node.get("status", "source_verification_incomplete"), 180),
                                 "error": error, "verification": verification,
                                 "coverage_passed": coverage,
                                 "records_accepted": 0 if no_records else None,
                                 "decision": "repair_or_verify_source_before_retest",
                                 "interpretation": "missing_or_unverified_source_is_not_mechanism_falsification"})
            for key, value in node.items():
                if not re.search(r"token|password|secret|account|balance|billing|cookie|credential|nodes|result_json|raw", key, re.I):
                    walk(value, f"{pointer}/{key}", depth + 1)
    walk(payload)
    return findings


def _declared_source_record(text: str, path: str, digest: str, suffix: str) -> list[dict]:
    """Read literal inputs from registered source syntax without executing code."""
    ids = _ids(Path(path).parent.name + " " + Path(path).stem)
    fields = []
    formula = ""
    direction = None
    if suffix == ".py" and len(ids) == 1:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "factors":
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    fields.append(_safe_text(node.slice.value, 100))
    elif suffix == ".md" and "- Mode: formula" in text:
        local_id = re.search(r"Local factor:\s*`([FC]\d+)`", text)
        explicit_formula = re.search(r"(?m)^- Formula:\s*`([^`]+)`", text)
        explicit_direction = re.search(r"(?m)^- Direction:\s*`([01])`", text)
        if not local_id or not explicit_formula:
            return []
        ids = [_factor_id(local_id[1])]
        formula = _safe_text(explicit_formula[1], 1000)
        # These are literal identifiers in the declared platform formula, not a
        # dependency guess from the economic mechanism or a local VWAP shorthand.
        field_names = {"OPEN", "HIGH", "LOW", "CLOSE", "AMOUNT", "VOLUME", "TURNOVER", "PCT_CHG"}
        fields = sorted(token.lower() for token in set(re.findall(r"\b[A-Z_]+\b", formula)) & field_names)
        direction = int(explicit_direction[1]) if explicit_direction else None
    if len(ids) != 1 or not fields:
        return []
    return [{"hypothesis_id": ids[0], "family": "", "mechanism": "",
             "formula": formula, "fields": sorted(set(fields)), "direction": direction,
             "data_source": "registered_platform_source", "source_window": {},
             "evidence_kind": "registered_hypothesis", "evidence_status": "literal_input_declaration",
             "status": "declared_inputs", "error": "", "failure_reasons": [], "metrics": {},
             "decision": "research_prior_requires_quantaxis_field_mapping",
             "source_path": path, "source_sha256": digest,
             "source_pointer": "literal_factors_subscripts" if suffix == ".py" else "registered_formula_line"}]


def _candidate_sources(workspace: Path, output_dir: Path) -> Iterable[Path]:
    roots = [workspace / name for name in ("factor_workflow", "stockdb_factor_eval",
             "work", "archive", "panda_native_factors")]
    roots.extend(path for path in workspace.iterdir() if path.is_dir() and
                 path.name.startswith(("panda_", "candidates_")) and "results" in path.name)
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or output_dir in path.parents:
                continue
            relative = path.relative_to(workspace)
            if set(relative.parts) & _EXCLUDED_PARTS or _PRIVATE_NAMES.search(path.name):
                continue
            registered_python = path.suffix.lower() == ".py" and (root.name == "panda_native_factors" or
                                 "native_candidates" in relative.parts and path.name == "candidate.py")
            if path.suffix.lower() not in {".json", ".csv", ".md"} and not registered_python:
                continue
            # Never ingest per-stock panels or large row tables as conversation memory.
            if path.stat().st_size > 2_000_000:
                continue
            if registered_python or path.suffix.lower() == ".md" or _SOURCE_NAMES.search(path.name) or "batches" in relative.parts:
                yield path
            elif "results" in root.name and path.suffix.lower() == ".json":
                yield path
    for path in sorted(workspace.glob("*.md")):
        if not _PRIVATE_NAMES.search(path.name):
            yield path


def _family_map(knowledge: dict) -> tuple[dict[str, str], list[dict], list[dict]]:
    mapping = {}
    directions = []
    blocked = []
    for item in knowledge.get("working_mechanisms", []):
        if not isinstance(item, dict):
            continue
        family = _safe_text(item.get("axis", "unclassified"), 120)
        representatives = _ids(item.get("representatives", []))
        directions.append({"family": family, "representatives": representatives,
                           "lesson": _safe_text(item.get("lesson", "")),
                           "decision": "research_prior_requires_quantaxis_and_official_revalidation"})
        mapping.update({factor: family for factor in representatives})
    for item in knowledge.get("stopped_families", []):
        if not isinstance(item, dict):
            continue
        family = _safe_text(item.get("family", "unclassified"), 120)
        examples = _ids(item.get("examples", []))
        blocked.append({"family": family, "examples": examples,
                        "reason": _safe_text(item.get("reason", "")),
                        "rule": "do_not_repeat_unchanged_mechanism_fields_or_parameter_tuning",
                        "reopen_condition": "material_new_information_or_verified_source_repair; record_change_and_falsifier"})
        mapping.update({factor: family for factor in examples if factor not in mapping})
    return mapping, directions, blocked


def _markdown(memory: dict) -> str:
    lines = ["# PandaAI research bootstrap", "",
             f"Historical multiple-testing denominator: **{memory['multiple_testing_denominator']}**.",
             f"Recovered {memory['hypothesis_count']} distinct hypothesis IDs from "
             f"{memory['source_count']} sanitized source summaries.", "",
             "Raw evidence remains in the private legacy workspace. Source paths are relative "
             "to that workspace and every source has a SHA256. Old gate failures describe the "
             "old policy; source/runtime failures do not disprove an economic mechanism. "
             "Official standalone results and local return proxies remain separate.", "",
             "## Useful starting directions", ""]
    for item in memory["starting_directions"]:
        lines.append(f"- **{item['family']}** ({', '.join(item['representatives'])}): {item['lesson']}")
    lines.extend(["", "## Failure memory", ""])
    for item in memory["no_repeat_rules"]:
        lines.append(f"- **{item['family']}**: {item['reason']} Reopen only with a material "
                     "information change or verified source repair, a written change, and a falsifier.")
    lines.extend(["", "## Evidence inventory", "", "| Evidence kind | Count |",
                  "| --- | ---: |"])
    for key, value in sorted(memory["evidence_counts"].items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "Read memory.json as the hot research context (below 100KB). Resolve "
                  "evidence_refs on demand in evidence.jsonl; it retains every deduplicated "
                  "structured evidence item and unregistered source finding. source_index.jsonl "
                  "maps source hashes, historical denominators, and original artifact pointers.", ""])
    return "\n".join(lines)


def compact_workspace(workspace: str | Path, output_dir: str | Path) -> dict:
    """Write memory.json, memory.md, evidence.jsonl, source_index.jsonl and manifest.json.

    Re-running against identical inputs produces identical files. No source is changed
    or deleted. The denominator is the maximum recorded historical denominator or
    recovered F identifier; it can never fall merely because files were compacted.
    The hot memory is below 100KB. All recovered structured evidence is retained in
    evidence.jsonl as a searchable cold index. Return the manifest (counts, hashes,
    provenance and exclusions).
    """
    workspace = Path(workspace).resolve()
    output_dir = Path(output_dir).resolve()
    if not workspace.is_dir():
        raise ValueError("legacy workspace does not exist")
    if output_dir == workspace or any(root == output_dir for root in
                                     (workspace / "factor_workflow", workspace / "work",
                                      workspace / "stockdb_factor_eval", workspace / "archive")):
        raise ValueError("output must be a dedicated directory, not a legacy evidence root")
    knowledge_path = workspace / "factor_workflow" / "knowledge_base.json"
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8-sig")) if knowledge_path.exists() else {}
    families, directions, no_repeat = _family_map(knowledge)
    source_index = []
    all_records = []
    narrative_references = defaultdict(list)
    denominator_evidence = []
    parse_errors = []
    source_findings = []
    for source in _candidate_sources(workspace, output_dir):
        relative = source.relative_to(workspace).as_posix()
        raw = source.read_bytes()
        digest = _sha256(raw)
        records = []
        denominators = []
        try:
            text = raw.decode("utf-8-sig")
            if source.suffix.lower() == ".json":
                payload = json.loads(text)
                records, denominators = _extract_json(payload, relative, digest)
                source_findings.extend(_source_findings(payload, relative, digest))
            elif source.suffix.lower() == ".csv":
                for row in csv.DictReader(text.splitlines()):
                    records.extend(_extract_json(row, relative, digest)[0])
            elif source.suffix.lower() == ".py":
                records = _declared_source_record(text, relative, digest, ".py")
            else:
                # Narrative histories provide IDs and source hashes, not copied prose:
                # prose can contain account receipts or stale operating constraints.
                ids = _ids(text)
                declared_records = _declared_source_record(text, relative, digest, ".md")
                all_records.extend(declared_records)
                for factor in ids:
                    narrative_references[factor].append({
                        "hypothesis_id": factor, "family": "", "mechanism": "",
                        "formula": "", "fields": [], "data_source": "legacy_narrative",
                        "direction": None,
                        "source_window": {}, "evidence_kind": "narrative_reference",
                        "evidence_status": "unverified_narrative", "status": "referenced",
                        "error": "", "failure_reasons": [], "metrics": {},
                        "decision": "locate_original_structured_evidence_before_retest",
                        "source_path": relative, "source_sha256": digest,
                        "source_pointer": "factor_id_mention"})
                source_index.append({"source_path": relative, "source_sha256": digest,
                                     "bytes": len(raw), "format": "md", "hypothesis_ids": ids,
                                     "evidence_count": len(declared_records), "summary": "narrative_reference_and_explicit_formula_declarations"})
                continue
        except (ValueError, UnicodeError, csv.Error):
            parse_errors.append({"source_path": relative, "source_sha256": digest,
                                 "error": "unreadable_structured_artifact"})
        all_records.extend(records)
        denominator_evidence.extend({"value": value, "source_path": relative,
                                     "source_sha256": digest} for value in set(denominators))
        source_index.append({"source_path": relative, "source_sha256": digest,
                             "bytes": len(raw), "format": source.suffix[1:],
                             "hypothesis_ids": sorted({r["hypothesis_id"] for r in records}),
                             "evidence_count": len(records),
                             "historical_denominators": sorted(set(denominators)),
                             "evidence_kinds": sorted({r["evidence_kind"] for r in records}),
                             "summary": "sanitized_allowlisted_fields"})
    hypotheses = defaultdict(list)
    seen = set()
    for record in all_records:
        # Exact semantic duplication within one artifact is common in checkpoint trees;
        # keep separate source provenance when the same finding occurs in another file.
        signature = _sha256(json.dumps({key: value for key, value in record.items()
                                      if key != "source_pointer"}, sort_keys=True).encode())
        if signature not in seen:
            seen.add(signature)
            hypotheses[record["hypothesis_id"]].append(record)
    highest_id = max((int(factor[1:]) for factor in hypotheses if factor.startswith("F")), default=0)
    for factor, references in narrative_references.items():
        if factor not in hypotheses:
            hypotheses[factor].append(references[0])
    highest_id = max(highest_id, max((int(factor[1:]) for factor in hypotheses if factor.startswith("F")), default=0))
    denominator = max([highest_id] + [item["value"] for item in denominator_evidence])
    normalized = []
    retained_count = 0
    cold_evidence = []
    for evidence in hypotheses.values():
        for record in evidence:
            record["evidence_id"] = _sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode())
            cold_evidence.append(record)
    for factor in sorted(hypotheses, key=lambda item: (item[0], int(item[1:]))):
        evidence = sorted(hypotheses[factor], key=lambda item: (item["source_path"], item["source_pointer"]))
        by_kind = defaultdict(list)
        for item in evidence:
            by_kind[item["evidence_kind"]].append(item)
        def priority(item: dict) -> tuple:
            path = item["source_path"]
            # Prefer released decision packets and formal full-window summaries over
            # duplicated checkpoints and fold-level fragments, with stable tie breaks.
            score = (40 * ("decision_packet" in path) + 30 * ("official_validation" in path)
                     + 20 * ("formal_summary" in path or "classification" in path)
                     + 10 * bool(item["mechanism"]) + 5 * bool(item["formula"])
                     + len(item["metrics"]))
            return (-score, path, item["source_pointer"])
        compact_evidence = [sorted(items, key=priority)[0] for _, items in sorted(by_kind.items())]
        # A contradictory official validation is kept as a second result; this avoids
        # turning an earlier passing standalone result into an unconditional admission.
        for kind in ("official_validation", "official_runtime"):
            items = sorted(by_kind.get(kind, []), key=priority)
            if len(items) > 1 and items[1]["status"] != items[0]["status"]:
                compact_evidence.append(items[1])
        retained_count += len(compact_evidence)
        declared_directions = {item["direction"] for item in evidence if item["direction"] is not None}
        direction = next(iter(declared_directions)) if len(declared_directions) == 1 else None
        direction_status = "recorded_unambiguous" if direction is not None else (
            "conflicting_direction_evidence" if declared_directions else "needs_direction_evidence")
        direction_sources = [{"direction": item["direction"], "source_path": item["source_path"],
                              "source_sha256": item["source_sha256"], "source_pointer": item["source_pointer"]}
                             for item in evidence if item["direction"] is not None]
        # Keep every direction declaration in the cold index; hot provenance prefers
        # frozen candidate specs or explicit Panda mappings, without inventing a sign.
        direction_sources.sort(key=lambda item: (0 if "candidate_specs" in item["source_pointer"] or
                                                "panda_mappings" in item["source_pointer"] else 1,
                                                item["source_path"], item["source_pointer"]))
        fields = sorted({field for item in evidence for field in item["fields"]})
        normalized.append({"hypothesis_id": factor, "family": families.get(factor,
                           next((item["family"] for item in evidence if item["family"]), "unclassified")),
                           "mechanism": next((item["mechanism"] for item in evidence if item["mechanism"]), ""),
                           "formula": next((item["formula"] for item in evidence if item["formula"]), ""),
                           "fields": fields, "direction": direction, "direction_status": direction_status,
                           "direction_evidence": direction_sources[:2],
                           "data_requirements": ["quantaxis_field_mapping_pending", "source_pit_validation_pending",
                                                 "official_runtime_parity_pending"] + ([] if fields else ["needs_field_evidence"])
                                                + ([] if direction is not None else ["needs_direction_evidence"]),
                           "decision": "research_prior_requires_revalidation",
                           "historical_evidence_count": len(evidence),
                           "failure_reasons": sorted({failure for item in evidence for failure in item["failure_reasons"]}),
                           "evidence": compact_evidence})
    evidence_counts = dict(sorted(Counter(record["evidence_kind"] for evidence in
                                         hypotheses.values() for record in evidence).items()))
    by_id = {item["hypothesis_id"]: item for item in normalized}
    focus = ["F141", "F174", "F253", "F414", "F415", "F66", "F62", "F63", "F76",
             "F409", "F105", "F193", "F287", "F401", "F402"]
    focus.extend(item["hypothesis_id"] for item in normalized
                 if any(evidence["evidence_kind"].startswith("official") for evidence in item["evidence"]))
    focus.extend(factor for item in no_repeat for factor in item["examples"][:1])
    focus.extend(item["hypothesis_id"] for item in reversed(normalized))
    slots = max(10, 80 - len(directions) - len(no_repeat))
    hot_hypotheses = []
    selected = set()
    for factor in focus:
        if factor in selected or factor not in by_id:
            continue
        item = by_id[factor]
        selected.add(factor)
        hot_hypotheses.append({key: item[key] for key in ("hypothesis_id", "family", "decision",
                                                         "historical_evidence_count", "direction", "direction_status",
                                                         "direction_evidence", "data_requirements")})
        hot = hot_hypotheses[-1]
        hot.update({"mechanism": item["mechanism"][:300], "formula": item["formula"][:600],
                    "fields": item["fields"], "failure_reasons": item["failure_reasons"][:12],
                    "evidence_refs": [{"evidence_id": evidence["evidence_id"],
                                       "evidence_kind": evidence["evidence_kind"],
                                       "evidence_status": evidence["evidence_status"]}
                                      for evidence in item["evidence"]]})
        if len(hot_hypotheses) >= slots:
            break
    memory = {"schema_version": SCHEMA_VERSION, "as_of": _safe_text(knowledge.get("as_of", "unknown"), 30),
              "multiple_testing_denominator": denominator,
              "denominator_policy": "retain_max_recorded_history_and_recovered_F_identifier; never_reset_after_compaction",
              "denominator_evidence": sorted((item for item in denominator_evidence if item["value"] == denominator),
                                             key=lambda item: item["source_path"])[:3],
              "source_count": len(source_index), "hypothesis_count": len(normalized),
              "retained_evidence_count": retained_count,
              "evidence_counts": evidence_counts, "starting_directions": directions,
              "source_findings": source_findings[:12],
              "no_repeat_rules": no_repeat, "hypotheses": hot_hypotheses,
              "cold_evidence_index": "evidence.jsonl",
              "cold_index_policy": "all_deduplicated_structured_evidence_and_unregistered_source_findings; resolve_evidence_id_on_demand",
              "interpretation": {"legacy_gate_failure": "prior_policy_rejection_not_permanent_mechanism_ban",
                                 "source_coverage_failure": "data_availability_or_PIT_issue_not_alpha_falsification",
                                 "execution_failure": "runtime_issue_not_alpha_falsification",
                                 "official_validation": "only_explicitly_reported_checks; cost_and_pool_proxies_stay_proxies",
                                 "legacy_proxy_backtest": "must_revalidate_quantaxis_data_and_official_runtime"}}
    output_dir.mkdir(parents=True, exist_ok=True)
    # Enforce a predictable agent-context size without deleting cold evidence.
    while len(json.dumps(memory, ensure_ascii=False, indent=2).encode()) > 99_000 and len(memory["hypotheses"]) > 5:
        memory["hypotheses"].pop()
    cold_rows = [dict(item, record_type="hypothesis_evidence") for item in cold_evidence]
    cold_rows.extend(dict(item, record_type="source_finding") for item in source_findings)
    generated = {
        "memory.json": (json.dumps(memory, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
        "memory.md": _markdown(memory).encode(),
        "source_index.jsonl": ("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True)
                                        for item in sorted(source_index, key=lambda item: item["source_path"])) + "\n").encode(),
        "evidence.jsonl": ("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                                    for item in sorted(cold_rows, key=lambda item: (item["source_path"], item["source_pointer"]))) + "\n").encode(),
    }
    for name, raw in generated.items():
        (output_dir / name).write_bytes(raw)
    manifest = {"schema_version": SCHEMA_VERSION, "operation": "sanitized_research_context_compaction",
                "raw_evidence_modified": False, "raw_evidence_deleted": False,
                "multiple_testing_denominator": denominator, "source_count": len(source_index),
                "input_bytes_indexed": sum(item["bytes"] for item in source_index),
                "hypothesis_count": len(normalized), "evidence_counts": evidence_counts,
                "retained_evidence_count": retained_count,
                "cold_evidence_count": len(cold_evidence),
                "hot_hypothesis_count": len(memory["hypotheses"]),
                "source_finding_count": len(source_findings),
                "semantic_duplicates_removed": len(all_records) - len(seen),
                "parse_errors": parse_errors,
                "excluded": ["account_and_billing_artifacts", "credentials_and_tokens", "raw_panels",
                             "large_tables_over_2MB", "raw_filing_corpus", "broken_environments"],
                "generated_files": {name: {"bytes": len(raw), "sha256": _sha256(raw)}
                                    for name, raw in generated.items()},
                "provenance_root": "private_legacy_workspace_relative_paths"}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False,
                                                        sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(compact_workspace(args.workspace, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
