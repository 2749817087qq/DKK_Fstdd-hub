"""hub_client 的单元测试。

用本地假 hub（标准库 http.server）驱动，**不依赖真实中枢**，
因此可进 CI 回归。覆盖点偏向契约而非实现细节：
请求是否带齐必填字段、204 是否正确转成 None、非 2xx 是否抛 HubError。
"""

from __future__ import annotations

import io
import json
import pathlib
import socket
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from hub_client import HubClient, HubError  # noqa: E402

REQUESTS: list[tuple[str, str, dict | None]] = []
CLAIM_EMPTY = {"value": False}


class _FakeHub(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音测试输出
        pass

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def _send(self, code: int, obj):
        body = json.dumps(obj).encode("utf-8") if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        REQUESTS.append(("GET", self.path, None))
        if self.path == "/health":
            self._send(200, {"ok": True, "tasks": 0})
        elif self.path == "/nodes":
            self._send(200, {"nodes": [{"node_id": "n1", "status": "online"}]})
        elif self.path == "/tasks":
            self._send(200, {"tasks": []})
        elif self.path.startswith("/messages?node_id="):
            self._send(200, {"messages": [{"id": "m1"}]})
        elif self.path == "/messages":
            self._send(400, {"error": "node_id query parameter is required"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        payload = self._read()
        REQUESTS.append(("POST", self.path, payload))
        if self.path == "/nodes/register":
            self._send(200, {"ok": True, "node_id": payload["node_id"]})
        elif self.path == "/nodes/heartbeat":
            self._send(200, {"ok": True})
        elif self.path == "/tasks":
            self._send(201, {"task_id": "t1", "status": "pending"})
        elif self.path == "/tasks/claim":
            if CLAIM_EMPTY["value"]:
                self._send(204, None)
            else:
                self._send(200, {"task": {"task_id": "t1"}, "lease_token="***REMOVED***"})
        elif self.path.endswith("/complete"):
            self._send(200, {"ok": True, "status": "done"})
        elif self.path.endswith("/fail"):
            self._send(200, {"ok": True, "status": "failed"})
        elif self.path.endswith("/heartbeat"):
            self._send(200, {"ok": True})
        elif self.path.endswith("/ack"):
            self._send(200, {"ok": True})
        elif self.path == "/messages":
            self._send(201, {
                "message_id": "m1", "conversation_id": "c1",
                "task_id": payload.get("task_id"),
                "from_node_id": payload["from_node_id"],
                "to_node_id": payload.get("to_node_id"),
                "kind": payload["kind"], "body": payload["body"],
                "created_at": "2026-01-01T00:00:00+00:00",
            })
        else:
            self._send(404, {"error": "not found"})


class HubClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHub)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        CLAIM_EMPTY["value"] = False
        self.hub = HubClient(self.url)

    # ------------------------------------------------------------ 只读

    def test_health(self):
        self.assertTrue(self.hub.health()["ok"])

    def test_nodes_and_tasks_unwrap(self):
        self.assertEqual(self.hub.nodes()[0]["node_id"], "n1")
        self.assertEqual(self.hub.tasks(), [])

    def test_messages_requires_node_id(self):
        self.assertEqual(self.hub.messages("n1"), [{"id": "m1"}])
        self.assertIn("?node_id=n1", REQUESTS[-1][1])
        with self.assertRaises(HubError) as ctx:
            self.hub.get("/messages")
        self.assertEqual(ctx.exception.status, 400)

    def test_hub_error_carries_status(self):
        with self.assertRaises(HubError) as ctx:
            self.hub.get("/nope")
        self.assertEqual(ctx.exception.status, 404)

    # ------------------------------------------------------------ 节点

    def test_register_sends_required_fields(self):
        self.hub.register("node-1", capabilities=["code"])
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/nodes/register")
        for field in ("node_id", "machine_name", "platform", "os", "ssh_fingerprint"):
            self.assertIn(field, payload)
        self.assertEqual(payload["capabilities"], ["code"])
        # 变异体防护：machine_name 曾被写死，不再回退本机 hostname
        self.assertEqual(payload["machine_name"], socket.gethostname())

    def test_register_is_repeatable(self):
        """同一 node_id 连跑两次，第二次依然 200（中枢 ON CONFLICT DO UPDATE）。"""
        self.hub.register("node-1")
        self.hub.register("node-1")
        ids = [r[2]["node_id"] for r in REQUESTS if r[1] == "/nodes/register"]
        self.assertEqual(ids, ["node-1", "node-1"])

    def test_heartbeat(self):
        self.hub.heartbeat("node-1")
        self.assertEqual(REQUESTS[-1][1], "/nodes/heartbeat")

    # ------------------------------------------------------------ 任务

    def test_create_task_requires_base_sha(self):
        scope = {"paths": ["tools/x.py"]}
        self.hub.create_task("parent-1", "do something", "abc1234",
                             scope=scope, child_change_id="child-1")
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/tasks")
        self.assertEqual(payload["base_git_sha"], "abc1234")
        self.assertEqual(payload["parent_change_id"], "parent-1")
        # 变异体防护：以下三个字段曾被「漏传」却仍能全绿
        self.assertEqual(payload["summary"], "do something")
        self.assertEqual(payload["scope"], scope)
        self.assertEqual(payload["child_change_id"], "child-1")
        self.assertIn("idempotency_key", payload)

    def test_claim_returns_body(self):
        got = self.hub.claim("node-1", lease_seconds=600)
        self.assertEqual(got["lease_token"], "tok-123")
        self.assertEqual(REQUESTS[-1][2]["lease_seconds"], 600)

    def test_claim_returns_none_on_204(self):
        """无待领任务时中枢回 204 空体，必须转成 None 而不是抛异常。"""
        CLAIM_EMPTY["value"] = True
        self.assertIsNone(self.hub.claim("node-1"))

    def test_claim_rejects_out_of_range_lease(self):
        for bad in (0, -1, 3601):
            with self.assertRaises(ValueError):
                self.hub.claim("node-1", lease_seconds=bad)

    def test_task_heartbeat_extends_lease(self):
        self.hub.task_heartbeat("t1", "tok", "node-1", lease_seconds=1800)
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/tasks/t1/heartbeat")
        self.assertEqual(payload["lease_seconds"], 1800)

    def test_complete_pins_result_sha(self):
        self.hub.complete("t1", "tok", "node-1", "deadbeef",
                          result_ref="refs/heads/master",
                          result={"files": ["tools/x.py"]})
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/tasks/t1/complete")
        self.assertEqual(payload["result_git_sha"], "deadbeef")
        self.assertEqual(payload["lease_token"], "tok")
        self.assertEqual(payload["node_id"], "node-1")
        # 变异体防护：产物引用与产物清单曾被漏传却仍能全绿
        self.assertEqual(payload["result_ref"], "refs/heads/master")
        self.assertEqual(payload["result"], {"files": ["tools/x.py"]})
        self.assertIn("idempotency_key", payload)

    def test_fail_requires_summary(self):
        self.hub.fail("t1", "tok", "node-1", "boom")
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/tasks/t1/fail")
        self.assertEqual(payload["summary"], "boom")
        self.assertEqual(payload["status"], "failed")

    def test_fail_accepts_blocked_status(self):
        """变异体防护：status 曾被写死为 failed，调用方传 blocked 会被静默改写。"""
        self.hub.fail("t1", "tok", "node-1", "waiting on review", status="blocked")
        self.assertEqual(REQUESTS[-1][2]["status"], "blocked")
        self.assertEqual(REQUESTS[-1][2]["summary"], "waiting on review")

    # ------------------------------------------------------------ 留言（BBS）

    def test_post_message_pins_task_id(self):
        self.hub.post_message("为什么卡住了？", kind="question", task_id="t1",
                              from_node_id="node-1", idempotency_key="msg-1")
        _, path, payload = REQUESTS[-1]
        self.assertEqual(path, "/messages")
        self.assertEqual(payload["task_id"], "t1")
        self.assertEqual(payload["body"], "为什么卡住了？")
        self.assertEqual(payload["kind"], "question")
        self.assertEqual(payload["from_node_id"], "node-1")
        self.assertEqual(payload["idempotency_key"], "msg-1")

    def test_post_message_is_broadcast_not_dm(self):
        """变异体防护：一旦带上 to_node_id，留言就只有收件人可见，留言板退化成私信。"""
        self.hub.post_message("hi", from_node_id="node-1")
        payload = REQUESTS[-1][2]
        self.assertNotIn("to_node_id", payload)

    def test_post_message_rejects_unknown_kind(self):
        with self.assertRaises(ValueError) as ctx:
            self.hub.post_message("hi", kind="chat", from_node_id="node-1")
        self.assertIn("question", str(ctx.exception))

    def test_post_message_requires_sender(self):
        bare = HubClient(self.url)  # 未设 node_id，也未显式传 from_node_id
        with self.assertRaises(ValueError):
            bare.post_message("hi")

    def test_ack_message_pins_path(self):
        """变异体防护（QA B7）：路径曾被改成 /ack2，测试全绿 —— 零覆盖。"""
        self.hub.ack_message("m1", node_id="node-1")
        _, path, _ = REQUESTS[-1]
        self.assertEqual(path, "/messages/m1/ack")

    def test_ack_message_payload_is_node_id_only(self):
        """变异体防护（QA B8）：payload 曾被漏传 node_id，测试全绿。"""
        self.hub.ack_message("m1", node_id="node-1")
        payload = REQUESTS[-1][2]
        self.assertEqual(payload, {"node_id": "node-1"})

    def test_ack_message_requires_node_id(self):
        """变异体防护（QA B9）：必填校验曾被去掉，测试全绿。"""
        bare = HubClient(self.url)
        with self.assertRaises(ValueError):
            bare.ack_message("m1")

    def test_list_messages_filters_by_task(self):
        self.hub.list_messages("node-1", task_id="t1")
        path = REQUESTS[-1][1]
        self.assertIn("node_id=node-1", path)
        self.assertIn("&task_id=t1", path)

    def test_list_messages_without_task_has_no_filter(self):
        self.hub.list_messages("node-1")
        path = REQUESTS[-1][1]
        self.assertIn("node_id=node-1", path)
        self.assertNotIn("task_id", path)

    # ------------------------------------------------------------ CLI

    def test_cli_health_exit_zero(self):
        from hub_client import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--url", self.url, "health"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_cli_claim_no_pending(self):
        from hub_client import main

        CLAIM_EMPTY["value"] = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--url", self.url, "claim", "node-1"])
        self.assertEqual(rc, 0)
        self.assertIn("no pending task", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
