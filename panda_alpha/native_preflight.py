"""Replay the known native Python contract before an official account mutation.

This is a local compatibility check, not a sandbox or an assurance about remote
data, all-A coverage, server resources, billing, or portfolio admission. The
wrapper below implements only observed attribute delegation. Unsupported SDK
operators fail locally instead of being silently replaced with guessed ones.
"""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import platform
from typing import Any

import numpy as np
import pandas as pd


SCHEMA = "panda-native-preflight-v1"
STRICT_PURPOSE = "full_universe_strict"
SOURCE_TRANSFER_PURPOSE = "source_qualified_transfer_research"
PROJECTION_RESEARCH_PURPOSE = "source_qualified_projection_research"
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
    purpose = _purpose(candidate)
    if purpose != STRICT_PURPOSE:
        result["native_preflight_purpose"] = purpose
    if purpose == PROJECTION_RESEARCH_PURPOSE:
        result["native_evaluation_contract"] = candidate.get("native_evaluation_contract")
    return result


def _purpose(candidate: dict) -> str:
    purpose = candidate.get("native_preflight_purpose", STRICT_PURPOSE)
    if purpose not in (STRICT_PURPOSE, SOURCE_TRANSFER_PURPOSE, PROJECTION_RESEARCH_PURPOSE):
        raise NativePreflightError("Native preflight requires a known explicit coverage purpose")
    return purpose


def _coverage_policy(purpose: str) -> dict:
    if purpose == STRICT_PURPOSE:
        return {"minimum_post_warmup_date_coverage": .8,
                "minimum_median_symbol_coverage": .8}
    if purpose == SOURCE_TRANSFER_PURPOSE:
        return {"minimum_requested_date_coverage": 1.0,
                "minimum_valid_assets_per_requested_date": 2000}
    if purpose == PROJECTION_RESEARCH_PURPOSE:
        return {"minimum_prior_price_rows": 258,
                "minimum_decision_date_coverage": 1.0,
                "minimum_finite_assets_per_decision_date": 10,
                "minimum_distinct_values_per_decision_date": 10,
                "first_finite_trimming_allowed": False}
    raise NativePreflightError("Native preflight coverage purpose is unknown")


def _scope(purpose: str) -> dict:
    if purpose == PROJECTION_RESEARCH_PURPOSE:
        return {"purpose": purpose, "research_only": True,
                "source_limited_exploration": True, "full_A_certified": False,
                "full_PIT_certified": False, "admission_qualified": False,
                "coverage_denominator": "all_evaluation_projection_symbol_rows",
                "remote_input_and_formation_grid_certified": False}
    return {"purpose": purpose,
            "research_only": purpose == SOURCE_TRANSFER_PURPOSE,
            "full_A_certified": False, "admission_qualified": False,
            "coverage_denominator": "all_fixture_symbol_rows"}


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


def _fixture_reader_binding(reader) -> dict:
    """Explicit trusted local injection; never import/eval a reader from data."""
    if not inspect.isfunction(reader) or not inspect.getsourcefile(reader):
        raise NativePreflightError("Projection fixture reader must be an explicit source-bound function")
    return {"artifact": _artifact(inspect.getsourcefile(reader)),
            "qualname": reader.__qualname__}


