"""Shared evidence I/O for local research scripts; no study or account actions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def binding(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def clean(value):
    """Keep the existing research JSON representation of NumPy scalar values."""
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def write_clean_json(path, value, *, trailing_newline=False):
    text = json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False)
    Path(path).write_text(text + ("\n" if trailing_newline else ""), encoding="utf-8")
