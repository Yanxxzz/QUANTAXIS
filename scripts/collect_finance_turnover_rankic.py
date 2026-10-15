"""Read an already settled score pair; no network, registration, retry or charge.

Both progress guards run before either economic report is opened. Raw-series
means remain separate from rounded service tables and chart headlines.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "research_runs/finance_turnover_rankic_20261015"
SECTIONS = (
    "query_factor_analysis_data", "query_one_group_data", "query_group_return_analysis",
    "query_last_date_top_factor", "query_return_chart", "query_ic_decay_chart",
    "query_ic_density_chart", "query_ic_sequence_chart", "query_ic_self_correlation_chart",
    "query_rank_ic_decay_chart", "query_rank_ic_density_chart", "query_rank_ic_sequence_chart",
    "query_rank_ic_self_correlation_chart", "query_factor_excess_chart",
)


class CollectionPending(ValueError):
    pass


def read(path):
    path = Path(path)
    if not path.is_file():
        raise CollectionPending("Missing artifact: " + str(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
                         + "\n", encoding="utf-8")


def binding(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def check_bound(item):
    if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
        raise CollectionPending("Missing exact artifact binding")
    actual = binding(item["path"])
    if actual["sha256"] != item["sha256"]:
        raise CollectionPending("Frozen binding changed: " + item["path"])
    return Path(item["path"])


def number(value, label):
    if isinstance(value, bool) or value is None:
        raise CollectionPending("Missing/non-numeric " + label)
    try:
        text = str(value).strip()
        result = float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except (TypeError, ValueError) as error:
        raise CollectionPending("Missing/non-numeric " + label) from error
    if not math.isfinite(result):
        raise CollectionPending("Nonfinite " + label)
    return result


def dates(values, label):
    if not isinstance(values, list):
        raise CollectionPending("Missing " + label + " dates")
    result = []
    for value in values:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as error:
            raise CollectionPending("Invalid " + label + " date") from error
        if parsed.tzinfo is not None or parsed.time().isoformat() != "00:00:00":
            raise CollectionPending("Non-naive whole-day " + label + " date")
        result.append(parsed.date().isoformat())
    if len(result) != len(set(result)):
        raise CollectionPending("Duplicate " + label + " dates")
    return result


def chart(analysis, key, expected):
    value = analysis.get(key)
    if not isinstance(value, dict) or not isinstance(value.get("title"), str):
        raise CollectionPending("Missing chart/title: " + key)
    x, y = value.get("x"), value.get("y")
    if not isinstance(x, list) or len(x) != 1 or not isinstance(y, list):
        raise CollectionPending("Unexpected chart axes: " + key)
    if dates(x[0].get("data"), key) != expected:
        raise CollectionPending("Formation dates differ from frozen sixteen: " + key)
    series = {}
    for row in y:
        name, raw = row.get("name"), row.get("data")
        if not isinstance(name, str) or name in series or not isinstance(raw, list):
            raise CollectionPending("Duplicate/missing chart series: " + key)
        if len(raw) != len(expected):
            raise CollectionPending("Incomplete chart series: " + key + ":" + name)
        series[name] = [number(v, key + ":" + name) for v in raw]
    return value["title"], series


def ic_result(analysis, key, series_name, expected, table, table_mean):
    title, values = chart(analysis, key, expected)
    if series_name not in values:
        raise CollectionPending("Missing raw IC series: " + series_name)
    raw = values[series_name]
    mean = statistics.fmean(raw)
    std = statistics.stdev(raw) if len(raw) > 1 else None
    headlines = {name: float(value) for name, value in re.findall(
        r"\b(Rank_IC|IC|IC_IR)=([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", title)}
    return {"formation_dates": expected, "raw_values": raw, "raw_count": len(raw),
            "raw_mean": mean, "raw_sample_std_ddof1": std,
            "raw_mean_over_sample_std": mean / std if std and std > 0 else None,
            "report_table_mean_raw": table[table_mean],
            "report_table_mean": number(table[table_mean], table_mean),
            "report_title_raw": title, "report_title_metrics": headlines,
            "report_pearson_IC_IR_table_raw": table["IC_IR"],
            "reported_IC_pvalue_raw": table["p-value"],
            "reported_IC_pvalue": number(table["p-value"], "p-value"),
            "pvalue_scope": "Service IC-table test; not a separate RankIC p-value or multiplicity-adjusted test"}


def role_report(unit, role, progress, protocol):
    folder = unit / role
    plan_path, candidate_path = folder / "batch_plan.json", folder / "candidate.json"
    plan, candidate = read(plan_path), read(candidate_path)
    if progress.get("fingerprint") != plan.get("fingerprint"):
        raise CollectionPending(role + ": progress/frozen-plan fingerprint differs")
    if check_bound(plan["candidate"]).resolve() != candidate_path.resolve():
        raise CollectionPending(role + ": candidate binding differs")
    check_bound(plan["protocol"])
    preflight_path = check_bound(plan["preflight"])
    preflight = read(preflight_path)
    if (candidate.get("direction") != 1 or plan.get("cycle") != 5 or plan.get("groups") != 10
            or preflight.get("research_replay_passed") is not True or preflight.get("passed") is not False):
        raise CollectionPending(role + ": direction/cycle/groups/research preflight differs")
    proof = preflight.get("proof", {})
    if (proof.get("finite_evaluation_rows") != 4296
            or proof.get("source_qualified_evaluation_rows") != 4296
            or proof.get("expected_finite_mask_matched") is not True):
        raise CollectionPending(role + ": expected 4296-row local common source proof differs")
    expected = dates(candidate["native_evaluation_contract"]["formation_dates"], "frozen formation")
    if len(expected) != 16 or expected != protocol["formation_dates"][:-6:5]:
        raise CollectionPending(role + ": expected sixteen formation dates are not frozen")
    code_path = check_bound(plan["code_file"])
    code = candidate.get("code")
    if not isinstance(code, str) or code_path.read_bytes() != code.encode("utf-8"):
        raise CollectionPending(role + ": frozen source file bytes differ")
    report_path = folder / "official_results" / (progress["run_id"] + ".json")
    report = read(report_path)
    if report.get("success") is not True or report.get("status") != 2:
        raise CollectionPending(role + ": official report not successful status2")
    nodes = report.get("nodes")
    if not isinstance(nodes, dict):
        raise CollectionPending(role + ": missing official nodes/code echo")
    echoes = [v["code"] for v in nodes.values() if isinstance(v, dict) and "code" in v]
    if len(echoes) != 1 or echoes[0] != code:
        raise CollectionPending(role + ": worker code echo differs or is ambiguous")
    analysis = report.get("factor_analysis")
    if not isinstance(analysis, dict) or any(name not in analysis for name in SECTIONS):
        raise CollectionPending(role + ": incomplete factor_analysis sections")
    table_rows = analysis["query_factor_analysis_data"]
    if not isinstance(table_rows, list):
        raise CollectionPending(role + ": missing IC table")
    table = {r["indicator"]: r["factor_value"] for r in table_rows}
    for name in ("IC_mean", "Rank_IC", "IC_IR", "p-value"):
        if name not in table:
            raise CollectionPending(role + ": missing IC headline " + name)
        number(table[name], name)
    embedded = [json.loads(v["result_json"]) for v in nodes.values()
                if isinstance(v, dict) and isinstance(v.get("result_json"), str)]
    if (len(embedded) != 1 or embedded[0].get("factor_data_analysis") != table_rows
            or embedded[0].get("group_return_analysis") != analysis["query_group_return_analysis"]):
        raise CollectionPending(role + ": embedded/fetched metric tables differ")
    rank = ic_result(analysis, "query_rank_ic_sequence_chart", "Rank_IC", expected, table, "Rank_IC")
    pearson = ic_result(analysis, "query_ic_sequence_chart", "IC", expected, table, "IC_mean")
    _, returns = chart(analysis, "query_return_chart", expected)
    _, excess = chart(analysis, "query_factor_excess_chart", expected)
    rows = analysis["query_group_return_analysis"]
    if not isinstance(rows, list) or len(rows) != 12:
        raise CollectionPending(role + ": expected ten groups plus two spreads")
    tables = {r["group"]: r for r in rows}
    if len(tables) != 12:
        raise CollectionPending(role + ": duplicate group table rows")
    metrics = []
    fields = {"annualized_return": "annualizedReturn", "annualized_excess": "excessAnnualized",
              "report_Sharpe": "sharpeRatio", "maximum_drawdown": "maxDrawdown", "turnover": "turnoverRate"}
    for n in range(1, 11):
        name, row = "组" + str(n), tables.get("分组" + str(n))
        if row is None or name not in returns or name not in excess:
            raise CollectionPending(role + ": missing prespecified group " + str(n))
        values = {}
        for label, field in fields.items():
            if field not in row:
                raise CollectionPending(role + ": missing group" + str(n) + " " + field)
            values[label] = number(row[field], field)
        metrics.append({"group": n, "cumulative_return": returns[name][-1],
                        "cumulative_excess": excess[name][-1], **values,
                        "report_metrics_raw": {field: row[field] for field in fields.values()}})
    markets = {name: [a - b for a, b in zip(returns[name], excess[name])] for name in ("组1", "组10")}
    result = {"role": role, "run_id": progress["run_id"], "factor_id": progress.get("factor_id"),
              "fingerprint": plan["fingerprint"], "state": "SETTLED", "execution_status": 2,
              "actual_observed_credits": number(progress.get("actual"), "settled actual credits"),
              "report_binding": binding(report_path), "plan_binding": binding(plan_path),
              "candidate_binding": binding(candidate_path), "preflight_binding": binding(preflight_path),
              "worker_code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
              "code_echo_exact": True, "embedded_and_fetched_tables_identical": True,
              "complete_analysis_section_count": len(SECTIONS), "reported_group_row_count": len(rows),
              "formation_dates": expected, "RankIC": rank, "PearsonIC": pearson, "all_ten_groups": metrics,
              "fixed_direction": 1, "fixed_selected_group": 10, "fixed_group10": metrics[9],
              "group1_diagnostic": metrics[0], "all_reported_group_headlines": rows,
              "local_source_common_finite_rows": 4296,
              "local_evaluation_output_rows": proof["evaluation_output_rows"],
              "local_source_mask_matched": True, "remote_actual_member_common_support_proved": False}
    return result, markets


def collect(unit):
    unit = Path(unit).resolve()
    # No report is opened until both role execution guards succeed.
    progresses = {role: read(unit / role / "official_progress.json") for role in ("target", "control")}
    for role, progress in progresses.items():
        if (progress.get("state") != "SETTLED" or progress.get("execution_status") != 2
                or not isinstance(progress.get("run_id"), str) or not progress["run_id"]):
            raise CollectionPending(role + ": both roles must already be SETTLED/status2")
    protocol_path = unit / "protocol.json"
    protocol = read(protocol_path)
    roles, markets = {}, {}
    for role in ("target", "control"):
        roles[role], markets[role] = role_report(unit, role, progresses[role], protocol)
    if roles["target"]["formation_dates"] != roles["control"]["formation_dates"]:
        raise CollectionPending("Target/control formation dates differ")
    difference = max(abs(a - b) for name in ("组1", "组10")
                     for a, b in zip(markets["target"][name], markets["control"][name]))
    within = {role: max(abs(a - b) for a, b in zip(values["组1"], values["组10"]))
              for role, values in markets.items()}
    bindings = {name: binding(unit / name) for name in (
        "protocol.json", "source_literal.json", "source_features.json", "source_plan.json")}
    context = [
        {"account_version": "Original financing common-support account", "return_30bp": .9092868971},
        {"account_version": "Finance conditional exit without hard expiry", "return_30bp": .7040446000},
        {"account_version": "Late continuous confirmed exit from t+25", "return_30bp_rounded": .3378},
    ]
    limitations = [
        "New finance-plus-turnover continuous score; not the old entry/10%take-profit/holding-exit account.",
        "Literal replay of public external inputs; close only carries the received official index.",
        "4296 common finite keys are local replay proof; remote membership, cleaning and coverage are unexported.",
        "Worker code equality/similar inferred market curves do not prove native finance, common raw members, PIT or production.",
        "Service Sharpe/returns are not certified 30bp net daily admission statistics; sixteen observations are not200 periods.",
        "IC table p-value is not a separate RankIC significance test or multiplicity-adjusted claim.",
        "Previously exposed history; no OOS or causal incremental alpha claim follows.",
    ]
    benchmark = {"construction": "Group cumulative return minus reported cumulative excess; additive inferred benchmark only",
                 "target_control_max_abs_curve_difference": difference,
                 "approximately_equal_at_1e_minus_6": difference <= 1e-6,
                 "within_role_group1_group10_implied_curve_max_difference": within,
                 "same_raw_members_or_cleaner_proved": False}
    review = {"schema": "finance-turnover-completed-official-pair-review-v1",
              "status": "COMPLETE_BOTH_SETTLED_STATUS2_REPORTS_REVIEWED",
              "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
              "evaluation_window": protocol["evaluation_window"], "cycle": 5, "groups": 10,
              "formation_count": 16, "direction": 1, "roles": roles, "benchmark_diagnostic": benchmark,
              "source_bindings": bindings, "historical_account_context_only": context,
              "historical_accounts_not_replayed": True, "historical_accounts_have_no_new_score_RankIC": True,
              "limitations": limitations, "formal_eligible": False, "new_contexts": 0,
              "new_network_calls": 0, "new_runs": 0, "new_charges": 0}
    save(unit / "complete_report_review.json", review)
    summary = {"status": review["status"], "review_binding": binding(unit / "complete_report_review.json"),
               "evaluation_window": protocol["evaluation_window"], "formations": 16,
               "source_common_mask_local_finite_rows": 4296,
               "roles": {role: {"run_id": r["run_id"], "factor_id": r["factor_id"],
                   "raw_RankIC_mean": r["RankIC"]["raw_mean"],
                   "report_RankIC_headline": r["RankIC"]["report_table_mean_raw"],
                   "raw_PearsonIC_mean": r["PearsonIC"]["raw_mean"],
                   "report_Pearson_ICIR": r["PearsonIC"]["report_pearson_IC_IR_table_raw"],
                   "report_IC_pvalue": r["PearsonIC"]["reported_IC_pvalue_raw"],
                   "fixed_group10": r["fixed_group10"], "group1_diagnostic": r["group1_diagnostic"],
                   "report_binding": r["report_binding"], "actual_observed_credits": r["actual_observed_credits"]}
                   for role, r in roles.items()},
               "target_minus_control_raw_RankIC_mean": roles["target"]["RankIC"]["raw_mean"] - roles["control"]["RankIC"]["raw_mean"],
               "benchmark_diagnostic": benchmark, "historical_account_context_only": context,
               "source_bindings": bindings, "limitations": limitations, "formal_eligible": False,
               "new_contexts": 0, "collection_new_fees": 0}
    save(unit / "completed_pair_summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", type=Path, default=UNIT)
    parser.add_argument("--confirm-pair-settled", action="store_true",
                        help="Explicit root confirmation, followed by local progress/status guards")
    args = parser.parse_args()
    if not args.confirm_pair_settled:
        parser.error("Root must confirm both roles SETTLED/status2; add --confirm-pair-settled")
    try:
        summary = collect(args.unit)
    except (CollectionPending, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        pending = {"status": "COLLECTION_PENDING", "reason": str(error), "new_contexts": 0,
                   "new_runs": 0, "new_charges": 0, "no_automatic_retry": True}
        save(args.unit / "complete_report_review.json", pending)
        print(json.dumps(pending, ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps({"status": summary["status"], "formations": summary["formations"],
        "roles": {role: {name: row[name] for name in (
            "run_id", "raw_RankIC_mean", "report_RankIC_headline", "raw_PearsonIC_mean",
            "report_IC_pvalue", "fixed_group10")} for role, row in summary["roles"].items()},
        "review_sha256": summary["review_binding"]["sha256"], "new_contexts": 0,
        "collection_new_fees": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