def _projection_contract(contract, window, cycle, groups) -> tuple:
    if not isinstance(contract, dict):
        raise NativePreflightError("Projection research requires an explicit native evaluation contract")
    if contract.get("input_window") != window:
        raise NativePreflightError("Projection input window must equal the actual build window")
    input_start, input_end = _parameters(window, cycle, groups)
    evaluation_start, evaluation_end = _parameters(contract.get("evaluation_window", {}), cycle, groups)
    if (input_start >= evaluation_start or evaluation_end != input_end
            or contract.get("holding_end") != contract["evaluation_window"]["end"]):
        raise NativePreflightError("Projection input/evaluation/holding endpoints do not match")
    if contract.get("warmup", {}).get("minimum_prior_price_rows") != 258:
        raise NativePreflightError("Projection requires the frozen 258 prior price rows (259 including evaluation)")
    for label in ("input_calendar", "source_qualification"):
        _check_artifact(contract.get(label), label)
    calendar_data = json.loads(Path(contract["input_calendar"]["path"]).read_text(encoding="utf-8"))
    raw_dates = calendar_data.get("dates") if isinstance(calendar_data, dict) else calendar_data
    formations = contract.get("formation_dates")
    for label, values in (("calendar", raw_dates), ("formation dates", formations)):
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value for value in values)):
            raise NativePreflightError(f"Projection needs an explicit frozen {label} list")
    calendar = pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="raise"))
    formation_dates = pd.DatetimeIndex(pd.to_datetime(formations, errors="raise"))
    for label, dates in (("calendar", calendar), ("formation dates", formation_dates)):
        if (dates.has_duplicates or not dates.is_monotonic_increasing or dates.hasnans
                or dates.tz is not None or not dates.equals(dates.normalize())):
            raise NativePreflightError(f"Projection {label} must be unique ordered naive dates")
    if (calendar[0] != input_start or calendar[-1] != input_end
            or evaluation_start not in calendar or evaluation_end not in calendar):
        raise NativePreflightError("Projection calendar must preserve exact input and evaluation endpoints")
    prior_dates = calendar[calendar < evaluation_start]
    if len(prior_dates) < 258:
        raise NativePreflightError("Projection has fewer than 258 prior input calendar rows")
    if (formation_dates[0] != evaluation_start or not formation_dates.isin(calendar).all()
            or formation_dates[-1] > evaluation_end):
        raise NativePreflightError("Projection formations must start at the fixed evaluation endpoint")
    evaluation_calendar = calendar[(calendar >= evaluation_start) & (calendar <= evaluation_end)]
    if len(evaluation_calendar) <= cycle + 1:
        raise NativePreflightError("Projection lacks complete decision and holding calendar dates")
    # Public close/open label uses shift(-(cycle+1))/shift(-1). Its necessary
    # tail comes from the independent full calendar, never a hand-shortened
    # formation list or observed first finite output. Remote sparse per-stock
    # shifts, tradeability and cleaner behavior remain separately unverified.
    decision_dates = evaluation_calendar[:-(cycle + 1)]
    if not formation_dates.equals(decision_dates[::cycle]):
        raise NativePreflightError("Projection formations must equal every frozen cycle on all decision dates")
    return evaluation_start, evaluation_end, calendar, formation_dates, decision_dates


