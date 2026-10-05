import importlib.util
from pathlib import Path
import struct
import unittest

import pandas as pd

from panda_alpha.data import AxisProvider, PendingDataError, adjust_prices, adjust_with_factors, migration_gate


class FakeCollection:
    def __init__(self, records=()):
        self.records = list(records)

    def find(self, query, projection=None):
        def match(row):
            for key, condition in query.items():
                value = row.get(key)
                if isinstance(condition, dict):
                    for op, limit in condition.items():
                        if op == "$in" and value not in limit:
                            return False
                        if op == "$gte" and (value is None or value < limit):
                            return False
                        if op == "$lte" and (value is None or value > limit):
                            return False
                elif value != condition:
                    return False
            return True
        return [dict(r) for r in self.records if match(r)]


class FakeDatabase(dict):
    def __getitem__(self, key):
        return self.setdefault(key, FakeCollection())


def bar(date, code="000001", close=10):
    return {"date": date, "code": code, "open": close, "high": close + 1,
            "low": close - 1, "close": close, "vol": 100, "amount": 1000}


def action(date, **kwargs):
    return {"date": date, "code": "000001", "source": "tdx", "category": 1, "fenhong": 0,
            "peigu": 0, "peigujia": 0, "songzhuangu": 0, **kwargs}


