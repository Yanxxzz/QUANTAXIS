"""Offline scope, native deadline, unit and isolated-candidate regressions."""
import json
import subprocess
import sys
import time

import pytest

from scripts import axis_stockdb_live_sync as sync


def query():
    return sync.validate_scope("600000", "2024-01-02", "2024-01-10")


def row(**changes):
    result = dict(date=20240102, code="600000", name="Example", open=10, high=11,
                  low=9, close=10.5, pre_close=10, volume=10000, amount=100000,
                  turnover=None, pct_chg=5, is_st=False, total_mv=None, pb=None, pe_ttm=None)
    return {**result, **changes}


def normalized(records=None):
    return sync.normalize_records(records or [row()], query(),
                                  snapshot_sha256="a" * 64, observed_at="2026-10-05T00:00:00Z")


@pytest.mark.parametrize("code,start,end", [
    ("all:", "2024-01-02", "2024-01-10"),
    ("6*", "2024-01-02", "2024-01-10"),
    ("600000,000001", "2024-01-02", "2024-01-10"),
    ("510300", "2024-01-02", "2024-01-10"),
    ("600000", "2024-01-01", "2024-02-01"),
    ("600000", "2019-09-19", "2019-09-20"),
    ("600000", "2026-09-18", "2026-09-19"),
    ("600000", "2024-01-10", "2024-01-02"),
])
def test_scope_cannot_expand_market_or_window(code, start, end):
    with pytest.raises(ValueError):
        sync.validate_scope(code, start, end)


@pytest.mark.parametrize("code", ["920002", "832171", "430001"])
def test_explicit_beijing_candidate_keeps_historical_identity_pending(code):
    q = sync.validate_scope(code, "2025-01-02", "2025-01-10")
    assert q["code"] == code and q["calls"] == 1
    assert q["identity_scope"] == "BJ/NEEQ-prefix historical identity pending"


def test_raw_values_preserved_missing_metadata_not_filled_and_units_mapped():
    bars, units = normalized()
    assert bars[0]["source_record"] == row()
    assert bars[0]["vol"] == 100 and bars[0]["volume_shares"] == 10000
    assert bars[0]["amount"] == 100000
    assert bars[0]["is_st"] is None and bars[0]["source_record"]["is_st"] is False
    assert bars[0]["source_record"]["pb"] is None
    assert bars[0]["research_eligibility"] == "candidate_only"
    assert bars[0]["price_adjustment_status"] == "pending_independent_raw_validation"
    assert units["traded_rows_checked"] == 1


@pytest.mark.parametrize("change", [
    {"code": "000001"}, {"date": 20240111}, {"low": 12},
    {"amount": 10000000}, {"volume": 100}, {"close": None},
    {"open": float("nan")}, {"volume": -1}, {"volume": 1.5},
    {"amount": 0}, {"date": "2024-01-02"},
])
def test_corrupt_stale_and_wrong_unit_rows_block(change):
    with pytest.raises(ValueError):
        normalized([row(**change)])


def test_duplicate_dates_and_missing_projection_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        normalized([row(), row()])
    missing = row()
    del missing["volume"]
    with pytest.raises(ValueError, match="projection"):
        normalized([missing])


def test_zero_activity_is_retained_without_inventing_suspension():
    bars, units = normalized([row(volume=0, amount=0)])
    assert units["zero_activity_rows"] == 1
    assert bars[0]["trade_status"] is None
    assert not bars[0]["security_status_verified"]


def test_empty_response_is_not_calendar_or_membership_evidence():
    bars, units = sync.normalize_records([], query(), snapshot_sha256="a" * 64, observed_at="now")
    assert bars == [] and units["independent_provider_validation"] == "pending"


def test_real_hanging_subprocess_is_killed_at_deadline():
    started = time.monotonic()
    with pytest.raises(sync.ProbeError, match="deadline_exceeded"):
        sync.bounded_probe(query(), deadline=1,
                           command=[sys.executable, "-c", "import time; time.sleep(20)"])
    assert time.monotonic() - started < 8


def test_worker_failure_is_not_retried_or_exposed(monkeypatch):
    calls = []
    def failed(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, b"private error", b"secret")
    monkeypatch.setattr(sync.subprocess, "run", failed)
    with pytest.raises(sync.ProbeError, match="native_subprocess_failed") as exc:
        sync.bounded_probe(query())
    assert len(calls) == 1 and "secret" not in str(exc.value)


