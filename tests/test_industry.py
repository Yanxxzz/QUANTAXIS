import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from panda_alpha.industry import IndustryVintage, asof_industry, parse_code_sorted_page, query_industry_asof


class MongoCollection:
    def __init__(self, records=()):
        self.records = list(records)

    def find(self, query, projection=None):
        return [dict(row) for row in self.records if all(row.get(key) == value for key, value in query.items())]

    def create_index(self, keys, unique=False):
        return "test_index"

    def update_one(self, query, update, upsert=False):
        if self.find(query):
            return SimpleNamespace(upserted_id=None)
        self.records.append(dict(update["$setOnInsert"]))
        return SimpleNamespace(upserted_id=len(self.records))


class MongoDatabase(dict):
    def __getitem__(self, key):
        return self.setdefault(key, MongoCollection())


def stored_edition(vintage="old", publication="2025-04-18"):
    return {"vintage_id": vintage, "publication_date": publication, "source_sha256": "a"*64,
            "status": "verified_scoped", "source_scope_codes": ["000001", "000002"],
            "scope_sha256": "b"*64, "parse_receipt_sha256": "c"*64, "source_scope_count": 2}


def stored_row(vintage="old", publication="2025-04-18", code="000001", major="66"):
    return {"code": code, "symbol": code, "vintage_id": vintage, "publication_date": publication,
            "source_sha256": "a"*64, "parse_receipt_sha256": "c"*64, "category_code": "J", "major_code": major}


def edition(vintage, publication, code="000001", major="66"):
    record = {"symbol": code, "category_code": "J", "major_code": major}
    return IndustryVintage(vintage, publication, "a" * 64, {code: record})


class IndustryAsOfTests(unittest.TestCase):
    def test_reference_halfyear_not_availability(self):
        values = [edition("2025H1", "2025-09-30")]
        self.assertEqual(asof_industry(values, "000001", "2025-08-14")["status"], "UNKNOWN_NO_PUBLIC_EDITION")

    def test_date_only_publication_not_same_day(self):
        values = [edition("old", "2025-04-18"), edition("new", "2025-09-30", major="69")]
        self.assertEqual(asof_industry(values, "000001", "2025-09-30")["major_code"], "66")
        self.assertEqual(asof_industry(values, "000001", "2025-10-09")["major_code"], "69")

    def test_future_edition_does_not_change_past(self):
        earlier = [edition("old", "2025-04-18")]
        extended = earlier + [edition("future", "2026-09-30", major="69")]
        self.assertEqual(asof_industry(earlier, "000001", "2026-08-26"), asof_industry(extended, "000001", "2026-08-26"))

    def test_latest_missing_code_does_not_fallback(self):
        values = [edition("old", "2025-04-18"), edition("new", "2025-09-30", code="000002")]
        self.assertEqual(asof_industry(values, "000001", "2025-10-09")["status"], "UNKNOWN_ABSENT_IN_LATEST_PUBLIC_EDITION")

    def test_same_day_edition_conflict_is_not_tie_broken(self):
        with self.assertRaises(ValueError):
            asof_industry([edition("one", "2025-04-18"), edition("two", "2025-04-18")], "000001", "2025-08-14")

    def test_wrapped_native_text_and_attached_code(self):
        rows = parse_code_sorted_page("000004*ST国华 I 信息传输、软件和信息技术\n服务业\n65 软件和信息技术服务业\n000008神州高铁 C 制造业 CG 专用、通用及交通运输设备 37 铁路、船舶\n", 1)
        self.assertEqual(rows[0]["symbol"], "000004")
        self.assertEqual(rows[0]["major_code"], "65")
        self.assertEqual(rows[1]["major_code"], "37")
        self.assertEqual(rows[1]["category_name"], "制造业")

    def test_ambiguous_row_rejected(self):
        with self.assertRaises(ValueError):
            parse_code_sorted_page("000001 平安银行 行业未知", 1)


