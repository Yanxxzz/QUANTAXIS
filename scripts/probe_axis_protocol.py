"""Read-only capture of bounded TDX bar payloads; never invent missing records."""
from pathlib import Path
import json
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.axis_sync import endpoint, safe_disconnect


def main():
    import pytdx.hq as hq
    captures = []
    for attribute in ["GetSecurityBarsCmd", "GetIndexBarsCmd"]:
        original = getattr(hq, attribute)

        def make(original, name):
            class Capturing(original):
                def parseResponse(self, body):
                    record = {"endpoint": endpoint_text, "parser": name, "category": self.category, "length": len(body),
                              "count": struct.unpack("<H", body[:2])[0] if len(body) >= 2 else None,
                              "body_hex": bytes(body).hex(), "request_hex": bytes(self.send_pkg).hex()}
                    captures.append(record)
                    try:
                        result = super().parseResponse(body)
                        record["decoded_rows"] = len(result)
                        record["sample"] = result[:2]
                        return result
                    except Exception as exc:
                        record["parse_error"] = str(exc)
                        raise
            return Capturing
        setattr(hq, attribute, make(original, attribute))
    for endpoint_text in sys.argv[1:] or ["124.71.187.122:7709"]:
        api = hq.TdxHq_API(raise_exception=True)
        host, port = endpoint(endpoint_text, 7709)
        try:
            api.connect(host, port, time_out=4)
            for category in [9, 4]:
                for method, market, code in [(api.get_security_bars, 0, "000001"), (api.get_index_bars, 1, "000001")]:
                    try:
                        method(category, market, code, 0, 3)
                    except Exception:
                        pass
        except Exception as exc:
            captures.append({"endpoint": endpoint_text, "connection_error": str(exc)})
        finally:
            safe_disconnect(api)
    path = Path("research_runs/protocol_probe.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in captures:
        print(json.dumps({k: v for k, v in item.items() if k != "body_hex"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
