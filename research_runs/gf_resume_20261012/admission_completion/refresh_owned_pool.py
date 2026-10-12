"""Free, read-only own pool/definition evidence; never dispatch or mutate a pool."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import PandaClient


def main():
    out = Path(__file__).parent
    if (out / "owned_pool_snapshot.json").exists():
        raise ValueError("Owned snapshot exists; inspect it instead of repeating")
    cfg = json.loads((ROOT / "config/panda-alpha.local.json").read_text(encoding="utf-8"))
    sys.path.insert(0, cfg["platform"]["site_packages"])
    import requests
    from pandaai import load_config, login
    from pandaai.auth import make_headers
    client = PandaClient(cfg["platform"])
    config = load_config(None)
    token, uid, _ = login(config)
    endpoint = "https://api.pandaaiquant.com/factorPool/pools"
    response = requests.get(endpoint, headers=make_headers(token=token, uid=uid), timeout=30)
    response.raise_for_status()
    payload = response.json()
    # Own pool response only; account credentials are never copied.
    record = {"endpoint": endpoint, "method": "GET", "http_status": response.status_code,
              "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "original_response_sha256": hashlib.sha256(response.content).hexdigest(),
              "content": payload, "official_runs": 0, "pool_operations": 0}
    (out / "owned_pool_snapshot.json").write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    old = json.loads((ROOT / "research_runs/night_research_20261011/current_own_pool_review.json").read_text(encoding="utf-8"))
    ids = {m["workflow_id"] for m in old["current_effective_members"]}
    ids.add("6abbd3fda8ba1ed343bf85bf")
    receipts = []
    for wid in sorted(ids):
        value = client.cli("factor_info", wid)
        path = out / ("definition_" + wid + ".json")
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        receipts.append({"workflow_id": wid, "path": str(path),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "name": value.get("name"), "content_sha256": hashlib.sha256(value.get("content", "").encode()).hexdigest()})
    (out / "definition_receipts.json").write_text(json.dumps(receipts, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": "OWN_POOL_AND_SEVEN_DEFINITIONS_READ_ONLY_COMPLETE", "definitions": receipts, "official_runs": 0, "pool_operations": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
