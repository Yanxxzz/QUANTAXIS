"""Audit and publish completed stop-policy accounts without source replays."""
from pathlib import Path
from collections import Counter
import sys
import json

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.research_io import read, binding, clean, write_clean_json
from panda_alpha.evaluation import performance

OUT = ROOT / "research_runs/limit_stoploss_20261014"
PARENT = ROOT / "research_runs/limit_reversal_optimization_20261014"
NAMES = {"stop_t1_limit_down": "t+1触跌停", "stop_t2_limit_down": "t+2触跌停",
         "stop_t2_close_loss15": "t+2累计亏损>15%"}


def audit(item, calendar):
    key = item["path"]
    artifact = item.get("artifact_prefix", key)
    nav = pd.read_csv(OUT / (artifact + "_nav.csv"), dtype={"date": str})
    trades = pd.read_csv(OUT / (artifact + "_trades.csv"), dtype={"date": str, "symbol": str})
    events = read(OUT / (artifact + "_stop_events.json"))
    if len(nav) != 659 or nav.date.duplicated().any():
        raise ValueError("Only complete independent account dates can be published")
    metric = performance(nav.nav / nav.nav.shift(1).fillna(100000) - 1)
    metric_error = max(abs(metric[k] - item["metrics"][k]) for k in metric)
    if metric_error > 1e-10 or abs(nav.nav.iloc[-1] - item["terminal_nav"]) > 1e-7:
        raise ValueError("Completed metrics or terminal NAV mismatch")
    buys, sells = trades[trades.side.eq("buy")], trades[trades.side.eq("sell")]
    stops = sells[~sells.reason.eq("take_profit")]
    unrealized = sum(position["quantity"] * position["last_close"] -
                     position["entry_notional"] - position["entry_fee"]
                     for position in item["terminal_holdings"].values())
    realized = float(sells.realized_net_pnl.sum()) if len(sells) else 0.
    pnl_error = abs(realized + unrealized - (item["terminal_nav"] - 100000))
    if pnl_error > 1e-7 or abs(trades.fee.sum() - item["total_fees"]) > 1e-7:
        raise ValueError("Closed/unrealized PnL or fee ledger mismatch")
    wealth = np.r_[100000., nav.nav.to_numpy()]
    peak = np.maximum.accumulate(wealth)
    trough = int(np.argmax(1 - wealth / peak))
    peak_i = int(np.argmax(wealth[:trough + 1]))
    delays = pd.to_numeric(stops.stop_execution_delay_sessions, errors="coerce").dropna()
    stop_returns = pd.to_numeric(stops.economic_price_return, errors="coerce").dropna()
    annual = []
    returns = nav.nav / nav.nav.shift(1).fillna(100000) - 1
    for year, group in returns.groupby(nav.date.str[:4]):
        annual.append({"year": year, "compounded_return": float(np.prod(1 + group) - 1)})
    result = {"path": key, "metrics": metric, "annual_returns": annual,
              "buys": len(buys), "sells": len(sells), "sale_reasons": dict(Counter(sells.reason)),
              "stop_triggers": len(events), "ambiguous_same_bar_profit_and_stop": sum(x["same_bar_profit_stop_ambiguous"] for x in events),
              "suspension_mark_triggers": sum(x["mark_role"] == "source_explicit_suspension_carry" for x in events),
              "executed_stops": len(stops), "stop_execution_delay_median_sessions": float(delays.median()) if len(delays) else None,
              "stop_execution_delay_max_sessions": int(delays.max()) if len(delays) else None,
              "stops_executed_later_than_trigger": int(delays.gt(0).sum()),
              "stops_with_economic_loss_over15percent": int(stop_returns.lt(-.15 - 1e-12).sum()),
              "worst_stop_economic_return": float(stop_returns.min()) if len(stop_returns) else None,
              "total_fees_CNY": item["total_fees"], "closed_net_pnl_CNY": realized,
              "terminal_unrealized_pnl_CNY": unrealized,
              "terminal_holdings_count": len(item["terminal_holdings"]),
              "terminal_pending_stops": sum("pending_exit" in p for p in item["terminal_holdings"].values()),
              "terminal_cash_CNY": item["terminal_cash"], "terminal_exposure": item["terminal_exposure"],
              "sell_blocks": {k: v for k, v in item["blocked_orders"].items() if k.startswith("sell_")},
              "mean_exposure": float(nav.exposure.mean()),
              "completed_holding_median_sessions": float(sells.holding_sessions.median()) if len(sells) else None,
              "max_drawdown_peak_date": nav.date.iloc[max(0, peak_i - 1)],
              "max_drawdown_trough_date": nav.date.iloc[max(0, trough - 1)],
              "max_drawdown_loss_CNY": float(wealth[peak_i] - wealth[trough]),
              "metrics_reconciliation_error": metric_error, "PnL_reconciliation_error_CNY": pnl_error,
              "queue_fills_certified": False}
    return result, nav


