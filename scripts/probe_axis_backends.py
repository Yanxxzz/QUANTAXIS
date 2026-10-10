"""Bounded read-only comparison of current TDX and QA's supported BaoStock source."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.axis_sync import safe_disconnect


def main():
    results = []
    try:
        from tdxpy.hq import TdxHq_API
        api = TdxHq_API(raise_exception=True)
        try:
            api.connect("124.71.187.122", 7709, time_out=4)
            result = {"source": "tdxpy-0.2.7"}
            for category in [9, 4]:
                try:
                    bars = api.get_security_bars(category, 0, "000001", 0, 3)
                    result[str(category)] = {"rows": len(bars) if bars is not None else None, "sample": bars[:2] if bars else []}
                except Exception as exc:
                    original = getattr(exc, "original_exception", exc)
                    result[str(category)] = {"error": str(original)}
            results.append(result)
        finally:
            safe_disconnect(api)
    except Exception as exc:
        results.append({"source": "tdxpy-0.2.7", "error": str(exc)})
    try:
        import baostock as bs
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(login.error_msg)
        try:
            rs = bs.query_history_k_data_plus("sz.000001", "date,code,open,high,low,close,volume,amount",
                start_date="2026-09-01", end_date="2026-09-07", frequency="d", adjustflag="3")
            if rs.error_code != "0":
                raise RuntimeError(rs.error_msg)
            rows = []
            while rs.next():
                rows.append(dict(zip(rs.fields, rs.get_row_data())))
            results.append({"source": "baostock-0.9.4", "rows": len(rows), "sample": rows[:2]})
        finally:
            bs.logout()
    except Exception as exc:
        results.append({"source": "baostock-0.9.4", "error": str(exc)})
    out = Path("research_runs/backend_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
