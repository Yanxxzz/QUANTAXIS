import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from panda_alpha.team import data_sources as data
from panda_alpha.team.evidence import artifact
from panda_alpha.team.runtime_io import read, write
from panda_alpha.team.host import Host
from test_team_lifecycle import lifecycle, spec


def request(tmp_path, kind="stockdb_market"):
    sdk = tmp_path / "stockdb.pyd"
    sdk.write_text("not executed: offline fixture", encoding="utf-8")
    return {"id": "market-input", "kind": kind, "codes": ["000001", "600006"],
            "start": "2026-01-05", "end": "2026-01-07", "sdk": artifact(sdk),
            "timeout_seconds": 30, "query_timeout_seconds": 3, "akshare_version": "1.19.1"}


def market_captures():
    # Entire middle day absent for both stocks: it must remain in the time axis.
    return [[{"trade_date": "2026-01-05"}, {"trade_date": "2026-01-06"}, {"trade_date": "2026-01-07"}],
            [[20260105, "000001", 2, 3, 1, 2.5, 100, 200, .1, 0],
             [20260107, "000001", 2, 3, 1, 2.5, 100, 200, .1, 0]],
            [[20260105, "600006", 3, 4, 2, 3.5, 100, 300, .1, 0]]]


def test_independent_calendar_preserves_whole_missing_day_and_keys(tmp_path):
    value = data.normalize(tmp_path, request(tmp_path), market_captures())
    grid = pd.read_parquet(tmp_path / "market-grid.parquet")
    assert value["grid_keys"] == 6 and value["missing_keys"] == 3
    missing_day = grid[grid.date == "2026-01-06"]
    assert len(missing_day) == 2 and not missing_day.source_row_present.any()
    assert missing_day[["open", "close", "turnover"]].isna().all().all()
    assert value["historical_backfill_allowed"] is False


@pytest.mark.parametrize("change", [
    {"kind": "arbitrary_command"}, {"codes": ["000001", "000001"]},
    {"id": "../escape"}, {"timeout_seconds": 901}, {"query_timeout_seconds": 91},
    {"start": "2027-01-01"}, {"akshare_version": "latest"},
])
def test_request_has_frozen_bounded_allowlist(tmp_path, change):
    with pytest.raises(ValueError):
        data.validate({"data_requests": [dict(request(tmp_path), **change)]})


def test_financial_snapshot_never_promotes_provider_date_to_historical_pit(tmp_path):
    req = request(tmp_path, "akshare_financial")
    req.update(start="2025-12-31", end="2025-12-31")
    row = {"报告日": "20251231", "类型": "合并期末", "币种": "CNY",
           "公告日期": "20260829", "更新日期": "2026-08-30",
           "在建工程": None, "固定资产净额": "20", "资产总计": "100"}
    summary = data.normalize(tmp_path, req, [[row], [dict(row, 在建工程="3")]])
    records = read(tmp_path / "financial-current.json")
    assert summary["rows"] == 2
    assert all(r["historical_backfill_allowed"] is False for r in records)
    assert records[0]["construction_in_progress"] is None
    assert records[0]["provider_announcement_date_raw"] == "20260829"
    assert "available_at" not in records[0]
    with pytest.raises(ValueError, match="Ambiguous"):
        data.normalize(tmp_path, req, [[row, row], [row]])


def test_capture_recovers_committed_queries_and_reuses_original_deadline(tmp_path):
    req, root = request(tmp_path), tmp_path / "capture"
    calls, interrupt = [], [True]
    values = dict(zip([("calendar", ""), ("stockdb", "000001"), ("stockdb", "600006")], market_captures()))
    def runner(root, request, kind, code, deadline, active):
        path = root / (kind + code + ".json")
        if not path.exists():
            if code == "600006" and interrupt[0]:
                raise InterruptedError("operator stop")
            calls.append((kind, code))
            write(path, values[(kind, code)])
        binding = artifact(path)
        return {"status": "captured", "artifacts": [binding], "data": binding}
    with pytest.raises(InterruptedError):
        data.capture(root, req, time.time() + 300, lambda: True, runner=runner)
    original_bounds = read(root / "bounds.json")
    interrupt[0] = False
    result = data.capture(root, req, time.time() + 600, lambda: True, runner=runner)
    assert result["status"] == "captured" and len(calls) == 3
    assert read(root / "bounds.json") == original_bounds
    assert data.capture(root, req, time.time() + 900, lambda: True,
                        runner=lambda *a: pytest.fail("must not refetch")) == result
    # Changing a completed provider artifact must not silently trigger a fresh fetch.
    (root / "calendar.json").write_text("[]")
    with pytest.raises(ValueError):
        data.capture(root, req, time.time() + 900, lambda: True)


