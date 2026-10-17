"""Bounded local event-strategy study; no Panda account or collection calls.

Prepare freezes the existing source identity/calendar. Run requires an externally
frozen, preregistered protocol and retains small portfolio results, not a copy of
the market database. Daily touching prices are potential executions only.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from pymongo import MongoClient
from panda_alpha.evaluation import check_window, performance
from panda_alpha.registry import ResearchRegistry
from scripts.research_io import read, binding

PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@lru_cache(maxsize=300000)
def limit_price(reference, rate, side):
    if reference is None or not np.isfinite(reference) or reference <= 0:
        return float("nan")
    value = Decimal(str(reference))
    delta = Decimal(str(rate)) * (1 if side == "up" else -1)
    result = (value * (Decimal(1) + delta)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    if abs(result - value) < Decimal(".01"):
        result = value + (Decimal(".01") if side == "up" else -Decimal(".01"))
    return float(result) if result > 0 else float("nan")


def client(cfg):
    return MongoClient(cfg["data"]["mongo_uri"], serverSelectionTimeoutMS=10000, socketTimeoutMS=180000)


def prepare(cfg, output, start, end):
    check_window(start, end, cfg["research"].get("sealed_windows", []))
    scope_file = ROOT / "research_runs/stockdb_research_20261008/scope_codes.json"
    codes = sorted(code for code in read(scope_file) if code.startswith(PREFIXES))
    profile = cfg["data"]["source_profiles"]["stockdb"]
    with client(cfg) as c:
        ref, db = c[profile["reference_database"]], c[profile["database"]]
        all_days = sorted({x["date"] for x in ref.trade_calendar.find(
            {"source": profile["calendar_source"], "date": {"$lte": end}}, {"_id": 0, "date": 1})})
        days = [d for d in all_days if start <= d <= end]
        receipts = list(ref.panda_axis_sync.find({"dataset": "trade_calendar", "code": "SSE", "source": "baostock",
            "status": "complete", "start": {"$lte": start}, "through": {"$gte": end}}, {"_id": 0}))
        if not days or not receipts:
            raise ValueError("Independent calendar receipt does not cover the requested window")
        lives = list(ref.stock_lifecycle.find({"code": {"$in": codes}, "source": "baostock"},
            {"_id": 0, "code": 1, "ipo_date": 1, "delisted_date": 1, "lifecycle_issues": 1,
             "membership_pit": 1, "source": 1, "security_type": 1}))
        source_count = db.stock_day.count_documents({"source": "stockdb", "code": {"$in": codes},
            "date": {"$gte": start, "$lte": end}})
    meta = {"status": "SOURCE_METADATA_PREPARED_NO_SIGNALS_OR_RETURNS", "start": start, "end": end,
        "codes": codes, "scope_identity_count": len(codes), "source_rows": source_count,
        "calendar": days, "all_reference_days_through_end": all_days, "calendar_receipts": receipts,
        "lifecycle": lives, "source_profile": {k: profile[k] for k in ["database", "price_source", "reference_database", "calendar_source"]},
        "scope_origin": binding(scope_file), "reader": binding(__file__),
        "acceptance": binding(ROOT / "research_runs/stockdb_research_20261008/acceptance.json"),
        "full_historical_membership_PIT_or_queue_certified": False,
        "corporate_action_accounting": "same-source cumulative total-return equivalent units; not actual shares/dividend cash",
        "official_calls_or_fees": 0}
    save(output / "source_metadata.json", meta)
    print(json.dumps({"status": meta["status"], "source_rows": source_count, "scope_codes": len(codes),
        "calendar_days": len(days), "lifecycle_rows": len(lives)}, ensure_ascii=False), flush=True)


def load_bars(cfg, meta):
    profile, codes = meta["source_profile"], meta["codes"]
    window = {"$gte": meta["start"], "$lte": meta["end"]}
    cols = {name: 1 for name in ["date", "code", "open", "high", "low", "close", "preclose", "volume_shares",
        "amount", "is_st", "trade_status", "adjustment", "source_row_sha256", "source_snapshot_sha256"]}
    cols["_id"] = 0
    chunks, buf, raw_hash = [], [], hashlib.sha256()
    with client(cfg) as c:
        db, ref = c[profile["database"]], c[profile["reference_database"]]
        cursor = db.stock_day.find({"source": "stockdb", "code": {"$in": codes}, "date": window}, cols).sort([("code", 1), ("date", 1)]).batch_size(5000)
        for row in cursor:
            raw_hash.update((row["code"] + "|" + row["date"] + "|" + row.get("source_row_sha256", "") + "\n").encode())
            buf.append(row)
            if len(buf) == 100000:
                chunks.append(pd.DataFrame(buf)); buf.clear()
        if buf:
            chunks.append(pd.DataFrame(buf)); buf.clear()
        frame = pd.concat(chunks, ignore_index=True); chunks.clear()
        factors = pd.DataFrame(list(db.stock_adj.find({"source": "stockdb", "code": {"$in": codes},
            "date": {"$lte": meta["end"]}}, {"_id": 0, "code": 1, "date": 1, "adj": 1})))
        suspensions = list(ref.stock_day.find({"source": "baostock", "trade_status": "0", "code": {"$in": codes},
            "date": window}, {"_id": 0, "code": 1, "date": 1}))
    if len(frame) != meta["source_rows"] or frame.duplicated(["code", "date"]).any():
        raise ValueError("Source count/unique stock-date identities changed")
    if not frame.adjustment.eq("none").all():
        raise ValueError("Raw limit decisions require unadjusted source quotes")
    if not set(frame.date) <= set(meta["calendar"]):
        raise ValueError("Observed quotes outside independent reference calendar")
    lives = {}
    for x in meta["lifecycle"]:
        if x.get("ipo_date") and not x.get("lifecycle_issues"):
            lives.setdefault(x["code"], x)
    days = meta["all_reference_days_through_end"]
    eligible_at = {}
    for code, life in lives.items():
        index = bisect_left(days, life["ipo_date"]) + 20
        eligible_at[code] = days[index] if index < len(days) else "9999-12-31"
    unknown_life_rows = int((~frame.code.isin(lives)).sum())
    frame["entry_eligible"] = frame.code.map(eligible_at).notna() & frame.date.ge(frame.code.map(eligible_at).fillna("9999-12-31")) & frame.is_st.eq(False)
    for code, life in lives.items():
        if life.get("delisted_date"):
            frame.loc[frame.code.eq(code) & frame.date.ge(life["delisted_date"]), "entry_eligible"] = False
    st_known = frame.is_st.isin([True, False])
    rates = np.where(frame.is_st.eq(True) & frame.date.lt("2026-07-06"), .05, .10)
    frame["up_limit"] = [limit_price(p, r, "up") for p, r in zip(frame.preclose, rates)]
    frame["down_limit"] = [limit_price(p, r, "down") for p, r in zip(frame.preclose, rates)]
    frame.loc[~st_known, ["up_limit", "down_limit"]] = np.nan
    frame = frame.rename(columns={"code": "symbol", "preclose": "prevclose", **{x: "raw_" + x for x in ["open", "high", "low", "close"]}})
    frame["adjustment_factor"] = np.nan
    factor_records = []
    for code, positions in frame.groupby("symbol", sort=False).groups.items():
        part = factors[factors.code.eq(code)].sort_values("date")
        if part.empty:
            continue
        if part.date.duplicated().any() or not (np.isfinite(part.adj) & part.adj.gt(0)).all():
            raise ValueError("Invalid or duplicate same-source cumulative factors")
        dates = frame.loc[positions, "date"].to_numpy()
        ix = np.searchsorted(part.date.to_numpy(), dates, side="right") - 1
        valid = ix >= 0
        v = np.full(len(ix), np.nan); v[valid] = part.adj.to_numpy()[ix[valid]]
        frame.loc[positions, "adjustment_factor"] = v
        factor_records.extend(part[["code", "date", "adj"]].itertuples(index=False, name=None))
    factor_hash = hashlib.sha256(json.dumps(sorted(factor_records), separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    # Exact-date state-only sidecars never import another source's prices.
    observed = set(zip(frame.symbol, frame.date))
    added = [{"symbol": x["code"], "date": x["date"], "trade_status": "0",
        "source_status": "source_explicit_suspension", "suspension_confirmed": True,
        "entry_eligible": False} for x in suspensions if (x["code"], x["date"]) not in observed]
    if added:
        for row in added:
            part = factors[factors.code.eq(row["symbol"])].sort_values("date")
            prior = part[part.date.le(row["date"])]
            row["adjustment_factor"] = float(prior.adj.iloc[-1]) if not prior.empty else np.nan
        frame = pd.concat([frame, pd.DataFrame(added)], ignore_index=True)
    frame["entry_eligible"] = frame["entry_eligible"].map(lambda x: x is True or isinstance(x, np.bool_) and bool(x))
    # Delisting markers are claim status, never fictitious suspension prices.
    delisted = []
    for code, life in lives.items():
        date = life.get("delisted_date")
        if date and meta["start"] <= date <= meta["end"] and (code, date) not in observed:
            delisted.append({"symbol": code, "date": date, "trade_status": "delisted", "entry_eligible": False})
    if delisted:
        frame = pd.concat([frame, pd.DataFrame(delisted)], ignore_index=True)
    keep = ["date", "symbol", "raw_open", "raw_high", "raw_low", "raw_close", "prevclose", "volume_shares",
        "trade_status", "up_limit", "down_limit", "entry_eligible", "adjustment_factor", "source_status", "suspension_confirmed"]
    for k in keep:
        if k not in frame:
            frame[k] = None
    receipt = {"raw_rows": meta["source_rows"], "raw_identity_row_hash": raw_hash.hexdigest(), "factor_record_hash": factor_hash,
        "factor_rows_through_end": len(factors), "missing_factor_rows": int(frame.adjustment_factor.isna().sum()),
        "unknown_lifecycle_source_rows": unknown_life_rows, "explicit_suspension_sidecar_rows": len(added),
        "delisting_claim_markers": len(delisted), "derived_limit_price_role": "raw source reference+time-correct exchange ratios+ROUND_HALF_UP; official daily limit series not supplied",
        "historical_ST_flag_role": "retained source day flag; complete vendor PIT transitions unverified",
        "raw_panel_persisted_or_source_database_changed": False}
    return frame[keep].sort_values(["date", "symbol"]), receipt


def run(cfg, output):
    from panda_alpha.limit_reversal import simulate
    protocol = read(output / "protocol.json")
    for b in protocol["bindings"].values():
        if binding(b["path"]) != b:
            raise ValueError("Frozen source/runtime binding changed")
    if (output / "summary.json").exists():
        raise ValueError("Complete study already saved; no duplicate replay")
    r = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    registered = r.registrations(); r.close()
    for item in protocol["registrations"]:
        if registered.get(item["key"], {}).get("definition") != item["definition"]:
            raise ValueError("Both frozen policies must be registered before signals/returns")
    meta = read(output / "source_metadata.json")
    frame, source = load_bars(cfg, meta)
    save(output / "actual_input_receipt.json", source)
    curves, summaries, trades, examples = [], [], [], []
    for policy in ["baseline", "risk_guard"]:
        for cost in [.003, .005]:
            key = policy + "_" + str(int(cost * 10000)) + "bp"
            result = simulate(frame, meta["calendar"], policy=policy, cost=cost,
                initial_cash=100000, max_slots=5, slot_fraction=.20, lot_size=100, tick=.01,
                adjustment_factor_role="total_return_factor")
            s = result["summary"]
            s["path"] = key
            s["signals_count"] = len(result["signals"])
            s["buy_fills"] = sum(t["side"] == "buy" for t in result["trades"])
            sales = [t for t in result["trades"] if t["side"] == "sell"]
            s["closed_trade_win_rate"] = sum(t["realized_net_pnl"] > 0 for t in sales) / len(sales) if sales else None
            s["exit_reason_counts"] = dict(Counter(t["reason"] for t in sales))
            s["median_completed_holding_sessions"] = float(np.median([t["holding_sessions"] for t in sales])) if sales else None
            annual = []
            for year, rows in pd.DataFrame(result["daily"]).groupby(pd.DataFrame(result["daily"]).date.str[:4]):
                annual.append({"year": year, **performance(rows.net_return), "sessions": len(rows)})
            s["calendar_year_diagnostics"] = annual
            summaries.append(s)
            curves.append(pd.DataFrame(result["daily"])[["date", "nav", "cash", "exposure"]].rename(columns={k: key + "_" + k for k in ["nav", "cash", "exposure"]}).set_index("date"))
            if cost == .003:
                trades.extend({"policy": policy, **t} for t in result["trades"])
                if policy == "baseline":
                    examples = [t for t in result["trades"] if t["side"] == "buy" and t["date"] >= "2025-01-02"][:20]
            save(output / (key + "_summary.json"), s)
            print(json.dumps({"path": key, "status": s["status"], "metrics": s["metrics"], "buys": s["buy_fills"], "round_trips": s["completed_round_trips"], "source_pending": s["source_pending"]}, ensure_ascii=False), flush=True)
    pd.concat(curves, axis=1).to_csv(output / "daily_nav.csv", encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(output / "trades_30bp.csv", index=False, encoding="utf-8-sig")
    save(output / "minute_audit_selected_entries.json", {"selection": "First20 chronological baseline30bp buys fromJan2025; no selection on exits/returns", "entries": examples})
    result = {"status": "ALL_FOUR_EVENT_PATHS_COMPLETE" if all(x["status"] == "POTENTIAL_DAILY_BAR_BACKTEST_COMPLETE" for x in summaries) else "SOURCE_PENDING_SOME_PATHS",
        "window": {"start": meta["start"], "end": meta["end"]}, "calendar_sessions": len(meta["calendar"]), "scope_identity_count": len(meta["codes"]),
        "source": source, "paths": summaries, "official_compute_spent": 0, "formal_pool_candidate": False,
        "auction_queue_or_real_capacity_certified": False, "not_fresh_unseen_OOS": True}
    save(output / "summary.json", result)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["prepare", "run"])
    ap.add_argument("--config", default=str(ROOT / "config/panda-alpha.local.json"))
    ap.add_argument("--output", type=Path, default=ROOT / "research_runs/limit_reversal_20261014")
    ap.add_argument("--start", default="2021-09-22")
    ap.add_argument("--end", default="2026-09-18")
    args = ap.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    cfg = read(args.config)
    if args.stage == "prepare":
        prepare(cfg, args.output, args.start, args.end)
    else:
        run(cfg, args.output)


if __name__ == "__main__":
    main()
