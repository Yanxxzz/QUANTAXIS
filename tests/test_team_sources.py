from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
import json
from pathlib import Path

import pytest

from panda_alpha.team import public_docs, sources
from panda_alpha.team.contracts import canonical
from panda_alpha.team.evidence import artifact
from panda_alpha.team.host import Host
from test_team_lifecycle import lifecycle, settled, close, result_message, spec


def intake(env, task, result, **changes):
    _, _, _, root = env
    path = root / "new-source.txt"
    path.write_text("Official document describes report period; it does not bind native PIT. 中文。", encoding="utf-8")
    return dict({"source_id": "period-doc", "request_hash": task["request_hash"],
                 "prior_decision_event_id": "close", "target_event_id": result["event_id"],
                 "claim_delta": "Report period documented, native historical availability unresolved",
                 "kind": "official_document", "origin": "https://www.pandaaiquant.com/docs/factor-development",
                 "observed_at": datetime.now(timezone.utc).isoformat(),
                 "artifact": artifact(path), "limitations": ["Document is not native input evidence"]}, **changes)


def test_source_snapshot_delivery_reopen_and_scientific_closure(lifecycle):
    c, registry, _, root = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    jobs, total, authority = deepcopy(c.ledger.jobs()), registry.total, c.board.mode()
    value = intake(lifecycle, task, result)
    receipt = sources.submit(c.board, task["task_id"], value)
    assert c.board.task(task["task_id"]) == task
    assert receipt["status"] == "queued"
    Path(value["artifact"]["path"]).unlink()
    assert sources.submit(c.board, task["task_id"], value) == receipt
    host = Host(c, registry, "source-review", spec(seed_tasks=[task["task_id"]]))
    packet = host.next()["packet"]
    assert packet["action"] == "result_review"
    assert "report period" in packet["source_material"]["items"][0]["text"]
    assert packet["delivery_observation"]["observation"]["synthetic"]
    assert packet == Host(c, registry, "source-review").next()["packet"]
    message = result_message(c.board.task(task["task_id"]), result)
    message["artifacts"] = message["artifacts"] + [receipt["artifact"]]
    message["claim"] = "New period documentation does not resolve native inputs or clock"
    host.respond(packet["work_id"], {"work_id": packet["work_id"], "output": message})
    conclusion = host.next()["packet"]
    assert conclusion["action"] == "conclude"
    host.respond(conclusion["work_id"], {"work_id": conclusion["work_id"], "output": {
        "decision": "confirm", "reason": "Only capability; retain unresolved native source"}})
    assert registry.total == total and c.ledger.jobs() == jobs and c.board.mode() == authority
    assert len([e for e in c.board.events(task["task_id"]) if e["kind"] == "evidence_review_requested"]) == 1
    assert registry.reconciled_memory()["registry_constraints"]["source_pending"]
    report = sources.readiness(c)
    assert report["funding_authorization"] is False
    assert report["tasks"][0]["source_status"] == "pending"


def test_no_new_review_for_renamed_unchanged_content_and_mutated_ids(lifecycle):
    c, _, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    value = intake(lifecycle, task, result)
    sources.submit(c.board, task["task_id"], value)
    with pytest.raises(ValueError, match="different content"):
        sources.submit(c.board, task["task_id"], dict(value, claim_delta="changed"))
    with pytest.raises(ValueError, match="Unchanged source"):
        sources.submit(c.board, task["task_id"], dict(value, source_id="another-name"))
    assert len(sources.receipts(c.board, task["task_id"])) == 1


def test_legacy_result_gains_cache_observation_without_changing_original(lifecycle, monkeypatch):
    c, registry, _, _ = lifecycle
    original_event = c.board.event
    def old_event(task_id, actor, kind, payload, event_id=None):
        if kind == "result":
            payload = deepcopy(payload)
            binding = payload.pop("evidence_contract")
            payload["artifacts"].remove(binding)
        return original_event(task_id, actor, kind, payload, event_id)
    monkeypatch.setattr(c.board, "event", old_event)
    task, result = settled(lifecycle)
    before = deepcopy(result)
    jobs = deepcopy(c.ledger.jobs())
    packet = Host(c, registry, "legacy", spec(seed_tasks=[task["task_id"]])).next()["packet"]
    assert packet["delivery_observation"]["observation"]["run_id"] == result["payload"]["run_id"]
    assert c.board.events(task["task_id"])[-1] == before
    assert c.ledger.jobs() == jobs


