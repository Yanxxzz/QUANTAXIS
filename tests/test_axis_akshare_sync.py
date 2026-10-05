"""Offline source contract, isolation, deadline and durable-receipt regression tests."""
import copy
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from panda_alpha.data import AxisProvider, migration_gate
from scripts import axis_akshare_sync as sync


def source_rows(scale=1):
    return [{"date": "2024-01-02", "open": 10 * scale, "close": 10 * scale,
             "high": 11 * scale, "low": 9 * scale, "volume": 10000, "amount": 100000,
             "turnover": 0.001},
            {"date": "2024-01-03", "open": 11 * scale, "close": 11 * scale,
             "high": 12 * scale, "low": 10 * scale, "volume": 10000, "amount": 110000,
             "turnover": 0.0011}]


def payload(code="600519", start="2024-01-02", end="2024-01-03"):
    return {"source": sync.SOURCE, "akshare_version": "1.19.1", "api": "stock_zh_a_hist_tx",
            "code": code, "start": start, "end": end, "observed_at": "2026-10-05T00:00:00Z",
            "http_evidence": [], "raw": source_rows(), "hfq": source_rows(2)}


def matches(row, query):
    for key, value in query.items():
        actual = row.get(key)
        if isinstance(value, dict):
            for operator, operand in value.items():
                if operator == "$gte" and not actual >= operand:
                    return False
                if operator == "$lte" and not actual <= operand:
                    return False
                if operator == "$in" and actual not in operand:
                    return False
        elif actual != value:
            return False
    return True


class Collection:
    def __init__(self):
        self.rows = []
        self.indices = []
    def find(self, query=None, projection=None):
        return [copy.deepcopy(r) for r in self.rows if matches(r, query or {})]
    def find_one(self, query, projection=None):
        return next(iter(self.find(query)), None)
    def count_documents(self, query):
        return len(self.find(query))
    def insert_one(self, row):
        self.rows.append(copy.deepcopy(row))
    def update_one(self, query, update, upsert=False):
        target = next((r for r in self.rows if matches(r, query)), None)
        if target is None and upsert:
            target = copy.deepcopy(query)
            target.update(copy.deepcopy(update.get("$setOnInsert", {})))
            self.rows.append(target)
        if target is not None:
            target.update(copy.deepcopy(update.get("$set", {})))
    def bulk_write(self, operations, ordered):
        for operation in operations:
            self.update_one(operation._filter, operation._doc, operation._upsert)
    def create_index(self, keys, unique):
        self.indices.append((keys, unique))


class Database:
    def __init__(self):
        self.collections = {}
    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())
    def __getattr__(self, name):
        return self[name]


class NormalizationTests(unittest.TestCase):
    def test_raw_shares_convert_to_qa_lots_while_source_fields_and_cash_are_preserved(self):
        original = source_rows()
        rows = sync.normalize_tencent_frame(original, "600519", "2024-01-02", "2024-01-03")
        self.assertEqual(rows[0]["vol"], 100)
        self.assertEqual(rows[0]["volume_shares"], 10000)
        self.assertEqual(rows[0]["amount"], 100000)
        self.assertEqual(rows[0]["source_original"], original[0])
        self.assertEqual(rows[0]["source"], "akshare_tencent")
        self.assertEqual(rows[0]["exchange"], "SH")
        self.assertEqual(rows[0]["date_stamp"], sync.qa_date_stamp("2024-01-02"))
        self.assertIsNone(rows[0]["trade_status"])
        self.assertIn("required", rows[0]["execution_state"])

    def test_bad_units_blanks_negative_prices_duplicate_and_outside_dates_are_rejected(self):
        cases = []
        for key, value in (("volume", 100), ("amount", 10), ("amount", None), ("close", -1),
                           ("low", 20), ("high", 5), ("volume", float("nan")),
                           ("date", "2024-01-04"), ("date", "2024-01-02T00:00:00")):
            rows = source_rows()
            rows[0][key] = value
            cases.append(rows)
        cases.extend([[], source_rows() + source_rows()[:1]])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(sync.DataQualityError):
                sync.normalize_tencent_frame(rows, "600519", "2024-01-02", "2024-01-03")

    def test_hfq_ohlc_is_validated_without_comparing_adjusted_price_to_raw_cash(self):
        raw = sync.normalize_tencent_frame(source_rows(), "600519", "2024-01-02", "2024-01-03")
        hfq = sync.normalize_tencent_frame(source_rows(2), "600519", "2024-01-02", "2024-01-03", "hfq")
        audit = sync.audit_pair(raw, hfq)
        self.assertEqual(audit["raw_hfq_dates"], 2)
        self.assertEqual(audit["absolute_ipo_anchor"], "pending")
        self.assertEqual(audit["beijing"], "unverified")
        hfq.pop()
        with self.assertRaises(sync.DataQualityError):
            sync.audit_pair(raw, hfq)

    def test_pair_rejects_foreign_source_and_changed_cash_volume(self):
        for field, value in (("source", "baostock"), ("amount", 2), ("volume_shares", 20000)):
            raw = sync.normalize_tencent_frame(source_rows(), "600519", "2024-01-02", "2024-01-03")
            hfq = sync.normalize_tencent_frame(source_rows(2), "600519", "2024-01-02", "2024-01-03", "hfq")
            hfq[0][field] = value
            with self.subTest(field=field), self.assertRaises(sync.DataQualityError):
                sync.audit_pair(raw, hfq)


