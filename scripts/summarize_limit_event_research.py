"""Publish compact evidence from completed accounts; never rerun the study."""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.evaluation import performance
from scripts.research_io import read, binding

OUT = ROOT / "research_runs/limit_reversal_optimization_20261014"


def build():
    source = read(OUT / "resumed_summary3.json")
    if source["status"] != "ALL_EVENT_FACTOR_AND_FOUR_STRATEGY_PATHS_COMPLETE":
        raise ValueError("Only complete fixed accounts can be published")
    daily = pd.read_csv(OUT / "completed_daily_nav3.csv", dtype={"date": str})
    trades = pd.read_csv(OUT / "completed_trades3.csv", dtype={"date": str, "symbol": str})
    if len(daily) != 659 or daily.date.duplicated().any():
        raise ValueError("Complete independent account dates are required")
    paths = []
    for item in source["paths"]:
        key = item["path"]
        nav = daily[key + "_nav"]
        returns = nav / nav.shift(1).fillna(100000) - 1
        computed = performance(returns)
        error = max(abs(computed[k] - item["metrics"][k]) for k in computed)
        if error > 1e-10 or abs(nav.iloc[-1] - item["terminal_nav"]) > 1e-7:
            raise ValueError("Saved account metrics do not reconcile")
        account = trades[trades.path.eq(key)]
        if int(account.side.eq("buy").sum()) != item["buy_fills"]:
            raise ValueError("Complete trade counts do not reconcile")
        annual = [{"year": year, "compounded_return": float(np.prod(1 + group) - 1)}
                  for year, group in returns.groupby(daily.date.str[:4])]
        paths.append({"path": key, "single_side_cost": item["cost_single_side"],
                      "metrics": computed, "annual_returns": annual,
                      "buys": item["buy_fills"], "sells": int(account.side.eq("sell").sum()),
                      "total_fees_CNY": item["total_fees"],
                      "terminal_holdings_count": len(item["terminal_holdings"]),
                      "terminal_cash_CNY": item["terminal_cash"],
                      "metrics_reconciliation_error": error})
    attr = read(ROOT / "research_runs/limit_reversal_20261014/drawdown_attribution.json")
    attribution = {"peak_date": attr["peak"]["date"], "trough_date": attr["trough"]["date"],
                   "drawdown": attr["maximum_drawdown"], "loss_CNY": -attr["nav_change"],
                   "contributors": [{"code": x["code"], "net_loss_CNY": -x["net_nav_contribution"],
                                     "drawdown_percentage_points": x["nav_drawdown_percentage_point_contribution"]}
                                    for x in attr["contributors"]],
                   "reconciliation_error": attr["reconciliation_error"],
                   "source": binding(ROOT / "research_runs/limit_reversal_20261014/drawdown_attribution.json")}
    # Exact keys vary with the prior bounded audit; retain only its small totals.
    for value in attr.values():
        if isinstance(value, dict) and "fee_share_of_max_drawdown_loss" in value:
            attribution.update({k: value[k] for k in ["fee_share_of_max_drawdown_loss", "price_contribution_sum", "terminal_unrealized_mark_minus_entry_outlay_sum"]})
    result = {"status": source["status"], "validation_window": {"start": daily.date.iloc[0], "end": daily.date.iloc[-1], "sessions": len(daily)},
              "discovery_window": {"start": "2021-09-22", "end": "2023-12-29"},
              "not_untouched_OOS": True, "selection": {k: source["selection"][k] for k in ["feature", "cutoff", "trained_only_until", "training_support"]},
              "training_descriptors": source["selection"]["all_three_training_results"],
              "original_five_year_drawdown": attribution,
              "validation_event_diagnostic": source["validation_event_diagnostic"],
              "accounts": paths, "factor": source["factor"],
              "source_receipt": read(OUT / "source_receipt.json"),
              "official_compute_spent": 0, "new_unique_contexts": 6,
              "registry_total": source["registry_total"], "formal_candidate": False,
              "conditions": ["Daily limit touch is a potential fill, not a queue certificate.",
                             "Only-profit exits still retain terminal losing positions.",
                             "Same-source total-return equivalent units are not actual dividend/share ledgers.",
                             "Historical member, ST, IPO exceptions and formation-time availability are not fully PIT certified.",
                             "Three discovery descriptors, overlapping events and month bootstrap do not establish causality.",
                             "Close-formed sparse factor is not the same-day bid account or a ten-decile portfolio."],
              "bindings": {name: binding(OUT / name) for name in ["protocol.json", "validation_protocol.json", "training_summary.json", "resumed_summary3.json", "source_repair_resume_protocol.json", "source_repair_resume_protocol2.json", "source_repair_resume_protocol3.json"]}}
    audit_path = OUT / "completed_results_readonly_audit.json"
    audit = read(audit_path)
    result["readonly_account_audit"] = {
        "source": binding(audit_path),
        "paths": [{k: item[k] for k in ["path", "closed_net_pnl_sum", "terminal_unrealized_mark_minus_entry_outlay_sum", "closed_plus_unrealized_reconciliation_error", "maxdrawdown"]}
                  for item in audit["paths"]],
    }
    result_path = ROOT / "docs/results/limit-event-factor-20261014.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    plot(daily)
    print(json.dumps({"accounts": paths, "result_bytes": result_path.stat().st_size, "plot": "docs/assets/limit-event-factor-20261014.png"}, ensure_ascii=False))