def _projection_proof(output, frame, fields, contract, window, cycle, groups,
                      policy, factor_class, input_hash) -> dict:
    start, end, calendar, formations, decisions = _projection_contract(contract, window, cycle, groups)
    input_dates = frame.index.get_level_values("date").unique().sort_values()
    build_start = pd.Timestamp(window["start"])
    declared_dates = input_dates[(input_dates >= build_start) & (input_dates <= end)]
    if not declared_dates.equals(calendar) or input_dates[-1] > end:
        raise NativePreflightError("Projection fixture dates differ from the independent input calendar")
    if "close" not in fields:
        raise NativePreflightError("Projection needs explicit close prices for warmup support diagnostics")
    expected_index = frame.index[frame.index.get_level_values("date").to_series(index=frame.index).between(start, end)]
    mask = pd.read_parquet(contract["source_qualification"]["path"])
    required = {"date", "symbol", "source_qualified", "expected_finite"}
    if not required.issubset(mask.columns):
        raise NativePreflightError("Projection source mask needs source_qualified and expected_finite booleans")
    mask = mask[list(required)].copy()
    mask["date"] = pd.to_datetime(mask["date"], errors="raise")
    if (mask[["date", "symbol"]].isna().any().any()
            or not mask["symbol"].map(lambda x: isinstance(x, str) and bool(x)).all()):
        raise NativePreflightError("Projection source mask has invalid row identities")
    mask = mask.set_index(["date", "symbol"])
    if (not mask.index.is_unique or len(mask) != len(expected_index)
            or not mask.index.difference(expected_index).empty
            or not expected_index.difference(mask.index).empty):
        raise NativePreflightError("Projection source mask must retain every evaluation input row")
    mask = mask.reindex(expected_index)
    for column in ("source_qualified", "expected_finite"):
        if not pd.api.types.is_bool_dtype(mask[column]) or mask[column].isna().any():
            raise NativePreflightError("Projection source qualification must be explicit nonmissing booleans")
    if (mask.expected_finite & ~mask.source_qualified).any():
        raise NativePreflightError("Projection expected finite rows must be source qualified")
    values = output.to_numpy(dtype=float)
    observed = np.isfinite(values)
    if not np.array_equal(observed, mask.expected_finite.to_numpy(dtype=bool)):
        raise NativePreflightError("Projection finite/NaN support differs from the frozen source computability mask")
    if not np.isnan(values[~observed]).all():
        raise NativePreflightError("Projection unqualified or uncomputable rows must remain genuine NaN")
    finite = output[observed]
    count = finite.groupby(level="date").size().reindex(decisions, fill_value=0)
    distinct = finite.groupby(level="date").nunique().reindex(decisions, fill_value=0)
    minimum_assets = max(groups, policy["minimum_finite_assets_per_decision_date"])
    minimum_distinct = max(groups, policy["minimum_distinct_values_per_decision_date"])
    if (count.lt(minimum_assets).any() or distinct.lt(minimum_distinct).any()):
        raise NativePreflightError("Projection lacks ten finite/distinct values on every decision date; grid would drift")
    for day in decisions:
        try:
            labels = pd.qcut(finite.xs(day, level="date").to_numpy(dtype=float),
                             q=groups, labels=False, duplicates="raise")
        except ValueError as exc:
            raise NativePreflightError("Projection lacks deterministic group coverage on every decision date") from exc
        if len(np.unique(labels)) != groups:
            raise NativePreflightError("Projection has an empty requested group on a decision date")
    # Calendar count is not per-peer price sufficiency. Missing dates/prices
    # remain missing in this independent support diagnostic; no forward fill.
    prices = frame["close"].unstack("symbol").reindex(calendar)
    valid = np.isfinite(prices) & prices.gt(0)
    prior = valid.loc[calendar < start]
    initial_position = calendar.get_loc(start)
    trailing = valid.iloc[:initial_position + 1].iloc[::-1].cumprod().sum(axis=0)
    support = [{"symbol": str(symbol), "positive_finite_prior_price_rows": int(prior[symbol].sum()),
                "missing_or_invalid_prior_price_rows": int((~prior[symbol]).sum()),
                "consecutive_positive_prices_through_evaluation_start": int(trailing[symbol])}
               for symbol in prices.columns]
    first_mask = mask.xs(start, level="date")
    uncomputable = set(first_mask.index[first_mask.source_qualified & ~first_mask.expected_finite])
    support_summary = {
        "peers": len(support),
        "positive_prior_price_rows_histogram": dict(Counter(str(row["positive_finite_prior_price_rows"]) for row in support)),
        "missing_prior_price_rows_histogram": dict(Counter(str(row["missing_or_invalid_prior_price_rows"]) for row in support)),
        "consecutive_prices_through_first_evaluation_histogram": dict(Counter(str(row["consecutive_positive_prices_through_evaluation_start"]) for row in support)),
        "first_evaluation_source_qualified_but_uncomputable_peers": len(uncomputable),
        "uncomputable_examples": [row for row in support if row["symbol"] in uncomputable][:10],
        "all_peer_support_sha256": _digest(support),
        "full_support_retained_only_in_memory": True,
    }
    all_dates = calendar[(calendar >= start) & (calendar <= end)]
    holding_tail = all_dates[len(decisions):]
    total = output.groupby(level="date").size().reindex(all_dates, fill_value=0)
    finite_all = finite.groupby(level="date").size().reindex(all_dates, fill_value=0)
    first = finite.index.get_level_values("date").min()
    return {"contract": "long-input/exact-fixed-evaluation-index-projection",
            "factor_class": factor_class, "input_fields": fields,
            "input_rows": len(frame), "input_frame_sha256": input_hash,
            "input_index_names": list(frame.index.names),
            "input_symbols": int(frame.index.get_level_values("symbol").nunique()),
            "input_calendar_dates": len(calendar), "prior_input_calendar_rows": int((calendar < start).sum()),
            "additional_prebuild_input_dates": int((input_dates < build_start).sum()),
            "peer_price_support": support_summary,
            "peer_full_F141_computability_certified": False,
            "evaluation_window": contract["evaluation_window"], "holding_end": contract["holding_end"],
            "evaluation_output_rows": len(output), "evaluation_fixture_dates": len(all_dates),
            "evaluation_start_rows": int(total.loc[start]), "holding_end_rows": int(total.loc[end]),
            "holding_end_finite_rows": int(finite_all.loc[end]),
            "holding_tail_dates": [date.date().isoformat() for date in holding_tail],
            "holding_tail_finite_rows": int(finite_all.loc[holding_tail].sum()),
            "holding_tail_NaN_rows": int((total - finite_all).loc[holding_tail].sum()),
            "finite_evaluation_rows": len(finite), "first_finite_date": first.isoformat(),
            "source_qualified_evaluation_rows": int(mask.source_qualified.sum()),
            "expected_finite_mask_matched": True, "genuine_missing_rows": int((~observed).sum()),
            "decision_dates": [date.date().isoformat() for date in decisions],
            "formation_dates": [date.date().isoformat() for date in formations],
            "usable_group_dates": len(decisions), "decision_date_coverage": 1.0,
            "minimum_valid_symbols_on_decision_dates": int(count.min()),
            "minimum_distinct_values_on_decision_dates": int(distinct.min()),
            "median_symbol_coverage": float((finite_all / total).median()),
            "coverage_policy_purpose": PROJECTION_RESEARCH_PURPOSE,
            "coverage_evaluation_basis": "all_frozen_decision_dates_without_first_finite_trimming",
            "grouping_smoke_test": {"method": "deterministic_no_jitter_qcut_all_requested_groups_nonempty",
                                    "requested_groups": groups, "quantile_ready_dates": len(decisions),
                                    "public_source": GROUPING_SOURCE,
                                    "remote_tradeability_label_filtering_and_jitter_simulated": False},
            "output_index_is_exact_evaluation_projection": True,
            "full_PIT_certified": False, "admission_qualified": False,
            "output_sha256": hashlib.sha256(pd.util.hash_pandas_object(output, index=True).values.tobytes()).hexdigest()}