class HttpAndDeadlineTests(unittest.TestCase):
    def test_worker_deadline_is_passed_to_subprocess_and_never_retried(self):
        with patch.object(sync.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)) as run:
            with self.assertRaises(sync.SourceUnavailable):
                sync.fetch_with_deadline("600519", "2024-01-02", "2024-01-03", deadline=1)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 1)
        self.assertNotIn("mongodb", " ".join(run.call_args.args[0]))

    def test_worker_source_failure_is_propagated_without_retry(self):
        result = SimpleNamespace(returncode=1, stdout=json.dumps({"error_kind": "source_unavailable", "error": "HTTP 429"}))
        with patch.object(sync.subprocess, "run", return_value=result) as run:
            with self.assertRaises(sync.SourceUnavailable):
                sync.fetch_with_deadline("600519", "2024-01-02", "2024-01-03")
        run.assert_called_once()

    def test_guard_disables_retry_redirect_and_stops_on_first_http_refusal(self):
        calls, adapters = [], []
        class Session:
            def __init__(self):
                pass
            def mount(self, scheme, adapter):
                adapters.append((scheme, adapter))
            def request(self, method, url, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(status_code=429, content=b"rate limited")
        module = SimpleNamespace(sessions=SimpleNamespace(Session=Session),
                                 adapters=SimpleNamespace(HTTPAdapter=lambda **kw: kw),
                                 exceptions=SimpleNamespace(RequestException=ConnectionError))
        evidence = sync.install_http_guard(3, module)
        with self.assertRaises(sync.SourceUnavailable):
            Session().request("GET", "https://proxy.finance.qq.com/public", timeout=None, allow_redirects=True)
        self.assertEqual(calls, [{"timeout": 3, "allow_redirects": False}])
        self.assertTrue(all(a[1]["max_retries"] == 0 for a in adapters))
        self.assertEqual(evidence[0]["http_status"], 429)
        with self.assertRaises(sync.SourceUnavailable):
            Session().request("GET", "https://push2his.eastmoney.com/public")
        self.assertEqual(len(calls), 1)

    def test_worker_uses_two_explicit_iso_date_calls_without_accounts(self):
        calls = []
        def api(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(empty=False, to_dict=lambda _: source_rows(2 if kwargs["adjust"] else 1))
        with patch.dict("sys.modules", {"akshare": SimpleNamespace(stock_zh_a_hist_tx=api)}), \
             patch("importlib.metadata.version", return_value="1.19.1"), \
             patch.object(sync, "install_http_guard", return_value=[]):
            result = sync.worker_payload("600519", "2024-01-02", "2024-01-03", 8)
        self.assertEqual([c["adjust"] for c in calls], ["", "hfq"])
        self.assertTrue(all(c["start_date"] == "2024-01-02" and c["timeout"] == 8 for c in calls))
        self.assertEqual(result["akshare_version"], "1.19.1")
        self.assertTrue(all(set(c) == {"symbol", "start_date", "end_date", "adjust", "timeout"} for c in calls))

    def test_worker_stops_before_hfq_after_raw_source_refusal(self):
        calls = []
        def api(**kwargs):
            calls.append(kwargs["adjust"])
            raise sync.SourceUnavailable("Tencent HTTP refused/unavailable: 403")
        with patch.dict("sys.modules", {"akshare": SimpleNamespace(stock_zh_a_hist_tx=api)}), \
             patch("importlib.metadata.version", return_value="1.19.1"), \
             patch.object(sync, "install_http_guard", return_value=[]):
            with self.assertRaises(sync.SourceUnavailable):
                sync.worker_payload("600519", "2024-01-02", "2024-01-03", 8)
        self.assertEqual(calls, [""])

    def test_unaudited_library_version_is_rejected_before_any_api_call(self):
        with patch.dict("sys.modules", {"akshare": None}), \
             patch("importlib.metadata.version", return_value="1.18.1"), \
             patch.object(sync, "install_http_guard", return_value=[]):
            with self.assertRaisesRegex(ValueError, "1.19.1"):
                sync.worker_payload("600519", "2024-01-02", "2024-01-03", 8)


class StorageAndCheckpointTests(unittest.TestCase):
    def test_sync_writes_same_source_prices_and_real_receipts_then_resumes_without_fetch(self):
        db = Database()
        with TemporaryDirectory() as directory:
            first = sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "run", directory,
                                   fetch=lambda *a, **kw: payload())
            self.assertEqual(first["status"], "complete")
            self.assertEqual(db.stock_day.count_documents({}), 2)
            self.assertEqual(db.stock_adjusted_day.count_documents({}), 2)
            self.assertTrue(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-03"))
            with patch.object(sync, "sync_code", side_effect=AssertionError("No remote resume")):
                report, code = sync.run_batch(db, ["600519"], "2024-01-02", "2024-01-03", directory, "run")
            self.assertEqual(code, 0)
            self.assertTrue(report["results"][0]["resumed"])
            db.stock_adjusted_day.rows.pop()
            self.assertFalse(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-03"))

    def test_downloaded_native_hfq_works_in_axis_but_does_not_open_global_migration_gate(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "run", directory,
                           fetch=lambda *a, **kw: payload())
            data = AxisProvider(db=db).daily(["600519"], "2024-01-02", "2024-01-03", "qfq",
                                            expected_dates=["2024-01-02", "2024-01-03"])
        self.assertEqual(list(data.frame.close), [10, 11])
        self.assertEqual(data.coverage["raw_sources"], [sync.SOURCE])
        self.assertEqual(data.coverage["adjustment_evidence"]["status"], "verified")
        self.assertFalse(migration_gate(data.coverage)["can_retire_legacy"])

    def test_failed_retry_preserves_successful_values_receipts_and_artifact(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "run", directory,
                           fetch=lambda *a, **kw: payload())
            old_rows = copy.deepcopy(db.stock_day.rows)
            before = db.panda_axis_sync.find_one({"dataset": "stock_day", "code": "600519"})
            failed = sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "retry", directory,
                                    fetch=lambda *a, **kw: (_ for _ in ()).throw(sync.SourceUnavailable("refused")))
            after = db.panda_axis_sync.find_one({"dataset": "stock_day", "code": "600519"})
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(db.stock_day.rows, old_rows)
            self.assertEqual(after["artifact_sha256"], before["artifact_sha256"])
            self.assertEqual(after["status"], "complete")
            self.assertEqual(after["rows"], 2)
            self.assertIn("latest_attempt_failure", after)
            self.assertTrue(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-03"))

    def test_source_revision_conflict_is_rejected_before_any_dataset_is_written(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "run", directory,
                           fetch=lambda *a, **kw: payload())
            stored = copy.deepcopy(db.stock_day.rows)
            changed = payload()
            changed["raw"] = source_rows(1.1)
            changed["raw"][0]["amount"] *= 1.1
            changed["raw"][1]["amount"] *= 1.1
            changed["hfq"][0]["amount"] *= 1.1
            changed["hfq"][1]["amount"] *= 1.1
            result = sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "retry", directory,
                                    fetch=lambda *a, **kw: changed)
        self.assertEqual(result["status"], "failed")
        self.assertIn("revision", result["error"])
        self.assertEqual(db.stock_day.rows, stored)

    def test_source_failure_stops_undispatched_codes_and_persisted_circuit_prevents_more_calls(self):
        db, called = Database(), []
        def failed(code, *a, **kw):
            called.append(code)
            raise sync.SourceUnavailable("HTTP 403")
        with TemporaryDirectory() as directory:
            report, code = sync.run_batch(db, ["600519", "000001"], "2024-01-02", "2024-01-03",
                                          directory, "run", fetch=failed)
            again, second_code = sync.run_batch(db, ["600519"], "2024-01-02", "2024-01-03",
                                                directory, "run", fetch=failed)
            saved = json.loads((Path(directory) / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(called, ["600519"])
        self.assertEqual((code, second_code), (2, 2))
        self.assertEqual(report["remaining_codes"], 2)
        self.assertEqual(report["undispatched_codes"], ["000001"])
        self.assertEqual(again["network_workers_started"], 0)
        self.assertEqual(saved["status"], "source_blocked_waiting")
        self.assertIsNone(db.panda_axis_sync.find_one({"code": "000001"}))

    def test_local_exception_report_does_not_copy_connection_credentials(self):
        db = Database()
        with TemporaryDirectory() as directory:
            result = sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "run", directory,
                                    fetch=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("mongodb://user:secret@host")))
        self.assertNotIn("secret", json.dumps(result))
        self.assertFalse(result["source_unavailable"])

    def test_disjoint_successful_windows_do_not_certify_the_gap_or_full_history(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "jan", directory,
                           fetch=lambda *a, **kw: payload())
            second = payload(start="2024-02-01", end="2024-02-02")
            for name in ("raw", "hfq"):
                for row, day in zip(second[name], ("2024-02-01", "2024-02-02")):
                    row["date"] = day
            result = sync.sync_code(db, "600519", "2024-02-01", "2024-02-02", "feb", directory,
                                    fetch=lambda *a, **kw: second)
            self.assertEqual(result["status"], "complete")
            current = db.panda_axis_sync.find_one({"dataset": "stock_day", "code": "600519"})
            self.assertEqual((current["start"], current["through"], current["rows"]), ("2024-02-01", "2024-02-02", 2))
            self.assertEqual(db.panda_axis_window_receipts.count_documents({}), 4)
            self.assertTrue(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-03"))
            self.assertFalse(sync.resumable_code(db, "600519", "2024-01-02", "2024-02-02"))
            self.assertEqual(current["all_a"], "pending")

    def test_hfq_storage_failure_retains_old_prices_and_old_successful_scope(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "first", directory,
                           fetch=lambda *a, **kw: payload())
            old_raw = copy.deepcopy(db.stock_day.rows)
            old_hfq = copy.deepcopy(db.stock_adjusted_day.rows)
            extended = payload(end="2024-01-04")
            for name, scale in (("raw", 1), ("hfq", 2)):
                extended[name].append({"date": "2024-01-04", "open": 12 * scale, "close": 12 * scale,
                                       "high": 13 * scale, "low": 11 * scale,
                                       "volume": 10000, "amount": 120000})
            with patch.object(db.stock_adjusted_day, "bulk_write", side_effect=RuntimeError("storage failed")):
                result = sync.sync_code(db, "600519", "2024-01-02", "2024-01-04", "retry", directory,
                                        fetch=lambda *a, **kw: extended)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(db.stock_day.rows[:2], old_raw)
            self.assertEqual(db.stock_adjusted_day.rows, old_hfq)
            for dataset in ("stock_day", "stock_adjusted_day"):
                receipt = db.panda_axis_sync.find_one({"dataset": dataset, "code": "600519"})
                self.assertEqual((receipt["status"], receipt["rows"], receipt["through"]), ("complete", 2, "2024-01-03"))
            self.assertFalse(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-04"))

    def test_repeated_success_does_not_rewrite_accepted_rows_and_tampered_artifact_is_not_resumable(self):
        db = Database()
        with TemporaryDirectory() as directory:
            sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "first", directory,
                           fetch=lambda *a, **kw: payload())
            old = copy.deepcopy(db.stock_day.rows)
            repeated = sync.sync_code(db, "600519", "2024-01-02", "2024-01-03", "repeat", directory,
                                      fetch=lambda *a, **kw: payload())
            self.assertEqual((repeated["new_raw_rows"], repeated["new_hfq_rows"]), (0, 0))
            self.assertEqual(db.stock_day.rows, old)
            receipt = sync.find_receipt(db, "stock_day", "600519", "2024-01-02", "2024-01-03")
            Path(receipt["artifact_path"]).write_text("changed", encoding="utf-8")
            self.assertFalse(sync.resumable_code(db, "600519", "2024-01-02", "2024-01-03"))


class CliTests(unittest.TestCase):
    def args(self, *extra):
        return sync.parse_args(["--source", sync.SOURCE, "--codes", "600519", "--start", "2024-01-02",
                                "--end", "2024-01-03", *extra])

    def test_default_database_is_isolated_and_one_security(self):
        args = self.args()
        self.assertEqual(args.database, "quantaxis_akshare_tencent")
        self.assertEqual(args.max_codes, 1)
        self.assertEqual(sync.tencent_symbol("000001"), "sz000001")

    def test_existing_database_beijing_unbounded_batch_and_bad_deadline_are_rejected(self):
        inputs = [("--database", "quantaxis"), ("--codes", "600519", "000001"),
                  ("--max-codes", "11"), ("--deadline", "1", "--http-timeout", "2"),
                  ("--codes", "920002"), ("--codes", "900001")]
        for extra in inputs:
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.args(*extra)

    def test_backend_must_be_explicit_and_cannot_silently_use_other_sources(self):
        with self.assertRaises(SystemExit):
            sync.parse_args(["--codes", "600519", "--start", "2024-01-02", "--end", "2024-01-03"])
        with self.assertRaises(SystemExit):
            self.args("--source", "baostock")


if __name__ == "__main__":
    unittest.main()