def test_sdk_worker_uses_only_projected_single_query(monkeypatch, tmp_path):
    calls = []
    class RD:
        def vals(self, *args):
            calls.append(("vals", args))
            return self
        def get(self, fields):
            calls.append(("get", fields))
            return self
        def do(self):
            calls.append(("do",))
            return [list(row().values())]
    def init(**kwargs):
        calls.append(("init", kwargs))
        return RD()
    monkeypatch.setitem(sys.modules, "stockdb", type("SDK", (), {"init": staticmethod(init)}))
    assert sync.native_query(query(), tmp_path, 20) == [row()]
    assert calls[0][1]["host"] == "127.0.0.1"
    assert calls[1] == ("vals", ("日k", "600000", "20240102<20240110"))
    assert len(calls) == 4


def test_optional_source_nan_is_preserved_as_explicit_tag_not_zero():
    raw = sync.portable_value(row(pb=float("nan"), pe_ttm=float("inf")))
    assert raw["pb"] == {"stockdb_source_nonfinite": "NaN"}
    assert raw["pe_ttm"] == {"stockdb_source_nonfinite": "+Infinity"}
    bars, _ = normalized([raw])
    assert bars[0]["source_record"] == raw
    assert bars[0]["source_nonfinite_fields"] == ["pb", "pe_ttm"]
    assert sync.canonical_bytes(raw)


def test_required_source_nan_remains_explicit_and_blocks_import():
    raw = sync.portable_value(row(close=float("nan")))
    with pytest.raises(ValueError, match="required OHLCV"):
        normalized([raw])


class Collection:
    def __init__(self):
        self.rows, self.indexes = [], []
    def find_one(self, key):
        return next((r for r in self.rows if all(r.get(k) == v for k, v in key.items())), None)
    def create_index(self, fields, **kwargs):
        self.indexes.append((fields, kwargs))
    def insert_one(self, value):
        self.rows.append(dict(value))
    def update_one(self, key, value, **kwargs):
        if not self.find_one(key):
            self.rows.append({**key, **value["$setOnInsert"]})


class Database:
    def __init__(self, name=sync.DATABASE):
        self.name = name
        self.stock_day = Collection()
        self.stockdb_row_receipt = Collection()
        self.panda_axis_sync = Collection()


def test_only_isolated_db_with_immutable_unique_rows_and_scoped_receipts():
    bars, _ = normalized()
    receipt = {"receipt_id": "stockdb_live:example", "research_accepted": False,
               "requested_window_complete": False, "status": "candidate_collected"}
    with pytest.raises(ValueError, match="only enter"):
        sync.import_candidate(Database("quantaxis"), bars, receipt)
    db = Database()
    assert sync.import_candidate(db, bars, receipt) == {"inserted": 1, "duplicates": 0}
    assert sync.import_candidate(db, bars, receipt) == {"inserted": 0, "duplicates": 1}
    assert db.stock_day.indexes[0][1] == {"unique": True}
    assert len(db.stockdb_row_receipt.rows) == 1
    assert not db.panda_axis_sync.rows[0]["research_accepted"]
    revised, _ = normalized([row(close=10.6)])
    with pytest.raises(ValueError, match="no overwrite"):
        sync.import_candidate(db, revised, receipt)
    assert db.stock_day.rows[0]["close"] == 10.5


def test_snapshot_hash_and_receipt_artifacts_are_immutable(tmp_path):
    path = tmp_path / "source.json"
    sha = sync.save_json(path, {"records": [row()]})
    assert sha == sync.save_json(path, {"records": [row()]})
    assert len(sha) == 64 and json.loads(path.read_bytes())["records"][0] == row()
    with pytest.raises(ValueError, match="overwrite"):
        sync.save_json(path, {"records": []})


def test_cli_rejects_alternate_db_remote_and_credential_uris_before_probe(monkeypatch):
    monkeypatch.setattr(sync, "bounded_probe", lambda *a, **k: pytest.fail("probe called"))
    for uri in ("mongodb://127.0.0.1:27018/quantaxis", "mongodb://remote:27018",
                "mongodb://user:password@localhost:27018"):
        with pytest.raises(SystemExit):
            sync.main(["--code", "600000", "--start", "2024-01-02", "--end", "2024-01-10",
                       "--uri", uri])
