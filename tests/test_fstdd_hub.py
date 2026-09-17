"""Tests for the FSTDD 8788 coordination hub Slice B."""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_HUB_PATH = Path(__file__).resolve().parents[1] / "tools" / "fstdd_hub.py"
_spec = importlib.util.spec_from_file_location("fstdd_hub", _HUB_PATH)
fstdd_hub = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(fstdd_hub)


@pytest.fixture
def hub(tmp_path):
    db = tmp_path / "hub.sqlite3"
    fstdd_hub.init_db(db)
    fstdd_hub.HubHandler.db_path = db
    server = ThreadingHTTPServer(("127.0.0.1", 0), fstdd_hub.HubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield base
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def call(base, method, path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw or b"{}")


def register(base, node_id="FSTDD005"):
    return call(base, "POST", "/nodes/register", {
        "node_id": node_id,
        "machine_name": "dev-machine",
        "platform": "workbuddy",
        "os": "windows",
        "capabilities": ["python", "fstdd-cli"],
        "ssh_fingerprint": "SHA256:test",
    })


def task_payload(key="create-1"):
    return {
        "idempotency_key": key,
        "parent_change_id": "2026-09-17-distributed-task-coordination",
        "child_change_id": "child-a",
        "kind": "change",
        "summary": "implement hub slice",
        "scope": {"allowed": ["tools/fstdd_hub.py"]},
        "base_git_sha": "abc123",
    }


def test_health_and_node_registration_are_idempotent(hub):
    assert call(hub, "GET", "/health")[0] == 200
    assert register(hub)[0] == 200
    assert register(hub)[0] == 200
    status, body = call(hub, "GET", "/nodes")
    assert status == 200
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["node_id"] == "FSTDD005"


def test_task_create_is_idempotent_and_conflicting_key_rejected(hub):
    register(hub)
    payload = task_payload()
    status, first = call(hub, "POST", "/tasks", payload)
    assert status == 201
    status, second = call(hub, "POST", "/tasks", payload)
    assert status == 201
    assert second == first
    changed = dict(payload, summary="different payload")
    status, body = call(hub, "POST", "/tasks", changed)
    assert status == 400
    assert "idempotency" in body["error"]
    status, body = call(hub, "GET", "/tasks?status=pending")
    assert status == 200
    assert len(body["tasks"]) == 1


def test_claim_is_atomic_and_returns_lease(hub):
    register(hub)
    assert call(hub, "POST", "/tasks", task_payload())[0] == 201
    status, body = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD005", "idempotency_key": "claim-1", "lease_seconds": 30,
    })
    assert status == 200
    assert body["task"]["status"] == "claimed"
    assert body["task"]["owner_node_id"] == "FSTDD005"
    assert body["lease_token"]
    status, empty = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD005", "idempotency_key": "claim-2", "lease_seconds": 30,
    })
    assert status == 204
    assert empty == {}


def test_claim_replay_returns_same_lease(hub):
    register(hub)
    call(hub, "POST", "/tasks", task_payload())
    claim = {"node_id": "FSTDD005", "idempotency_key": "claim-replay", "lease_seconds": 30}
    first = call(hub, "POST", "/tasks/claim", claim)
    second = call(hub, "POST", "/tasks/claim", claim)
    assert first == second


def test_heartbeat_complete_and_old_token_is_rejected(hub):
    register(hub)
    call(hub, "POST", "/tasks", task_payload())
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD005", "idempotency_key": "claim-work", "lease_seconds": 30,
    })
    task_id = claimed["task"]["task_id"]
    token = claimed["lease_token"]
    status, body = call(hub, "POST", f"/tasks/{task_id}/heartbeat", {
        "node_id": "FSTDD005", "lease_token": token, "lease_seconds": 30,
    })
    assert status == 200
    assert body["status"] == "running"
    complete = {
        "node_id": "FSTDD005", "lease_token": token, "idempotency_key": "complete-1",
        "result_git_sha": "def456", "result_ref": "task/task-1", "result": {"report": "test-report.md"},
    }
    status, body = call(hub, "POST", f"/tasks/{task_id}/complete", complete)
    assert status == 200
    assert body["status"] == "done"
    status, body = call(hub, "POST", f"/tasks/{task_id}/complete", dict(complete, idempotency_key="complete-2"))
    assert status == 400
    assert "lease" in body["error"] or "owner" in body["error"]


def test_expired_lease_is_reclaimed_and_old_token_fails(hub):
    register(hub)
    call(hub, "POST", "/tasks", task_payload())
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD005", "idempotency_key": "claim-expire", "lease_seconds": 1,
    })
    task_id = claimed["task"]["task_id"]
    token = claimed["lease_token"]
    import time
    time.sleep(1.1)
    status, body = call(hub, "GET", "/tasks?status=pending")
    assert status == 200
    assert body["tasks"][0]["task_id"] == task_id
    status, body = call(hub, "POST", f"/tasks/{task_id}/complete", {
        "node_id": "FSTDD005", "lease_token": token, "idempotency_key": "expired-complete",
        "result_git_sha": "bad",
    })
    assert status == 400
    assert "lease" in body["error"]


def test_failure_and_blocker_message_ack(hub):
    register(hub)
    call(hub, "POST", "/tasks", task_payload())
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD005", "idempotency_key": "claim-fail", "lease_seconds": 30,
    })
    task_id = claimed["task"]["task_id"]
    token = claimed["lease_token"]
    status, body = call(hub, "POST", f"/tasks/{task_id}/fail", {
        "node_id": "FSTDD005", "lease_token": token, "idempotency_key": "fail-1",
        "status": "blocked", "kind": "git-conflict", "summary": "scope conflict",
        "evidence": {"path": "tools/fstdd_hub.py"},
    })
    assert status == 200
    assert body["status"] == "blocked"
    status, body = call(hub, "POST", "/messages", {
        "idempotency_key": "msg-1", "conversation_id": "conv-1", "task_id": task_id,
        "from_node_id": "FSTDD005", "kind": "blocker", "body": "need integration help",
    })
    assert status == 201
    message_id = body["message_id"]
    status, body = call(hub, "GET", "/messages?node_id=FSTDD005&task_id=" + task_id)
    assert status == 200
    assert len(body["messages"]) == 1
    status, _ = call(hub, "POST", f"/messages/{message_id}/ack", {"node_id": "FSTDD005"})
    assert status == 200
    assert call(hub, "GET", "/messages?node_id=FSTDD005&task_id=" + task_id)[1]["messages"] == []


def test_invalid_owner_and_unregistered_node_are_rejected(hub):
    status, body = call(hub, "POST", "/nodes/heartbeat", {"node_id": "FSTDD999"})
    assert status == 400
    assert "not registered" in body["error"]
    status, body = call(hub, "POST", "/tasks", task_payload())
    assert status == 201
    status, body = call(hub, "POST", "/tasks/claim", {
        "node_id": "FSTDD999", "idempotency_key": "bad-claim", "lease_seconds": 30,
    })
    assert status == 400
    assert "not registered" in body["error"]
