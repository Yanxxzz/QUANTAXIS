"""Final portfolio admission uses verified evidence, not exploratory rank/IC."""
import math


def assess_admission(evidence: dict, policy: dict, *, evidence_directory=".", window_check=None) -> dict:
    if policy.get("identity") in ("pool_admission_v1_20261010", "pool_admission_v2_20261010_balanced"):
        from .pool_admission import assess_pool_admission
        return assess_pool_admission(evidence, policy, evidence_directory=evidence_directory,
                                     window_check=window_check)
    required = ("source_coverage", "point_in_time_universe", "execution_audit",
                "size_stress", "fixed_pool_increment", "temporal_stability",
                "actual_factor_diversity", "official_transfer", "direction_parity")
    pending, failed = [], []
    for name in required:
        item = evidence.get(name, {})
        if item.get("status") == "failed":
            failed.append(name)
        elif item.get("status") != "verified" or not item.get("artifact_sha256"):
            pending.append(name)
    for cost in policy["one_way_costs"]:
        entry = evidence.get("net_returns", {}).get(str(cost), {})
        numbers = (entry.get("sharpe"), entry.get("compounded_return"), entry.get("relative_wealth_excess"))
        if entry.get("status") != "verified" or not entry.get("artifact_sha256") or any(type(x) not in (int, float) or not math.isfinite(x) for x in numbers):
            pending.append(f"net_returns@{cost}")
        elif numbers[0] < policy["minimum_net_sharpe"] or numbers[1] <= 0 or numbers[2] <= 0:
            failed.append(f"net_returns@{cost}")
    points = evidence.get("monthly_points", {})
    if points.get("status") != "verified" or points.get("type") != "official_complete_month_ledger":
        pending.append("official_complete_month_points")
    elif not points.get("same_dates") or not points.get("gain_positive"):
        failed.append("official_complete_month_points")
    status = "rejected" if failed else "pending" if pending else "eligible_for_review"
    return {"status": status, "failed": failed, "pending": pending,
            "pool_operation": "requires explicit user instruction",
            "research_exploration_allowed": True}
