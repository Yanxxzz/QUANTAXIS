"""Frozen conservative source intersection and self-contained native GF pair.

This prospective context preserves the baseline reader and generated adapter.
It does not obtain labels, alter source stores, or dispatch official research.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .native_gf_transfer import file_sha256, frozen_g_source, render_native_gf


SOURCE_KEY = ["date", "symbol", "announcement_id", "report_date", "source_record_sha256"]
MODES = {"GF", "F141_CONTROL"}


def intersect_and_rank_g(source, parent):
    """Exact parent intersection; rank in the financial cohort before native F."""
    if source.duplicated(SOURCE_KEY).any() or parent.duplicated(SOURCE_KEY).any():
        raise ValueError("Conservative parent identities must be unique")
    columns = SOURCE_KEY + ["pit_usable", "source_status", "blocking_event_id"]
    parent = parent[columns].rename(columns={key: key + "_current_parent"
                                            for key in columns if key not in SOURCE_KEY})
    merged = source.merge(parent, on=SOURCE_KEY, how="left", validate="one_to_one")
    # The original source has already applied its strict annual, industry and
    # old F141 support rules. A missing or pending current parent cannot enter.
    keep = merged.pit_usable.eq(True) & merged.pit_usable_current_parent.eq(True)
    result = merged.loc[keep, source.columns].copy()
    result["G_pct"] = result.groupby(["date", "sector"]).GP_delta.rank(
        method="average", pct=True)
    return result, {
        "original_rows": len(source), "conservative_rows": len(result),
        "excluded_rows": int((~keep).sum()),
        "exact_identity_missing": int(merged.pit_usable_current_parent.isna().sum()),
        "excluded_parent_status": {str(k): int(v) for k, v in
                                   merged.loc[~keep, "source_status_current_parent"].value_counts().items()},
        "G_reranked_before_native_F_missingness": True,
    }


def conservative_g_source(protocol_path):
    """Read the root-frozen financial source contract; no prices or labels."""
    protocol_path = Path(protocol_path).resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    candidates = protocol["context_candidates"]
    if len(candidates) != 2 or [c["direction"] for c in candidates] != [1, 0]:
        raise ValueError("Expected the registered GF and direction0 control pair")
    specs = [c["strategy_spec"] for c in candidates]
    shared = ("source_bindings", "additional_parent_binding", "all_price_peer_scope_binding",
              "calendar_binding", "input_window", "evaluation_window", "formation_dates",
              "holding_end", "cycle", "groups", "shared_finite_support")
    if any(specs[0][key] != specs[1][key] for key in shared):
        raise ValueError("Conservative pair must have one shared input/source contract")
    count_binding = protocol["source_count_review"]
    if file_sha256(count_binding["path"]) != count_binding["sha256"]:
        raise ValueError("Frozen source-count review changed")
    count_review = json.loads(Path(count_binding["path"]).read_text(encoding="utf-8"))
    old_binding = count_review["inputs"]["original_GF_protocol"]
    if file_sha256(old_binding["path"]) != old_binding["sha256"]:
        raise ValueError("Original GF protocol changed")
    source, calendar, formations, original_receipt = frozen_g_source(old_binding["path"])
    for key in ("components", "annual_basis", "parent"):
        if specs[0]["source_bindings"][key] != original_receipt["source_bindings"][key]:
            raise ValueError("Inherited conservative source binding changed: " + key)
    for key in ("additional_parent_binding", "calendar_binding", "all_price_peer_scope_binding"):
        binding = specs[0][key]
        if file_sha256(binding["path"]) != binding["sha256"]:
            raise ValueError("Frozen conservative input binding changed: " + key)
    if (calendar[0] != specs[0]["evaluation_window"]["start"]
            or calendar[-1] != specs[0]["holding_end"]
            or formations != specs[0]["formation_dates"]):
        raise ValueError("The registered evaluation/formation calendar changed")
    binding = specs[0]["additional_parent_binding"]
    parent = pd.read_parquet(binding["path"], columns=SOURCE_KEY + [
        "pit_usable", "source_status", "blocking_event_id"])
    selected, review = intersect_and_rank_g(source, parent)
    expected = protocol["source_only_count_summary"]
    if (len(selected) != expected["conservative_source_identities"]
            or len(selected[selected.date.isin(formations)]) != expected["new_51_anchor_identities"]):
        raise ValueError("Frozen conservative financial cohort no longer reproduces")
    return selected, calendar, formations, {
        "protocol": {"path": str(protocol_path), "sha256": file_sha256(protocol_path)},
        "source_count_review": count_binding,
        "original_G_source": original_receipt,
        "additional_parent_binding": binding,
        "intersection": review,
        "current_and_comparative_from_same_bound_annual_report": True,
        "new_income_resolutions_used": False, "new_scope_clears_used": False,
        "new_labels_or_returns_read": 0,
    }


_CANONICAL_METHOD = '''    @staticmethod
    def _peer_code(symbol):
        if len(symbol) == 6 and symbol.isascii() and symbol.isdigit():
            return symbol
        if symbol.count('.') == 1:
            code, suffix = symbol.split('.')
            if (len(code) == 6 and code.isascii() and code.isdigit()
                    and suffix in {'SH', 'SZ', 'BJ', 'XSHG', 'XSHE', 'XBSE'}):
                return code
        raise ValueError('Unsupported canonical native price-peer identity')

'''


def _replace_once(code, old, new):
    if code.count(old) != 1:
        raise ValueError("Native baseline template drifted; cannot certify pair rendering")
    return code.replace(old, new, 1)


def render_source_conservative(source, markets, input_calendar, evaluation_window,
                               mode, class_name):
    """Render standalone worker code; modes share finance/F/variance support."""
    if mode not in MODES:
        raise ValueError("Unknown frozen conservative signal mode")
    code = render_native_gf(source, markets, input_calendar, evaluation_window, class_name)
    code = _replace_once(code, "class " + class_name + "(Factor):\n",
                         "class " + class_name + "(Factor):\n    MODE = " + repr(mode) + "\n")
    code = _replace_once(code, "    def _source_code(self, symbol):\n",
                         _CANONICAL_METHOD + "    def _source_code(self, symbol):\n")
    old = """        known_codes = {symbol: self._source_code(symbol) for symbol in pd.unique(symbols)}
        recognized = frame.assign(code=frame.symbol.map(known_codes)).dropna(subset=['code'])
        if recognized.duplicated(['date', 'code']).any():
            raise ValueError('Duplicate native aliases would double-count peer stocks')
