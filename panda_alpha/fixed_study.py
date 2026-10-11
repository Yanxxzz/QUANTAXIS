"""Complete frozen signal portfolios on shared dates before exposing outcomes."""
from pathlib import Path
import json
import pandas as pd
from . import study


def review_fixed_portfolios(schedules, signals, calendar, anchors, days, quotes, costs, checkpoint):
    """Keep finished paths on errors; persist only aggregate portfolio accounts."""
    checkpoint = Path(checkpoint)
    if checkpoint.exists():
        raise ValueError("prior execution checkpoint exists; inspect without automatic replay")
    names = list(schedules)
    book = study.PotentialQuoteBook(study._quotes_frame(quotes))
    results, daily, completed = [], [], []

    def save(status, error=None):
        payload = {"status": status, "completed": completed}
        if status != "COMPLETED":
            payload.update(completed_results=results, completed_portfolio_daily=daily, error=error)
        checkpoint.write_text(json.dumps(study._clean(payload), separators=(",", ":"), allow_nan=False), encoding="utf-8")

    save("RUNNING")
    try:
        for cost in costs:
            for name in names:
                print("RUNNING_FIXED_PATH", name, cost, flush=True)
                run = study.replay_portfolio(name, schedules[name], days, book, cost)
                if [r["date"] for r in run["daily"]] != days or run["accounting"]["max_absolute_accounting_error"] > 1e-8:
                    raise ValueError("holding dates or portfolio accounting changed")
                results.append({"name": name, "cost": cost, "metrics": run["metrics"], "accounting": run["accounting"],
                    "exposures": run["exposures"]["summary"], "blocked_orders": run["blocked_orders"],
                    "turnover": run["turnover"], "terminal_liquidation_pending": run["terminal_liquidation_pending"],
                    "terminal_position_count": len(run["final_open_positions"]),
                    "terminal_residual_fraction": sum(q*book.price(c, days[-1], "close")[0]
                        for c, q in run["final_open_positions"].items())/run["daily"][-1]["nav"]})
                daily.extend({"name": name, "cost": cost, **{k: r[k] for k in ["date", "net_return", "nav", "fees"]}} for r in run["daily"])
                completed.append(name+":"+str(cost))
                save("RUNNING")
        positions = {d: i for i, d in enumerate(calendar)}
        ic = {k: [] for k in signals if k not in ["date", "symbol"]}
        marks = 0
        for day in anchors:
            rows = signals[signals.date.eq(day)].set_index("symbol")
            entry, end = [calendar[positions[day]+n] for n in [1, 6]]
            labels = {}
            for code in rows.index:
                first, m1 = book.price(code, entry, "open")
                last, m2 = book.price(code, end, "open")
                labels[code] = last/first-1
                marks += int(m1 or m2)
            labels = pd.Series(labels)
            for name in ic:
                ic[name].append(float(rows[name].corr(labels, method="spearman")))
        wealth = {(r["name"], r["cost"]): pd.Series([x["net_return"] for x in daily if x["name"] == r["name"] and x["cost"] == r["cost"]]) for r in results}
        dependence = {str(cost): {a+":"+b: float(wealth[(a, cost)].corr(wealth[(b, cost)]))
                        for i, a in enumerate(names) for b in names[i+1:]} for cost in costs}
        save("COMPLETED")
        return {"results": results, "portfolio_daily": daily, "RankIC": {k: float(pd.Series(v).mean()) for k, v in ic.items()},
                "RankIC_periods": len(anchors), "known_suspension_mark_labels": marks, "daily_net_dependence": dependence}
    except Exception as error:
        save("EXECUTION_PENDING", {"type": type(error).__name__, "message": str(error)})
        raise
