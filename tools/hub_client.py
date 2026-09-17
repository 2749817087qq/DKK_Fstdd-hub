#!/usr/bin/env python3
"""FSTDD Hub 客户端 —— 节点侧（L3）与中枢（L2 控制面）交互的封装。

为什么需要这个模块
------------------
`fstdd_git.py` 只覆盖**本地** git 隔离（create-worktree / scope-conflicts /
integrate），**没有任何与中枢交互的封装**。2026-09-18 首个闭环实测时，
register / claim / complete 全部靠临时 inline 脚本完成，过程无法复用、
也无法进回归。本模块补上这一层。

设计约束
--------
* 仅依赖标准库 —— 与中枢侧 `fstdd_hub.py` 一致，零第三方依赖。
* 所有写操作都带 `idempotency_key`，可安全重试。在「远端命令会被执行两次」
  的环境里这是硬性要求；缺了它，一次双执行就是两条任务。
* `lease_token` **只在 claim 响应中明文返回一次**，中枢只存哈希。
  调用方必须自行保存，本模块不做隐式持久化。
* 中枢默认租约 `DEFAULT_LEASE_SECONDS = 300`（5 分钟），上限 3600。
  长任务必须显式调 `task_heartbeat()` 续租，否则 complete 会报 lease expired。

用法
----
    from hub_client import HubClient
    hub = HubClient("http://127.0.0.1:8788")
    hub.register("node-1", capabilities=["code"])
    got = hub.claim("node-1", lease_seconds=1800)
    if got:
        hub.complete(got["task"]["task_id"], got["lease_token"], "node-1",
                     result_git_sha="<sha>")

命令行：
    python tools/hub_client.py health
    python tools/hub_client.py register <node_id> [--capabilities code,fstdd]
    python tools/hub_client.py claim <node_id> [--lease-seconds 1800]
    python tools/hub_client.py complete <task_id> <lease_token> <node_id> <sha>
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8788"
# 与中枢一致：默认 300s，上限 3600s。见 fstdd_hub.py:28-29
DEFAULT_LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 3600


class HubError(RuntimeError):
    """中枢返回非 2xx。"""

    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"hub {path} -> HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body
        self.path = path


class HubClient:
    """FSTDD 中枢的极简 HTTP 客户端。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 20,
        node_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.node_id = node_id

    # ------------------------------------------------------------------ 底层

    def _request(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raise HubError(
                exc.code, exc.read().decode("utf-8", "replace"), path
            ) from None

    def _post(self, path: str, payload: dict):
        return self._request("POST", path, payload)[1]

    # ------------------------------------------------------------------ 只读

    def get(self, path: str) -> Any:
        return self._request("GET", path)[1]

    def health(self) -> dict:
        return self.get("/health")

    def nodes(self) -> list[dict]:
        return self.get("/nodes")["nodes"]

    def tasks(self) -> list[dict]:
        return self.get("/tasks")["tasks"]

    def messages(self, node_id: str) -> list[dict]:
        """/messages 必须带 node_id 查询参数，裸请求会被拒。"""
        return self.get(f"/messages?node_id={node_id}")["messages"]

    # ------------------------------------------------------------------ 节点

    def register(
        self,
        node_id: str,
        machine_name: str | None = None,
        platform: str | None = None,
        os_name: str | None = None,
        capabilities: list[str] | None = None,
        ssh_fingerprint: str = "unverified",
        metadata: dict | None = None,
    ) -> dict:
        """注册或刷新节点。中枢用 INSERT ... ON CONFLICT DO UPDATE，天然幂等。"""
        platform = platform or sys.platform
        return self._post(
            "/nodes/register",
            {
                "node_id": node_id,
                "machine_name": machine_name or socket.gethostname(),
                "platform": platform,
                "os": os_name or platform,
                "ssh_fingerprint": ssh_fingerprint,
                "capabilities": capabilities or [],
                "metadata": metadata or {},
            },
        )

    def heartbeat(self, node_id: str) -> dict:
        return self._post("/nodes/heartbeat", {"node_id": node_id})

    # ------------------------------------------------------------------ 任务

    def create_task(
        self,
        parent_change_id: str,
        summary: str,
        base_git_sha: str,
        idempotency_key: str | None = None,
        kind: str = "change",
        scope: dict | None = None,
        child_change_id: str | None = None,
    ) -> dict:
        return self._post(
            "/tasks",
            {
                "idempotency_key": idempotency_key or f"task-{secrets.token_hex(8)}",
                "parent_change_id": parent_change_id,
                "child_change_id": child_change_id,
                "kind": kind,
                "summary": summary,
                "scope": scope or {},
                "base_git_sha": base_git_sha,
            },
        )

    def claim(
        self,
        node_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        idempotency_key: str | None = None,
    ) -> dict | None:
        """原子领取最早的一条 pending 任务。

        中枢用 BEGIN IMMEDIATE + WHERE status='pending' 保证原子性。
        返回 None 表示当前没有待领任务（HTTP 204）。
        """
        if not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError(f"lease_seconds must be 1..{MAX_LEASE_SECONDS}")
        status, body = self._request(
            "POST",
            "/tasks/claim",
            {
                "node_id": node_id,
                "idempotency_key": idempotency_key or f"claim-{secrets.token_hex(8)}",
                "lease_seconds": lease_seconds,
            },
        )
        if status == 204 or not body:
            return None
        return body

    def task_heartbeat(
        self,
        task_id: str,
        lease_token: str,
        node_id: str,
        lease_seconds: int | None = None,
    ) -> dict:
        """续租。长任务必须定期调用，否则租约到期后 complete 会被拒。"""
        payload: dict[str, Any] = {"node_id": node_id, "lease_token="***REMOVED***"}
        if lease_seconds is not None:
            payload["lease_seconds"] = lease_seconds
        return self._post(f"/tasks/{task_id}/heartbeat", payload)

    def complete(
        self,
        task_id: str,
        lease_token: str,
        node_id: str,
        result_git_sha: str,
        idempotency_key: str | None = None,
        result_ref: str | None = None,
        result: dict | None = None,
    ) -> dict:
        """标记完成。中枢会把「done」与产物 sha 焊死，并清空租约（只能成功一次）。"""
        payload: dict[str, Any] = {
            "node_id": node_id,
            "lease_token="***REMOVED***",
            "idempotency_key": idempotency_key or f"done-{secrets.token_hex(8)}",
            "result_git_sha": result_git_sha,
        }
        if result_ref is not None:
            payload["result_ref"] = result_ref
        if result is not None:
            payload["result"] = result
        return self._post(f"/tasks/{task_id}/complete", payload)

    def fail(
        self,
        task_id: str,
        lease_token: str,
        node_id: str,
        summary: str,
        status: str = "failed",
        kind: str = "error",
        evidence: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """标记失败。`status` 只能是 failed 或 blocked。"""
        return self._post(
            f"/tasks/{task_id}/fail",
            {
                "node_id": node_id,
                "lease_token="***REMOVED***",
                "idempotency_key": idempotency_key or f"fail-{secrets.token_hex(8)}",
                "status": status,
                "kind": kind,
                "summary": summary,
                "evidence": evidence or {},
            },
        )


# ---------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="FSTDD hub client")
    p.add_argument("--url", default=DEFAULT_BASE_URL)
    p.add_argument("--node-id", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("nodes")
    sub.add_parser("tasks")

    r = sub.add_parser("register")
    r.add_argument("node_id")
    r.add_argument("--capabilities", default="")

    c = sub.add_parser("claim")
    c.add_argument("node_id")
    c.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)

    t = sub.add_parser("create-task")
    t.add_argument("parent_change_id")
    t.add_argument("summary")
    t.add_argument("base_git_sha")

    comp = sub.add_parser("complete")
    comp.add_argument("task_id")
    comp.add_argument("lease_token")
    comp.add_argument("node_id")
    comp.add_argument("result_git_sha")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    hub = HubClient(args.url, node_id=args.node_id)
    try:
        if args.cmd == "health":
            out = hub.health()
        elif args.cmd == "nodes":
            out = hub.nodes()
        elif args.cmd == "tasks":
            out = hub.tasks()
        elif args.cmd == "register":
            caps = [c for c in args.capabilities.split(",") if c]
            out = hub.register(args.node_id, capabilities=caps)
        elif args.cmd == "claim":
            out = hub.claim(args.node_id, lease_seconds=args.lease_seconds)
            if out is None:
                print("no pending task")
                return 0
        elif args.cmd == "create-task":
            out = hub.create_task(args.parent_change_id, args.summary, args.base_git_sha)
        elif args.cmd == "complete":
            out = hub.complete(
                args.task_id, args.lease_token, args.node_id, args.result_git_sha
            )
        else:  # pragma: no cover
            raise SystemExit(f"unknown command: {args.cmd}")
    except HubError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
