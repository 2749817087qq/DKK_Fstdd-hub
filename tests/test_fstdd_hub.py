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


def test_register_persists_fingerprint_and_online_status(hub):
    """列错位回归守卫。

    此前 INSERT 的 VALUES 第 6 个是字面量 'online'，而第 6 列是 ssh_fingerprint、
    第 7 列才是 status —— 两列整体错位：
      * ssh_fingerprint 被写成 'online'（真实指纹永久丢失）
      * 首次插入的 status 变成指纹串，直到第二次注册才被 ON CONFLICT 兜回 'online'
    /health 的 online_nodes 按 status='online' 计数，因此**首次注册的节点会被漏算**。
    """
    register(hub, "FRESH")           # helper 传 ssh_fingerprint="SHA256:test"
    _, body = call(hub, "GET", "/nodes")
    row = next(n for n in body["nodes"] if n["node_id"] == "FRESH")
    assert row["status"] == "online"
    assert row["ssh_fingerprint"] == "SHA256:test"

    # 首次注册即计入在线数（此前会漏算）
    _, health = call(hub, "GET", "/health")
    assert health["online_nodes"] == 1


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


def test_db_connection_enables_foreign_keys(tmp_path):
    """PRAGMA foreign_keys 是**连接级**、默认 OFF。

    SCHEMA 里声明的 FOREIGN KEY 若连接不开启就形同虚设 —— 此前正是如此：
    声明写在 SCHEMA 常量里，connect_db 从未设置。这条断言让该 pragma 变得
    可观测（否则即便去掉它，上层显式校验也会兜住，测试不会转红）。
    """
    db = tmp_path / "fk.sqlite3"
    fstdd_hub.init_db(db)
    conn = fstdd_hub.connect_db(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_orphan_task_message_is_rejected(hub):
    """孤儿留言会静默丢失：task_id 打错一个字，留言在任何任务下都看不到。

    messages.task_id 的外键此前从未生效（PRAGMA foreign_keys 是连接级、默认 OFF，
    只写在 SCHEMA 常量里），实测 POST 不存在的 task_id 会返回 201。
    """
    register(hub)
    status, body = call(hub, "POST", "/messages", {
        "idempotency_key": "orphan-1", "task_id": "task-DOES-NOT-EXIST",
        "from_node_id": "FSTDD005", "kind": "status", "body": "where am I",
    })
    assert status == 400
    assert "task not found" in body["error"]


def test_task_discussion_can_only_be_acked_by_its_author(hub):
    """非作者 ack 任务讨论串 = 删帖：ack 后该留言对所有人的 GET 消失。

    不能一刀切禁止 ack：既有的 blocker 工作流是「自己发求助 -> 自己 ack 表示已处理」，
    数据形态与讨论串完全相同（task_id 非空 + to_node_id 空），无法区分。
    故收窄为「仅作者可 ack」。
    """
    register(hub, "AUTHOR")
    register(hub, "OTHER")
    _, created = call(hub, "POST", "/tasks", task_payload("t-ack"))
    task_id = created["task_id"]
    _, posted = call(hub, "POST", "/messages", {
        "idempotency_key": "disc-1", "task_id": task_id,
        "from_node_id": "AUTHOR", "kind": "question", "body": "why blocked?",
    })
    mid = posted["message_id"]

    # 非作者 ack -> 被拒，且留言对所有人仍然可见
    status, body = call(hub, "POST", f"/messages/{mid}/ack", {"node_id": "OTHER"})
    assert status == 400
    assert "author" in body["error"]
    left = call(hub, "GET", f"/messages?node_id=OTHER&task_id={task_id}")[1]["messages"]
    assert len(left) == 1

    # 作者本人 ack -> 200，留言消失（作者删自己的留言）
    status, _ = call(hub, "POST", f"/messages/{mid}/ack", {"node_id": "AUTHOR"})
    assert status == 200
    assert call(hub, "GET", f"/messages?node_id=AUTHOR&task_id={task_id}")[1]["messages"] == []


def test_broadcast_notice_without_task_is_still_ackable_by_anyone(hub):
    """告警通道不受护栏影响：无 task_id 的广播 notice，任何节点都能 ack。

    服务器镜像失败钩子正是走这条通道上报 + 由运维节点消费。
    """
    register(hub, "OPS")
    _, posted = call(hub, "POST", "/messages", {
        "idempotency_key": "notice-1", "from_node_id": "OPS",
        "kind": "notice", "body": "[MIRROR-FAILED] rc=128",
    })
    mid = posted["message_id"]
    status, _ = call(hub, "POST", f"/messages/{mid}/ack", {"node_id": "OPS"})
    assert status == 200


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


# ---------------------------------------------------------------------------
# 多 agent 并发与租约生命周期（第二轮闭环补的守卫）
#
# 背景：既有的 test_claim_is_atomic_and_returns_lease 名字里写着 atomic，但两次
# claim 是**串行**的（第二次拿到 204 只是因为池子空了），从没让两个节点在同一
# 瞬间抢过。而租约过期后原 owner 的处境此前完全无人验证。
# ---------------------------------------------------------------------------


def test_true_concurrent_claim_grants_task_to_exactly_one_node(hub):
    """真并发守卫：N 个线程同时 claim 同一个任务，只能有一个拿到。

    并发安全实际由 BEGIN IMMEDIATE + 每请求独立连接保证（SQLite 串行化写事务），
    但没有任何测试守着它 —— 一旦有人把 BEGIN IMMEDIATE 去掉或改成共享连接，
    两个 agent 就会拿到同一个任务且无人察觉。
    """
    import threading

    register(hub, "A")
    register(hub, "B")
    assert call(hub, "POST", "/tasks", task_payload("conc-task"))[0] == 201

    n = 8
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(i):
        node = "A" if i % 2 == 0 else "B"
        barrier.wait()  # 尽量让所有请求同时打到服务端
        st, body = call(hub, "POST", "/tasks/claim", {
            "node_id": node, "idempotency_key": f"conc-{i}", "lease_seconds": 60})
        with lock:
            results.append((node, st, body.get("task", {}).get("task_id")))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    winners = [r for r in results if r[1] == 200]
    assert len(winners) == 1, f"并发被击穿：{len(winners)} 个节点拿到了同一任务 -> {winners}"
    assert all(r[1] == 204 for r in results if r[1] != 200), results


def test_owner_can_renew_after_lease_expiry_if_nobody_took_over(hub):
    """「租约过期」的语义是**别人可以来抢**，不是**我自己不能续**。

    旧实现在 _lease_update 里无条件 `raise ValueError("lease expired")`，
    后果：长任务心跳晚一秒就被永久锁死，已做的工作全部作废，哪怕池子里根本
    没人接手这个任务。而且它收到的错误是 "invalid task owner or lease token"
    —— 明明还是 owner，却被自己人的校验拒绝。
    """
    import time

    register(hub, "A")
    call(hub, "POST", "/tasks", task_payload("renew-task"))
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "A", "idempotency_key": "renew-1", "lease_seconds": 1})
    tid = claimed["task"]["task_id"]
    token = claimed["lease_token"]

    time.sleep(1.2)  # 租约已过期；注意**不能**做 GET /tasks，那会触发回收
    st, body = call(hub, "POST", f"/tasks/{tid}/heartbeat",
                    {"node_id": "A", "lease_token": token, "lease_seconds": 60})
    assert st == 200, body
    assert body["status"] == "running"

    # 续租后仍能正常完成
    st, body = call(hub, "POST", f"/tasks/{tid}/complete", {
        "node_id": "A", "lease_token": token, "idempotency_key": "renew-done",
        "result_git_sha": "cafebabe"})
    assert st == 200, body