class IndustryMongoTests(unittest.TestCase):
    def database(self):
        return MongoDatabase(stock_industry_editions=MongoCollection([stored_edition()]),
                             stock_industry_pit=MongoCollection([stored_row()]))

    def test_same_day_unavailable_and_future_edition_does_not_change_past(self):
        db = self.database()
        self.assertEqual(query_industry_asof(db, code="000001", decision_date="2025-04-18")["status"], "UNKNOWN_NO_PUBLIC_EDITION")
        before = query_industry_asof(db, code="000001", decision_date="2025-08-14")
        db["stock_industry_editions"].records.append(stored_edition("future", "2026-04-03"))
        self.assertEqual(before, query_industry_asof(db, code="000001", decision_date="2025-08-14"))

    def test_global_latest_omission_differs_from_outside_scope(self):
        db = self.database()
        db["stock_industry_editions"].records.append(stored_edition("new", "2025-09-30"))
        db["stock_industry_pit"].records.append(stored_row("new", "2025-09-30", code="000002"))
        latest = query_industry_asof(db, code="000001", decision_date="2025-10-10")
        self.assertEqual(latest["status"], "UNKNOWN_ABSENT_IN_LATEST_PUBLIC_EDITION")
        self.assertFalse(latest["out_of_verified_scope"])
        outside = query_industry_asof(db, code="000003", decision_date="2025-10-10")
        self.assertEqual(outside["status"], "UNKNOWN_OUTSIDE_VERIFIED_SOURCE_SCOPE")
        self.assertTrue(outside["out_of_verified_scope"])

    def test_leading_zero_major_and_scope_provenance_returned(self):
        db = self.database()
        db["stock_industry_pit"].records[0].update(category_code="A", major_code="01")
        result = query_industry_asof(db, code="000001", decision_date="2025-08-14")
        self.assertEqual(result["sector"], "A01")
        self.assertEqual(result["source_scope_count"], 2)
        self.assertFalse(result["full_a_industry_verified"])

    def test_same_publication_conflict_or_row_provenance_fails(self):
        db = self.database()
        db["stock_industry_editions"].records.append(stored_edition("conflict"))
        with self.assertRaises(ValueError):
            query_industry_asof(db, code="000001", decision_date="2025-08-14")
        db = self.database()
        db["stock_industry_pit"].records[0]["parse_receipt_sha256"] = "d"*64
        with self.assertRaises(ValueError):
            query_industry_asof(db, code="000001", decision_date="2025-08-14")

    def migration_module(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_industry.py"
        spec = importlib.util.spec_from_file_location("test_industry_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_idempotent_conflicts_rejected_and_other_collections_untouched(self):
        module = self.migration_module()
        db = MongoDatabase(stock_day=MongoCollection([{"code": "000001", "close": 10}]),
                           stock_financial_pit=MongoCollection([{"code": "000001", "value": 20}]))
        metadata, row = stored_edition(), stored_row()
        pack = [(metadata, [row])]
        first = module.migrate_pack(db, pack)
        second = module.migrate_pack(db, pack)
        self.assertEqual(first["editions"][0]["inserted_rows"], 1)
        self.assertEqual(second["editions"][0]["inserted_rows"], 0)
        self.assertFalse(second["editions"][0]["inserted_edition"])
        self.assertEqual(db["stock_day"].records, [{"code": "000001", "close": 10}])
        self.assertEqual(db["stock_financial_pit"].records, [{"code": "000001", "value": 20}])
        changed = {**row, "major_code": "69"}
        with self.assertRaises(ValueError):
            module.migrate_pack(db, [(metadata, [changed])])
        self.assertEqual(db["stock_industry_pit"].records, [row])

    def test_incomplete_row_import_does_not_activate_edition(self):
        module = self.migration_module()
        class MissingWrite(MongoCollection):
            def update_one(self, query, update, upsert=False):
                return SimpleNamespace(upserted_id=None)
        db = MongoDatabase(stock_industry_pit=MissingWrite())
        with self.assertRaises(ValueError):
            module.migrate_pack(db, [(stored_edition(), [stored_row()])])
        self.assertEqual(db["stock_industry_editions"].records, [])


if __name__ == "__main__":
    unittest.main()
