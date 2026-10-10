import numpy as np
import pandas as pd
import pytest

from panda_alpha.evaluation import FormulaEvaluator, check_window, evaluate_signal


def panel():
    rng = np.random.default_rng(10)
    dates = pd.bdate_range("2023-01-02", periods=100)
    prices = 20 * np.exp(np.cumsum(rng.normal(0.001, 0.01, (100, 40)), axis=0))
    return pd.DataFrame([{"date": date, "symbol": str(j), "open": prices[i, j],
                          "close": prices[i, j], "volume": 1000, "adjustment": "qfq"}
                         for i, date in enumerate(dates) for j in range(40)])


def test_no_future_data_or_python_execution():
    f = FormulaEvaluator(panel())
    result = f.evaluate("RANK(close/DELAY(close,5)-1)")
    assert result.iloc[:5].isna().all().all()
    for text in ("__import__('os')", "close.shift(-1)", "DELAY(close,-1)"):
        with pytest.raises(ValueError):
            f.evaluate(text)


def test_sealed_window_includes_warmup():
    with pytest.raises(ValueError):
        check_window("2022-01-01", "2023-01-01", [{"start": "2021-01-01", "end": "2021-12-31"}], "2021-12-20")


def test_platform_mean_is_not_rolling_mean():
    evaluator = FormulaEvaluator(panel())
    with pytest.raises(ValueError, match="multiple series"):
        evaluator.evaluate("MEAN(close,20)")
    assert evaluator.evaluate("MA(close,20)").iloc[:19].isna().all().all()
    assert evaluator.evaluate("MEAN(close,open)").equals(evaluator.fields['close'])


def test_cost_and_direction_are_fixed():
    frame = panel()
    values = FormulaEvaluator(frame).evaluate("RANK(close/DELAY(close,5)-1)")
    free = evaluate_signal(frame, values, cost=0)
    paid = evaluate_signal(frame, values, cost=0.003)
    assert paid["net"]["compounded_return"] < free["net"]["compounded_return"]
    assert paid["admission_status"].startswith("PENDING")
    raw = frame.assign(adjustment="none")
    with pytest.raises(ValueError, match="Raw prices"):
        evaluate_signal(raw, values)


def test_missing_held_exit_is_not_zero_return():
    frame = panel()
    values = FormulaEvaluator(frame).evaluate("RANK(close)")
    chosen = values.iloc[0].idxmax()
    frame.loc[(frame.date == frame.date.unique()[2]) & (frame.symbol == chosen), "open"] = np.nan
    with pytest.raises(ValueError, match="Missing held"):
        evaluate_signal(frame, values)
