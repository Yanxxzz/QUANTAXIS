import unittest

from scripts.repair_axis_adjustments import audit_hfq_window, blocked_report


def bar(date, **extra):
    return {"date": date, "open": 10., "high": 11., "low": 9., "close": 10.,
            "trade_status": "1", **extra}


class NativeRepairTests(unittest.TestCase):
    def test_complete_relative_prices_do_not_claim_absolute_ipo_baseline(self):
        audit = audit_hfq_window([bar("2024-01-02"), bar("2024-01-03", trade_status="0")],
                                 [bar("2024-01-02", valid_ohlc=True)])
        self.assertEqual(audit["status"], "complete")
        self.assertEqual(audit["absolute_ipo_anchor"], "pending")
        self.assertEqual(audit["qfq_anchor"], "sample_end_only")

    def test_missing_invalid_or_source_suspended_hfq_prices_keep_scope_pending(self):
        raw = [bar("2024-01-02"), bar("2024-01-03")]
        for second in [bar("2024-01-03", valid_ohlc=False), bar("2024-01-03", valid_ohlc=True, trade_status="0")]:
            audit = audit_hfq_window(raw, [bar("2024-01-02", valid_ohlc=True), second])
            self.assertEqual(audit["status"], "partial")
            self.assertEqual(audit["missing_hfq_dates"], ["2024-01-03"])
        self.assertEqual(audit_hfq_window([], [bar("2024-01-02", valid_ohlc=True)])["status"], "partial")

    def test_persisted_blacklist_uses_a_zero_network_waiting_checkpoint(self):
        report = blocked_report({"status": "blocked", "error_code": "10001011"}, ["001872"], "2024-01-02", "2024-01-03")
        self.assertEqual(report["network_attempts"], 0)
        self.assertEqual(report["status"], "source_blocked_waiting")


if __name__ == "__main__":
    unittest.main()
