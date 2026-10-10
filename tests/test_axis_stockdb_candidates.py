"""Offline archive inventory must not become research data or a fallback."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from panda_alpha.legacy_archive import ARCHIVE_COLLECTION, archive_document, file_sha256
from scripts.axis_stockdb_candidates import FORMAL_CACHE, load_audit, query_candidates


SOURCE_ID = "a" * 64


def make_audit():
    manifest = {"schema_version": 1, "path": FORMAL_CACHE, "source": "original fixture StockDB export",
        "file_sha256": SOURCE_ID, "logical_hash": "b" * 64, "rows": 6, "codes": 2,
        "start": 20210922, "end": 20260921,
        "columns": ["date", "code", "open", "high", "low", "close", "volume", "amount", "pre_close", "pct_chg", "is_st"]}
    return {**{key: manifest[key] for key in ("file_sha256", "logical_hash", "rows", "codes", "start", "end")},
        "path": "D:/cache/" + FORMAL_CACHE, "manifest": manifest, "source_id": SOURCE_ID,
        "manifest_sha256": "c" * 64, "source_dates": [20210922, 20210923, 20260918, 20260921],
        "by_code": {"000001": {"rows": 4}, "600000": {"rows": 2}}}


def bar(code="000001", date=20210922, source_id=SOURCE_ID, source_row=0, **changes):
    original = dict(code=code, date=date, open=10., high=11., low=9., close=10., volume=100,
                    amount=1000., pre_close=9., pct_chg=11.111, is_st=False)
    original.update(changes)
    return archive_document(original, source_id, source_row)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def sort(self, keys):
        self.rows.sort(key=lambda row: tuple(row[key] for key, _ in keys))
        return self

    def __iter__(self):
        return iter(copy.deepcopy(self.rows))

    def close(self):
        self.closed = True


class ReadOnlyArchive:
    def __init__(self, rows, ignore_query=False):
        self.rows = copy.deepcopy(rows)
        self.queries = []
        self.ignore_query = ignore_query
        self.cursor = None

    def find(self, query, projection):
        self.queries.append(copy.deepcopy(query))

        def matches(row):
            for field, value in query.items():
                if isinstance(value, dict):
                    if "$in" in value and row[field] not in value["$in"]:
                        return False
                    if "$gte" in value and row[field] < value["$gte"]:
                        return False
                    if "$lte" in value and row[field] > value["$lte"]:
                        return False
                elif row[field] != value:
                    return False
            return True

        self.cursor = Cursor([row for row in self.rows if self.ignore_query or matches(row)])
        return self.cursor

    def update_one(self, *args, **kwargs):
        raise AssertionError("Candidate queries may not mutate the archive")

    insert_one = delete_many = create_index = update_one


class ArchiveOnlyDatabase:
    def __init__(self, rows=(), ignore_query=False):
        self.archive = ReadOnlyArchive(rows, ignore_query)
        self.accesses = []

    def __getitem__(self, name):
        self.accesses.append(name)
        if name != ARCHIVE_COLLECTION:
            raise AssertionError("Candidate queries must not read formal prices, factors, calendar or receipts")
        return self.archive


class StockDBCandidateTests(unittest.TestCase):
    def test_selects_only_formal_source_and_open_dates_without_any_mutation(self):
        rows = [bar(date=20210922), bar(date=20260918, source_row=1),
                bar(date=20210917, source_row=2), bar(date=20260921, source_row=3),
                bar(date=20210922, source_id="d" * 64), bar(code="600000", date=20210923)]
        db = ArchiveOnlyDatabase(rows)
        before = copy.deepcopy(db.archive.rows)
        report = query_candidates(db, make_audit(), codes=["000001"])
        self.assertEqual(report["coverage"]["rows"], 2)
        self.assertEqual([row["date"] for row in report["candidates"]], ["2021-09-22", "2026-09-18"])
        self.assertEqual(db.archive.queries[0], {"source_id": SOURCE_ID,
            "code": {"$in": ["000001"]}, "date": {"$gte": "2021-09-22", "$lte": "2026-09-18"}})
        self.assertEqual(before, db.archive.rows)
        self.assertEqual(db.accesses, [ARCHIVE_COLLECTION])
        self.assertTrue(db.archive.cursor.closed)
        self.assertFalse(report["selection"]["automatic_fallback"])
        self.assertFalse(report["acceptance"]["research_accepted"])
        self.assertFalse(report["acceptance"]["can_retire_legacy"])
        self.assertFalse(report["source"]["qfq_hfq_supported"])

    def test_reports_cache_grid_gaps_and_unknown_codes_without_calendar_claims(self):
        db = ArchiveOnlyDatabase([bar(), bar(date=20260918, source_row=1)])
        report = query_candidates(db, make_audit(), codes=["000001", "920001"])
        existing, absent = report["coverage"]["by_code"]
        self.assertEqual(existing["observed_grid_coverage"], 2 / 3)
        self.assertEqual(existing["reference_grid_missing_dates"], ["2021-09-23"])
        self.assertFalse(absent["audited_cache_code"])
        self.assertEqual(absent["rows"], 0)
        self.assertEqual(report["coverage"]["reference_grid_missing_cells"], 4)
        self.assertFalse(report["coverage"]["independent_trading_calendar"])
        self.assertFalse(report["coverage"]["lifecycle_reconciled"])
        self.assertEqual(report["source"]["price_basis"], "unknown_unverified")

    def test_all_code_inventory_includes_zero_rows_but_does_not_read_other_collections(self):
        db = ArchiveOnlyDatabase([bar(code="600000")])
        report = query_candidates(db, make_audit(), sample_limit=0)
        self.assertEqual(report["selection"]["codes"], ["000001", "600000"])
        self.assertNotIn("code", db.archive.queries[0])
        self.assertEqual(report["coverage"]["codes_with_rows"], 1)
        self.assertEqual(report["candidates"], [])
        self.assertTrue(report["candidate_sample_truncated"])
        self.assertTrue(all(value == 0 for value in report["permissions"].values()))

    def test_empty_archive_stays_empty_and_cannot_fall_back(self):
        report = query_candidates(ArchiveOnlyDatabase(), make_audit(), codes=["000001"])
        self.assertEqual(report["coverage"]["rows"], 0)
        self.assertEqual(report["coverage"]["reference_grid_missing_cells"], 3)
        self.assertEqual(report["candidates"], [])
        self.assertFalse(report["acceptance"]["research_accepted"])

    def test_duplicates_invalid_prices_and_zero_volume_are_reported_without_imputation(self):
        db = ArchiveOnlyDatabase([bar(volume=0, amount=0), bar(source_row=1, close=float("nan"))])
        report = query_candidates(db, make_audit(), codes=["000001"])
        quality = report["coverage"]["quality_counts"]
        self.assertEqual(quality["duplicate_code_date_rows"], 1)
        self.assertEqual(quality["invalid_observation_rows"], 1)
        self.assertEqual(quality["zero_volume_rows_with_unknown_suspension_status"], 1)
        self.assertEqual(report["coverage"]["rows"], 2)
        self.assertEqual(report["coverage"]["observed_grid_cells"], 1)
        self.assertEqual(report["candidates"][0]["original"]["volume"], 0)
        self.assertEqual(report["candidates"][1]["original"]["close"], "NaN")
        json.dumps(report, allow_nan=False)

    def test_sample_limit_does_not_limit_coverage_scan(self):
        db = ArchiveOnlyDatabase([bar(), bar(date=20210923, source_row=1), bar(date=20260918, source_row=2)])
        report = query_candidates(db, make_audit(), codes=["000001"], sample_limit=1)
        self.assertEqual(report["coverage"]["rows"], 3)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertTrue(report["candidate_sample_truncated"])

    def test_sealed_source_dates_and_invalid_selectors_rejected_before_database_access(self):
        db = ArchiveOnlyDatabase()
        sealed = make_audit()
        sealed["path"] = "D:/cache/batch28_warmup_20200803_20210921.parquet"
        with self.assertRaisesRegex(ValueError, "sealed warmup"):
            query_candidates(db, sealed)
        for kwargs in ({"start": "2021-09-17"}, {"end": "2026-09-21"}, {"codes": []},
                       {"codes": ["1"]}, {"sample_limit": -1}, {"sample_limit": 1001}):
            with self.assertRaises(ValueError):
                query_candidates(db, make_audit(), **kwargs)
        self.assertEqual(db.accesses, [])

    def test_inconsistent_manifest_and_unselected_archive_rows_fail_closed(self):
        invalid = make_audit()
        invalid["file_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "mismatch"):
            query_candidates(ArchiveOnlyDatabase(), invalid)
        for row, error in [(bar(source_id="d" * 64), "unselected"),
                           ({**bar(), "adjustment": "hfq"}, "metadata"),
                           ({**bar(), "original": {**bar()["original"], "date": 20210923}}, "identity")]:
            db = ArchiveOnlyDatabase([row], ignore_query=True)
            with self.assertRaisesRegex(ValueError, error):
                query_candidates(db, make_audit(), codes=["000001"])
            self.assertTrue(db.archive.cursor.closed)

    def test_existing_manifest_sha_and_content_are_checked_without_reading_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = make_audit()
            manifest_path = Path(directory) / (FORMAL_CACHE + ".manifest.json")
            manifest_path.write_text(json.dumps(audit["manifest"]), encoding="utf-8")
            audit["manifest_path"] = str(manifest_path)
            audit["manifest_sha256"] = file_sha256(manifest_path)
            audit_path = Path(directory) / "execution.audit.json"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            self.assertEqual(load_audit(audit_path)["source_id"], SOURCE_ID)
            manifest_path.write_text(json.dumps({**audit["manifest"], "source": "changed"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA256 changed"):
                load_audit(audit_path)
            audit["manifest_sha256"] = file_sha256(manifest_path)
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                load_audit(audit_path)


if __name__ == "__main__":
    unittest.main()
