import unittest
from types import SimpleNamespace
from unittest.mock import patch

from panda_alpha.universe import (normalize_lifecycles, window_lifecycles, members_on,
                                  lifecycle_sessions, lifecycle_window)
from scripts.axis_market_sync import (audit_raw_rows, receipt_covers, resumable_code, summarize,
                                     login_source, storage_identity, fetch_native_hfq_panel)
from scripts.axis_market_sync import read_baostock_packet
from scripts.axis_market_sync import SourceBlockedError, source_blocked_error, receipt_after_failure


def basic(code, ipo="2000-01-01", out="", status="1", kind="1"):
    return {"code": code, "code_name": "source name", "ipoDate": ipo,
            "outDate": out, "status": status, "type": kind}


class UniverseTests(unittest.TestCase):
    def test_inactive_stocks_are_retained_while_indices_b_shares_and_bj_are_excluded(self):
        result = normalize_lifecycles([basic("sh.600001", out="2024-01-03", status="0"),
            basic("sz.300001"), basic("sh.900001"), basic("sh.000001", kind="2"), basic("bj.920002")])
        self.assertEqual([r["code"] for r in result["records"]], ["300001", "600001"])
        self.assertEqual(result["inactive_rows"], 1)
        self.assertEqual(result["records"][1]["delisted_date"], "2024-01-03")
        self.assertFalse(result["records"][0]["membership_pit"])

    def test_effective_membership_is_ipo_inclusive_and_delisting_exclusive(self):
        records = normalize_lifecycles([basic("sh.600001", ipo="2024-01-02", out="2024-01-04", status="0"),
                                        basic("sz.000001", ipo="2024-01-05")])["records"]
        self.assertEqual(members_on(records, "2024-01-01"), [])
        self.assertEqual(members_on(records, "2024-01-02"), ["600001"])
        self.assertEqual(members_on(records, "2024-01-04"), [])
        self.assertEqual([r["code"] for r in window_lifecycles(records, "2024-01-02", "2024-01-04")], ["600001"])
        self.assertEqual(lifecycle_window(records[1], "2020-01-01", "2026-01-01"), ("2024-01-02", "2024-01-03"))
        self.assertEqual(lifecycle_sessions(records[1], ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
                                           "2024-01-01", "2024-01-04"), ["2024-01-02", "2024-01-03"])

    def test_missing_lifecycle_dates_remain_unresolved_instead_of_silent_survivor_filter(self):
        result = normalize_lifecycles([basic("sh.600001", ipo="", status="0")])
        self.assertEqual(len(window_lifecycles(result["records"], "2020-01-01", "2024-01-01")), 1)
        self.assertEqual(len(result["issues"]), 2)
        with self.assertRaises(ValueError):
            members_on(result["records"], "2024-01-01")

    def test_universe_hash_ignores_snapshot_timestamp(self):
        rows = [basic("sh.600001")]
        self.assertEqual(normalize_lifecycles(rows, "first")["universe_hash"],
                         normalize_lifecycles(rows, "second")["universe_hash"])