def plot(daily):
    width, height = 1400, 900
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    def font(size):
        for path in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"]:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()
    draw.text((70, 30), "两连板后跌停挂单：固定前期涨幅筛选", fill="#172333", font=font(32))
    draw.text((70, 83), "2024-01-02 至 2026-09-18 · 同源主板 · 单边30bp · 日线潜在成交", fill="#566170", font=font(21))
    series = [("common_control_30bp", "共同支持原规则", "#727a86"),
              ("filtered_30bp", "前20日涨幅≤18.60%", "#176981")]
    for row, (label, is_dd) in enumerate([("组合净值（初始=1）", False), ("距历史高点回撤（%）", True)]):
        left, right, top, bottom = 115, 1320, 180 + row * 300, 420 + row * 300
        y_min, y_max = (-50, 0) if is_dd else (.5, 1.8)
        draw.text((70, top - 42), label, fill="#172333", font=font(23))
        for value in np.linspace(y_min, y_max, 6):
            y = bottom - (value - y_min) / (y_max - y_min) * (bottom - top)
            draw.line((left, y, right, y), fill="#e1e5e9", width=1)
            draw.text((44, y - 11), f"{value:.0f}" if is_dd else f"{value:.2f}", fill="#566170", font=font(18))
        draw.line((left, top, left, bottom), fill="#687482", width=1)
        for key, name, color in series:
            values = daily[key + "_nav"].to_numpy() / 100000
            if is_dd:
                values = (values / np.maximum.accumulate(np.r_[1, values])[1:] - 1) * 100
            points = [(left + i / (len(values) - 1) * (right - left),
                       bottom - (v - y_min) / (y_max - y_min) * (bottom - top)) for i, v in enumerate(values)]
            draw.line(points, fill=color, width=3)
        if is_dd:
            for i in np.linspace(0, len(daily) - 1, 6).astype(int):
                x = left + i / (len(daily) - 1) * (right - left)
                draw.text((x - 45, bottom + 10), daily.date.iloc[i][:7], fill="#566170", font=font(18))
            draw.text((650, bottom + 43), "参考交易日期", fill="#566170", font=font(19))
    for j, (_, name, color) in enumerate(series):
        x = 740 + j * 300
        draw.line((x, 139, x + 35, 139), fill=color, width=4)
        draw.text((x + 45, 124), name, fill="#172333", font=font(18))
    draw.text((70, 811), "来源：既有StockDB未复权行情与同源总回报代理；验证历史曾被研究使用。", fill="#566170", font=font(19))
    draw.text((70, 845), "两条账户均保留期末5只持仓；非排队成交证明，非因子组合净值。", fill="#566170", font=font(19))
    image.save(ROOT / "docs/assets/limit-event-factor-20261014.png")


if __name__ == "__main__":
    build()
