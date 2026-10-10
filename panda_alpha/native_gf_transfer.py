"""Generate a self-contained GF transport; never acquire labels or dispatch runs.

The immutable G percentile is rebuilt exactly as the original GF source protocol:
filter the old common source through its strict annual overlay, then rank GP_delta
within that cohort's dated industry.  The native price component is computed from
all received peers, before the financial/source mask is applied.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _day(value):
    return date.fromisoformat(str(value)[:10]).isoformat()


def frozen_g_source(protocol_path):
    """Return only bound G inputs; no quote, return-label or result reads."""
    protocol_path = Path(protocol_path).resolve()
    p = _read(protocol_path)
    bindings = p["source_bindings"]
    for name in ("components", "annual_basis", "parent"):
        binding = bindings[name]
        if file_sha256(binding["path"]) != binding["sha256"]:
            raise ValueError("Original GF source binding changed: " + name)
    columns = ["date", "symbol", "announcement_id", "report_date",
               "source_record_sha256", "pit_usable", "F141_usable", "sector",
               "sector_asof", "sector_pit_usable", "GP_delta"]
    frame = pd.read_parquet(bindings["components"]["path"], columns=columns)
    overlay = pd.read_parquet(bindings["annual_basis"]["path"], columns=[
        "code", "announcement_id", "report_date", "stock_record_sha256",
        "available_date", "status"])
    overlay = overlay[overlay.status.eq("bound_annual_pair_ready")].rename(columns={
        "code": "symbol", "stock_record_sha256": "source_record_sha256",
        "available_date": "annual_available_date"})
    keys = ["symbol", "announcement_id", "report_date", "source_record_sha256"]
    frame = frame.merge(overlay[keys + ["annual_available_date"]], on=keys,
                        how="left", validate="many_to_one")
    available = pd.to_datetime(frame.annual_available_date, errors="coerce")
    known = available.notna() & available.le(pd.to_datetime(frame.date))
    known &= frame.pit_usable.eq(True) & frame.F141_usable.eq(True)
    industry_asof = pd.to_datetime(frame.sector_asof, errors="coerce")
    known &= (frame.sector_pit_usable.eq(True) & industry_asof.notna()
              & industry_asof.le(pd.to_datetime(frame.date)) & frame.sector.ne("UNKNOWN"))
    known &= np.isfinite(frame.GP_delta)
    frame = frame.loc[known].copy()
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate frozen G source")
    # Original run_information.py computes G_pct AFTER the exact strict overlay.
    frame["G_pct"] = frame.groupby(["date", "sector"]).GP_delta.rank(
        method="average", pct=True)
    parent = _read(bindings["parent"]["path"])
    calendar = parent["calendar"]
    w = p["window"]
    evaluation_dates = [d for d in calendar if w["decisions_start"] <= d <= w["holding_end"]]
    formation_dates = [d for d in calendar if w["decisions_start"] <= d <= w["decisions_end"]][::5]
    receipt_path = protocol_path.parent / "input_receipt.json"
    receipt = _read(receipt_path)
    expected = pd.read_parquet(receipt["formation_signals"]["path"], columns=[
        "date", "symbol", "announcement_id", "report_date", "source_record_sha256", "G_pct"])
    if file_sha256(receipt["formation_signals"]["path"]) != receipt["formation_signals"]["sha256"]:
        raise ValueError("Original GF formation source changed")
    actual = frame[frame.date.isin(formation_dates)][expected.columns]
    sort = ["date", "symbol"]
    a, b = actual.sort_values(sort).reset_index(drop=True), expected.sort_values(sort).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=True)
    return frame, evaluation_dates, formation_dates, {
        "protocol": {"path": str(protocol_path), "sha256": file_sha256(protocol_path)},
        "source_bindings": {k: bindings[k] for k in ("components", "annual_basis", "parent")},
        "original_formation_receipt": {"path": str(receipt_path), "sha256": file_sha256(receipt_path)},
        "G_source_formation_exact_match": True,
        "original_source_support_includes_old_StockDB_F141_available_mask": True,
        "native_F141_value_not_taken_from_that_mask": True,
        "G_percentile_not_recomputed_after_native_F141_missingness": True,
        "financial_tail": "Original bound component panel ends at the frozen last formation; missing tail G remains NaN.",
        "new_labels_read": 0,
    }


def compress_g_intervals(frame, evaluation_calendar):
    """Consecutive source-calendar epochs, no filling across missing source dates."""
    positions = {d: n for n, d in enumerate(evaluation_calendar)}
    source = {}
    for code, rows in frame.groupby("symbol", sort=True):
        code = str(code)
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("G source requires six-digit identities")
        intervals = []
        previous = None
        for r in rows.sort_values("date").itertuples(index=False):
            if r.date not in positions:
                continue
            value = float(r.G_pct)
            if not np.isfinite(value):
                raise ValueError("Unknown G cannot enter a numeric source interval")
            signature = (value, str(r.announcement_id), r.report_date,
                         r.source_record_sha256, r.sector, r.sector_asof)
            if (previous is not None and signature == previous[0]
                    and positions[r.date] == previous[1] + 1):
                intervals[-1][1] = r.date
            else:
                intervals.append([r.date, r.date, value])
            previous = (signature, positions[r.date])
        if intervals:
            source[code] = intervals
    return source


# No local-module imports in this generated body.  Factor is supplied by the
# documented MacroFactor loader, and only NumPy/Pandas are needed in the worker.
_BODY = r'''
    @staticmethod
    def _peer_share(close):
        valid = close.where(np.isfinite(close) & close.gt(0))
        returns = valid / valid.shift(1) - 1.0
        counts = returns.notna().sum(axis=1)
        total = returns.sum(axis=1, min_count=1)
        market = returns.rsub(total, axis=0).div(counts - 1, axis=0)
        peers = pd.DataFrame(np.broadcast_to((counts - 1 >= 30).to_numpy()[:, None], returns.shape),
                             index=returns.index, columns=returns.columns)
        market = market.where(returns.notna() & peers)
        mr = returns.rolling(120, min_periods=120).mean()
        mm = market.rolling(120, min_periods=120).mean()
        mrm = (returns * market).rolling(120, min_periods=120).mean()
        mm2 = market.pow(2).rolling(120, min_periods=120).mean()
        mv = mm2 - mm.pow(2)
        beta = ((mrm - mr * mm) / mv).where(mv * 120 > 1e-12)
        alpha = mr - beta * mm
        endpoint = returns - alpha - beta * market
        total_var = returns.rolling(120, min_periods=120).var(ddof=1)
        residual_var = endpoint.rolling(120, min_periods=120).var(ddof=1)
        share = residual_var / (total_var + 1e-6)
        share = share.where(share.ge(-1e-12) & share.le(1.0 + 1e-12)).clip(0.0, 1.0)
        return share.rolling(20, min_periods=20).mean()

    def _source_code(self, symbol):
        if len(symbol) == 6 and symbol.isascii() and symbol.isdigit():
            return symbol
        if symbol.count('.') == 1:
            code, suffix = symbol.split('.')
            aliases = {'SH': 'sh', 'XSHG': 'sh', 'SZ': 'sz', 'XSHE': 'sz', 'BJ': 'bj', 'XBSE': 'bj'}
            if (len(code) == 6 and code.isascii() and code.isdigit()
                    and suffix in aliases and self.MARKETS.get(code) == aliases[suffix]):
                return code
        return None

    def calculate(self, factors):
        close = factors['close']
        original = close.index
        if (not isinstance(original, pd.MultiIndex) or original.nlevels != 2
                or list(original.names) != ['date', 'symbol'] or not original.is_unique):
            raise ValueError('Unique named daily date/symbol index required')
        days = pd.to_datetime(original.get_level_values('date').astype(str), errors='raise')
        if days.tz is not None:
            days = days.tz_localize(None)
        days = days.normalize()
        day_strings = days.strftime('%Y-%m-%d').to_numpy()
        symbols = original.get_level_values('symbol').astype(str).to_numpy()
        prices = pd.to_numeric(pd.Series(close.values), errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        frame = pd.DataFrame({'date': days, 'symbol': symbols, 'close': prices})
        if frame.duplicated(['date', 'symbol']).any():
            raise ValueError('Multiple observations on the same daily native identity')
        known_codes = {symbol: self._source_code(symbol) for symbol in pd.unique(symbols)}
        recognized = frame.assign(code=frame.symbol.map(known_codes)).dropna(subset=['code'])
        if recognized.duplicated(['date', 'code']).any():
            raise ValueError('Duplicate native aliases would double-count peer stocks')
        calendar = sorted(set(days) | set(pd.to_datetime(self.INPUT_CALENDAR)))
        # Full received price universe first.  Financial eligibility NEVER
        # defines the market proxy, rolling fit or residual variance.
        wide = frame.pivot(index='date', columns='symbol', values='close').reindex(calendar)
        share = self._peer_share(wide)
        share_long = share.rename_axis(index='date', columns='symbol').stack(dropna=False)
        observed_pairs = pd.MultiIndex.from_arrays([days, symbols], names=['date', 'symbol'])
        native_f = share_long.reindex(observed_pairs).to_numpy(dtype=float)
        g_values = np.full(len(original), np.nan)
        for symbol, positions in frame.groupby('symbol', sort=False).groups.items():
            code = known_codes[symbol]
            if code not in self.SOURCE:
                continue
            ids = np.asarray(positions, dtype=int)
            current = day_strings[ids]
            for first, last, g in self.SOURCE[code]:
                use = (current >= first) & (current <= last)
                g_values[ids[use]] = g
        projected = ((day_strings >= self.EVALUATION_START)
                     & (day_strings <= self.EVALUATION_END))
        output = np.full(len(original), np.nan)
        common = projected & np.isfinite(g_values) & np.isfinite(native_f)
        selected = pd.DataFrame({'date': day_strings[common], 'symbol': symbols[common],
                                 'G_pct': g_values[common], 'F_share': native_f[common]},
                                index=np.flatnonzero(common))
        for _, rows in selected.groupby('date', sort=False):
            # Full-peer percentile followed by percentile within this common
            # cohort is order/tie equivalent to ranking the raw share here.
            f_pct = rows.F_share.rank(method='average', pct=True)
            signals = []
            for value in (rows.G_pct, -f_pct):
                clipped = value.clip(*value.quantile([.01, .99]).tolist())
                sd = float(clipped.std(ddof=0))
                if not np.isfinite(sd) or sd <= 1e-12:
                    signals = []
                    break
                signals.append((clipped - clipped.mean()) / sd)
            if len(signals) == 2:
                output[rows.index.to_numpy()] = ((signals[0] + signals[1]) / 2.0).to_numpy()
        # Fixed full evaluation projection, including real unqualified NaNs and
        # holding-only tail; never trim to the first/last finite output.
        result = pd.Series(output[projected], index=original[projected], name='value')
        self.diagnostics = {'input_rows': len(original), 'projected_rows': len(result),
                            'source_qualified_rows': int((projected & np.isfinite(g_values)).sum()),
                            'expected_finite_rows': int(np.isfinite(result.to_numpy()).sum())}
        return result
'''


def render_native_gf(source, markets, input_calendar, evaluation_window,
                     class_name="WC03NativeGF"):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", class_name):
        raise ValueError("Invalid generated class identity")
    first, last = _day(evaluation_window["start"]), _day(evaluation_window["end"])
    if first > last or not input_calendar or input_calendar != sorted(set(input_calendar)):
        raise ValueError("Ordered independent input calendar required")
    for code, intervals in source.items():
        if code not in markets or markets[code] not in {"sh", "sz", "bj"}:
            raise ValueError("Source market identity missing")
        for a, b, value in intervals:
            if not first <= a <= b <= last or not np.isfinite(value):
                raise ValueError("Source interval outside fixed evaluation or unknown value")
    header = "import numpy as np\nimport pandas as pd\n\n\nclass " + class_name + "(Factor):\n"
    header += "    SOURCE = " + repr(source) + "\n"
    header += "    MARKETS = " + repr(markets) + "\n"
    header += "    INPUT_CALENDAR = " + repr(input_calendar) + "\n"
    header += "    EVALUATION_START = " + repr(first) + "\n"
    header += "    EVALUATION_END = " + repr(last) + "\n"
    return header + _BODY


def read_mongo_gf_fixture(descriptor_path):
    """Trusted read-only local source view; no persistent price snapshot.

    Descriptor credentials are forbidden.  The existing local profile supplies
    its connection only in memory.  Mutable-source frame hashes are verified by
    the prospective native-preflight caller; this reader does not lock MongoDB.
    """
    from pymongo import MongoClient
    descriptor_path = Path(descriptor_path).resolve()
    d = _read(descriptor_path)
    if d.get("format") != "native_gf_readonly_mongo_v1":
        raise ValueError("Unknown source-view descriptor")
    if d.get("source") != "stockdb" or d.get("database") != "quantaxis_stockdb_research":
        raise ValueError("Only the named existing StockDB local proxy is supported")
    if d.get("price_basis") not in {"raw_stockdb_close_proxy", "stockdb_asof_hfq_proxy"}:
        raise ValueError("An explicit proxy price construction is required")
    peer_binding = d.get("peer_scope_protocol")
    if not peer_binding or file_sha256(peer_binding["path"]) != peer_binding["sha256"]:
        raise ValueError("Original full acquisition peer scope must be bound")
    peer_codes = _read(peer_binding["path"])["codes"]
    if len(peer_codes) != len(set(peer_codes)) or any(not re.fullmatch(r"\d{6}", str(c)) for c in peer_codes):
        raise ValueError("Invalid original full peer identities")
    if d["price_basis"] == "stockdb_asof_hfq_proxy":
        binding = d.get("existing_adjustment_contract")
        if not binding or file_sha256(binding["path"]) != binding["sha256"]:
            raise ValueError("As-of HFQ requires the existing source adjustment contract")
        contract = _read(binding["path"])
        if contract.get("status") != "SCOPED_RESEARCH_SOURCE_READY_FULL_MARKET_ACCEPTANCE_PENDING":
            raise ValueError("Unaccepted same-source cumulative adjustment history")
    cfg = _read(d["config_path"])
    profile = cfg["data"]["source_profiles"]["stockdb"]
    if profile["database"] != d["database"]:
        raise ValueError("Configured source database changed")
    first, last = _day(d["input_window"]["start"]), _day(d["input_window"]["end"])
    if first > last:
        raise ValueError("Invalid input window")
    with MongoClient(cfg["data"]["mongo_uri"], serverSelectionTimeoutMS=5000) as client:
        db = client[d["database"]]
        cursor = db.stock_day.find({"source": "stockdb", "code": {"$in": peer_codes},
                                   "date": {"$gte": first, "$lte": last}},
                                  {"_id": 0, "date": 1, "code": 1, "close": 1}).sort([("date", 1), ("code", 1)])
        chunks, rows = [], []
        for row in cursor:
            rows.append(row)
            if len(rows) == 50000:
                chunks.append(pd.DataFrame.from_records(rows)); rows = []
        if rows:
            chunks.append(pd.DataFrame.from_records(rows))
        if not chunks:
            raise ValueError("Source view has no actual price records")
        frame = pd.concat(chunks, ignore_index=True)
        del chunks, rows
        # Reuse only already-bound, separately verified minute-to-daily repairs.
        for binding in d.get("existing_price_repairs", []):
            if file_sha256(binding["path"]) != binding["sha256"]:
                raise ValueError("Existing price repair binding changed")
            records = _read(binding["path"])["repairs"]
            replacements = pd.DataFrame([{"date": r["date"], "code": r["code"], "close": r["close"]}
                                         for r in records if first <= r["date"] <= last and r["code"] in peer_codes])
            if not replacements.empty:
                key = pd.MultiIndex.from_frame(replacements[["date", "code"]])
                if key.has_duplicates:
                    raise ValueError("Duplicate accepted repair identity")
                original_keys = pd.MultiIndex.from_frame(frame[["date", "code"]])
                frame = pd.concat([frame[~original_keys.isin(key)], replacements], ignore_index=True)
        if d["price_basis"] == "stockdb_asof_hfq_proxy":
            factors = pd.DataFrame.from_records(db.stock_adj.find({"source": "stockdb", "date": {"$lte": last}},
                                                                 {"_id": 0, "date": 1, "code": 1, "adj": 1}))
            if not factors.empty:
                if factors.duplicated(["date", "code"]).any():
                    raise ValueError("Conflicting adjustment event identities")
                factors["date"] = pd.to_datetime(factors.date)
                factor_groups = {code: g.sort_values("date") for code, g in factors.groupby("code")}
            else:
                factor_groups = {}
            frame["date"] = pd.to_datetime(frame.date)
            parts = []
            for code, g in frame.groupby("code", sort=False):
                f = factor_groups.get(code)
                if f is not None:
                    g = pd.merge_asof(g.sort_values("date"), f[["date", "adj"]], on="date", direction="backward")
                    # The existing accepted native factor contract has IPO baseline1.
                    scale = pd.to_numeric(g.adj, errors="coerce").fillna(1.0)
                    if (scale <= 0).any() or not np.isfinite(scale).all():
                        raise ValueError("Invalid same-source cumulative factor")
                    g["close"] = pd.to_numeric(g.close, errors="coerce") * scale
                parts.append(g[["date", "code", "close"]])
            frame = pd.concat(parts, ignore_index=True)
            frame["date"] = frame.date.dt.strftime("%Y-%m-%d")
    frame = frame.rename(columns={"code": "symbol"})
    frame["symbol"] = frame.symbol.astype(str)
    if not frame.symbol.str.fullmatch(r"\d{6}").all() or frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Invalid source identities in actual fixture view")
    frame["close"] = pd.to_numeric(frame.close, errors="coerce")
    frame = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    return frame[["date", "symbol", "close"]]