def test_lease_failure_reasons_are_distinguishable(hub):
    """两种语义相反的失败必须给出不同的 error_code。

    - 租约过期、任务已回池、还没人接 -> lease_reaped，调用方应**重新 claim 继续干**
    - 任务已被别的节点接管            -> task_taken_over，调用方应**立刻停手**

    此前两者都返回同一句 "invalid task owner or lease token"，原 owner 只能猜，
    猜错就是重复劳动或白扔已完成的工作。
    """
    import time

    register(hub, "A")
    register(hub, "B")
    call(hub, "POST", "/tasks", task_payload("distinguish-task"))
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "A", "idempotency_key": "dist-1", "lease_seconds": 1})
    tid = claimed["task"]["task_id"]
    token = claimed["lease_token"]

    time.sleep(1.2)
    call(hub, "GET", "/tasks?status=pending")  # 触发回收（列一次表就回收一次）
    st, reaped_err = call(hub, "POST", f"/tasks/{tid}/heartbeat",
                          {"node_id": "A", "lease_token": token, "lease_seconds": 60})
    assert st == 400
    assert reaped_err.get("error_code") == "lease_reaped", reaped_err

    # B 接管
    st, taken = call(hub, "POST", "/tasks/claim", {
        "node_id": "B", "idempotency_key": "dist-2", "lease_seconds": 60})
    assert st == 200
    assert taken["task"]["task_id"] == tid

    st, taken_err = call(hub, "POST", f"/tasks/{tid}/heartbeat",
                         {"node_id": "A", "lease_token": token, "lease_seconds": 60})
    assert st == 400
    assert taken_err.get("error_code") == "task_taken_over", taken_err
    assert "B" in taken_err["error"]

    # 核心断言：语义相反，错误码必须不同
    assert reaped_err["error_code"] != taken_err["error_code"]


