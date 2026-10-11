"""Read one identity-bound retained filing, without creating source copies."""
from pathlib import Path
import gzip
import hashlib
import json


def read_bound_statement(db, case):
    row = db.execute("SELECT period,original_json_gzip,source_sha256 FROM captures WHERE code=? AND annid=?",
                     (case["symbol"], case["announcement_id"])).fetchone()
    if (not row or row[0] != case["report_date"] or row[2] != case["capture_sha256"]
            or hashlib.sha256(row[1]).hexdigest() != row[2]):
        raise ValueError("captured identity/blob binding changed")
    stock = json.loads(gzip.decompress(row[1]))["stock"]
    if (str(stock["code"]) != case["symbol"] or str(stock["announcement_id"]) != case["announcement_id"]
            or stock["report_date"] != case["report_date"] or stock["record_sha256"] != case["source_record_sha256"]):
        raise ValueError("captured stock identity/record binding changed")
    prov = dict(stock["provenance"])
    path = Path(case["text_path"])
    if prov["text_sha256"] != case["text_sha256"] or Path(prov["text_path"]).resolve() != path.resolve():
        raise ValueError("retained text identity binding changed")
    compressed = path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != case["text_sha256"]:
        raise ValueError("retained text gzip binding changed")
    content = gzip.decompress(compressed)
    prov.update(text_hash_reverified=True, text_content_sha256=hashlib.sha256(content).hexdigest(),
                pdf_hash_reverified=False)
    return content.decode("utf-8"), stock, prov
