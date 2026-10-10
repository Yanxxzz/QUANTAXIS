"""Prospective pool-change review. Reads bound evidence; never submits a pool.

Historical A+C is a research proxy. Missing official B and forward validation
are separate from economic rejection, and cannot be replaced by historical IC.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path
import statistics

from .pool_scoring import monthly_c, normalized_a, new_b_score
from .registry import verify_artifact


IDENTITY = "pool_admission_v1_20261010"
CONTRACTS = ("source_coverage", "point_in_time_universe", "execution_audit",
             "size_stress", "temporal_stability", "actual_factor_diversity",
             "official_transfer", "direction_parity", "current_pool_snapshot",
             "independent_calendar", "benchmark", "contest_construction",
             "same_formation_support")


class EconomicRejection(ValueError):
    """Complete evidence disproves this frozen proposed change."""


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite numeric evidence required")
    return value


def _date(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return parsed


def _members(members):
    if not isinstance(members, list) or not members:
        raise ValueError("Explicit pool members required")
    keys = []
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Pool member must be an object")
        digest = member.get("definition_sha256", "")
        if (not isinstance(member.get("id"), str) or not member["id"] or
                not isinstance(member.get("version"), str) or not member["version"] or
                type(member.get("direction")) is not int or member["direction"] not in (0, 1) or
                type(member.get("effective")) is not bool or
                not isinstance(digest, str) or len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("Members require ID, version, definition SHA256, direction and effective status")
        keys.append(member["id"])
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate pool member")
    return {member["id"]: member for member in members}


def _plan(plan, gate):
    baseline, proposed = _members(plan["baseline"]), _members(plan["proposed"])
    if (type(plan.get("cycle")) is not int or plan["cycle"] != 5 or
            type(plan.get("groups")) is not int or plan["groups"] != 10):
        raise ValueError("Freeze the current five-day, ten-group contest protocol")
    start, end = _date(plan["window"]["start"]), _date(plan["window"]["end"])
    cutoff, change = _date(plan["as_of"]), _date(plan["planned_change_date"])
    if start >= end or end > cutoff or change < cutoff:
        raise ValueError("Invalid frozen evidence or prospective change dates")
    operation = plan.get("operation")
    added, removed = set(proposed) - set(baseline), set(baseline) - set(proposed)
    changed = {key for key in set(baseline) & set(proposed)
               if any(baseline[key][field] != proposed[key][field]
                      for field in ("version", "definition_sha256", "direction"))}
    candidates = plan.get("candidate_ids")
    if not isinstance(candidates, list) or len(set(candidates)) != len(candidates):
        raise ValueError("Unique explicit candidate IDs required")
    if operation == "add":
        valid = added and not removed and not changed and set(candidates) == added
    elif operation == "remove":
        valid = removed and not added and not changed and set(candidates) == removed
    elif operation == "replace":
        valid = (added or changed) and set(candidates) == added | changed
    else:
        valid = False
    if not valid:
        raise ValueError("The complete proposed change does not match operation/candidates")
    if operation != "add" and change.day not in (1, 2, 3):
        raise ValueError("Deletion/modification is outside the saved monthly 1–3 window")
    stages = plan.get("transitions")
    if not isinstance(stages, list) or not stages or stages[0] != plan["baseline"] or stages[-1] != plan["proposed"]:
        raise ValueError("Explicit transitions must start at actual baseline and end at proposed pool")
    for stage in stages:
        _members(stage)
        if (sum(member["effective"] for member in stage) < gate["minimum_effective_factors"] or
                len(stage) > gate["maximum_factors"]):
            raise ValueError("Every transition needs at least five effective members and at most fifty members")
    return baseline, proposed


def _performance(rows, dates, benchmark):
    if not isinstance(rows, list) or [row.get("date") for row in rows] != dates:
        raise ValueError("Baseline, proposed and benchmark must use exactly the same ordered dates")
    returns, turnover = [], []
    for row in rows:
        value = _number(row.get("net_return"), "net_return")
        turn, fee = _number(row.get("turnover"), "turnover"), _number(row.get("fee"), "fee")
        if value <= -1 or turn < 0 or fee < 0:
            raise ValueError("Invalid daily return, turnover or paid fee")
        returns.append(value)
        turnover.append(turn)
    if any(value <= -1 for value in benchmark):
        raise ValueError("Benchmark returns must be greater than -100%")
    sd = statistics.stdev(returns)
    if sd <= 0:
        raise ValueError("Undefined daily Sharpe")
    net_growth, benchmark_growth = math.prod(1 + value for value in returns), math.prod(1 + value for value in benchmark)
    if net_growth <= 0 or benchmark_growth <= 0:
        raise ValueError("Nonpositive compounded wealth")
    wealth = peak = 1.
    drawdown = 0.
    for value in returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        drawdown = max(drawdown, 1 - wealth / peak)
    whole = {"N": len(returns), "Rp": net_growth - 1, "Rb": benchmark_growth - 1,
             "CAGR": math.expm1(math.log(net_growth) * 252 / len(returns)),
             "SR_ann": statistics.fmean(returns) / sd * math.sqrt(252),
             "MaxDD": drawdown, "Turnover_total": sum(turnover),
             "fee_total": sum(row["fee"] for row in rows),
             "relative_wealth_excess": net_growth / benchmark_growth - 1}
    if any(not math.isfinite(value) for value in whole.values()):
        raise ValueError("Nonfinite portfolio metrics")
    return whole, returns, turnover


def assess_pool_admission(evidence: dict, policy: dict, *, evidence_directory=".",
                          window_check=None) -> dict:
    pending, failed, economic = [], [], []
    metrics, receipts = {}, {}
    result = {"policy": IDENTITY, "effective_policy_sha256": plan_digest(policy),
              "candidate_ids": evidence.get("plan", {}).get("candidate_ids", []),
              "official_total_points_gain_verified": False, "pool_operations": 0,
              "paid_runs": 0, "research_exploration_allowed": True}

    def finish():
        result.update(failed=sorted(set(failed)), pending=sorted(set(pending)),
                      economic_rejected=bool(economic), metrics=metrics, verified_artifacts=receipts)
        result["status"] = ("rejected" if failed else "pending" if pending else
                            "eligible_for_review" if result.get("forward_validation_verified") else
                            "ready_for_shadow_validation")
        return result

    if evidence.get("schema_version") != 1 or policy.get("identity") != IDENTITY:
        pending.append("prospective_evidence_schema")
        return finish()
    plan = evidence.get("plan", {})
    try:
        baseline, proposed = _plan(plan, policy["portfolio_gate"])
        digest = plan_digest(plan)
    except (KeyError, TypeError, ValueError) as exc:
        pending.append(f"plan: {exc}")
        # A known unsafe change is rejected, not confused with missing sources.
        if "Every transition" in str(exc) or "outside the saved" in str(exc):
            failed.append("illegal_pool_transition")
        return finish()
    result["plan_sha256"] = digest

    def load(name):
        artifact = evidence.get("artifacts", {}).get(name)
        if not isinstance(artifact, dict):
            return None
        try:
            path = Path(artifact.get("path", ""))
            if not path.is_absolute():
                path = Path(evidence_directory) / path
            checked = verify_artifact({"path": str(path), "sha256": artifact.get("sha256")})
            body = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(body, dict) or body.get("plan_sha256") != digest:
                raise ValueError("Artifact belongs to a different candidate/pool/version/window plan")
            if window_check:
                window_check(body)
            receipts[name] = checked
            if body.get("status") == "failed":
                failed.append(f"{name}: bound evidence failed")
            elif body.get("status") != "verified":
                pending.append(f"{name}: evidence not verified")
            return body if body.get("status") == "verified" else None
        except (OSError, ValueError, TypeError) as exc:
            pending.append(f"{name}: {exc}")
            return None

    contracts, quality, ledger, points = (load(key) for key in ("contracts", "quality", "ledger", "points"))
    required_bodies = [("contracts", contracts), ("ledger", ledger), ("points", points)]
    if plan["operation"] != "remove":
        required_bodies.append(("quality", quality))
    for key, body in required_bodies:
        if body is None:
            pending.append(key)
    if contracts:
        for key in CONTRACTS:
            status = contracts.get("checks", {}).get(key, {}).get("status")
            if status == "failed":
                failed.append(f"contract:{key}")
            elif status != "verified":
                pending.append(f"contract:{key}")
        if contracts.get("construction") != "winsor_1_99_zscore_direction_equal_top10_long_only":
            pending.append("contest signal construction (not averaged sleeve returns)")
    if quality and plan["operation"] != "remove":
        gate = policy["common_gate"]
        checks = (("coverage", ">=", "coverage_min"), ("periods", ">=", "periods_min"),
                  ("direction_rank_ic", ">=", "direction_rank_ic_min"),
                  ("folds_positive", ">=", "folds_positive_required"), ("bh_q", "<=", "batch_bh_q_max"),
                  ("net30", ">=", "net30_min"), ("net50", ">=", "net50_min"),
                  ("turnover", "<=", "turnover_max"),
                  ("size_direction_rank_ic", ">=", "size_direction_rank_ic_min"),
                  ("size_net30", ">=", "size_net30_min"),
                  ("max_abs_corr_existing_pool", "<=", "max_abs_corr_existing_pool"))
        try:
            candidates = plan["candidate_ids"]
            by_candidate = quality.get("metrics_by_candidate")
            if by_candidate is None and len(candidates) == 1:
                by_candidate = {candidates[0]: quality.get("metrics", {})}
            if not isinstance(by_candidate, dict) or set(by_candidate) != set(candidates):
                raise ValueError("Every changed candidate requires its own quality metrics")
            for candidate, q in by_candidate.items():
                if not isinstance(q, dict):
                    raise ValueError("Candidate quality metrics must be an object")
                for key in ("coverage", "bh_q", "max_abs_corr_existing_pool"):
                    if not 0 <= _number(q.get(key), key) <= 1:
                        raise ValueError(f"{key} outside its numeric domain")
                for key in ("direction_rank_ic", "size_direction_rank_ic"):
                    if not -1 <= _number(q.get(key), key) <= 1:
                        raise ValueError(f"{key} outside IC domain")
                for key in ("periods", "folds_positive", "cumulative_hypotheses"):
                    if type(q.get(key)) is not int or q[key] < 0:
                        raise ValueError(f"{key} requires a nonnegative integer")
                if _number(q.get("turnover"), "turnover") < 0:
                    raise ValueError("Negative turnover")
                for key, op, threshold in checks:
                    value = _number(q.get(key), key)
                    violates = value < gate[threshold] if op == ">=" else value > gate[threshold]
                    if violates:
                        failed.append(f"common:{key}")
                for key in ("p25_fold_net", "size_net50"):
                    if _number(q.get(key), key) <= 0:
                        failed.append(f"common:{key}")
                if q["cumulative_hypotheses"] < policy.get("minimum_hypotheses", 0):
                    pending.append("multiple-testing denominator is older than retained research history")
            metrics["quality_by_candidate"] = by_candidate
        except (ValueError, TypeError) as exc:
            pending.append(f"quality: {exc}")
    if not ledger or not points:
        return finish()
    try:
        if ledger.get("type") != "pool_signal_daily_ledger":
            raise ValueError("Full pool signal ledger required; sleeve averages are not a pool")
        calendar = ledger["calendar_months"]
        if not isinstance(calendar, dict) or not calendar:
            raise ValueError("Independent complete-month calendar required")
        calendar_dates = []
        for month, days in sorted(calendar.items()):
            if not isinstance(days, list) or len(days) < 2 or days != sorted(set(days)):
                raise ValueError("Ordered unique full-month trading dates required")
            for day in days:
                if _date(day).strftime("%Y-%m") != month:
                    raise ValueError("Calendar month/date mismatch")
            calendar_dates.extend(days)
        independent_calendar = contracts.get("calendar_months", {}) if contracts else {}
        if any(independent_calendar.get(month) != days for month, days in calendar.items()):
            raise ValueError("Ledger calendar must match the independently sourced bound calendar")
        start, end = plan["window"]["start"], plan["window"]["end"]
        dates = [day for day in calendar_dates if start <= day <= end]
        if dates != sorted(set(dates)) or len(dates) < 2:
            raise ValueError("Calendar/window date mismatch")
        benchmark_rows = ledger["benchmark"]
        if [row.get("date") for row in benchmark_rows] != dates:
            raise ValueError("Benchmark dates differ from independent calendar")
        benchmark = [_number(row.get("return"), "benchmark return") for row in benchmark_rows]
        complete = {month: days for month, days in sorted(calendar.items())
                    if days[0] >= start and days[-1] <= end}
        recent_count = policy["portfolio_gate"]["recent_months"]
        if len(complete) < recent_count:
            pending.append(f"complete_months: need at least {recent_count} for the frozen recent window")
        point_rows = points.get("months", [])
        if [row.get("month") for row in point_rows] != list(complete):
            raise ValueError("A evidence must match every paired complete calendar month")
        ac = {side: {} for side in ("baseline", "proposed")}
        for row in point_rows:
            a_by_member = {}
            for side, members in (("baseline", baseline), ("proposed", proposed)):
                scores = row[side]["a_scores"]
                if len(scores) != sum(member["effective"] for member in members.values()):
                    raise ValueError("A denominator differs from frozen effective pool")
                a_by_member[side] = dict(zip((key for key, member in members.items() if member["effective"]), scores))
                ac[side][row["month"]] = normalized_a(scores, row[side]["decay"], row[side]["a_cap"])
            for field in ("decay", "a_cap"):
                if row["baseline"][field] != row["proposed"][field]:
                    raise ValueError("Same pool age/decay/cap required for a pool change")
            for key in set(a_by_member["baseline"]) & set(a_by_member["proposed"]):
                if (all(baseline[key][field] == proposed[key][field] for field in ("version", "definition_sha256", "direction")) and
                        a_by_member["baseline"][key] != a_by_member["proposed"][key]):
                    raise ValueError("Unchanged members must use identical original A evidence")
        comparisons = {}
        indexes = {day: i for i, day in enumerate(dates)}
        for cost in policy["one_way_costs"]:
            paths = ledger["costs"][str(cost)]
            summaries, daily, turns, monthly = {}, {}, {}, {}
            for side in ("baseline", "proposed"):
                summaries[side], daily[side], turns[side] = _performance(paths[side], dates, benchmark)
                monthly[side] = {}
                for month, days in complete.items():
                    ix = [indexes[day] for day in days]
                    c = monthly_c([daily[side][i] for i in ix], [benchmark[i] for i in ix],
                                  sum(turns[side][i] for i in ix))
                    c["NA"] = ac[side][month]
                    c["AC_points_proxy"] = 40000 * (.2 * c["NA"] + .45 * c["NC"])
                    monthly[side][month] = c
            old, new = summaries["baseline"], summaries["proposed"]
            delta_cagr, delta_sharpe = new["CAGR"] - old["CAGR"], new["SR_ann"] - old["SR_ann"]
            if new["Rp"] <= 0 or new["relative_wealth_excess"] <= 0 or new["SR_ann"] <= 0:
                economic.append(f"portfolio@{cost}")
            if cost == .003 and (delta_cagr <= policy["portfolio_gate"]["cagr_delta30_min"] or
                                  delta_sharpe < policy["portfolio_gate"]["sharpe_delta_min"]):
                economic.append("portfolio_increment@0.003")
            if cost == .005 and delta_cagr < policy["portfolio_gate"]["cagr_delta50_min"]:
                economic.append("cost_stress@0.005")
            recent_months = list(complete)[-recent_count:]
            recent_dates = [day for month in recent_months for day in complete[month]]
            recent_return = {side: math.prod(1 + daily[side][indexes[day]] for day in recent_dates) - 1
                             for side in ("baseline", "proposed")}
            if recent_return["proposed"] < recent_return["baseline"]:
                economic.append(f"recent_return@{cost}")
            old_ac = sum(row["AC_points_proxy"] for row in monthly["baseline"].values())
            new_ac = sum(row["AC_points_proxy"] for row in monthly["proposed"].values())
            recent_delta = sum(monthly["proposed"][m]["AC_points_proxy"] - monthly["baseline"][m]["AC_points_proxy"]
                               for m in recent_months)
            if old_ac <= 0:
                pending.append(f"AC baseline denominator@{cost}")
            elif new_ac / old_ac - 1 < policy["portfolio_gate"]["ac_gain_min"] or recent_delta <= 0:
                economic.append(f"monthly_AC_increment@{cost}")
            risk = {side: {"full_drawdown": summaries[side]["MaxDD"],
                           "negative_months": sum(row["Rp"] < 0 for row in monthly[side].values()),
                           "NC_zero_months": sum(row["NC"] == 0 for row in monthly[side].values()),
                           "worst_month_drawdown": max(row["MaxDD_month"] for row in monthly[side].values())}
                    for side in ("baseline", "proposed")}
            if any(risk["proposed"][key] > risk["baseline"][key] for key in risk["baseline"]):
                pending.append(f"risk_tradeoff_review@{cost}: drawdown or negative/NC-zero months increased")
            comparisons[str(cost)] = {"baseline": old, "proposed": new, "CAGR_delta": delta_cagr,
                                      "Sharpe_delta": delta_sharpe, "recent_returns": recent_return,
                                      "AC_gain": new_ac / old_ac - 1 if old_ac > 0 else None,
                                      "recent_AC_delta": recent_delta, "risk_review": risk, "months": monthly}
        metrics["comparisons"] = comparisons
        metrics["complete_months"] = list(complete)
        metrics["B"] = {"status": "pending", "reason": "Post-effective B and its backend denominator require separate evidence; historical A cannot fill B"}
        if any(not item.startswith("risk_tradeoff_review@") for item in pending):
            metrics["economic_observations_pending_certification"] = list(economic)
            economic.clear()
        else:
            failed.extend(economic)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        metrics["economic_observations_pending_certification"] = list(economic)
        economic.clear()
        pending.append(f"paired_pool_ledger: {exc}")
    # Passing proxy economics opens a forward-validation stage, not a claim
    # that an unadmitted candidate already earned official B or total points.
    if not failed and not pending:
        shadow = load("shadow")
        if shadow:
            try:
                result["forward_validation"] = _forward_review(shadow, plan, baseline, proposed, policy, contracts)
                result["forward_validation_verified"] = True
            except EconomicRejection as exc:
                failed.append(f"forward_shadow: {exc}")
                economic.append("forward_shadow")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                pending.append(f"forward_shadow: {exc}")
        result["formal_admission"] = {"status": "review_required" if result.get("forward_validation_verified") else "pending_forward_validation",
                                      "official_total_points_verified": False, "automatic_promotion": False}
    return finish()


def _forward_review(shadow, plan, baseline, proposed, policy, contracts):
    """Check a frozen shadow month and conservative B dilution scenarios.

    Candidate IC here is forward shadow evidence, never already-earned official
    B. Both possible B denominators are examined until a backend contract exists.
    """
    if shadow.get("scope") != "frozen_forward_shadow" or shadow.get("type") != "pool_signal_daily_ledger":
        raise ValueError("A bound forward pool ledger is required, not a verified flag")
    days = shadow["calendar"]
    if (not isinstance(days, list) or len(days) < 10 or days != sorted(set(days)) or
            any(_date(day).strftime("%Y-%m") != shadow["month"] for day in days) or
            _date(days[0]) <= _date(plan["as_of"])):
        raise ValueError("Forward shadow must be one complete calendar month strictly after freeze")
    if contracts.get("calendar_months", {}).get(shadow["month"]) != days:
        raise ValueError("Forward calendar differs from independently sourced complete month")
    review = shadow["B_review"]
    if review.get("independent_calendar_status") != "verified" or review.get("version_freeze_status") != "verified":
        raise ValueError("Independent forward calendar and unchanged versions need verification")
    if [row.get("date") for row in shadow["benchmark"]] != days:
        raise ValueError("Forward benchmark/calendar mismatch")
    benchmark = [_number(row.get("return"), "benchmark") for row in shadow["benchmark"]]
    b_scores = {}
    b_by_member = {}
    for side, members in (("baseline", baseline), ("proposed", proposed)):
        rows = review[side]
        if not isinstance(rows, list) or {row.get("id") for row in rows} != set(members) or len(rows) != len(members):
            raise ValueError("Forward B review must bind every frozen pool member")
        scores, b_by_member[side] = [], {}
        for row in rows:
            member = members[row["id"]]
            if row.get("version") != member["version"] or row.get("definition_sha256") != member["definition_sha256"]:
                raise ValueError("Forward B member version/definition differs from frozen plan")
            b_by_member[side][row["id"]] = {key: row.get(key) for key in ("source", "effective_date", "records")}
            if row.get("source") not in ("official_post_effective", "shadow_post_freeze", "cold_start_model"):
                raise ValueError("Historical IC is not post-effective or forward evidence")
            if not member["effective"]:
                if row.get("records"):
                    raise ValueError("A pending member cannot contribute B")
                continue
            if row["source"] == "cold_start_model":
                if row.get("records") != []:
                    raise ValueError("Cold start cannot contain historical IC")
                if side == "proposed" and row["id"] in plan["candidate_ids"]:
                    raise ValueError("Changed candidates require completed post-freeze shadow IC before final review")
                scores.append(None)
                continue
            if (side == "proposed" and row["id"] in plan["candidate_ids"] and
                    row["source"] == "official_post_effective" and _date(row["effective_date"]) < _date(plan["as_of"])):
                raise ValueError("Changed candidate B cannot reuse its pre-freeze history")
            boundary = row["effective_date"] if row["source"] == "official_post_effective" else plan["as_of"]
            score = new_b_score(row["records"], member["version"], boundary,
                                member["direction"], as_of=days[-1])
            if score["status"] != "scored":
                raise ValueError("Forward ICIR not estimable; keep B pending")
            scores.append(score["rawB"])
        b_scores[side] = scores
    for key in set(baseline) & set(proposed):
        if (all(baseline[key][field] == proposed[key][field] for field in ("version", "definition_sha256", "direction")) and
                b_by_member["baseline"][key] != b_by_member["proposed"][key]):
            raise ValueError("Unchanged members must use identical original B records")
    comparisons = {}
    for cost in policy["one_way_costs"]:
        statistics_by_side, c_by_side = {}, {}
        for side, members in (("baseline", baseline), ("proposed", proposed)):
            metrics, returns, turnover = _performance(shadow["costs"][str(cost)][side], days, benchmark)
            c = monthly_c(returns, benchmark, sum(turnover))
            a = shadow["A"][side]
            if len(a["a_scores"]) != sum(member["effective"] for member in members.values()):
                raise ValueError("Forward A denominator differs from frozen pool")
            c["NA"] = normalized_a(a["a_scores"], a["decay"], a["a_cap"])
            statistics_by_side[side], c_by_side[side] = metrics, c
        if any(shadow["A"]["baseline"][field] != shadow["A"]["proposed"][field] for field in ("decay", "a_cap")):
            raise ValueError("Forward A must preserve the same pool age/decay/cap")
        a_identity = {side: dict(zip((key for key, member in members.items() if member["effective"]), shadow["A"][side]["a_scores"]))
                      for side, members in (("baseline", baseline), ("proposed", proposed))}
        for key in set(a_identity["baseline"]) & set(a_identity["proposed"]):
            if (all(baseline[key][field] == proposed[key][field] for field in ("version", "definition_sha256", "direction")) and
                    a_identity["baseline"][key] != a_identity["proposed"][key]):
                raise ValueError("Unchanged forward members must preserve original A scores")
        old, new = statistics_by_side["baseline"], statistics_by_side["proposed"]
        if new["Rp"] <= 0 or new["relative_wealth_excess"] <= 0 or new["SR_ann"] <= 0 or new["Rp"] < old["Rp"]:
            raise EconomicRejection("Forward cost-adjusted pool economics did not confirm")
        deltas = {}
        for denominator in ("all_effective", "eligible_only"):
            normalized = {}
            for side in ("baseline", "proposed"):
                valid = [value for value in b_scores[side] if value is not None]
                count = len(b_scores[side]) if denominator == "all_effective" else len(valid)
                nb = min(max(sum(valid) / count / .06, 0), 1) if count else 0
                normalized[side] = .2 * c_by_side[side]["NA"] + .35 * nb + .45 * c_by_side[side]["NC"]
            deltas[denominator] = normalized["proposed"] - normalized["baseline"]
        if min(deltas.values()) <= 0:
            raise EconomicRejection("Forward score improvement does not survive conservative B denominator/dilution scenarios")
        comparisons[str(cost)] = {"metrics": statistics_by_side, "score_delta_scenarios": deltas}
    return {"scope": "shadow_proxy_not_official_B_or_total_points", "month": shadow["month"], "comparisons": comparisons}
