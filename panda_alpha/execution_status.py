"""Attach verified historical suspension status without inventing quotations."""
from __future__ import annotations

import math


def add_suspension_statuses(book, rows):
    """Add status-only observations to an existing execution quote book.

    The caller binds the source evidence and dates. This consumer does not
    classify missing prices as suspensions or populate price/flow/risk fields.
    Valuation remains the quote book's strictly prior close rule.
    """
    seen = set()
    additions = {}
    for item in rows:
        key = (item['symbol'], item['date'])
        if key in seen:
            raise ValueError('Duplicate suspension sidecar key: ' + str(key))
        seen.add(key)
        if (item.get('trade_status') != 0
                or item.get('source_status') != 'source_explicit_suspension'
                or not item.get('evidence_sha256')):
            raise ValueError('Explicit bound suspension status required')
        for field in ('open', 'high', 'low', 'close', 'raw_open', 'raw_high',
                      'raw_low', 'raw_close', 'amount', 'volume'):
            if item.get(field) is not None:
                raise ValueError('Status-only sidecar cannot supply ' + field)
        old = book.rows.get(key)
        if old is not None:
            if book.suspended(old):
                continue
            # A positive quotation or a received trading status is a conflict,
            # even when its amount or another column is absent.
            positive = False
            for field in ('open', 'high', 'low', 'close', 'raw_open', 'raw_high',
                          'raw_low', 'raw_close', 'amount', 'volume'):
                try:
                    value = float(old.get(field))
                    positive |= math.isfinite(value) and value > 0
                except (TypeError, ValueError):
                    pass
            if positive or str(old.get('trade_status')) in ('1', '1.0'):
                raise ValueError('Suspension sidecar conflicts with quotation: ' + str(key))
        additions[key] = {**(old or {}), **item}
    book.rows.update(additions)
    return len(additions)
