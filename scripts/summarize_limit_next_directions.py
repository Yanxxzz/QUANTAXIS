"""Read complete accounts only; paired fixed inference and compact chart.

Does not fetch quotes, resimulate accounts, construct factors or register rules.
"""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backtest_limit_reversal import read, binding
from scripts.research_limit_event_factor import clean
from panda_alpha.limit_paired_comparison import compare_complete_nav

OUT = ROOT / "research_runs/limit_next_directions_20261014"
PUBLIC = ROOT / "docs/results/limit-next-directions-20261014.json"
LABELS = {"existing_filtered": "原18.60%筛选版", "recovery_parent_next_open": "触跌停后次开父样本",
          "recovery_next_open": "脱离跌停后次开", "time_after_5": "t+6时间退出",
          "market_gate": "前日退潮过滤", "firstboard_parent_next_open": "首板分歧父样本",
          "firstboard_repair_next_open": "首板分歧修复"}
PAIRS = {"recovery_next_open": "recovery_parent_next_open", "time_after_5": "existing_filtered",
         "market_gate": "existing_filtered", "firstboard_repair_next_open": "firstboard_parent_next_open"}


def save(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def run():
    file = next(OUT / x for x in ("summary_final.json", "summary_complete.json", "summary.json") if (OUT / x).exists())
    summary = read(file)
    if summary["status"] != "ALL_TWELVE_FIXED_NEXT_DIRECTION_PATHS_COMPLETE":
        raise ValueError("No partial-return comparisons: collect all twelve complete accounts first")
    protocol = read(OUT / "protocol.json")
    audit = read(OUT / "completed_account_audit.json")
    if audit["status"] != "ALL_TWELVE_ACCOUNT_IDENTITIES_AND_METRICS_PASS" or audit["errors"]:
        raise ValueError("Complete independent account audit required")
    meta = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")
    dates = [d for d in meta["calendar"] if d >= protocol["common"]["start"]]
    old = pd.read_csv(ROOT / "research_runs/limit_reversal_optimization_20261014/completed_daily_nav3.csv", dtype={"date": str})
    accounts, navs = {}, {}
    for item in summary["paths"]:
        key = item["path"]
        prefix = item.get("artifact_prefix", key)
        nav = pd.read_csv(OUT / (prefix + "_nav.csv"), dtype={"date": str})
        if nav.date.tolist() != dates:
            raise ValueError("Complete independent comparison calendar required")
        navs[key] = nav
        accounts[key] = {"name": item["unit"], "label": LABELS[item["unit"]], "path": key,
                         "cost_single_side": item["cost_single_side"], "metrics": item["metrics"],
                         "buys": item["buys"], "sale_reasons": item["sale_reasons"],
                         "fees": item["total_fees"], "terminal_cash": item["terminal_cash"],
                         "terminal_market_value": item["terminal_market_value"],
                         "terminal_holdings_count": len(item["terminal_holdings"]),
                         "terminal_exposure": item["terminal_exposure"],
                         "mean_exposure": float(nav.exposure.mean()), "blocked_orders": item["blocked_orders"],
                         "source_pending": item["source_pending"]}
    for item in summary["existing_filtered"]:
        suffix = str(int(item["cost_single_side"] * 10000)) + "bp"
        key = "existing_filtered_" + suffix
        column = "filtered_" + suffix
        navs[key] = old[["date", column + "_nav"]].rename(columns={column + "_nav": "nav"})
        accounts[key] = {"name": "existing_filtered", "label": LABELS["existing_filtered"],
                         "path": key, "cost_single_side": item["cost_single_side"], "metrics": item["metrics"],
                         "buys": item.get("buys", item.get("buy_fills")), "fees": item["total_fees"],
                         "terminal_holdings_count": len(item["terminal_holdings"]),
                         "terminal_exposure": item["terminal_exposure"], "old_result_not_replayed": True}
    comparisons = []
    for target, control in PAIRS.items():
        results = []
        for suffix in ("30bp", "50bp"):
            a, b = target + "_" + suffix, control + "_" + suffix
            effect = compare_complete_nav(navs[a][["date", "nav"]], navs[b][["date", "nav"]], expected_dates=dates)
            am, bm = accounts[a]["metrics"], accounts[b]["metrics"]
            economic = {"net_return_higher": am["compounded_return"] > bm["compounded_return"],
                        "sharpe_higher": am["sharpe"] > bm["sharpe"],
                        "drawdown_lower": am["max_drawdown"] < bm["max_drawdown"]}
            results.append({"cost": suffix, "candidate": a, "control": b,
                            "metric_differences": {k: am[k] - bm[k] for k in ("compounded_return", "cagr", "sharpe", "max_drawdown")},
                            "economic": economic, "all_three_economic_improve": all(economic.values()),
                            "paired_inference": effect})
        comparisons.append({"candidate": target, "control": control,
                            "full_economic_upgrade_at_both_costs": all(x["all_three_economic_improve"] for x in results),
                            "positive_paired_interval_at_both_costs": all(x["paired_inference"]["confidence_interval_95"][0] > 0 for x in results),
                            "cost_results": results})
    masks = read(OUT / "frozen_masks.json")
    result = {"status": "ALL_TWELVE_FIXED_LOCAL_PATHS_AND_FOUR_PAIRED_COMPARISONS_COMPLETE",
              "start": dates[0], "end": dates[-1], "days": len(dates), "source_identities": len(meta["codes"]),
              "accounts": list(accounts.values()), "comparisons": comparisons,
              "source_masks": read(OUT / "formation_receipt.json")["coverage"],
              "evaluation_mask_counts": {k: sum(dates[0] <= x[0] <= dates[-1] for x in values) for k, values in masks.items()},
              "account_audit": {"status": audit["status"], "errors": audit["errors"], "artifact": binding(OUT / "completed_account_audit.json")},
              "protocol": binding(OUT / "protocol.json"),
              "clock_repair": binding(OUT / "clock_repair_protocol.json"),
              "statistics_runtime": binding(OUT / "comparison_runtime.json"),
              "registry_total": summary["registry_total"], "new_unique_contexts": 6,
              "official_compute_spent": 0, "old_baseline_replayed": False,
              "not_untouched_OOS": True, "formal_candidate": False,
              "execution": "Daily-bar potential fills; opening auction and limit queue uncertified; total-return equivalent units, not actual CA inventory",
              "statistical_limit": "Month-block descriptive CI; previously exposed sample; multiple comparisons unadjusted; not CAGR interval or proof of independent alpha"}
    save(OUT / "completed_comparisons.json", result)
    save(PUBLIC, result)
    plot(navs, dates)
    print(json.dumps({"status": result["status"], "accounts": len(accounts), "pairs": len(comparisons),
                      "full_economic_upgrade": [x["candidate"] for x in comparisons if x["full_economic_upgrade_at_both_costs"]]}, ensure_ascii=False))


def plot(navs, dates):
    image = Image.new("RGB", (1500, 1070), "#f8fafb")
    draw = ImageDraw.Draw(image)
    font = "C:/Windows/Fonts/msyh.ttc"
    f = ImageFont.truetype(font, 20); small = ImageFont.truetype(font, 17); title = ImageFont.truetype(font, 29)
    draw.text((55, 25), "四项独立改进与必要对照：净值 / 回撤", font=title, fill="#162636")
    draw.text((55, 67), "2024-01-02至2026-09-18 · 单边30bp · 10万元 · 既有主板3489身份 · 日线潜在成交模型", font=small, fill="#556270")
    draw.text((55, 93), "原18.60%筛选版为旧结果；其余12个成本路径新跑。已暴露样本，非全新样本外。", font=small, fill="#556270")
    for i, (target, control) in enumerate(PAIRS.items()):
        x = 55 + (i % 2) * 730; y = 145 + (i // 2) * 430
        draw.text((x, y), LABELS[target] + " vs " + LABELS[control], font=f, fill="#162636")
        points = [(target, "#137b8c"), (control, "#747e89")]
        vals = [navs[name + "_30bp"].nav.to_numpy() / 100000 for name, _ in points]
        low, high = min(min(v) for v in vals), max(max(v) for v in vals)
        pad = max((high-low)*.08, .05); low -= pad; high += pad
        xx, yy, w, h = x + 58, y + 47, 600, 190
        for tick in np.linspace(low, high, 4):
            ty = yy + h - (tick-low)/(high-low)*h
            draw.line((xx, ty, xx+w, ty), fill="#dbe1e5")
            draw.text((x, ty-9), f"{tick:.2f}", font=small, fill="#556270")
        for (name, color), val in zip(points, vals):
            pts = [(xx+j*w/(len(val)-1), yy+h-(v-low)/(high-low)*h) for j, v in enumerate(val)]
            draw.line(pts, fill=color, width=3)
        draw.text((xx, yy+h+5), "2024-01", font=small, fill="#556270")
        draw.text((xx+w-76, yy+h+5), "2026-09", font=small, fill="#556270")
        draw.text((xx+220, y+26), "净值（初始=1）", font=small, fill="#556270")
        draw.text((xx+250, yy+h+5), "日期", font=small, fill="#556270")
        dd = [v/np.maximum.accumulate(np.r_[1., v])[1:]-1 for v in vals]
        dl = min(min(v) for v in dd)-.02; dy=y+279; dh=86
        draw.line((xx, dy, xx+w, dy), fill="#dbe1e5")
        for (name,color), val in zip(points, dd):
            draw.line([(xx+j*w/(len(val)-1), dy+v/dl*dh) for j,v in enumerate(val)], fill=color,width=2)
        draw.text((x, dy-2), "回撤", font=small, fill="#556270")
        draw.text((x, dy+dh-8), f"{dl:.0%}", font=small, fill="#556270")
        draw.text((xx, y+378), LABELS[target], font=small, fill=points[0][1])
        draw.text((xx+280, y+378), LABELS[control], font=small, fill=points[1][1])
    draw.text((55, 1010), "来源：完整本地账户账本；价格/公司行动与历史成员资格沿原来源合同。潜在触价或开盘价不保证真实成交。", font=small, fill="#556270")
    image.save(ROOT / "docs/assets/limit-next-directions-20261014.png")


if __name__ == "__main__":
    run()
