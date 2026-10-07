"""Replay the known native Python contract before an official account mutation.

This is a local compatibility check, not a sandbox or an assurance about remote
data, all-A coverage, server resources, billing, or portfolio admission. The
wrapper below implements only observed attribute delegation. Unsupported SDK
operators fail locally instead of being silently replaced with guessed ones.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
from typing import Any

import numpy as np
import pandas as pd


SCHEMA = "panda-native-preflight-v1"
LIMITATION = ("Known local FactorSeries attribute-delegation/date-first contract only; "
              "not remote all-A input equivalence, server-success or billing assurance. "
              "Grouping uses conservative deterministic no-jitter qcut; remote tradeability "
              "filters, outlier cleaning, standardization and random jitter are not simulated.")
GROUPING_SOURCE = ("https://github.com/PandaAI-Tech/panda_factor/blob/"
                   "a783e69732da1f9ffc93844dc522375a1f67c507/"
                   "panda_factor/panda_factor/analysis/factor_analysis_workflow.py")


class NativePreflightError(ValueError):
    """An unpaid local check prevented a new official job."""


class FactorSeriesFixture:
    def __init__(self, series: pd.Series):
        self.series = series

    def __getattr__(self, name: str):
        return getattr(self.series, name)


class _FactorFixture:
    def print(self, *args, **kwargs):
        # A candidate's diagnostics do not become proof or leak into account logs.
        pass


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _definition(candidate: dict, window: dict, cycle: int, groups: int) -> dict:
    result = {key: candidate.get(key) for key in ("code", "formula", "direction")}
    result.update(window=window, cycle=cycle, groups=groups)
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _artifact(path: str | Path) -> dict:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise NativePreflightError("Preflight evidence artifact is missing")
    return {"path": str(resolved), "sha256": _sha(resolved)}


def _check_artifact(artifact: Any, label: str):
    if not isinstance(artifact, dict) or not artifact.get("path") or not artifact.get("sha256"):
        raise NativePreflightError(f"Native preflight {label} evidence is missing")
    try:
        actual = _sha(artifact["path"])
    except OSError as exc:
        raise NativePreflightError(f"Native preflight {label} evidence is missing") from exc
    if actual != artifact["sha256"]:
        raise NativePreflightError(f"Native preflight {label} evidence changed")


def _parameters(window: dict, cycle: int, groups: int):
    if type(cycle) is not int or not 1 <= cycle <= 10:
        raise NativePreflightError("Native preflight requires a valid frozen cycle")
    if type(groups) is not int or not 2 <= groups <= 10:
        raise NativePreflightError("Native preflight requires valid frozen groups")
    try:
        start, end = pd.Timestamp(window["start"]), pd.Timestamp(window["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NativePreflightError("Native preflight requires a dated window") from exc
    if pd.isna(start) or pd.isna(end) or start > end or start.tz or end.tz:
        raise NativePreflightError("Native preflight requires a valid naive-date window")
    return start, end


def _replay(code: str, fixture: str, fields: list[str], window: dict,
            cycle: int, groups: int, policy: dict) -> dict:
    start, end = _parameters(window, cycle, groups)
    if (not isinstance(fields, list) or not fields or len(set(fields)) != len(fields)
            or any(not isinstance(field, str) or not field or field in ("date", "symbol") for field in fields)):
        raise NativePreflightError("Native preflight requires explicit input fields")
    frame = pd.read_parquet(fixture)
    if not {"date", "symbol", *fields}.issubset(frame.columns):
        raise NativePreflightError("Native fixture lacks required source fields")
    frame = frame[["date", "symbol", *fields]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame.empty or frame[["date", "symbol"]].isna().any().any():
        raise NativePreflightError("Native fixture has empty or invalid index observations")
    if frame["date"].dt.tz is not None or not frame["date"].eq(frame["date"].dt.normalize()).all():
        raise NativePreflightError("Native fixture requires naive trading dates")
    if not frame["symbol"].map(lambda value: isinstance(value, str) and bool(value)).all():
        raise NativePreflightError("Native fixture requires string symbols")
    frame = frame.set_index(["date", "symbol"])
    if not frame.index.is_unique:
        raise NativePreflightError("Native fixture has duplicate date/symbol observations")
    in_window = frame.index.get_level_values("date").to_series(index=frame.index).between(start, end)
    fixture_dates = frame.index.get_level_values("date")[in_window].unique().sort_values()
    if len(fixture_dates) < max(2, cycle):
        raise NativePreflightError("Native fixture has insufficient window dates")
    # A tail-only sample cannot certify a full requested dispatch window.
    if fixture_dates.min() > start + pd.Timedelta(days=7) or fixture_dates.max() < end - pd.Timedelta(days=7):
        raise NativePreflightError("Native fixture does not span the requested window")
    for field in fields:
        if not pd.api.types.is_numeric_dtype(frame[field]) or pd.api.types.is_complex_dtype(frame[field]):
            raise NativePreflightError("Native fixture requires real numeric input fields")
        if np.isinf(frame[field].to_numpy(dtype=float)).any():
            raise NativePreflightError("Native fixture contains infinite input values")
    # Local repository imports could succeed here but fail in the remote worker.
    # This deliberately supports only self-contained numeric native submissions.
    allowed_imports = {"numpy", "pandas", "math", "typing", "__future__",
                       "statistics", "itertools", "functools", "collections", "datetime"}
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [node.module.split(".")[0]] if node.module and not node.level else [""]
        else:
            continue
        if any(root not in allowed_imports for root in roots):
            raise NativePreflightError("Native dependency is outside the known self-contained replay contract")
    namespace = {"Factor": _FactorFixture, "__name__": "native_preflight_candidate"}
    exec(compile(code, "<native-preflight-candidate>", "exec"), namespace)
    classes = [value for value in namespace.values()
               if isinstance(value, type) and value is not _FactorFixture
               and issubclass(value, _FactorFixture)]
    if len(classes) != 1:
        raise NativePreflightError("Native source must define exactly one Factor subclass")
    inputs = {field: FactorSeriesFixture(frame[field]) for field in fields}
    output = classes[0]().calculate(inputs)
    if not isinstance(output, pd.Series) or output.name != "value":
        raise NativePreflightError("Native output must be a Series named value")
    if (not isinstance(output.index, pd.MultiIndex) or list(output.index.names) != ["date", "symbol"]
            or not output.index.is_unique or not output.index.equals(frame.index)):
        raise NativePreflightError("Native output must restore the exact date-first input index")
    if not pd.api.types.is_numeric_dtype(output) or pd.api.types.is_complex_dtype(output):
        raise NativePreflightError("Native output must contain real numeric values")
    values = output.to_numpy(dtype=float)
    if np.isinf(values).any():
        raise NativePreflightError("Native output contains infinite values")
    # The public process_result names these two levels then filters by date.
    filtered = output[in_window]
    finite = filtered[np.isfinite(filtered.to_numpy(dtype=float))]
    if finite.empty:
        raise NativePreflightError("Native output has no finite values after window filtering")
    count = finite.groupby(level="date").size().reindex(fixture_dates, fill_value=0)
    unique = finite.groupby(level="date").nunique().reindex(fixture_dates, fill_value=0)
    if unique.max() < 2:
        raise NativePreflightError("Native output is cross-sectionally constant")
    if unique.max() < groups:
        raise NativePreflightError("Native output has fewer distinct values than requested groups")
    first = count[count.gt(0)].index.min()
    eligible_dates = fixture_dates[fixture_dates >= first]
    # The pinned public workflow first rejects nunique < group_cnt, then adds
    # random N(0,1e-10) jitter before qcut and skips ValueError dates. We cannot
    # reproduce unknown remote filters/cleaning or guarantee random edges. A
    # deterministic no-jitter cut is an explicitly stricter local smoke test.
    quantile_ready = pd.Series(False, index=eligible_dates)
    for date, daily_values in finite.groupby(level="date"):
        if date not in quantile_ready.index or len(daily_values) < groups or daily_values.nunique() < groups:
            continue
        try:
            labels = pd.qcut(daily_values.to_numpy(dtype=float), q=groups,
                             labels=False, duplicates="raise")
            quantile_ready.loc[date] = len(np.unique(labels)) == groups
        except ValueError:
            continue
    good_dates = (count.reindex(eligible_dates).ge(groups)
                  & unique.reindex(eligible_dates).ge(groups) & quantile_ready)
    minimum = max(2, cycle)
    coverage = float(good_dates.mean())
    if good_dates.sum() < minimum or coverage < policy["minimum_post_warmup_date_coverage"]:
        raise NativePreflightError("Native output lacks post-warmup date/group coverage")
    by_date_total = filtered.groupby(level="date").size().reindex(eligible_dates)
    cross_coverage = float((count.reindex(eligible_dates) / by_date_total).median())
    if cross_coverage < policy["minimum_median_symbol_coverage"]:
        raise NativePreflightError("Native output lacks cross-sectional source coverage")
    return {"contract": "attribute-delegation/date-first/Series-value/date-filter",
            "factor_class": classes[0].__name__, "input_rows": len(frame), "input_fields": fields,
            "input_index_names": list(frame.index.names), "input_symbols": int(frame.index.get_level_values("symbol").nunique()),
            "window_fixture_dates": len(fixture_dates), "first_finite_date": first.isoformat(),
            "finite_window_rows": len(finite), "usable_group_dates": int(good_dates.sum()),
            "post_warmup_date_coverage": coverage, "median_symbol_coverage": cross_coverage,
            "minimum_valid_symbols_on_usable_dates": int(count.reindex(eligible_dates)[good_dates].min()),
            "minimum_distinct_values_on_usable_dates": int(unique.reindex(eligible_dates)[good_dates].min()),
            "grouping_smoke_test": {"method": "deterministic_no_jitter_qcut_all_requested_groups_nonempty",
                                    "requested_groups": groups, "quantile_ready_dates": int(quantile_ready.sum()),
                                    "public_source": GROUPING_SOURCE,
                                    "public_workflow_adds_random_jitter": True,
                                    "remote_filtering_cleaning_and_jitter_simulated": False},
            "output_index_restored": True, "date_filter_nonempty": True,
            "output_sha256": hashlib.sha256(pd.util.hash_pandas_object(output, index=True).values.tobytes()).hexdigest()}


def build_native_preflight(candidate: dict, window: dict, cycle: int, groups: int,
                           fixture_path: str | Path, source_evidence: list[str | Path],
                           contract_evidence: list[str | Path], receipt_path: str | Path,
                           fields: list[str] | None = None) -> dict:
    """Save an evidence-bound replay, including explicit failure records.

    fixture_path is a source-derived parquet with date/symbol and required input
    fields. Source and observed-contract files are retained as hashed evidence.
    No account, CLI, network, budget or experiment ledger operation occurs here.
    """
    code = candidate.get("code")
    if not isinstance(code, str) or not code.strip() or candidate.get("formula"):
        raise NativePreflightError("Native preflight applies to an exact Python code candidate")
    if candidate.get("direction") not in (0, 1):
        raise NativePreflightError("Native preflight requires a frozen direction")
    if not source_evidence or not contract_evidence:
        raise NativePreflightError("Source and observed-contract evidence are required")
    fields = fields or ["close"]
    receipt = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
               "definition_fingerprint": _digest(_definition(candidate, window, cycle, groups)),
               "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
               "window": window, "cycle": cycle, "groups": groups, "direction": candidate.get("direction"),
               "harness": _artifact(__file__), "fixture": _artifact(fixture_path),
               "source_evidence": [_artifact(path) for path in source_evidence],
               "contract_evidence": [_artifact(path) for path in contract_evidence],
               "fields": fields, "policy": {"minimum_post_warmup_date_coverage": .8,
                                             "minimum_median_symbol_coverage": .8},
               "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
               "limitation": LIMITATION}
    try:
        receipt["proof"] = _replay(code, receipt["fixture"]["path"], fields, window, cycle, groups, receipt["policy"])
        receipt["passed"] = True
    except Exception as exc:
        receipt.update(passed=False, failure={"type": type(exc).__name__, "message": str(exc)})
    target = Path(receipt_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def verify_native_preflight(candidate: dict, window: dict, cycle: int, groups: int) -> dict:
    """Recompute the proof; a hand-written passed=true is never sufficient."""
    path = candidate.get("native_preflight_path")
    if not isinstance(path, str) or not path:
        raise NativePreflightError("A current native preflight receipt is required before account access")
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativePreflightError("Native preflight receipt is missing or invalid") from exc
    if (not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
            or receipt.get("passed") is not True or not isinstance(receipt.get("proof"), dict)):
        raise NativePreflightError("Native preflight has no successful replay proof")
    definition = _digest(_definition(candidate, window, cycle, groups))
    if (receipt.get("definition_fingerprint") != definition
            or receipt.get("code_sha256") != hashlib.sha256(candidate["code"].encode()).hexdigest()
            or receipt.get("window") != window or receipt.get("cycle") != cycle
            or receipt.get("groups") != groups or receipt.get("direction") != candidate.get("direction")):
        raise NativePreflightError("Native preflight source or dispatch parameters changed")
    if receipt.get("harness") != _artifact(__file__):
        raise NativePreflightError("Native preflight harness changed; regenerate the receipt")
    for label in ("source_evidence", "contract_evidence"):
        artifacts = receipt.get(label)
        if not isinstance(artifacts, list) or not artifacts:
            raise NativePreflightError(f"Native preflight {label} is missing")
        for artifact in artifacts:
            _check_artifact(artifact, label)
    _check_artifact(receipt.get("fixture"), "fixture")
    fixed_policy = {"minimum_post_warmup_date_coverage": .8, "minimum_median_symbol_coverage": .8}
    if receipt.get("policy") != fixed_policy:
        raise NativePreflightError("Native preflight coverage policy changed")
    try:
        proof = _replay(candidate["code"], receipt["fixture"]["path"], receipt.get("fields"),
                        window, cycle, groups, fixed_policy)
    except Exception as exc:
        raise NativePreflightError("Native preflight replay failed; no official job was dispatched") from exc
    if proof != receipt["proof"]:
        raise NativePreflightError("Native preflight replay evidence no longer matches the receipt")
    return {"receipt_path": str(Path(path).resolve()), "receipt_sha256": _sha(path),
            "definition_fingerprint": definition, "proof": proof, "limitation": LIMITATION}