class ResumeAndAuditTests(unittest.TestCase):
    def test_persisted_blacklist_stops_a_queued_code_before_any_dataset_request(self):
        import scripts.axis_market_sync as sync
        saved = []
        db = SimpleNamespace(panda_axis_market_sync=SimpleNamespace(update_one=lambda *a, **k: saved.append(a)))
        context = {"start": "2024-01-02", "end": "2024-01-03", "sessions": ["2024-01-02", "2024-01-03"],
                   "retries": 10, "run_id": "run"}
        record = {"code": "000001", "ipo_date": "2000-01-01", "delisted_date": None,
                  "listing_status": "1", "lifecycle_issues": []}
        with patch.object(sync, "_db", db), patch.object(sync, "_context", context), \
             patch.object(sync, "ensure_source_available", side_effect=SourceBlockedError("source circuit open")), \
             patch.object(sync, "record_source_block"), patch.object(sync, "_receipt") as receipt, \
             patch.object(sync, "fetch_baostock_bars") as query, patch.object(sync, "login_source") as login:
            result = sync._sync_code(record)
        self.assertTrue(result["source_blocked"])
        self.assertFalse(result["transport_complete"])
        self.assertEqual(result["datasets"], {})
        self.assertEqual(result["failures"][0]["dataset"], "source")
        query.assert_not_called()
        login.assert_not_called()
        receipt.assert_called_once()
        self.assertTrue(receipt.call_args.kwargs["source_blocked"])
        self.assertEqual(len(saved), 1)

    def test_blacklist_circuit_stops_login_on_first_result_without_logout_or_retry(self):
        class API:
            calls = 0
            def login(self):
                self.calls += 1
                class Result:
                    error_code, error_msg = "10001011", "blacklisted"
                return Result()
            def logout(self):
                raise AssertionError("Must not contact a blocked source again")
        api = API()
        with self.assertRaises(SourceBlockedError):
            login_source(api, sleep=lambda _: self.fail("Must not retry a blacklist"))
        self.assertEqual(api.calls, 1)
        self.assertTrue(source_blocked_error("RuntimeError: BaoStock 10001011 rejected"))

    def test_source_blocked_attempt_is_not_classified_as_missing_security(self):
        results = {"000001": {"source": "baostock", "transport_complete": False,
                               "source_blocked": True, "failures": [{"dataset": "source"}]}}
        report = summarize(results, 5452, "now", "run")
        self.assertEqual(report["failed_codes"], 0)
        self.assertEqual(report["source_blocked_attempts"], 1)
        self.assertEqual(report["remaining_codes"], 5452)

    def test_failed_retry_preserves_real_rows_window_source_and_factor_evidence(self):
        previous = {"dataset": "stock_adj", "code": "000001", "status": "partial", "rows": 10,
                    "source": "baostock", "start": "2019-09-20", "through": "2026-09-18", "history_complete": False}
        failure = {"dataset": "stock_adj", "code": "000001", "status": "failed", "rows": 0,
                   "source": "baostock", "synced_at": "now", "error": "source blocked"}
        preserved = receipt_after_failure(previous, failure)
        self.assertEqual(preserved["rows"], 10)
        self.assertEqual(preserved["status"], "partial")
        self.assertEqual(preserved["through"], "2026-09-18")
        self.assertFalse(preserved["history_complete"])
        self.assertEqual(preserved["latest_attempt_failure"], failure)
        self.assertEqual(receipt_after_failure(None, failure), failure)

    def test_baostock_socket_eof_never_becomes_an_infinite_loop_or_empty_success(self):
        class Connection:
            def __init__(self, chunks):
                self.chunks = iter(chunks)
            def recv(self, size):
                return next(self.chunks)
        with self.assertRaises(ConnectionError):
            read_baostock_packet(Connection([b"partial", b""]))
        message = b"actual_response<![CDATA[]]>\n"
        self.assertEqual(read_baostock_packet(Connection([message[:12], message[12:]])), message)
        with self.assertRaises(ValueError):
            read_baostock_packet(Connection([message]), max_bytes=4)

    def test_native_hfq_is_source_prices_not_a_fabricated_ipo_factor(self):
        class API:
            def query_history_k_data_plus(self, *args, **kwargs):
                self.flag = kwargs["adjustflag"]
                class Result:
                    error_code, error_msg = "0", ""
                    fields = ["date", "code", "open", "high", "low", "close", "tradestatus"]
                    count = 0
                    def next(self):
                        self.count += 1
                        return self.count == 1
                    def get_row_data(self):
                        return ["2024-01-02", "sz.001872", "35", "36", "34", "35", "1"]
                return Result()
        api = API()
        panel = fetch_native_hfq_panel(api, "001872", "2024-01-02", "2024-01-02")
        self.assertEqual(api.flag, "1")
        self.assertEqual(panel[0]["close"], 35)
        self.assertEqual(panel[0]["absolute_ipo_anchor"], "pending")
        self.assertTrue(panel[0]["valid_ohlc"])

    def test_resume_store_identity_changes_with_mongo_target_without_exposing_uri(self):
        self.assertNotEqual(storage_identity("mongodb://host:27018", "quantaxis"),
                            storage_identity("mongodb://host:27018", "another"))
        self.assertNotIn("mongodb", storage_identity("mongodb://user:secret@host", "quantaxis"))

    def test_transient_login_failure_is_retried_without_poisoning_process_pool(self):
        class API:
            calls = 0
            def login(self):
                self.calls += 1
                class Result:
                    error_code = "0" if self.calls == 2 else "10002007"
                    error_msg = "temporary timeout"
                return Result()
            def logout(self):
                pass
        api = API()
        login_source(api, sleep=lambda _: None)
        self.assertEqual(api.calls, 2)

    def test_successful_partial_quality_response_is_resumable_but_failures_retry(self):
        self.assertTrue(resumable_code({"source": "baostock", "transport_complete": True, "data_acceptance": "pending"}))
        self.assertFalse(resumable_code({"source": "baostock", "transport_complete": False}))
        self.assertFalse(resumable_code({"source": "another", "transport_complete": True}))

    def test_reuse_requires_same_source_and_requested_range_and_verified_factor_history(self):
        receipt = {"source": "baostock", "start": "2019-01-01", "through": "2026-09-18",
                   "status": "partial", "scope": "successful_requested_query; audit"}
        self.assertTrue(receipt_covers(receipt, "2021-01-01", "2026-09-18", "stock_day"))
        self.assertFalse(receipt_covers({**receipt, "source": "tdx"}, "2021-01-01", "2026-09-18", "stock_day"))
        self.assertFalse(receipt_covers(receipt, "2018-01-01", "2026-09-18", "stock_day"))
        self.assertFalse(receipt_covers(receipt, "2019-01-01", "2026-09-18", "stock_adj"))
        self.assertTrue(receipt_covers({**receipt, "history_complete": True}, "2019-01-01", "2026-09-18", "stock_adj"))

    def test_absent_source_dates_suspend_and_unknown_blank_are_separate(self):
        rows = [{"date": "2024-01-02", "trade_status": "1", "missing_numeric_fields": []},
                {"date": "2024-01-03", "trade_status": "0", "missing_numeric_fields": ["volume"]},
                {"date": "2024-01-04", "trade_status": "1", "missing_numeric_fields": ["amount"]}]
        audit = audit_raw_rows(rows, ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        self.assertEqual(audit["source_missing_dates"], ["2024-01-05"])
        self.assertEqual(audit["suspended_dates"], ["2024-01-03"])
        self.assertEqual(audit["unknown_missing_dates"], ["2024-01-04"])
        self.assertEqual(audit["priced_tradable_rows"], 1)
        self.assertEqual(audit["raw_coverage"], 0.75)

    def test_progress_never_claims_legacy_retirement_on_transport_completion(self):
        result = {"000001": {"source": "baostock", "transport_complete": True, "listing_status": "0",
                              "datasets": {"stock_day": {"raw_rows": 12}, "stock_adj": {"rows": 1, "history_complete": True}}}}
        report = summarize(result, 1, "now", "run")
        self.assertEqual(report["inactive_codes_completed"], 1)
        self.assertEqual(report["status"], "downloaded_source_scope_requires_acceptance")
        self.assertFalse(report["migration"]["can_retire_legacy"])


if __name__ == "__main__":
    unittest.main()
