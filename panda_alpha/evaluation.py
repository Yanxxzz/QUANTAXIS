"""Restricted formula evaluation and next-open local research proxies.

These reports are explicitly not official competition or executable admission
evidence: price limits, suspensions, delisted universes and corporate action
cashflows need a separate execution audit.
"""
from __future__ import annotations

import ast
import operator
import math
import numpy as np
import pandas as pd


def check_window(start: str, end: str, sealed: list[dict], warmup_start: str | None = None):
    first, last = pd.Timestamp(warmup_start or start), pd.Timestamp(end)
    if first > pd.Timestamp(start) or pd.Timestamp(start) > last:
        raise ValueError("Invalid research interval")
    for window in sealed:
        if first <= pd.Timestamp(window["end"]) and last >= pd.Timestamp(window["start"]):
            raise ValueError("Research/warmup interval overlaps sealed OOS")


class FormulaEvaluator:
    def __init__(self, frame: pd.DataFrame):
        if frame.duplicated(["date", "symbol"]).any():
            raise ValueError("Duplicate stock-days")
        self.fields = {name.lower(): frame.pivot(index="date", columns="symbol", values=name).sort_index()
                       for name in ("open", "high", "low", "close", "volume", "amount") if name in frame}

    def evaluate(self, formula: str) -> pd.DataFrame:
        tree = ast.parse(formula, mode="eval")
        if len(list(ast.walk(tree))) > 512:
            raise ValueError("Formula is too complex")
        binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Pow: operator.pow}
        compare = {ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge, ast.LtE: operator.le,
                   ast.Eq: operator.eq, ast.NotEq: operator.ne}

        def window(value):
            if not isinstance(value, (int, float)) or not float(value).is_integer() or not 1 <= value <= 1000:
                raise ValueError("Window must be an integer in [1,1000]")
            return int(value)

        functions = {
            "RANK": lambda x: x.rank(axis=1, pct=True),
            "DELAY": lambda x, n: x.shift(window(n)),
            "REF": lambda x, n: x.shift(window(n)),
            "MEAN": lambda *xs: sum(xs)/len(xs),
            "MA": lambda x, n: x.rolling(window(n), min_periods=window(n)).mean(),
            "TSMEAN": lambda x, n: x.rolling(window(n), min_periods=window(n)).mean(),
            "TS_MEAN": lambda x, n: x.rolling(window(n), min_periods=window(n)).mean(),
            "STDDEV": lambda x, n: x.rolling(window(n), min_periods=window(n)).std(),
            "SUM": lambda x, n: x.rolling(window(n), min_periods=window(n)).sum(),
            "TSMAX": lambda x, n: x.rolling(window(n), min_periods=window(n)).max(),
            "TS_MAX": lambda x, n: x.rolling(window(n), min_periods=window(n)).max(),
            "TSMIN": lambda x, n: x.rolling(window(n), min_periods=window(n)).min(),
            "TS_MIN": lambda x, n: x.rolling(window(n), min_periods=window(n)).min(),
            "TS_ZSCORE": lambda x, n: (x-x.rolling(window(n)).mean())/x.rolling(window(n)).std().replace(0, np.nan),
            "COUNT": lambda x, n: x.astype(float).rolling(window(n), min_periods=window(n)).sum(),
            "CORR": lambda x, y, n: x.rolling(window(n), min_periods=window(n)).corr(y),
            "ABS": abs,
            "LOG": lambda x: np.log(x.where(x > 0)),
            "SQRT": lambda x: np.sqrt(x.where(x >= 0)),
            "MAX": np.maximum,
            "MIN": np.minimum,
            "IF": lambda cond, yes, no: pd.DataFrame(np.where(cond, yes, no), index=cond.index, columns=cond.columns),
        }

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                if not math.isfinite(node.value) or abs(node.value) > 1e12:
                    raise ValueError("Invalid numeric constant")
                return node.value
            if isinstance(node, ast.Name) and node.id.lower() in self.fields:
                return self.fields[node.id.lower()]
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Div):
                    right = right.replace(0, np.nan) if isinstance(right, pd.DataFrame) else (right or np.nan)
                if isinstance(node.op, ast.Pow) and (not isinstance(right, (int, float)) or abs(right) > 8):
                    raise ValueError("Power exponent outside permitted range")
                return binary[type(node.op)](left, right)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                return -visit(node.operand) if isinstance(node.op, ast.USub) else visit(node.operand)
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in compare:
                return compare[type(node.ops[0])](visit(node.left), visit(node.comparators[0]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.upper() in functions and not node.keywords:
                if node.func.id.upper() == "MEAN" and (not node.args or any(not isinstance(visit(a), pd.DataFrame) for a in node.args)):
                    raise ValueError("MEAN averages multiple series; use MA/TS_MEAN for rolling windows")
                return functions[node.func.id.upper()](*[visit(a) for a in node.args])
            raise ValueError("Unsupported formula node; Python candidates run in the platform sandbox")

        value = visit(tree)
        if not isinstance(value, pd.DataFrame):
            raise ValueError("Factor must produce a stock-by-date panel")
        return value.replace([np.inf, -np.inf], np.nan)


def performance(returns: pd.Series) -> dict:
    returns = returns.astype(float)
    if not len(returns) or not np.isfinite(returns).all() or (returns <= -1).any():
        raise ValueError("Invalid daily returns")
    wealth = (1 + returns).cumprod()
    peak = wealth.cummax().clip(lower=1)
    std = returns.std(ddof=1)
    return {"days": len(returns), "compounded_return": float(wealth.iloc[-1] - 1),
            "cagr": float(wealth.iloc[-1] ** (252 / len(returns)) - 1),
            "sharpe": float(returns.mean() / std * np.sqrt(252)) if std > 0 else None,
            "max_drawdown": float((1 - wealth / peak).max())}


def evaluate_signal(frame: pd.DataFrame, values: pd.DataFrame, cycle: int = 5,
                    direction: int = 1, cost: float = 0.003, groups: int = 10,
                    minimum_assets: int = 30) -> dict:
    if not 1 <= cycle <= 10 or not 2 <= groups <= 10 or direction not in (0, 1):
        raise ValueError("Invalid fixed experiment settings")
    if not math.isfinite(cost) or not 0 <= cost < 1:
        raise ValueError("Invalid one-way transaction cost")
    if set(frame["adjustment"].unique()) - {"qfq", "hfq"}:
        raise ValueError("Raw prices cannot be used as continuous-return labels")
    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    scores = values.reindex(index=opens.index, columns=opens.columns) * (1 if direction else -1)
    future = opens.shift(-(cycle + 1)) / opens.shift(-1) - 1
    # Rank only shared finite observations; constant cross sections yield NaN.
    score_ranks = scores.where(future.notna()).rank(axis=1)
    future_ranks = future.where(scores.notna()).rank(axis=1)
    ic = score_ranks.corrwith(future_ranks, axis=1)
    valid_counts = (scores.notna() & future.notna()).sum(axis=1)
    ic = ic.where(valid_counts >= minimum_assets).dropna()
    if not len(ic):
        raise ValueError("Insufficient overlapping factor values and return labels")
    weights = pd.Series(0.0, index=opens.columns)
    cash = 1.0
    rows = []
    # A close-of-day signal is tradable no earlier than the next session open.
    for i in range(1, len(opens) - 1):
        fee = 0.0
        if (i - 1) % cycle == 0:
            signal = scores.iloc[i - 1].dropna()
            if len(signal) >= minimum_assets:
                selected = signal.sort_values(ascending=False, kind="mergesort").head(max(1, len(signal) // groups)).index
                if not (opens.iloc[i].loc[selected] > 0).all():
                    raise ValueError("Untradeable next-open entry requires execution audit")
                target = pd.Series(0.0, index=weights.index)
                target.loc[selected] = 1 / len(selected)
                fee = cost * float((target - weights).abs().sum())
                weights, cash = target, 0.0
        asset_returns = opens.iloc[i + 1] / opens.iloc[i] - 1
        held = weights > 0
        if asset_returns.loc[held].isna().any() or (asset_returns.loc[held] <= -1).any():
            raise ValueError("Missing held-stock exit/return; cannot fill with zero")
        gross = float((weights.loc[held] * asset_returns.loc[held]).sum())
        # Fees paid at the opening auction reduce equity before the holding return.
        net = (1 - fee) * (1 + gross) - 1
        if i == len(opens) - 2:
            net = (1 + net) * (1 - cost * float(weights.abs().sum())) - 1
        rows.append({"date": str(opens.index[i + 1]), "gross": gross, "net": net, "traded_weight": fee / cost if cost else 0})
        growth = 1 + gross
        weights.loc[held] *= (1 + asset_returns.loc[held]) / growth
        cash /= growth
    daily = pd.DataFrame(rows).set_index("date")
    if daily.empty:
        raise ValueError("No executable proxy periods")
    return {"evidence_status": "LOCAL_RESEARCH_PROXY", "admission_status": "PENDING_EXECUTION_AND_OFFICIAL_EVIDENCE",
            "direction": direction, "cycle": cycle, "one_way_cost": cost,
            "rank_ic_mean": float(ic.mean()), "rank_ic_dates": len(ic),
            "gross": performance(daily.gross), "net": performance(daily.net),
            "yearly": {str(y): performance(group.net) for y, group in daily.groupby(pd.to_datetime(daily.index).year)},
            "daily": daily.reset_index().to_dict(orient="records"),
            "limitations": ["Next-open proxy: no limit-up/down, ST, suspension or capacity model",
                            "Point-in-time delisted universe and official pool increment still required",
                            "Statistics do not certify final Sharpe or competition points"]}