def test_interruption_after_reopen_commit_does_not_repeat_it(lifecycle, monkeypatch):
    c, registry, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    sources.submit(c.board, task["task_id"], intake(lifecycle, task, result))
    real = c.board.reopen_evidence
    def interrupted(*args):
        real(*args)
        raise RuntimeError("power loss after commit")
    monkeypatch.setattr(c.board, "reopen_evidence", interrupted)
    host = Host(c, registry, "recover-intake", spec(seed_tasks=[task["task_id"]]))
    with pytest.raises(RuntimeError, match="power loss"):
        host.next()
    monkeypatch.setattr(c.board, "reopen_evidence", real)
    packet = Host(c, registry, "recover-intake").next()["packet"]
    assert packet["action"] == "result_review" and packet["version"] == task["version"] + 1
    assert len([e for e in c.board.events(task["task_id"]) if e["kind"] == "evidence_review_requested"]) == 1


def test_active_packet_and_unselected_or_expired_sessions_do_not_consume_source(lifecycle):
    c, registry, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    host = Host(c, registry, "live", spec(seed_tasks=[task["task_id"]]))
    pending = host.next()["packet"]
    sources.submit(c.board, task["task_id"], intake(lifecycle, task, result))
    assert host.next()["packet"] == pending
    assert c.board.task(task["task_id"]) == task
    Host(c, registry, "not-owned", spec()).next()
    expired = Host(c, registry, "expired-intake", spec(seed_tasks=[task["task_id"]],
        deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    assert expired.next()["reason"] == "deadline"
    assert sources.receipts(c.board, task["task_id"])[0]["status"] == "queued"


def test_stale_closure_and_corrupted_snapshot_stop_in_intake(lifecycle):
    c, registry, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    value = intake(lifecycle, task, result, prior_decision_event_id="missing")
    receipt = sources.submit(c.board, task["task_id"], value)
    Host(c, registry, "blocked-intake", spec(seed_tasks=[task["task_id"]])).next()
    assert sources.receipts(c.board, task["task_id"])[0]["status"] == "blocked"
    assert c.board.task(task["task_id"]) == task
    repaired = dict(value, source_id="corrected-target", prior_decision_event_id="close")
    sources.submit(c.board, task["task_id"], repaired)
    Path(receipt["artifact"]["path"]).write_text("corrupted snapshot", encoding="utf-8")
    sources.apply_pending(c.board, {task["task_id"]})
    assert all(r["status"] == "blocked" for r in sources.receipts(c.board, task["task_id"]))
    assert c.board.task(task["task_id"]) == task


def test_document_capture_only_one_public_get_and_immutable_output(tmp_path, monkeypatch):
    raw = b'<html><nav>unrelated</nav><article><h1>Guide</h1><script>secret()</script><p>' + b'Explicit public content. ' * 10 + b'</p></article></html>'
    class Response:
        status = 200
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return raw[:n]
    class Opener:
        def open(self, request, timeout):
            assert request.method == "GET" and request.data is None and timeout == 20
            assert not any(k.lower() in {"cookie", "authorization"} for k in request.headers)
            return Response()
    monkeypatch.setattr(public_docs, "build_opener", lambda handler: Opener())
    result = public_docs.capture("https://www.pandaaiquant.com/docs/factor-development", tmp_path / "capture")
    text = Path(result["artifact"]["path"]).read_text(encoding="utf-8")
    assert "secret" not in text and "unrelated" not in text and "Explicit public content" in text
    assert result["followed_links"] == 0
    with pytest.raises(ValueError, match="new capture directory"):
        public_docs.capture(result["origin"], tmp_path / "capture")
    with pytest.raises(ValueError, match="redirect refused"):
        public_docs.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_bounded_host_collects_explicit_document_once_then_reviews(lifecycle, monkeypatch):
    c, registry, _, root = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    value = intake(lifecycle, task, result)
    calls = []
    def capture(url, output, timeout):
        calls.append(url)
        assert timeout <= 20
        return {k: value[k] for k in ("origin", "observed_at", "kind", "artifact", "limitations")}
    monkeypatch.setattr(public_docs, "capture", capture)
    watch = {"task_id": task["task_id"], "url": value["origin"], "claim_delta": value["claim_delta"]}
    settings = spec(seed_tasks=[task["task_id"]], source_watches=[watch])
    host = Host(c, registry, "doc-watch", settings)
    packet = host.next()["packet"]
    assert packet["action"] == "result_review" and len(packet["source_material"]["items"]) == 1
    assert Host(c, registry, "doc-watch").next()["packet"] == packet
    assert calls == [watch["url"]]
    assert sources.readiness(c)["source_watches"][0]["status"] == "queued"


def test_watch_unchanged_and_failure_are_bounded_without_reopening(lifecycle, monkeypatch):
    c, registry, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    value = intake(lifecycle, task, result)
    sources.submit(c.board, task["task_id"], value)
    watch = {"task_id": task["task_id"], "url": value["origin"], "claim_delta": value["claim_delta"]}
    settings = spec(seed_tasks=[task["task_id"]], source_watches=[watch])
    def capture(*args, **kw):
        return {k: value[k] for k in ("origin", "observed_at", "kind", "artifact", "limitations")}
    monkeypatch.setattr(public_docs, "capture", capture)
    sources.refresh_watches(c.board, "unchanged", settings)
    assert sources.readiness(c)["source_watches"][-1]["status"] == "unchanged"
    attempts = []
    def fail(*args, **kw):
        attempts.append(True)
        raise OSError("Document unavailable")
    monkeypatch.setattr(public_docs, "capture", fail)
    sources.refresh_watches(c.board, "failure", settings)
    sources.refresh_watches(c.board, "failure", settings)
    assert attempts == [True]
    assert c.board.task(task["task_id"]) == task
    assert sources.readiness(c)["source_watches"][-1]["status"] == "attention"
    with pytest.raises(ValueError, match="uniquely"):
        Host(c, registry, "bad-watch", spec(source_watches=[watch]))


def test_watch_recovery_after_intake_commit_reuses_capture(lifecycle, monkeypatch):
    c, _, _, _ = lifecycle
    task, result = settled(lifecycle)
    task = close(lifecycle, task, result)
    value = intake(lifecycle, task, result)
    watch = {"task_id": task["task_id"], "url": value["origin"], "claim_delta": value["claim_delta"]}
    settings = spec(seed_tasks=[task["task_id"]], source_watches=[watch])
    calls = []
    def capture(url, output, **kw):
        calls.append(url)
        output.mkdir(parents=True)
        receipt = {k: value[k] for k in ("origin", "observed_at", "kind", "artifact", "limitations")}
        (output / "capture.json").write_text(canonical(receipt), encoding="utf-8")
        return receipt
    monkeypatch.setattr(public_docs, "capture", capture)
    original = c.board.event
    def interrupted(task_id, actor, kind, payload, event_id=None):
        if kind == "source_watch_checked":
            raise RuntimeError("interrupt after intake")
        return original(task_id, actor, kind, payload, event_id)
    monkeypatch.setattr(c.board, "event", interrupted)
    with pytest.raises(RuntimeError, match="interrupt"):
        sources.refresh_watches(c.board, "recover-watch", settings)
    monkeypatch.setattr(c.board, "event", original)
    sources.refresh_watches(c.board, "recover-watch", settings)
    assert calls == [watch["url"]]
    assert len(sources.receipts(c.board, task["task_id"])) == 1


@pytest.mark.parametrize("url", ["https://www.pandaaiquant.com/api/run", "https://www.pandaaiquant.com/docs/api?id=25",
    "https://www.pandaaiquant.com/docs/factor-development?token=private", "https://example.com/docs/a",
    "http://www.pandaaiquant.com/docs/a", "https://user:pass@www.pandaaiquant.com/docs/a"])
def test_capture_rejects_account_endpoints_before_network(tmp_path, monkeypatch, url):
    monkeypatch.setattr(public_docs, "build_opener", lambda *args: pytest.fail("No request allowed"))
    with pytest.raises(ValueError, match="Select one"):
        public_docs.capture(url, tmp_path / "capture")