def test_expired_capture_never_renews_its_fetch_budget(tmp_path):
    req, root = request(tmp_path), tmp_path / "capture"
    root.mkdir()
    write(root / "bounds.json", {"request": req, "deadline": time.time() - 1})
    result = data.capture(root, req, time.time() + 900, lambda: True,
                          runner=lambda *a: pytest.fail("expired capture"))
    assert result["status"] == "failed" and "expired" in result["error"]
    assert data.capture(root, req, time.time() + 900, lambda: True) == result


def test_query_recovers_written_answer_without_spawning(tmp_path, monkeypatch):
    req = request(tmp_path)
    target = tmp_path / "queries/calendar-all/1"
    target.mkdir(parents=True)
    write(target / "data.json", market_captures()[0])
    binding = artifact(target / "data.json")
    result = {"status": "captured", "artifacts": [binding], "data": binding}
    write(target / "result.json", result)
    monkeypatch.setattr(data.subprocess, "Popen", lambda *a, **kw: pytest.fail("already captured"))
    assert data.query(tmp_path, req, "calendar", "", time.time() + 10, lambda: True) == result
    assert read(target.parent / "complete.json") == result


def test_query_stop_terminates_owned_child_and_preserves_attempt(tmp_path, monkeypatch):
    req, killed = request(tmp_path), []
    class Process:
        pid = 123
        def poll(self):
            return None
        def wait(self, timeout):
            return 1
    monkeypatch.setattr(data.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(data, "identity", lambda *a: {"pid": 123, "created": 456})
    monkeypatch.setattr(data, "terminate", lambda value: killed.append(value))
    states = iter([True, False])
    with pytest.raises(InterruptedError):
        data.query(tmp_path, req, "calendar", "", time.time() + 30, lambda: next(states))
    assert killed and all(p == {"pid": 123, "created": 456} for p in killed)
    receipt = read(tmp_path / "queries/calendar-all/1/result.json")
    assert receipt["status"] == "interrupted"


def test_host_delivers_frozen_data_receipts_before_model_work(lifecycle, monkeypatch):
    c, registry, _, root = lifecycle
    req = request(root)
    seen = []
    values = dict(zip(data.jobs(req), market_captures()))
    def runner(folder, request, kind, code, deadline, active):
        seen.append((kind, code))
        path = folder / f"{kind}{code}.json"
        write(path, values[(kind, code)])
        binding = artifact(path)
        return {"status": "captured", "data": binding, "artifacts": [binding]}
    monkeypatch.setattr(data, "query", runner)
    host = Host(c, registry, "automatic-data", spec(data_requests=[req]))
    first = host.next()
    assert first["status"] == "work" and len(seen) == 3
    packet = first["packet"]
    assert packet["role"] == "lead"
    item = packet["data_material"]["items"][0]
    assert item["summary"]["missing_keys"] == 3 and item["receipt"]["sha256"]
    assert len(item["artifacts"]) == 2  # Raw files live in the hashed receipt, not prompt flooding.
    assert Host(c, registry, "automatic-data").next() == first and len(seen) == 3
    assert registry.total == 0 and c.ledger.jobs() == []


def test_host_stops_fetch_on_operator_pause_and_cannot_dispatch_failed_data(lifecycle, monkeypatch):
    c, registry, _, root = lifecycle
    req = request(root)
    host = Host(c, registry, "paused-data", spec(data_requests=[req]))
    monkeypatch.setattr(data, "query", lambda *a: pytest.fail("must not query while paused"))
    assert host.next(should_continue=lambda: False)["status"] == "paused"
    assert host.state()["actions"] == 0
    saved = data.folder(c.board, "paused-data", req)
    bounds = read(saved / "bounds.json")
    write(saved / "bounds.json", dict(bounds, deadline=time.time() - 1))
    assert host.next(execution=True)["reason"] == "data_source_failed"
    assert host.state()["actions"] == 0 and c.ledger.jobs() == []
