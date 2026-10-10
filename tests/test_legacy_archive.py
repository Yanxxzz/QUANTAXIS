import hashlib
from datetime import datetime
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from panda_alpha.legacy_archive import (ARCHIVE_COLLECTION, RECEIPT_COLLECTION, LOGICAL_COLUMNS,
                                      audit_price_file, archive_document, file_sha256, migrate_price_file,
                                      same_original_value, verify_archive_samples)


def make_cache(directory, rows=None):
    rows = rows or [dict(date=20210917, code="000001", open=1., high=2., low=1., close=2.,
                        volume=100, amount=150., extra="all source fields remain"),
                    dict(date=20260921, code="600000", open=2., high=3., low=2., close=3.,
                        volume=0, amount=0., extra="zero stays zero")]
    frame = pd.DataFrame(rows)
    path = Path(directory) / "sample.parquet"
    frame.to_parquet(path, index=False)
    columns = [c for c in LOGICAL_COLUMNS if c in frame.columns]
    digest = hashlib.sha256("|".join(columns).encode())
    digest.update(pd.util.hash_pandas_object(frame[columns], index=False).to_numpy(dtype=np.uint64).tobytes())
    manifest = {"schema_version": 1, "source": "local verified fixture", "columns": list(frame.columns),
                "file_sha256": file_sha256(path), "logical_hash": digest.hexdigest(), "rows": len(frame),
                "codes": frame.code.nunique(), "start": int(frame.date.min()), "end": int(frame.date.max())}
    path.with_suffix(".parquet.manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    return path


class Collection:
    def __init__(self):
        self.documents = {}
    def find_one(self, query):
        return next((dict(d) for d in self.documents.values() if all(d.get(k) == v for k, v in query.items())), None)
    def update_one(self, query, update, upsert=False):
        key = query["source_id"]
        self.documents[key] = {**self.documents.get(key, {}), **update["$set"]}
    def bulk_write(self, operations, ordered=False):
        for operation in operations:
            self.documents[operation._doc["_id"]] = operation._doc
    def count_documents(self, query):
        return sum(all(d.get(k) == v for k, v in query.items()) for d in self.documents.values())
    def create_index(self, keys):
        pass


class Database:
    def __init__(self):
        self.collections = {}
    def __getitem__(self, name):
        if name not in {ARCHIVE_COLLECTION, RECEIPT_COLLECTION}:
            raise AssertionError("An archive must never write ordinary price/adjustment/provider collections")
        return self.collections.setdefault(name, Collection())


class LegacyArchiveTests(unittest.TestCase):
    def test_batch_boundaries_do_not_change_producer_hash_and_sealed_rows_are_storage_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_cache(directory)
            audit = audit_price_file(path, batch_size=1)
            self.assertEqual(audit["rows"], 2)
            self.assertEqual(audit["sealed_rows"], 1)
            self.assertEqual(audit["outside_requested_window_rows"], 1)
            self.assertEqual(audit["archive_policy"]["research_eligibility"], "archive_only")

    def test_file_or_logical_hash_mismatch_refuses_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_cache(directory)
            manifest_path = path.with_suffix(".parquet.manifest.json")
            manifest = json.loads(manifest_path.read_text())
            manifest["logical_hash"] = "wrong"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "logical_hash"):
                audit_price_file(path)
            manifest["file_sha256"] = "wrong"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                audit_price_file(path)

    def test_unsorted_source_cannot_receive_a_false_stream_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [dict(date=d, code="000001", open=1., high=1., low=1., close=1., volume=100, amount=100.)
                    for d in [20260921, 20210917]]
            path = make_cache(directory, rows)
            with self.assertRaisesRegex(ValueError, "not sorted"):
                audit_price_file(path, batch_size=1)

    def test_all_original_values_including_nan_null_zero_are_kept(self):
        original = dict(date=20260918, code="000001", open=None, high=float("nan"), low=0., close=1.,
                        volume=12345, amount=0., extra={"unknown": "retained"})
        doc = archive_document(original, "hash", 4)
        self.assertEqual(doc["vol"], 123.45)
        self.assertIsNone(doc["original"]["open"])
        self.assertTrue(math.isnan(doc["original"]["high"]))
        self.assertEqual(doc["original"]["low"], 0.)
        self.assertEqual(doc["original"]["extra"], original["extra"])
        self.assertEqual(doc["adjustment"], "unknown_unverified")
        self.assertEqual(doc["date"], "2026-09-18")
        self.assertEqual(doc["date_stamp"], datetime(2026, 9, 18).timestamp())

    def test_import_resume_preserves_every_original_field_and_only_touches_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = audit_price_file(make_cache(directory), batch_size=1)
            db = Database()
            saved = []
            receipt = migrate_price_file(db, audit, batch_size=1, progress=lambda r: saved.append(r["next_row"]))
            self.assertEqual(receipt["rows"], 2)
            self.assertEqual(saved, [1, 2])
            doc = db[ARCHIVE_COLLECTION].documents[audit["source_id"] + ":0"]
            self.assertEqual(doc["original"]["extra"], "all source fields remain")
            self.assertEqual(doc["original"]["volume"], 100)
            self.assertEqual(doc["vol"], 1)
            second = migrate_price_file(db, audit, batch_size=1)
            self.assertEqual(second["status"], "archive_complete")
            self.assertEqual(len(db[ARCHIVE_COLLECTION].documents), 2)
            verification = verify_archive_samples(db, audit, positions=[0, 1])
            self.assertEqual(len(verification["samples"]), 2)
            doc["original"]["volume"] = 101
            with self.assertRaisesRegex(ValueError, "differs"):
                verify_archive_samples(db, audit, positions=[0])

    def test_nan_custody_comparison_does_not_accept_null_or_numeric_coercion(self):
        self.assertTrue(same_original_value(float("nan"), float("nan")))
        self.assertFalse(same_original_value(float("nan"), None))
        self.assertFalse(same_original_value(100, 100.0))

    def test_failed_archive_import_keeps_durable_cursor_and_reports_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = audit_price_file(make_cache(directory))
            db = Database()
            def interruption(receipt):
                raise RuntimeError("local shutdown")
            with self.assertRaisesRegex(RuntimeError, "shutdown"):
                migrate_price_file(db, audit, batch_size=1, progress=interruption)
            receipt = db[RECEIPT_COLLECTION].find_one({"source_id": audit["source_id"]})
            self.assertEqual(receipt["next_row"], 1)
            self.assertEqual(receipt["status"], "archive_interrupted")
            finished = migrate_price_file(db, audit, batch_size=1)
            self.assertEqual(finished["rows"], 2)


if __name__ == "__main__":
    unittest.main()