class AxisDataTests(unittest.TestCase):
    def database(self):
        return FakeDatabase({
            "stock_day": FakeCollection([bar("2024-01-02"), bar("2024-01-03", close=9)]),
            "stock_xdxr": FakeCollection([action("2024-01-03", fenhong=10)]),
            "panda_axis_sync": FakeCollection([{"dataset": "stock_xdxr", "code": "000001",
                                                "source": "tdx", "status": "complete", "through": "2024-01-05"}]),
            "stock_list": FakeCollection([{"code": "000001"}]),
        })

    def test_cash_dividend_qfq_preserves_raw_volume_and_dates(self):
        result = AxisProvider(db=self.database()).daily(["000001.SZ"], "2024-01-02", "2024-01-03",
                                                       expected_dates=["2024-01-02", "2024-01-03"])
        self.assertEqual(result.frame["close"].tolist(), [9, 9])
        self.assertEqual(result.frame["volume"].tolist(), [100, 100])
        self.assertEqual(result.coverage["daily"]["coverage"], 1)
        self.assertFalse(migration_gate(result.coverage)["can_retire_legacy"])

    def test_lifecycle_dates_reconcile_pre_ipo_and_delisted_calendar_days(self):
        db = self.database()
        db["stock_day"] = FakeCollection([bar("2024-01-03")])
        db["stock_lifecycle"] = FakeCollection([{"code": "000001", "ipo_date": "2024-01-03",
                                                  "delisted_date": "2024-01-04", "lifecycle_issues": []}])
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-04", "none",
                                          expected_dates=["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(result.coverage["daily"]["expected_rows"], 1)
        self.assertEqual(result.coverage["daily"]["coverage"], 1)
        self.assertEqual(result.coverage["daily"]["missing_by_code"], {"000001": []})
        self.assertEqual(result.frame["raw_close"].tolist(), [10])
        db["stock_day"].records.append(bar("2024-01-02"))
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-04", "none",
                                          expected_dates=["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(len(result.frame), 1)
        self.assertEqual(result.coverage["daily"]["excluded_rows"][0]["reason"], "off_lifecycle_raw_bar")
        self.assertFalse(migration_gate(result.coverage, require_adjustment=False,
                                       require_all_a=False, require_delisted=False,
                                       require_pit_financial=False)["can_retire_legacy"])

    def test_unverified_or_empty_corporate_actions_cannot_be_assumed_complete(self):
        db = self.database()
        db["panda_axis_sync"] = FakeCollection()
        db["stock_xdxr"] = FakeCollection()
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03")
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03", "none")
        self.assertEqual(result.coverage["xdxr"]["status"], "pending")
        self.assertIsNone(result.coverage["daily"]["coverage"])

    def test_split_rights_hfq_and_nontrading_event_alignment(self):
        frame = pd.DataFrame([bar("2024-01-02"), bar("2024-01-05", close=5)])
        frame["date"] = pd.to_datetime(frame["date"])
        split = pd.DataFrame([action("2024-01-03", songzhuangu=10)])
        self.assertEqual(adjust_prices(frame, split, "qfq")["close"].tolist(), [5, 5])
        self.assertEqual(adjust_prices(frame, split, "hfq")["close"].tolist(), [10, 10])
        rights = pd.DataFrame([action("2024-01-03", peigu=5, peigujia=6)])
        self.assertAlmostEqual(adjust_prices(frame, rights, "qfq")["close"].iloc[0], 130 / 15)

    def test_future_action_is_not_applied_and_missing_sessions_remain_visible(self):
        db = self.database()
        db["stock_xdxr"].records.append(action("2025-01-02", songzhuangu=100))
        db["panda_axis_sync"].records.append({"dataset": "stock_xdxr", "code": "600000",
                                              "status": "complete", "through": "2024-01-05"})
        result = AxisProvider(db=db).daily(["000001", "600000"], "2024-01-02", "2024-01-03",
                                          expected_dates=["2024-01-02", "2024-01-03"])
        self.assertEqual(result.frame["close"].tolist(), [9, 9])
        self.assertEqual(result.coverage["daily"]["coverage"], 0.5)
        self.assertEqual(len(result.coverage["daily"]["missing_by_code"]["600000"]), 2)

    def test_quality_errors_and_unverified_calendar_block_retirement(self):
        db = self.database()
        db["stock_day"].records.extend([bar("2024-01-02"), {**bar("2024-01-03"), "low": 100}])
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03",
                                          expected_dates=["2024-01-02", "2024-01-03"])
        self.assertEqual(result.coverage["daily"]["duplicate_rows"], 2)
        self.assertEqual(result.coverage["daily"]["invalid_rows"], 1)
        self.assertFalse(migration_gate(result.coverage, require_all_a=False, require_delisted=False,
                                        require_pit_financial=False)["can_retire_legacy"])

    def test_source_suspension_and_unknown_blank_bars_remain_missing(self):
        db = self.database()
        db["panda_axis_sync"].records[0]["through"] = "2024-01-06"
        db["stock_day"].records.extend([
            {**bar("2024-01-04"), "vol": None, "amount": None, "trade_status": "0",
             "missing_numeric_fields": ["volume", "amount"]},
            {**bar("2024-01-05"), "vol": None, "trade_status": "1", "missing_numeric_fields": ["volume"]},
            {**bar("2024-01-06"), "trade_status": "0"}])
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-06",
            expected_dates=["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06"])
        self.assertEqual(len(result.frame), 2)
        self.assertEqual(result.coverage["daily"]["invalid_rows"], 3)
        self.assertEqual(result.coverage["daily"]["coverage"], 0.8)
        self.assertEqual(result.coverage["daily"]["tradable_price_coverage"], 0.4)
        self.assertEqual(result.coverage["daily"]["known_suspended_rows"], 2)
        self.assertEqual(result.coverage["daily"]["unexplained_invalid_rows"], 1)
        self.assertEqual([r["reason"] for r in result.coverage["daily"]["excluded_rows"]],
                         ["source_explicit_suspension", "invalid_or_missing_raw_bar", "source_explicit_suspension"])

    def test_scope_acceptance_requires_evidence_and_dates(self):
        db = self.database()
        db["panda_axis_validation"] = FakeCollection([
            {"capability": name, "status": "verified", "start": "2024-01-01", "end": "2024-01-05",
             "evidence": "independent-acceptance-record", "evidence_sha256": "a" * 64,
             "full_universe_complete": True, "scope": "all_a_historical"}
             for name in ["all_a", "delisted", "pit_financial"]])
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03",
                                          expected_dates=["2024-01-02", "2024-01-03"])
        self.assertTrue(migration_gate(result.coverage)["can_retire_legacy"])
        db["panda_axis_validation"].records[0]["scope"] = "sample"
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03",
                                          expected_dates=["2024-01-02", "2024-01-03"])
        self.assertFalse(migration_gate(result.coverage)["can_retire_legacy"])
        self.assertFalse(migration_gate(result.coverage, require_minutes=True)["can_retire_legacy"])

    def test_financial_report_date_is_not_announcement_date(self):
        db = self.database()
        db["financial"] = FakeCollection([
            {"code": "000001", "report_date": "2023-12-31", "profit": 100},
            {"code": "000001", "report_date": "2023-12-31", "ann_date": "2024-04-10", "profit": 200},
        ])
        result = AxisProvider(db=db).financial(["000001"], "2024-01-01", "2024-04-01")
        self.assertTrue(result.frame.empty)
        self.assertEqual(result.coverage["missing_announcement_rows"], 1)

    def test_compact_integer_announcement_date_is_not_epoch_nanoseconds(self):
        db = self.database()
        db["financial"] = FakeCollection([{"code": "000001", "ann_date": 20240410, "profit": 200}])
        result = AxisProvider(db=db).financial(["000001"], "2024-04-01", "2024-04-30")
        self.assertEqual(result.frame["available_at"].tolist(), ["2024-04-10"])
        self.assertEqual(result.coverage["status"], "announcement_timestamp_present")

    def test_unsupported_capital_action_is_pending(self):
        frame = pd.DataFrame([bar("2024-01-02"), bar("2024-01-03")])
        frame["date"] = pd.to_datetime(frame["date"])
        with self.assertRaises(PendingDataError):
            adjust_prices(frame, pd.DataFrame([action("2024-01-03", category=11)]), "qfq")

    def test_cumulative_factors_match_backward_and_never_use_future_fore_factors(self):
        frame = pd.DataFrame([bar("2024-01-02", close=10), bar("2024-01-03", close=5)])
        frame["date"] = pd.to_datetime(frame["date"])
        factors = pd.DataFrame([{"date": "2020-01-01", "adj": 3, "fore_adj": 0.01},
                                {"date": "2024-01-03", "adj": 6, "fore_adj": 0.02},
                                {"date": "2025-01-01", "adj": 600, "fore_adj": 1}])
        qfq = adjust_with_factors(frame, factors, "qfq")
        hfq = adjust_with_factors(frame, factors, "hfq")
        self.assertEqual(qfq["close"].tolist(), [5, 5])
        self.assertEqual(hfq["close"].tolist(), [30, 30])
        self.assertEqual(qfq["factor_date"].dt.strftime("%Y-%m-%d").tolist(), ["2020-01-01", "2024-01-03"])
        self.assertEqual(qfq["vol"].tolist(), [100, 100])
        self.assertEqual(qfq["amount"].tolist(), [1000, 1000])

    def test_missing_predecessor_cannot_assume_ipo_baseline(self):
        frame = pd.DataFrame([bar("2024-01-02")])
        frame["date"] = pd.to_datetime(frame["date"])
        with self.assertRaises(PendingDataError):
            adjust_with_factors(frame, pd.DataFrame([{"date": "2024-01-03", "adj": 2}]), "qfq")
        with self.assertRaises(PendingDataError):
            adjust_with_factors(frame, pd.DataFrame(), "qfq")

    def test_qfq_anchor_uses_requested_end_even_if_final_bar_is_missing(self):
        frame = pd.DataFrame([bar("2024-01-02", close=10)])
        factors = pd.DataFrame([{"date": "2020-01-01", "adj": 1}, {"date": "2024-01-03", "adj": 2},
                                {"date": "2024-01-04", "adj": 200}])
        result = adjust_with_factors(frame, factors, "qfq", asof_end="2024-01-03")
        self.assertEqual(result["close"].tolist(), [5])
        self.assertEqual(result["factor_date"].dt.strftime("%Y-%m-%d").tolist(), ["2020-01-01"])

    def test_adjustment_requires_same_raw_provider_and_preserves_calendar_provenance(self):
        db = self.database()
        for row in db["stock_day"].records:
            row["source"] = "baostock"
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03")
        db["stock_adj"] = FakeCollection([{"code": "000001", "date": "2020-01-01", "adj": 2, "source": "baostock"}])
        db["panda_axis_sync"].records.extend([
            {"dataset": "stock_adj", "code": "000001", "source": "baostock", "history_complete": True,
             "status": "complete", "through": "2024-01-05"},
            {"dataset": "trade_calendar", "code": "SSE", "source": "baostock", "status": "complete",
             "start": "2024-01-01", "through": "2024-01-05"}])
        db["trade_calendar"] = FakeCollection([
            {"date": "2024-01-02", "exchange": "SSE", "source": "baostock_trade_dates"},
            {"date": "2024-01-03", "exchange": "SSE", "source": "baostock_trade_dates"},
            {"date": "2024-01-01", "exchange": "SSE", "source": "tdx_sse_composite"}])
        result = AxisProvider(db=db).daily(["000001"], "2024-01-02", "2024-01-03")
        self.assertEqual(result.coverage["raw_sources"], ["baostock"])
        self.assertEqual(result.coverage["calendar"]["source"], "baostock_trade_dates")
        self.assertEqual(result.coverage["daily"]["coverage"], 1)


class TDxPagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("axis_sync_test", Path(__file__).parents[1] / "scripts" / "axis_sync.py")
        cls.sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.sync)

    def test_pagination_reports_truncation_instead_of_claiming_full_history(self):
        class API:
            def get_security_bars(self, *args):
                return [{"datetime": "2024-01-05 15:00", **bar("2024-01-05")} for _ in range(800)]
        rows, span = self.sync.fetch_bars(API(), "000001", "2023-01-01", "2024-01-05", max_pages=1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(span["truncated"])
        self.assertFalse(span["reached_start"])

    def test_tdx_none_is_transport_failure_not_no_corporate_actions(self):
        class API:
            def get_xdxr_info(self, *args):
                return None
        with self.assertRaises(RuntimeError):
            self.sync.fetch_xdxr(API(), "000001")

    def test_failed_connect_disconnect_does_not_break_host_failover(self):
        class API:
            def disconnect(self):
                raise RuntimeError("disconnect err")
        self.sync.safe_disconnect(API())
        self.assertEqual(self.sync.endpoint("119.147.171.206:443", 7709), ("119.147.171.206", 443))

    def wire_record(self, name, code=b"600000"):
        return struct.pack("<H", 1) + struct.pack("<6sH8s4sBI4s", code, 100, name, b"\x00" * 4, 2, 1234, b"\x00" * 4)

    def test_name_truncation_compat_preserves_code_quote_and_raw_evidence(self):
        raw = "ABC中文文".encode("gbk")[:8]
        records = self.sync.parse_security_list(self.wire_record(raw), lambda x: x / 100)
        self.assertEqual(records[0]["code"], "600000")
        self.assertEqual(records[0]["pre_close"], 12.34)
        self.assertEqual(records[0]["name_raw_hex"], raw.hex())
        self.assertTrue(records[0]["name_truncated"])
        self.assertTrue(records[0]["name"].endswith("\ufffd"))

    def test_name_compat_does_not_mask_corrupt_packets_codes_or_middle_bytes(self):
        for body in [self.wire_record(b"Normal")[:-1], self.wire_record(b"Normal", b"60\xff000")]:
            with self.assertRaises((ValueError, UnicodeDecodeError)):
                self.sync.parse_security_list(body, lambda x: x / 100)
        with self.assertRaises(UnicodeDecodeError):
            self.sync.parse_security_list(self.wire_record(b"A\xffAvalid"), lambda x: x / 100)
        with self.assertRaises(ValueError):
            self.sync.parse_security_list(self.wire_record(b"Normal"), lambda x: float("nan"))

    def result(self, records, final_error="0"):
        class Result:
            error_code, error_msg = "0", "failure"
            def __init__(self):
                self.fields = list(records[0]) if records else []
                self.position = -1
            def next(self):
                self.position += 1
                if self.position >= len(records):
                    self.error_code = final_error
                    return False
                return True
            def get_row_data(self):
                return [records[self.position][f] for f in self.fields]
        return Result()

    def test_baostock_raw_shares_are_converted_to_qa_lots(self):
        result = self.result([{"date": "2024-01-02", "code": "sz.000001", "open": "10", "high": "11",
                               "low": "9", "close": "10", "volume": "12345", "amount": "123450",
                               "tradestatus": "1", "isST": "0"}])
        class API:
            def query_history_k_data_plus(self, *args, **kwargs):
                self.flag = kwargs["adjustflag"]
                return result
        api = API()
        row = self.sync.fetch_baostock_bars(api, "000001", "2024-01-02", "2024-01-02")[0]
        self.assertEqual(api.flag, "3")
        self.assertEqual(row["vol"], 123.45)
        self.assertEqual(row["volume_shares"], 12345)
        self.assertEqual(row["amount"], 123450)

    def test_baostock_blank_suspend_values_preserved_as_null_without_losing_security(self):
        rows = [{"date": date, "code": "sz.002049", "open": "78.8100", "high": "78.8100",
                 "low": "78.8100", "close": "78.8100", "volume": volume, "amount": amount,
                 "tradestatus": status, "isST": "0"}
                for date, volume, amount, status in [("2026-01-05", "", "", "0"),
                                                     ("2026-01-06", "10000", "788100", "1"),
                                                     ("2026-01-07", "", "788100", "1")]]
        result = self.result(rows)
        class API:
            def query_history_k_data_plus(self, *args, **kwargs):
                return result
        parsed = self.sync.fetch_baostock_bars(API(), "002049", "2026-01-05", "2026-01-07")
        self.assertEqual(len(parsed), 3)
        self.assertIsNone(parsed[0]["vol"])
        self.assertIsNone(parsed[0]["amount"])
        self.assertEqual(parsed[0]["data_status"], "suspended")
        self.assertEqual(parsed[1]["vol"], 100)
        self.assertEqual(parsed[2]["data_status"], "unknown_missing")
        self.assertEqual(parsed[2]["missing_numeric_fields"], ["volume"])

    def test_baostock_error_during_iteration_is_not_empty_success(self):
        with self.assertRaises(RuntimeError):
            self.sync.baostock_records(self.result([], final_error="10002007"))

    def test_calendar_holes_are_not_accepted_as_completed_calendar(self):
        result = self.result([{"calendar_date": "2024-01-02", "is_trading_day": "1"}])
        class API:
            def query_trade_dates(self, **kwargs):
                return result
        with self.assertRaises(ValueError):
            self.sync.fetch_baostock_calendar(API(), "2024-01-02", "2024-01-03")

    def test_calendar_receipt_merges_only_complete_overlapping_same_source_ranges(self):
        old = {"dataset": "trade_calendar", "code": "SSE", "status": "complete", "source": "baostock",
               "start": "2021-01-01", "through": "2026-09-18"}
        new = {**old, "start": "2026-01-01"}
        self.assertEqual(self.sync.merge_calendar_receipt(old, new)["start"], "2021-01-01")
        disjoint = {**new, "start": "2027-01-01", "through": "2027-02-01"}
        self.assertEqual(self.sync.merge_calendar_receipt(old, disjoint), disjoint)
        for previous in [{**old, "source": "tdx"}, {**old, "status": "partial"}]:
            self.assertEqual(self.sync.merge_calendar_receipt(previous, new), new)

    def test_adjustment_sync_requires_actual_ipo_factor_baseline(self):
        result = self.result([{"code": "sh.600000", "dividOperateDate": "2000-07-06",
                               "backAdjustFactor": "1.006502", "foreAdjustFactor": "0.07", "adjustFactor": "1.006502"}])
        class API:
            def query_adjust_factor(self, *args, **kwargs):
                self.origin = kwargs["start_date"]
                return result
        api = API()
        rows, evidence = self.sync.fetch_baostock_factors(api, "600000", "2024-01-03", {"ipo_date": "1999-11-10"})
        self.assertEqual(api.origin, "1990-01-01")
        self.assertEqual(rows[0]["adj"], 1.006502)
        self.assertFalse(evidence["history_complete"])


if __name__ == "__main__":
    unittest.main()
