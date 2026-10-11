"""Bounded G-midpoint x preferred-low native-F joint confirmation.

The wrapper only replaces the output composition of an immutable MID worker.
It retains the native all-peer F calculation, source projection, and both old
component variance gates. Existing GF/F/g renderers are never modified.
"""
from __future__ import annotations

import ast
import numpy as np
import pandas as pd


def _replace_once(code: str, old: str, new: str) -> str:
    if code.count(old) != 1:
        raise ValueError("Immutable midpoint native template drifted")
    return code.replace(old, new, 1)


def render_product_from_midpoint_code(midpoint_code: str, class_name: str) -> str:
    """Keep SOURCE literal untouched; rank raw F_share before any winsorization."""
    if not class_name.isidentifier():
        raise ValueError("A valid standalone native class name is required")
    classes = [node for node in ast.parse(midpoint_code).body if isinstance(node, ast.ClassDef)]
    if len(classes) != 1:
        raise ValueError("Exactly one immutable midpoint native class required")
    old_name = classes[0].name
    code = _replace_once(midpoint_code, "class " + old_name + "(Factor):\n",
                         "class " + class_name + "(Factor):\n")
    code = _replace_once(code, "    MODE = 'GF'\n", "    MODE = 'PRODUCT_GF'\n")
    return _replace_once(code,
        "component = ((signals[0] + signals[1]) / 2.0 if self.MODE == 'GF' else -signals[1])\n",
        "component = rows.G_pct * rows.F_share.rank(method='average', ascending=False, pct=True)\n")


def bounded_product_reference(frame: pd.DataFrame) -> pd.Series:
    """Independent reference on original full projection, with genuine NaNs.

G_pct already is the frozen financial-cohort midpoint empirical CDF, computed
before F missingness. Only F ranks are formed on the shared finite cohort.
Winsorization is retained for the old variance gates, not for product operands.
"""
    required = {"date", "symbol", "G_pct", "F_share"}
    if not required.issubset(frame) or not frame.index.is_unique or frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Unique dated source projection and true operands required")
    known = np.isfinite(frame.G_pct) & np.isfinite(frame.F_share)
    if not frame.loc[known, "G_pct"].between(0, 1, inclusive="neither").all():
        raise ValueError("Frozen G midpoint CDF must lie strictly within zero and one")
    output = pd.Series(np.nan, index=frame.index, name="value", dtype=float)
    for _, rows in frame.loc[known].groupby("date", sort=False):
        f_pct = rows.F_share.rank(method="average", pct=True)
        ready = True
        for operand in (rows.G_pct, -f_pct):
            clipped = operand.clip(*operand.quantile([.01, .99]).tolist())
            deviation = float(clipped.std(ddof=0))
            if not np.isfinite(deviation) or deviation <= 1e-12:
                ready = False
                break
        if ready:
            preferred_low_f = rows.F_share.rank(method="average", ascending=False, pct=True)
            output.loc[rows.index] = rows.G_pct * preferred_low_f
    return output