def test_takeover_notifies_the_previous_owner(hub):
    """被接管时主动通知原 owner —— 它此刻可能还在闷头干活。

    此前租约过期 -> 回收 -> 被别人 claim 全程静默。原 owner 要等到下一次心跳
    或完成请求被拒才知道，而那时它已经干完了。
    """
    import time

    register(hub, "A")
    register(hub, "B")
    call(hub, "POST", "/tasks", task_payload("takeover-task"))
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "A", "idempotency_key": "take-1", "lease_seconds": 1})
    tid = claimed["task"]["task_id"]

    time.sleep(1.2)
    call(hub, "GET", "/tasks?status=pending")
    st, _ = call(hub, "POST", "/tasks/claim", {
        "node_id": "B", "idempotency_key": "take-2", "lease_seconds": 60})
    assert st == 200

    _, msgs = call(hub, "GET", f"/messages?node_id=A")
    mine = [m for m in msgs["messages"] if m["task_id"] == tid]
    assert mine, f"A 没有收到任何关于 {tid} 的消息"
    assert any(m["from_node_id"] == "B" and m["to_node_id"] == "A" for m in mine), mine


def test_completed_task_reports_lease_released_not_invalid_owner(hub):
    """自己已经 complete 过，再操作应报 lease_released，而不是"你不是 owner"。

    complete/fail 会清空 lease_token_hash 但保留 owner_node_id，旧逻辑会走到
    "invalid task owner or lease token" 分支 —— 对调用方是彻底的误导。
    """
    register(hub, "A")
    call(hub, "POST", "/tasks", task_payload("released-task"))
    _, claimed = call(hub, "POST", "/tasks/claim", {
        "node_id": "A", "idempotency_key": "rel-1", "lease_seconds": 30})
    tid = claimed["task"]["task_id"]
    token = claimed["lease_token"]

    st, _ = call(hub, "POST", f"/tasks/{tid}/complete", {
        "node_id": "A", "lease_token": token, "idempotency_key": "rel-done",
        "result_git_sha": "abc"})
    assert st == 200

    st, body = call(hub, "POST", f"/tasks/{tid}/heartbeat",
                    {"node_id": "A", "lease_token": token, "lease_seconds": 30})
    assert st == 400
    assert body.get("error_code") == "lease_released", body


def test_init_db_migrates_legacy_schema_without_last_owner(tmp_path):
    """老库必须能自动补列 —— CREATE TABLE IF NOT EXISTS 对已存在的表不补列。

    生产库是旧表结构，只靠 SCHEMA 常量**永远**加不上 last_owner_node_id：
    表已存在时 CREATE TABLE IF NOT EXISTS 直接跳过。于是接管通知会静默不发，
    而没有任何测试会转红 —— 因为测试全都用全新库，列直接来自 SCHEMA。

    这条路径天然零覆盖：变异测试注入「去掉 _ensure_columns 调用」后 50 条用例
    依然全绿。本测试让它变得可观测。
    """
    import sqlite3

    db = tmp_path / "legacy.sqlite3"
    legacy_schema = fstdd_hub.SCHEMA.replace("    last_owner_node_id TEXT,\n", "")
    assert legacy_schema != fstdd_hub.SCHEMA  # 确认确实删掉了，否则本测试形同虚设

    conn = sqlite3.connect(str(db))
    conn.executescript(legacy_schema)
    conn.close()

    before = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(tasks)")}
    assert "last_owner_node_id" not in before

    fstdd_hub.init_db(db)  # 迁移

    conn = fstdd_hub.connect_db(db)
    try:
        after = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    finally:
        conn.close()
    assert "last_owner_node_id" in after
