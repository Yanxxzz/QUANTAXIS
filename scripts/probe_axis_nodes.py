"""Read-only protocol health probe for upstream TDX endpoints, no backtest spend."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.axis_sync import DEFAULT_HOSTS, endpoint, safe_disconnect, install_tdx_name_compat


def probe(value):
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(raise_exception=True)
    host, port = endpoint(value, 7709)
    result = {"endpoint": value}
    try:
        result["connected"] = bool(api.connect(host, port, time_out=3))
        if not result["connected"]:
            return result
        calls = {"stock_day": lambda: api.get_security_bars(9, 0, "000001", 0, 100),
                 "calendar": lambda: api.get_index_bars(9, 1, "000001", 0, 100),
                 "stock_list": lambda: api.get_security_list(0, 0),
                 "stock_xdxr": lambda: api.get_xdxr_info(0, "000001")}
        for name, call in calls.items():
            try:
                rows = call()
                result[name] = {"rows": len(rows) if rows is not None else None,
                                "first_date": rows[0].get("datetime") if rows else None}
            except Exception as exc:
                result[name] = {"error": type(exc).__name__}
    except Exception as exc:
        result["error"] = type(exc).__name__
    finally:
        safe_disconnect(api)
    return result


if __name__ == "__main__":
    install_tdx_name_compat()
    hosts = sys.argv[1:] or DEFAULT_HOSTS + ["180.153.18.170:7709", "119.147.212.81:7709", "106.120.74.86:7709"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, hosts))
    out = Path("research_runs/node_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    for item in results:
        print(json.dumps(item))
