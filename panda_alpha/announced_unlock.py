"""Causal projection of reviewed notice facts; no selling or alpha inference.

The generated Factor is an offline source-contract probe. Three notice sources
cannot support a ten-group experiment, and frozen facts are not a live updater.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd


def notice_events(review_path: str | Path) -> list[dict]:
    path = Path(review_path)
    review = json.loads(path.read_text(encoding="utf-8"))
    binding = review["body_source_receipt"]
    raw = Path(binding["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise ValueError("Bound original-notice receipt changed")
    original = {r["announcement_id"]: r for r in json.loads(raw)["records"]}
    events = []
    for r in review["records"]:
        source = original[r["announcement_id"]]
        if (not r["source_fact_review_performed"] or
                r["PDF_sha256"] != source["PDF_sha256"] or
                r["native_text_sha256"] != source["text_sha256"] or
                r["symbol"] != source["symbol"]):
            raise ValueError("Notice facts lack their reviewed original identity")
        fields = r["source_facts"]
        event = {"symbol": r["symbol"], "announcement_id": r["announcement_id"],
                 "available_reference_date": r["conservative_next_verified_reference_session"],
                 "announced_listing_date": fields["announced_listing_date"]["value"],
                 "announced_listing_shares": fields["announced_listing_shares"]["value"],
                 "event_type": fields["event_type"]["value"],
                 "holder_constraints": r["holder_restrictions"],
                 "actual_sales": None, "production_qualified": False,
                 "version_chain_certified": False}
        if (type(event["announced_listing_shares"]) is not int or
                event["announced_listing_shares"] <= 0 or
                event["available_reference_date"] >= event["announced_listing_date"]):
            raise ValueError("Expected positive future announced quantity")
        if not re.fullmatch(r"\d{6}", event["symbol"]):
            raise ValueError("Notice symbol must be six digits")
        events.append(event)
    if len({r["symbol"] for r in events}) != len(events):
        raise ValueError("Multi-notice same-symbol resolution has not been defined")
    return events


def project_announced_quantity(index: pd.MultiIndex, events: list[dict]) -> pd.Series:
    """Preserve every key; unknown, not-yet-known and expired facts stay NaN.

    Quantity is a fact about the notice's planned listing, never actual sales or
    unrestricted immediately saleable supply. Availability is reference close.
    """
    if not isinstance(index, pd.MultiIndex) or set(index.names) != {"date", "symbol"}:
        raise ValueError("Expected native date/symbol index")
    dates = pd.to_datetime(index.get_level_values("date"), errors="raise")
    symbols = pd.Index(index.get_level_values("symbol")).astype(str)
    symbols = symbols.str.split(".").str[0].str.zfill(6)
    result = np.full(len(index), np.nan)
    for event in events:
        keep = ((symbols == event["symbol"]) &
                (dates >= pd.Timestamp(event["available_reference_date"])) &
                (dates < pd.Timestamp(event["announced_listing_date"])))
        if np.isfinite(result[keep]).any():
            raise ValueError("Overlapping notice facts need explicit resolution")
        result[keep] = event["announced_listing_shares"]
    return pd.Series(result, index=index, name="value")


def render_notice_contract_probe(events: list[dict]) -> str:
    """Self-contained Python source, for offline replay only; never dispatch it."""
    rows = [(r["symbol"], r["available_reference_date"], r["announced_listing_date"],
             r["announced_listing_shares"]) for r in events]
    return '''# OFFLINE SOURCE CONTRACT ONLY: no alpha or production certification.
import numpy as np
import pandas as pd

class AnnouncedUnlockSourceContractProbe(Factor):
    def calculate(self, factors):
        close = factors['close']
        index = close.index
        dates = pd.to_datetime(index.get_level_values('date'), errors='raise')
        symbols = pd.Index(index.get_level_values('symbol')).astype(str)
        symbols = symbols.str.split('.').str[0].str.zfill(6)
        result = np.full(len(index), np.nan)
        for symbol, available, listing, quantity in ''' + repr(rows) + ''':
            known = ((symbols == symbol) & (dates >= pd.Timestamp(available))
                     & (dates < pd.Timestamp(listing)))
            result[known] = quantity
        return pd.Series(result, index=index, name='value')
'''
