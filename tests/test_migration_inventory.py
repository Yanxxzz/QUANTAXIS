"""Inventory receipts must never authorize retirement of the active source."""
from collections import Counter
import copy
import unittest

from panda_alpha.data import AxisProvider
from scripts.verify_axis_migration import audit


class ReadOnlyCollection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, query, projection=None):
        def matches(row):
            for key, condition in query.items():
                value = row.get(key)
                if isinstance(condition, dict):
                    if "$in" in condition and value not in condition["$in"]:
                        return False
                    if "$gte" in condition and (value is None or value < condition["$gte"]):
                        return False
                    if "$lte" in condition and (value is None or value > condition["$lte"]):
                        return False
                elif value != condition:
                    return False
            return True
        return [dict(row) for row in self.rows if matches(row)]

    def count_documents(self, query):
        return len(self.find(query))

    def aggregate(self, stages):
        rows = self.find(stages[0]["$match"])
        return [{"_id": source, "rows": count} for source, count in sorted(Counter(r.get("source") for r in rows).items())]

    def update_one(self, *args, **kwargs):
        raise AssertionError("Inventory must not mutate collections")

    insert_one = delete_many = update_one


class ReadOnlyDatabase(dict):
    def __getitem__(self, name):
        return self.get(name, ReadOnlyCollection())


def receipted_database(include_prices=False):
    first, last = "2026-07-06", "2026-07-07"
    receipts = [{"dataset": dataset, "code": "000001", "source": "synthetic", "status": "complete",
                 "start": first, "through": last, "scope": "successful_requested_query; lifecycle_calendar_coverage_audited",
                 "rows": 2, "history_complete": True, "source_missing_dates": [], "suspended_dates": [],
                 "unknown_missing_dates": [], "off_lifecycle_dates": [], "duplicate_dates": 0}
                for dataset in ("stock_day", "stock_adj")]
    receipts.append({"dataset": "trade_calendar", "code": "SSE", "source": "baostock", "status": "complete",
                     "start": first, "through": last, "rows": 2})
    capabilities = [{"capability": name, "status": "verified", "start": first, "end": last,
                     "full_universe_complete": True, "scope": "all_a_historical", "evidence": "old-independent-artifact",
                     "evidence_sha256": "a" * 64} for name in ("all_a", "delisted", "pit_financial", "minute")]
    prices = [{"code": "000001", "date": date, "source": "synthetic", "open": 10, "high": 11,
               "low": 9, "close": 10, "vol": 100, "amount": 1000} for date in (first, last)] if include_prices else []
    return ReadOnlyDatabase({
        "stock_lifecycle": ReadOnlyCollection([{"code": "000001", "source": "synthetic", "ipo_date": "2000-01-01",
                                                 "delisted_date": None, "lifecycle_issues": []}]),
        "panda_axis_sync": ReadOnlyCollection(receipts),
        "panda_axis_validation": ReadOnlyCollection(capabilities),
        "stock_day": ReadOnlyCollection(prices),
        "stock_adj": ReadOnlyCollection([{"code": "000001", "date": "2000-01-01", "adj": 1, "source": "synthetic"}] if include_prices else []),
        "trade_calendar": ReadOnlyCollection([{"date": date, "exchange": "SSE", "source": "baostock_trade_dates"}
                                               for date in (first, last)] if include_prices else []),
    }), first, last


class MigrationInventoryTests(unittest.TestCase):
    def test_empty_database_with_old_success_receipts_and_capabilities_cannot_pass(self):
        db, first, last = receipted_database()
        result = audit(AxisProvider(db=db), first, last)
        self.assertEqual(set(result["capabilities"].values()), {"verified"})
        self.assertEqual(result["raw_queries"]["completed_count"], 1)
        self.assertEqual(result["stored_collections"]["stock_day"], 0)
        self.assertEqual(result["stored_collections"]["stock_adj"], 0)
        self.assertEqual(result["stored_collections"]["trade_calendar"], 0)
        self.assertEqual(result["raw_price_rows_by_source_in_window"], [])
        self.assertFalse(result["migration"]["can_retire_legacy"])
        self.assertIn("inventory", result["status"].lower())
        self.assertNotEqual(result["status"], "full_migration_accepted")

    def test_even_populated_inventory_is_read_only_and_not_full_panel_acceptance(self):
        db, first, last = receipted_database(include_prices=True)
        original = {name: copy.deepcopy(collection.rows) for name, collection in db.items()}
        result = audit(AxisProvider(db=db), first, last)
        self.assertEqual(result["stored_collections"]["stock_day"], 2)
        self.assertEqual(result["adjustment_ready_codes"], ["000001"])
        self.assertFalse(result["migration"]["can_retire_legacy"])
        self.assertIn("inventory", result["status"].lower())
        self.assertTrue(all(v == 0 for v in result["permissions"].values()))
        self.assertEqual(original, {name: collection.rows for name, collection in db.items()})


if __name__ == "__main__":
    unittest.main()
