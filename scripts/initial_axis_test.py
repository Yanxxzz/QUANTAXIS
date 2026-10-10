"""Frozen, local AXIS engineering test; no platform calls or pool admission.

The sampled source pool grows while migration proceeds. A saved sample.json is
immutable and reused, so rerunning this directory does not cherry-pick stocks.
Historical source lifecycle dates describe coverage; today's listing_status is
never used to exclude a security from the return sample.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from panda_alpha.cli import TrialLedger, get_config, historical_denominator
from panda_alpha.data import AxisProvider, normalize_code
from panda_alpha.diversity import DiversityPolicy, cross_sectional_rank_correlation
from panda_alpha.evaluation import FormulaEvaluator, check_window, evaluate_signal
from panda_alpha.evolution import FailureEvidence, ResearchEngine, deterministic_candidates
from panda_alpha.execution import ExecutionPolicy, audit_next_open, mark_position, reviewed_2026_rulebook

START, END = "2026-06-18", "2026-09-18"
SEED = "panda-alpha-initial-engineering-v1"
COSTS = (.003, .005)
SCOPE = "FIXED_HASH_SHSZ_ENGINEERING_SAMPLE_NOT_ALL_A_EVIDENCE"


def clean(value: Any):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(clean(payload), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_pool(provider, start=START, end=END):
    """Source-complete active-window pool, including lifecycle-ending members.

    Known suspensions may have partial receipts; unresolved omissions may not.
    Raw receipts and same-source adjustment receipts must cover the window.
    """
    receipts = provider._records("panda_axis_sync")
    adjustment = {(r.get("code"), r.get("source")) for r in receipts
                  if r.get("dataset") in {"stock_adj", "stock_adjusted_day", "stock_xdxr"}
                  and r.get("status") == "complete" and r.get("through", "") >= end
                  and (r.get("dataset") != "stock_adj" or r.get("history_complete") is True)}
    raw = {}
    for receipt in receipts:
        if receipt.get("dataset") != "stock_day" or receipt.get("through", "") < end or receipt.get("start", "9999") > start:
            continue
        status = receipt.get("status")
        known_suspensions_only = (status == "partial" and isinstance(receipt.get("suspended_dates"), list)
            and all(receipt.get(k) == [] for k in ("source_missing_dates", "unknown_missing_dates", "off_lifecycle_dates"))
            and receipt.get("duplicate_dates") in (0, []))
        if (status == "complete" or known_suspensions_only) and (receipt.get("code"), receipt.get("source")) in adjustment:
            raw[receipt["code"]] = receipt
    lifecycles = provider._records("stock_lifecycle")
    active, retired = [], []
    seen = set()
    for row in sorted(lifecycles, key=lambda r: (str(r.get("source")), str(r.get("code")))):
        code = normalize_code(row["code"])
        selected_source = getattr(provider, "price_source", None)
        price_matches = raw.get(code, {}).get("source") == (selected_source or row.get("source"))
        if code in seen or code not in raw or not price_matches or row.get("sse") not in {"sh", "sz"}:
            continue
        seen.add(code)
        item = {**row, "code": code, "raw_receipt": raw[code]}
        if row.get("delisted_date") and row["delisted_date"] <= end:
            retired.append(item)
        # A future/current listing-status label does not filter this set.
        if row.get("ipo_date") and row["ipo_date"] <= end and (not row.get("delisted_date") or row["delisted_date"] > start):
            active.append(item)
    return active, retired, receipts


def fixed_sample(records, size=300, seed=SEED):
    if not 1 <= size <= 400:
        raise ValueError("Engineering sample size must be in [1,400]")
    strata = {exchange: [] for exchange in ("sh", "sz")}
    for row in records:
        entry = {**row, "selection_hash": hashlib.sha256(f"{seed}|{row['sse']}|{row['code']}".encode()).hexdigest()}
        strata[row["sse"]].append(entry)
    for bucket in strata.values():
        bucket.sort(key=lambda row: (row["selection_hash"], row["code"]))
    chosen = strata["sh"][:size // 2] + strata["sz"][:size - size // 2]
    selected = {r["code"] for r in chosen}
    remaining = sorted((r for rows in strata.values() for r in rows if r["code"] not in selected),
                       key=lambda row: (row["selection_hash"], row["code"]))
    chosen += remaining[:max(0, size - len(chosen))]
    return sorted(chosen, key=lambda row: (row["sse"], row["selection_hash"], row["code"]))


def calendar_snapshot(provider, start=START, end=END):
    receipts = provider._records("panda_axis_sync", {"dataset": "trade_calendar", "code": "SSE"})
    valid = [r for r in receipts if r.get("status") == "complete" and r.get("start", "9999") <= start and r.get("through", "") >= end]
    source = "baostock_trade_dates" if any(r.get("source") == "baostock" for r in valid) else None
    query = {"date": {"$gte": start, "$lte": end}, "exchange": "SSE"}
    if source:
        query["source"] = source
    rows = provider._records("trade_calendar", query)
    dates = sorted({str(r["date"])[:10] for r in rows})
    return {"status": "verified" if valid and dates else "pending", "sessions": dates,
            "source": source or "source_calendar", "receipts": valid, "sha256": digest(rows)}


def calendar_gate(frame, coverage, calendar):
    expected = set(calendar["sessions"])
    observed = set(pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")) if not frame.empty else set()
    missing, off = sorted(expected - observed), sorted(observed - expected)
    verified = calendar["status"] == "verified" and coverage.get("calendar", {}).get("status") == "verified"
    return {"status": "verified" if verified and expected and not missing and not off else "pending",
            "expected_sessions": len(expected), "observed_sessions": len(observed),
            "missing_whole_sessions": missing, "off_calendar_dates": off,
            "reason": "Complete independent sessions required; observed unions cannot compress a trading-day gap"}


def write_panel(path, frame):
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    return {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "rows": len(frame)}


def execution_rows(records):
    rows = []
    for record in records:
        row = {**record, "symbol": normalize_code(record["code"])}
        for field in ("open", "high", "low", "close"):
            row["raw_" + field] = record.get(field)
        row["raw_preclose"] = record.get("preclose")
        row["volume"] = record.get("vol", record.get("volume"))
        # Source daily data do not prove board/IPO/special-regime metadata or
        # the exchange's ex-rights reference. Never manufacture those flags.
        row.setdefault("security_status_verified", False)
        row.setdefault("limit_reference_verified", False)
        rows.append(row)
    return pd.DataFrame(rows, columns=None if rows else ["date", "symbol"])


def suspension_marks(frame):
    marks = []
    if frame.empty:
        return marks
    for symbol, group in frame.sort_values(["symbol", "date"]).groupby("symbol"):
        previous, previous_date = None, None
        for row in group.to_dict("records"):
            date = str(row["date"])[:10]
            if str(row.get("trade_status")) == "0":
                marks.append({"symbol": symbol, "date": date, **mark_position(row, date, previous, previous_date)})
            elif str(row.get("trade_status")) == "1":
                close = row.get("raw_close")
                if close is not None and pd.notna(close) and np.isfinite(float(close)) and float(close) > 0:
                    previous, previous_date = float(close), date
    return marks


def pairwise(values, policy):
    ids = list(values)
    signed = {i: {j: None for j in ids} for i in ids}
    absolute = {i: {j: None for j in ids} for i in ids}
    pairs = []
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            comparison = cross_sectional_rank_correlation(values[left], values[right], policy, right).to_dict()
            comparison["candidate_id"] = left
            # A pending gate can still carry descriptive observed statistics;
            # preserve its coverage status and never turn it into a diversity pass.
            observed = [r["correlation"] for r in comparison["daily"] if r["status"] == "valid"]
            comparison["observed_signed_mean"] = float(np.mean(observed)) if observed else None
            comparison["observed_mean_abs"] = float(np.mean(np.abs(observed))) if observed else None
            for a, b in ((left, right), (right, left)):
                signed[a][b] = comparison["observed_signed_mean"]
                absolute[a][b] = comparison["observed_mean_abs"]
            pairs.append(comparison)
    return {"basis": "per-session cross-sectional ranks of actual factor values; abs before time aggregation",
            "signed_observed_matrix": signed, "absolute_observed_matrix": absolute, "pairs": pairs,
            "existing_pool_status": "pending: no measured factor-value panels for the official existing pool"}


def chinese_report(payload):
    sample, outcomes = payload["sample"], payload["candidates"]
    lines = [f"# AXIS 工程初测（{payload['generated_at_asia_shanghai']}）", "",
        f"固定窗口 {START} 至 {END}，HFQ，5 个交易日换仓、10 组，方向在测试前冻结。",
        f"收益研究样本 {len(sample['codes'])} 只（SH {sum(r['sse']=='sh' for r in sample['members'])} / SZ {sum(r['sse']=='sz' for r in sample['members'])}），按固定 SHA256 分层选取，SH/SZ 不足配额以固定哈希补足；来源快照可用池 {payload['source_pool_snapshot']['active_window_completed_codes']} 只。",
        "这是同步途中工程样本，不是全 A 收益证据。当前上市/退市标签不筛选历史收益股票池；退市来源另作覆盖审计。", "",
        f"日期门：{payload['calendar_gate']['status']}；{payload['calendar_gate']['observed_sessions']}/{payload['calendar_gate']['expected_sessions']} 个交易日。",
        f"试验分母：历史 {payload['trial_ledger']['historical']} + 已登记 {payload['trial_ledger']['new_tested']} = {payload['trial_ledger']['total']}；本批 {payload['trial_ledger'].get('batch_unique_trials',len(outcomes))} 个唯一试验，两档成本不重复计数。", "",
        "| 机制（冻结方向） | RankIC 均值 / 日期数 | 30bp 净累计收益 | 50bp 净累计收益 | 状态 |",
        "| --- | --- | --- | --- | --- |"]
    def pct(value):
        return f"{value:.2%}" if value is not None else "待验证"
    for outcome in outcomes:
        results = outcome.get("cost_results", {})
        lower, upper = results.get("0.003", {}), results.get("0.005", {})
        ic = lower.get("rank_ic_mean")
        ic_text = f"{ic:.4f} / {lower.get('rank_ic_dates')}" if ic is not None else "待验证"
        lines.append(f"| {outcome['mechanism']}（{outcome['direction']}） | {ic_text} | {pct(lower.get('net',{}).get('compounded_return'))} | {pct(upper.get('net',{}).get('compounded_return'))} | {outcome['status']} |")
    lines += ["", "相关矩阵由每日期真实横截面因子值的秩相关计算，未使用收益/IC 摘要推断独立性。暖机、覆盖不足或常量日期仍保留 pending 状态；已入库平台池缺少因子值，增量独立性待验证。",
        "收益统计包含窗口内暖机期间的现金空仓日，不把因子缺值当作已有持仓的零收益。不同机制的有效持仓和 IC 日期数不同，不能凭短样本 Sharpe 排名做入池判断。",
        f"退市覆盖审计代码：{', '.join(payload['retirement_audit']['codes']) or '当前完整来源尚无退市代码'}；该窗口原始行数 {payload['retirement_audit']['raw_rows_by_code']}。窗口之前已退市者只核对生命周期，不生成本窗退市收益或退出成交。",
        "日线不能证明我方开盘竞价成交数量；执行审计、停牌退出、退市终值、PIT 全市场覆盖及官方迁移均待验证。成本收益是本地 next-open 研究代理，不能作为可执行净值或经济验收。",
        "本轮反思只保存在该报告，不改永久机制黑名单、不入池、不使用封存 OOS、不调用官网或充值。", "",
        f"样本与来源哈希：sample.json / source_receipts.json / daily_hfq.csv.gz / raw_execution.csv.gz；完整因子面板与每日相关、IC、收益、执行缺口见本目录 JSON/CSV。"]
    return "\n".join(lines) + "\n"


def engineering_summary(payload):
    rows = []
    for result in payload["candidates"]:
        a, b = result["cost_results"].get("0.003", {}), result["cost_results"].get("0.005", {})
        rows.append({"candidate_id": result["candidate_id"], "mechanism": result["mechanism"],
            "direction": result["direction"], "status": result["status"], "rank_ic": a.get("rank_ic_mean"),
            "rank_ic_dates": a.get("rank_ic_dates"), "net_return_30bp": a.get("net", {}).get("compounded_return"),
            "net_return_50bp": b.get("net", {}).get("compounded_return"),
            "proxy_sharpe_30bp": a.get("net", {}).get("sharpe"), "proxy_sharpe_50bp": b.get("net", {}).get("sharpe"),
            "execution": result.get("execution"), "reflection": result["reflection"]["action"]})
    return {"scope": payload["scope"], "status": payload["status"], "window": payload["window"],
            "generated_at_asia_shanghai": payload["generated_at_asia_shanghai"], "settings": payload["settings"],
            "sample_count": len(payload["sample"]["codes"]), "exchange_counts": dict(Counter(r["sse"] for r in payload["sample"]["members"])),
            "sample_sha256": payload["sample"]["definition_sha256"], "data_artifacts": payload["data_artifacts"],
            "calendar_gate": payload["calendar_gate"], "trial_ledger": payload["trial_ledger"],
            "results": rows, "correlations": payload["correlations"], "retirement_audit": payload["retirement_audit"],
            "permissions": payload["permissions"]}


def run_initial(provider, output, *, sample_size=300, seed=SEED, ledger_path=None,
                historical=415, sealed=(), start=START, end=END):
    if (start, end) != (START, END):
        raise ValueError("This engineering script is frozen to 2026-06-18..09-18")
    check_window(start, end, list(sealed))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    pool, retired, receipts = source_pool(provider, start, end)
    snapshot = {"active_window_completed_codes": len(pool), "exchange_counts": dict(Counter(r["sse"] for r in pool)),
                "retired_completed_codes": len(retired), "completion_snapshot_sha256": digest(pool),
                "note": "Growing download pool; no claim of exchange/PIT all-market acceptance"}
    sample_path = output / "sample.json"
    if sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        if sample["seed"] != seed or sample["requested_size"] != sample_size or sample["window"] != [start, end]:
            raise ValueError("Frozen sample identity changed; use a different output directory")
    else:
        selected = fixed_sample(pool, sample_size, seed)
        audit_selected = fixed_sample(retired, min(20, len(retired)), seed + "|retirement-audit") if retired else []
        core = {"scope": SCOPE, "seed": seed, "hash_definition": "SHA256(seed|source_exchange|six_digit_code)",
                "window": [start, end], "requested_size": sample_size, "codes": [r["code"] for r in selected],
                "members": selected, "retirement_audit_codes": [r["code"] for r in audit_selected],
                "retirement_audit_members": audit_selected,
                "selection_policy": "balanced SH/SZ then hash fallback; current listing_status never filters return sample"}
        identity = {k: core[k] for k in ("scope", "seed", "hash_definition", "window", "requested_size", "codes", "selection_policy")}
        sample = {**core, "definition_sha256": digest(identity), "metadata_snapshot_sha256": digest(core)}
        write_json(sample_path, sample)
    if len(sample["codes"]) < 30:
        raise ValueError("Fewer than 30 source-complete codes are available; wait for source migration")
    calendar = calendar_snapshot(provider, start, end)
    daily = provider.daily(sample["codes"], start, end, adjustment="hfq")
    gate = calendar_gate(daily.frame, daily.coverage, calendar)
    all_codes = sorted(set(sample["codes"] + sample["retirement_audit_codes"]))
    raw = provider._records("stock_day", {"code": {"$in": all_codes}, "date": {"$gte": start, "$lte": end}})
    raw_frame = execution_rows(raw)
    manifests = {"hfq": write_panel(output / "daily_hfq.csv.gz", daily.frame),
                 "raw_execution": write_panel(output / "raw_execution.csv.gz", raw_frame),
                 "raw_record_semantic_sha256": digest(sorted(raw, key=lambda r: (r["date"], r["code"]))),
                 "sample_definition_sha256": sample["definition_sha256"]}
    selected_receipts = [r for r in receipts if r.get("code") in all_codes or r.get("dataset") == "trade_calendar"]
    write_json(output / "source_receipts.json", {"receipts": selected_receipts, "sha256": digest(selected_receipts), "calendar": calendar})
    candidates = deterministic_candidates()[:6]
    write_json(output / "candidates_frozen.json", {"scope": SCOPE, "direction_frozen_before_values": True,
                                                "candidates": [c.to_dict() for c in candidates]})
    values, outcomes = {}, []
    researcher = ResearchEngine(backend=None)  # ephemeral; never persist engine state/blacklists
    ledger = TrialLedger(ledger_path or output / "trials.sqlite3", historical)
    try:
        for candidate in candidates:
            outcome = {"candidate_id": candidate.candidate_id, "mechanism": candidate.mechanism,
                       "direction": candidate.direction, "status": "pending", "cost_results": {},
                       "economic_status": "pending", "data_requirements": list(candidate.data_requirements)}
            if gate["status"] != "verified":
                outcome["reason"] = "Independent calendar/full-session gate is pending; no return labels created"
            else:
                try:
                    panel = FormulaEvaluator(daily.frame).evaluate(candidate.formula).reindex(pd.to_datetime(calendar["sessions"]))
                    values[candidate.candidate_id] = panel
                    artifact = output / f"{candidate.candidate_id}.values.csv.gz"
                    panel.to_csv(artifact, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
                    counts = panel.notna().sum(axis=1)
                    outcome["factor_values"] = {"path": str(artifact.resolve()), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                                "finite_values": int(panel.notna().sum().sum()), "dates_minimum_30_assets": int((counts >= 30).sum()),
                                                "missing_by_date": {d.strftime("%Y-%m-%d"): int(len(panel.columns) - n) for d, n in counts.items()}}
                    if (counts >= 30).any():
                        outcome["new_trial_registered"] = ledger.record(candidate.to_dict(),
                            {"start": start, "end": end, "adjustment": "hfq", "sample_sha256": sample["definition_sha256"], "scope": SCOPE}, 5, 10)
                        for cost in COSTS:
                            try:
                                outcome["cost_results"][str(cost)] = evaluate_signal(daily.frame, panel, cycle=5,
                                    direction=candidate.direction, cost=cost, groups=10, minimum_assets=30)
                            except ValueError as exc:
                                outcome["cost_results"][str(cost)] = {"status": "pending", "reason": str(exc)}
                    else:
                        outcome["reason"] = "No factor date reaches minimum 30-security coverage"
                    execution = audit_next_open(raw_frame[raw_frame["symbol"].isin(sample["codes"])], panel,
                        reviewed_2026_rulebook(), ExecutionPolicy(cycle=5, groups=10, direction=candidate.direction),
                        calendar=calendar["sessions"])
                    execution_path = output / f"{candidate.candidate_id}.execution.json"
                    write_json(execution_path, execution)
                    outcome["execution"] = {"path": str(execution_path.resolve()), "status": execution["status"],
                                            "order_counts": execution.get("order_counts", {}),
                                            "admission_status": execution.get("admission_status")}
                    if all(r.get("evidence_status") == "LOCAL_RESEARCH_PROXY" for r in outcome["cost_results"].values()) and len(outcome["cost_results"]) == 2:
                        outcome["status"] = "engineering_proxy_complete_economic_pending"
                except (ValueError, KeyError) as exc:
                    outcome["reason"] = f"{type(exc).__name__}: {exc}"
            outcomes.append(outcome)
        correlations = pairwise(values, DiversityPolicy(max_abs_correlation=.7, min_assets=30, min_days=20))
        write_json(output / "factor_rank_correlations.json", correlations)
        for candidate, outcome in zip(candidates, outcomes):
            related = [p for p in correlations["pairs"] if candidate.candidate_id in {p["candidate_id"], p["pool_id"]}
                       and p["status"] == "complete" and p.get("mean_abs_correlation", 0) > .7]
            others = tuple(p["pool_id"] if p["candidate_id"] == candidate.candidate_id else p["candidate_id"] for p in related)
            complete = outcome["status"] == "engineering_proxy_complete_economic_pending"
            evidence = FailureEvidence(failure_type="high_correlation" if others else "inconclusive_engineering_sample" if complete else "source_gap",
                observations=("Engineering sample is not all-A/PIT/executable or official admission evidence",
                              "Frozen directions and mechanisms were not chosen from outcome signs"),
                metrics={"costs": {c: r.get("net", {}) for c, r in outcome["cost_results"].items()}},
                artifacts=(str((output / "factor_rank_correlations.json").resolve()),),
                correlated_with=others, data_complete=True if complete else False,
                economic_validation={"status": "pending"})
            outcome["reflection"] = {**researcher.reflect(candidate, evidence).to_dict(),
                                     "scope": "report_only_no_persistent_family_blacklist",
                                     "economic_proxy_cannot_falsify_full_market_hypothesis": True}
        payload = {"schema_version": 1, "scope": SCOPE, "status": "engineering_test_complete_admission_pending" if gate["status"] == "verified" else "pending_calendar",
                   "generated_at_asia_shanghai": datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai")).isoformat(),
                   "window": [start, end], "settings": {"adjustment": "hfq", "cycle": 5, "groups": 10, "one_way_costs": COSTS},
                   "sample": sample, "source_pool_snapshot": snapshot, "data_artifacts": manifests,
                   "coverage": daily.coverage, "calendar_gate": gate, "candidates": outcomes,
                   "correlations": {"path": str((output / "factor_rank_correlations.json").resolve()),
                                    "signed_observed_matrix": correlations["signed_observed_matrix"], "absolute_observed_matrix": correlations["absolute_observed_matrix"],
                                    "pair_gate_counts": dict(Counter(p["status"] for p in correlations["pairs"])),
                                    "existing_pool_status": correlations["existing_pool_status"]},
                   "retirement_audit": {"codes": sample["retirement_audit_codes"], "scope": "coverage_and_suspension_only; no return selection from future lifecycle status",
                                        "raw_rows_by_code": {code: sum(r["code"] == code for r in raw) for code in sample["retirement_audit_codes"]},
                                        "known_suspension_marks": suspension_marks(raw_frame), "terminal_cashflows": "pending; no zero-return imputation"},
                   "trial_ledger": {"path": str(Path(ledger_path or output / "trials.sqlite3").resolve()), "historical": ledger.historical,
                                    "new_tested": ledger.new_tested, "total": ledger.total,
                                    "batch_unique_trials": sum("new_trial_registered" in r for r in outcomes),
                                    "batch_newly_registered": sum(bool(r.get("new_trial_registered")) for r in outcomes),
                                    "cost_variants_not_separate_hypotheses": True},
                   "permissions": {"platform_calls": 0, "recharge_spend": 0, "llm_calls": 0, "pool_admissions": 0,
                                   "permanent_blacklist_updates": 0, "sealed_oos_evaluations": 0}}
        write_json(output / "report.json", payload)
        write_json(output / "summary.json", engineering_summary(payload))
        (output / "report.md").write_text(chinese_report(payload), encoding="utf-8")
        return clean(payload)
    finally:
        ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO / "config/panda-alpha.local.json"))
    parser.add_argument("--sample-size", type=int, default=300)
    parser.add_argument("--seed", default=SEED)
    parser.add_argument("--output", default=str(REPO / "research_runs/initial_axis_300"))
    parser.add_argument("--trial-ledger", default=str(REPO / "research_runs/research_trials.sqlite3"))
    args = parser.parse_args(argv)
    if not 200 <= args.sample_size <= 400:
        parser.error("CLI engineering sample must contain 200..400 codes")
    cfg = get_config(args.config)
    from panda_alpha.sources import provider_from_config
    provider = provider_from_config(cfg)
    result = run_initial(provider, args.output, sample_size=args.sample_size, seed=args.seed,
                         ledger_path=args.trial_ledger, historical=historical_denominator(cfg),
                         sealed=cfg["research"]["sealed_windows"])
    print(json.dumps({"status": result["status"], "sample_codes": len(result["sample"]["codes"]),
                      "source_pool_available": result["source_pool_snapshot"]["active_window_completed_codes"],
                      "denominator": result["trial_ledger"]["total"], "report": str(Path(args.output) / "report.md"),
                      "generated_at_asia_shanghai": result["generated_at_asia_shanghai"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