def _replay(code: str, fixture: str, fields: list[str], window: dict,
            cycle: int, groups: int, policy: dict, purpose: str = STRICT_PURPOSE,
            evaluation_contract: dict | None = None, fixture_reader=None) -> dict:
    start, end = _parameters(window, cycle, groups)
    projected = purpose == PROJECTION_RESEARCH_PURPOSE
    if fixture_reader is not None and not projected:
        raise NativePreflightError("A trusted descriptor reader is only supported for explicit projection research")
    if projected:
        start, end, _, _, _ = _projection_contract(evaluation_contract, window, cycle, groups)
    if (not isinstance(fields, list) or not fields or len(set(fields)) != len(fields)
            or any(not isinstance(field, str) or not field or field in ("date", "symbol") for field in fields)):
        raise NativePreflightError("Native preflight requires explicit input fields")
    frame = fixture_reader(Path(fixture)) if fixture_reader is not None else pd.read_parquet(fixture)
    if not isinstance(frame, pd.DataFrame):
        raise NativePreflightError("Native fixture reader must return a DataFrame")
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
    input_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
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
    expected_index = frame.index[in_window] if projected else frame.index
    if (not isinstance(output.index, pd.MultiIndex) or list(output.index.names) != ["date", "symbol"]
            or not output.index.is_unique or not output.index.equals(expected_index)):
        message = ("Native output must restore the exact evaluation projection of the full input index"
                   if projected else "Native output must restore the exact date-first input index")
        raise NativePreflightError(message)
    if not pd.api.types.is_numeric_dtype(output) or pd.api.types.is_complex_dtype(output):
        raise NativePreflightError("Native output must contain real numeric values")
    values = output.to_numpy(dtype=float)
    if np.isinf(values).any():
        raise NativePreflightError("Native output contains infinite values")
    if projected:
        return _projection_proof(output, frame, fields, evaluation_contract, window, cycle, groups,
                                 policy, classes[0].__name__, input_hash)
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
    eligible_dates = (fixture_dates if purpose == SOURCE_TRANSFER_PURPOSE else
                      fixture_dates[fixture_dates >= first])
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
    required_date_coverage = (policy["minimum_requested_date_coverage"]
                             if purpose == SOURCE_TRANSFER_PURPOSE else
                             policy["minimum_post_warmup_date_coverage"])
    if good_dates.sum() < minimum or coverage < required_date_coverage:
        message = ("Native source transfer lacks date/group coverage on every requested date"
                   if purpose == SOURCE_TRANSFER_PURPOSE else
                   "Native output lacks post-warmup date/group coverage")
        raise NativePreflightError(message)
    if (purpose == SOURCE_TRANSFER_PURPOSE and
            count.reindex(fixture_dates).min() < policy["minimum_valid_assets_per_requested_date"]):
        raise NativePreflightError("Native source transfer lacks 2000 valid assets on every requested date")
    by_date_total = filtered.groupby(level="date").size().reindex(eligible_dates)
    cross_coverage = float((count.reindex(eligible_dates) / by_date_total).median())
    if purpose == STRICT_PURPOSE and cross_coverage < policy["minimum_median_symbol_coverage"]:
        raise NativePreflightError("Native output lacks cross-sectional source coverage")
    return {"contract": "attribute-delegation/date-first/Series-value/date-filter",
            "factor_class": classes[0].__name__, "input_rows": len(frame), "input_fields": fields,
            "input_index_names": list(frame.index.names), "input_symbols": int(frame.index.get_level_values("symbol").nunique()),
            "window_fixture_dates": len(fixture_dates), "first_finite_date": first.isoformat(),
            "finite_window_rows": len(finite), "usable_group_dates": int(good_dates.sum()),
            "post_warmup_date_coverage": coverage, "median_symbol_coverage": cross_coverage,
            "coverage_policy_purpose": purpose,
            "coverage_evaluation_basis": ("all_requested_fixture_dates" if purpose == SOURCE_TRANSFER_PURPOSE
                                          else "post_first_finite_fixture_dates"),
            "coverage_evaluation_dates": len(eligible_dates),
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
                           fields: list[str] | None = None, *, fixture_reader=None) -> dict:
    """Save an evidence-bound replay, including explicit failure records.

    fixture_path is a source-derived parquet with date/symbol and required input
    fields. Source and observed-contract files are retained as hashed evidence.
    No account, CLI, network, budget or experiment ledger operation occurs here.
    Explicit projection research may instead supply a trusted local reader and
    descriptor path; its source code, descriptor and actual frame are bound.
    The harness never discovers/imports a reader from candidate or descriptor.
    """
    code = candidate.get("code")
    if not isinstance(code, str) or not code.strip() or candidate.get("formula"):
        raise NativePreflightError("Native preflight applies to an exact Python code candidate")
    if candidate.get("direction") not in (0, 1):
        raise NativePreflightError("Native preflight requires a frozen direction")
    if not source_evidence or not contract_evidence:
        raise NativePreflightError("Source and observed-contract evidence are required")
    fields = fields or ["close"]
    purpose = _purpose(candidate)
    receipt = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
               "definition_fingerprint": _digest(_definition(candidate, window, cycle, groups)),
               "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
               "window": window, "cycle": cycle, "groups": groups, "direction": candidate.get("direction"),
               "harness": _artifact(__file__), "fixture": _artifact(fixture_path),
               "source_evidence": [_artifact(path) for path in source_evidence],
               "contract_evidence": [_artifact(path) for path in contract_evidence],
               "fields": fields, "purpose": purpose, "scope": _scope(purpose),
               "policy": _coverage_policy(purpose),
               "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
               "limitation": LIMITATION}
    projected = purpose == PROJECTION_RESEARCH_PURPOSE
    if projected:
        receipt.update(passed=False, research_replay_passed=False,
                       native_evaluation_contract=candidate.get("native_evaluation_contract"),
                       fixture_format="trusted_reader_descriptor" if fixture_reader is not None else "parquet")
    try:
        if fixture_reader is not None and not projected:
            raise NativePreflightError("Descriptor readers are only supported for explicit projection research")
        if projected:
            if any(key in candidate for key in ("fixture_reader", "native_fixture_reader")):
                raise NativePreflightError("Projection fixture reader must be an explicit keyword, never candidate data")
            if fixture_reader is not None:
                receipt["fixture_reader"] = _fixture_reader_binding(fixture_reader)
            contract = candidate.get("native_evaluation_contract")
            _projection_contract(contract, window, cycle, groups)
            for label in ("input_calendar", "source_qualification"):
                binding = _artifact(contract[label]["path"])
                if binding not in receipt["source_evidence"]:
                    raise NativePreflightError(f"Projection {label} must be bound in source evidence")
        receipt["proof"] = _replay(code, receipt["fixture"]["path"], fields, window, cycle, groups,
                                   receipt["policy"], purpose, candidate.get("native_evaluation_contract"), fixture_reader)
        receipt["research_replay_passed" if projected else "passed"] = True
        if projected:
            receipt["status"] = "source_limited_projection_replay_validated"
    except Exception as exc:
        receipt.update(passed=False, failure={"type": type(exc).__name__, "message": str(exc)})
    target = Path(receipt_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def verify_native_preflight(candidate: dict, window: dict, cycle: int, groups: int,
                            *, fixture_reader=None) -> dict:
    """Recompute the proof; a hand-written passed=true is never sufficient."""
    purpose = _purpose(candidate)
    path = candidate.get("native_preflight_path")
    if not isinstance(path, str) or not path:
        raise NativePreflightError("A current native preflight receipt is required before account access")
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativePreflightError("Native preflight receipt is missing or invalid") from exc
    projected = purpose == PROJECTION_RESEARCH_PURPOSE
    success = (isinstance(receipt, dict) and
               (receipt.get("research_replay_passed") is True and receipt.get("passed") is False
                if projected else receipt.get("passed") is True))
    if (not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
            or not success or not isinstance(receipt.get("proof"), dict)):
        raise NativePreflightError("Native preflight has no successful replay proof")
    definition = _digest(_definition(candidate, window, cycle, groups))
    if (receipt.get("definition_fingerprint") != definition
            or receipt.get("code_sha256") != hashlib.sha256(candidate["code"].encode()).hexdigest()
            or receipt.get("window") != window or receipt.get("cycle") != cycle
            or receipt.get("groups") != groups or receipt.get("direction") != candidate.get("direction")):
        raise NativePreflightError("Native preflight source or dispatch parameters changed")
    if receipt.get("harness") != _artifact(__file__):
        raise NativePreflightError("Native preflight harness changed; regenerate the receipt")
    if (receipt.get("purpose", STRICT_PURPOSE) != purpose or
            receipt.get("scope", _scope(STRICT_PURPOSE)) != _scope(purpose)):
        raise NativePreflightError("Native preflight coverage purpose or research scope changed")
    if projected:
        if any(key in candidate for key in ("fixture_reader", "native_fixture_reader")):
            raise NativePreflightError("Projection fixture reader must be an explicit keyword, never candidate data")
        contract = candidate.get("native_evaluation_contract")
        if receipt.get("native_evaluation_contract") != contract:
            raise NativePreflightError("Native projection evaluation contract changed")
        _projection_contract(contract, window, cycle, groups)
        format_expected = "trusted_reader_descriptor" if fixture_reader is not None else "parquet"
        if receipt.get("fixture_format") != format_expected:
            raise NativePreflightError("Native projection descriptor needs its explicit trusted fixture reader")
        if fixture_reader is not None and receipt.get("fixture_reader") != _fixture_reader_binding(fixture_reader):
            raise NativePreflightError("Native projection fixture reader code or identity changed")
    elif fixture_reader is not None:
        raise NativePreflightError("Descriptor readers are only supported for explicit projection research")
    for label in ("source_evidence", "contract_evidence"):
        artifacts = receipt.get(label)
        if not isinstance(artifacts, list) or not artifacts:
            raise NativePreflightError(f"Native preflight {label} is missing")
        for artifact in artifacts:
            _check_artifact(artifact, label)
    _check_artifact(receipt.get("fixture"), "fixture")
    if projected:
        for label in ("input_calendar", "source_qualification"):
            if _artifact(contract[label]["path"]) not in receipt["source_evidence"]:
                raise NativePreflightError(f"Projection {label} is not bound in source evidence")
    fixed_policy = _coverage_policy(purpose)
    if receipt.get("policy") != fixed_policy:
        raise NativePreflightError("Native preflight coverage policy changed")
    try:
        proof = _replay(candidate["code"], receipt["fixture"]["path"], receipt.get("fields"),
                        window, cycle, groups, fixed_policy, purpose,
                        candidate.get("native_evaluation_contract"), fixture_reader)
    except Exception as exc:
        raise NativePreflightError("Native preflight replay failed; no official job was dispatched") from exc
    if proof != receipt["proof"]:
        raise NativePreflightError("Native preflight replay evidence no longer matches the receipt")
    return {"receipt_path": str(Path(path).resolve()), "receipt_sha256": _sha(path),
            "definition_fingerprint": definition, "proof": proof, "purpose": purpose,
            "scope": _scope(purpose), "limitation": LIMITATION,
            **({"research_replay_passed": True, "passed": False,
                "native_evaluation_contract": contract,
                "fixture_format": receipt["fixture_format"],
                "fixture_reader": receipt.get("fixture_reader"),
                "reader_dependencies_complete_signature": False}
               if projected else {})}