def build():
    summary_path = OUT / "summary_complete.json" if (OUT / "summary_complete.json").exists() else OUT / "summary.json"
    summary = read(summary_path)
    if summary["status"] != "ALL_SIX_FIXED_STOP_PATHS_COMPLETE":
        raise ValueError("Pending source accounts cannot be published as complete")
    protocol = read(OUT / "protocol.json")
    calendar = read(ROOT / "research_runs/limit_reversal_20261014/source_metadata.json")["calendar"]
    audits, curves = [], {}
    for item in summary["paths"]:
        result, nav = audit(item, calendar)
        audits.append(result)
        if item["cost_single_side"] == .003:
            curves[item["path"].removesuffix("_30bp")] = nav
    parent_nav = pd.read_csv(PARENT / "completed_daily_nav3.csv", dtype={"date": str})
    curves["no_stop"] = parent_nav[["date", "filtered_30bp_nav"]].rename(columns={"filtered_30bp_nav": "nav"})
    public = {"status": summary["status"], "window": {"start": protocol["start"], "end": protocol["end"], "sessions": 659},
              "filter": {"feature": protocol["fixed_filter"]["feature"], "cutoff": protocol["fixed_filter"]["cutoff"]},
              "rules": {k: protocol[k] for k in ["t_role", "intraday_stops", "close_stop", "suspension_close_role", "triggered_exit", "take_profit"]},
              "accounts": audits,
              "existing_no_stop": [{"path": x["path"], "metrics": x["metrics"], "buys": x["buy_fills"],
                                    "sells": x["completed_round_trips"], "terminal_holdings_count": len(x["terminal_holdings"])}
                                   for x in summary["existing_no_stop"]],
              "bindings": {"protocol": binding(OUT / "protocol.json"), "source": binding(OUT / "source_receipt.json"),
                           "complete_accounts": binding(summary_path), "publisher": binding(__file__)},
              "official_compute_spent": 0, "new_contexts": 3, "registry_total": summary["registry_total"],
              "not_untouched_OOS": True, "formal_candidate": False,
              "execution_conditions": ["Daily touch is only potential execution; lower-limit queue and intraday order are uncertified.",
                                       "Stops are not guaranteed loss caps; blocked exits continue at later openings.",
                                       "Original same-source corporate-action equivalent-unit model and historical eligibility limitations remain.",
                                       "Only three user-fixed rules were tested; no additional sign/window/threshold selection."]}
    write_clean_json(OUT / "completed_account_audit.json", public)
    write_clean_json(ROOT / "docs/results/limit-stoploss-20261014.json", public, trailing_newline=True)
    plot(curves)
    print(json.dumps(clean({"status": public["status"], "paths": [{k: x[k] for k in ["path", "metrics", "stop_triggers", "executed_stops", "sell_blocks", "terminal_holdings_count", "PnL_reconciliation_error_CNY"]} for x in audits]}), ensure_ascii=False))


def plot(curves):
    image = Image.new("RGB", (1450, 940), "white")
    draw = ImageDraw.Draw(image)
    def font(size):
        for path in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"]:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()
    draw.text((65, 28), "固定18.60%筛选：三种指定止损与无止损对照", fill="#172333", font=font(30))
    draw.text((65, 79), "2024-01-02 至 2026-09-18 · 单边30bp · 同源日线潜在成交", fill="#566170", font=font(20))
    series = [("no_stop", "原无止损", "#777f8a"), ("stop_t1_limit_down", NAMES["stop_t1_limit_down"], "#176981"),
              ("stop_t2_limit_down", NAMES["stop_t2_limit_down"], "#a86929"),
              ("stop_t2_close_loss15", NAMES["stop_t2_close_loss15"], "#75649b")]
    for j, (_, name, color) in enumerate(series):
        x = 95 + 330 * j
        draw.line((x, 133, x + 28, 133), fill=color, width=4)
        draw.text((x + 38, 120), name, fill="#172333", font=font(18))
    all_values = np.concatenate([x.nav.to_numpy() / 100000 for x in curves.values()])
    ymax = np.ceil(max(1., all_values.max()) * 10) / 10
    ymin = max(0., np.floor(all_values.min() * 10) / 10)
    deepest = min(float((x.nav.to_numpy() / np.maximum.accumulate(np.r_[100000., x.nav.to_numpy()])[1:] - 1).min()) for x in curves.values())
    dd_min = -max(10., np.ceil(-deepest * 10) * 10)
    for row, (label, dd) in enumerate([("组合净值（初始=1）", False), ("距历史高点回撤（%）", True)]):
        left, right, top, bottom = 110, 1360, 200 + row * 310, 430 + row * 310
        a, b = (dd_min, 0) if dd else (ymin, ymax)
        draw.text((65, top - 39), label, fill="#172333", font=font(23))
        for value in np.linspace(a, b, 6):
            y = bottom - (value - a) / (b - a) * (bottom - top)
            draw.line((left, y, right, y), fill="#e1e5e9", width=1)
            draw.text((45, y - 10), f"{value:.0f}" if dd else f"{value:.2f}", fill="#566170", font=font(18))
        for key, _, color in series:
            nav = curves[key]
            values = nav.nav.to_numpy() / 100000
            if dd:
                values = (values / np.maximum.accumulate(np.r_[1., values])[1:] - 1) * 100
            pts = [(left + i / (len(values) - 1) * (right - left), bottom - (v - a) / (b - a) * (bottom - top)) for i, v in enumerate(values)]
            draw.line(pts, fill=color, width=3)
        if dd:
            for i in np.linspace(0, len(nav) - 1, 6).astype(int):
                x = left + i / (len(nav) - 1) * (right - left)
                draw.text((x - 40, bottom + 12), nav.date.iloc[i][:7], fill="#566170", font=font(18))
            draw.text((660, bottom + 44), "参考交易日期", fill="#566170", font=font(19))
    draw.text((65, 828), "来源：既有StockDB原始行情及同源总回报代理；不重跑已完成无止损对照。", fill="#566170", font=font(19))
    draw.text((65, 865), "t为买入日；触发后阻断会延迟退出。日线触价不保证跌停卖出或限制实际亏损。", fill="#566170", font=font(19))
    image.save(ROOT / "docs/assets/limit-stoploss-20261014.png")


if __name__ == "__main__":
    build()
