"""Independent E source projection/native wrapper; no data reads or dispatch."""
from __future__ import annotations

import ast
import re

import numpy as np
import pandas as pd

from panda_alpha.capital_efficiency import capital_efficiency
from panda_alpha.native_gf_transfer import compress_g_intervals

FINANCIAL_KEY = ["symbol", "announcement_id", "report_date", "source_record_sha256"]
AMOUNTS = ["operating_revenue_1", "operating_revenue_0", "total_assets_1", "total_assets_0"]
AMOUNT_FILE_KEY = ["code", *FINANCIAL_KEY[1:]]


def canonical_amounts(frame):
    """Explicit original amount-file code -> financial source symbol adapter."""
    if "symbol" in frame.columns or not set(AMOUNT_FILE_KEY + AMOUNTS).issubset(frame.columns):
        raise ValueError("Original bound annual amounts require the declared code-based schema")
    return frame[AMOUNT_FILE_KEY + AMOUNTS].rename(columns={"code": "symbol"}).copy()


def project_capital_source(original_source, amounts, accepted_identities):
    """Exact report-version selection; source roles are certified by the caller."""
    accepted = pd.DataFrame(accepted_identities, columns=FINANCIAL_KEY)
    if (accepted.empty or accepted.duplicated(FINANCIAL_KEY).any()
            or accepted[FINANCIAL_KEY].isna().any().any()
            or amounts.duplicated(FINANCIAL_KEY).any()
            or original_source.duplicated(["date", "symbol"]).any()):
        raise ValueError("Capital financial/report or formation identity is ambiguous")
    source = original_source.merge(accepted, on=FINANCIAL_KEY, how="inner", validate="many_to_one", sort=False)
    selected = source.merge(amounts[FINANCIAL_KEY + AMOUNTS], on=FINANCIAL_KEY,
                            how="left", validate="many_to_one", sort=False)
    index = pd.MultiIndex.from_frame(selected[["date", "symbol"]])
    operands = [pd.Series(selected[name].to_numpy(), index=index) for name in AMOUNTS]
    values = capital_efficiency(*operands)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("A source-closed identity lacks four positive finite E operands; no implicit extra exclusion")
    selected["E_raw"] = values.to_numpy()
    return selected


def capital_intervals(selected, calendar):
    # Reuse only the interval compressor; this temporary column is transport,
    # never an industry ranking or the original G definition.
    view = selected.copy()
    view["G_pct"] = view["E_raw"]
    return compress_g_intervals(view, calendar)


def _once(code, old, new):
    if code.count(old) != 1:
        raise ValueError("Bound native conservative template drift")
    return code.replace(old, new, 1)


def render_capital_code(original_GF_code, e_source, class_name, mode="E"):
    """Keep old SOURCE and full-peer F/old G variance gates; add raw E only.

    A same-source F141_CONTROL rendering is available after root separately
    registers it if actual support differs. Its direction remains 0.
    """
    if mode not in {"E", "F141_CONTROL"} or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", class_name):
        raise ValueError("Unknown fixed capital mode/class")
    cls = next(node for node in ast.parse(original_GF_code).body if isinstance(node, ast.ClassDef))
    constants = {target.id: ast.literal_eval(node.value) for node in cls.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)}
    if constants.get("MODE") != "GF" or "SOURCE" not in constants:
        raise ValueError("Capital wrapper needs the immutable original conservative GF code")
    for symbol, intervals in e_source.items():
        if symbol not in constants["SOURCE"]:
            raise ValueError("Capital source escaped the original financial source")
        for first, last, value in intervals:
            if not constants["EVALUATION_START"] <= first <= last <= constants["EVALUATION_END"] or not np.isfinite(value):
                raise ValueError("Capital interval has an unknown value or changed window")
    code = _once(original_GF_code, "class " + cls.name + "(Factor):\n", "class " + class_name + "(Factor):\n")
    code = _once(code, "    MODE = 'GF'\n", "    MODE = " + repr(mode) + "\n    E_SOURCE = " + repr(e_source) + "\n")
    code = _once(code, "        projected = ((day_strings >= self.EVALUATION_START)",
                 """        e_values = np.full(len(original), np.nan)
        for symbol, positions in frame.groupby('symbol', sort=False).groups.items():
            code = known_codes[symbol]
            ids = np.asarray(positions, dtype=int)
            current = day_strings[ids]
            for first, last, e in self.E_SOURCE.get(code, []):
                use = (current >= first) & (current <= last)
                e_values[ids[use]] = e
        projected = ((day_strings >= self.EVALUATION_START)""")
    code = _once(code, "common = projected & np.isfinite(g_values) & np.isfinite(native_f)",
                 "common = projected & np.isfinite(g_values) & np.isfinite(native_f) & np.isfinite(e_values)")
    code = _once(code, "'G_pct': g_values[common], 'F_share': native_f[common]",
                 "'G_pct': g_values[common], 'F_share': native_f[common], 'E_raw': e_values[common]")
    code = _once(code, "for value in (rows.G_pct, -f_pct):", "for value in (rows.G_pct, -f_pct, rows.E_raw):")
    code = _once(code, "if len(signals) == 2:", "if len(signals) == 3:")
    code = _once(code, "component = ((signals[0] + signals[1]) / 2.0 if self.MODE == 'GF' else -signals[1])",
                 "component = (signals[2] if self.MODE == 'E' else -signals[1])")
    ast.parse(code)
    return code


def capital_reference(frame):
    """Independent E/F within the exact common support, including old gates."""
    outputs = {name: pd.Series(np.nan, index=frame.index, name="value") for name in ["E", "F141_CONTROL"]}
    finite = np.isfinite(frame[["G_pct", "F_share", "E_raw"]].to_numpy()).all(axis=1)
    for _, rows in frame.loc[finite].groupby("date", sort=False):
        f_rank = rows.F_share.rank(method="average", pct=True)
        components = []
        for values in [rows.G_pct, -f_rank, rows.E_raw]:
            clipped = values.clip(*values.quantile([.01, .99]).tolist())
            deviation = float(clipped.std(ddof=0))
            if not np.isfinite(deviation) or deviation <= 1e-12:
                components = []
                break
            components.append((clipped - clipped.mean()) / deviation)
        if components:
            outputs["E"].loc[rows.index] = components[2]
            outputs["F141_CONTROL"].loc[rows.index] = -components[1]
    return outputs