"""
    new = """        canonical_peers = np.asarray([self._peer_code(symbol) for symbol in symbols])
        frame['peer_symbol'] = canonical_peers
        if frame.duplicated(['date', 'peer_symbol']).any():
            raise ValueError('Duplicate canonical native aliases would double-count peer stocks')
        known_codes = {symbol: self._source_code(symbol) for symbol in pd.unique(symbols)}
"""
    code = _replace_once(code, old, new)
    code = _replace_once(code,
                         "wide = frame.pivot(index='date', columns='symbol', values='close').reindex(calendar)",
                         "wide = frame.pivot(index='date', columns='peer_symbol', values='close').reindex(calendar)")
    code = _replace_once(code,
                         "observed_pairs = pd.MultiIndex.from_arrays([days, symbols], names=['date', 'symbol'])",
                         "observed_pairs = pd.MultiIndex.from_arrays([days, canonical_peers], names=['date', 'symbol'])")
    code = _replace_once(code,
                         "output[rows.index.to_numpy()] = ((signals[0] + signals[1]) / 2.0).to_numpy()",
                         "component = ((signals[0] + signals[1]) / 2.0 if self.MODE == 'GF' else -signals[1])\n"
                         "                output[rows.index.to_numpy()] = component.to_numpy()")
    return code
