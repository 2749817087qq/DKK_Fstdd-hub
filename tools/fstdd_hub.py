#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FSTDD internal coordination hub.

This is the control plane for distributed FSTDD work.  It deliberately has a
small standard-library-only surface: SQLite stores leases, node heartbeats,
tasks, messages, and idempotency records.  Git and each Change remain the
source of truth for code and FSTDD phase state.

The process should listen on 127.0.0.1 and be reached through SSH forwarding;
it is not a replacement for the public, unauthenticated 8787 experience inbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 3600
TASK_STATUSES = ("pending", "claimed", "running", "done", "failed", "blocked")
MESSAGE_KINDS = ("question", "blocker", "status", "notice")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    machine_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    os TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    ssh_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'online',
    last_seen REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    parent_change_id TEXT NOT NULL,
    child_change_id TEXT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    owner_node_id TEXT,
    lease_token_hash TEXT,
    lease_expires_at REAL,
    attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    base_git_sha TEXT NOT NULL,
    result_git_sha TEXT,
    result_ref TEXT,
    result_json TEXT,
    failure_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_node_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_pool ON tasks(status, lease_expires_at, created_at);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    task_id TEXT,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acked_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(from_node_id) REFERENCES nodes(node_id),
    FOREIGN KEY(to_node_id) REFERENCES nodes(node_id)
);
CREATE TABLE IF NOT EXISTS idempotency (
    idempotency_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_code INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path: Path) -> None:
    conn = connect_db(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def json_obj(value, default):
    if value is None:
        return default
    if not isinstance(value, (dict, list)):
        raise ValueError("must be an object or array")
    return value


def require_text(payload: dict, key: str, max_len: int = 2000) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    value = value.strip()
    if len(value) > max_len:
        raise ValueError(f"{key} is too long")
    return value


def validate_node(payload: dict) -> dict:
    node_id = require_text(payload, "node_id", 80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", node_id):
        raise ValueError("node_id contains invalid characters")
    return {
        "node_id": node_id,
        "machine_name": require_text(payload, "machine_name", 200),
        "platform": require_text(payload, "platform", 100),
        "os": require_text(payload, "os", 100),
        "capabilities": json_obj(payload.get("capabilities", []), []),
        "ssh_fingerprint": require_text(payload, "ssh_fingerprint", 200),
        "metadata": json_obj(payload.get("metadata", {}), {}),
    }


def validate_task(payload: dict) -> dict:
    idem = require_text(payload, "idempotency_key", 200)
    kind = payload.get("kind", "change")
    if kind not in ("change", "slice", "debug", "ops"):
        raise ValueError("invalid task kind")
    scope = json_obj(payload.get("scope", {}), {})
    return {
        "idempotency_key": idem,
        "parent_change_id": require_text(payload, "parent_change_id", 200),
        "child_change_id": payload.get("child_change_id"),
        "kind": kind,
        "summary": require_text(payload, "summary", 2000),
        "scope": scope,
        "base_git_sha": require_text(payload, "base_git_sha", 200),
    }


def row_task(row: sqlite3.Row) -> dict:
    return {
        "task_id": row["task_id"],
        "parent_change_id": row["parent_change_id"],
        "child_change_id": row["child_change_id"],
        "kind": row["kind"],
        "summary": row["summary"],
        "scope": json.loads(row["scope_json"]),
        "status": row["status"],
        "owner_node_id": row["owner_node_id"],
        "lease_expires_at": row["lease_expires_at"],
        "attempt": row["attempt"],
        "base_git_sha": row["base_git_sha"],
        "result_git_sha": row["result_git_sha"],
        "result_ref": row["result_ref"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "failure": json.loads(row["failure_json"]) if row["failure_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def reap_expired(conn: sqlite3.Connection) -> int:
    now = time.time()
    cur = conn.execute(
        "UPDATE tasks SET status='pending', owner_node_id=NULL, lease_token_hash=NULL, "
        "lease_expires_at=NULL, updated_at=? WHERE status IN ('claimed','running') "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (utc_now(), now),
    )
    return cur.rowcount


class HubHandler(BaseHTTPRequestHandler):
    db_path: Path = Path("./fstdd-hub.sqlite3")

    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, obj: dict, headers: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def _path_query(self):
        parsed = urlparse(self.path)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        return parsed.path.rstrip("/") or "/", query

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be 1..{MAX_REQUEST_BYTES} bytes")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"bad json: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def _with_db(self):
        return connect_db(self.db_path)

    def do_GET(self):  # noqa: N802
        path, query = self._path_query()
        conn = self._with_db()
        try:
            if path == "/health":
                row = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
                nodes = conn.execute("SELECT COUNT(*) AS n FROM nodes WHERE status='online'").fetchone()
                self._json(200, {"ok": True, "tasks": row["n"], "online_nodes": nodes["n"], "time": utc_now()})
            elif path == "/nodes":
                rows = conn.execute("SELECT * FROM nodes ORDER BY node_id").fetchall()
                self._json(200, {"nodes": [{
                    "node_id": r["node_id"], "machine_name": r["machine_name"],
                    "platform": r["platform"], "os": r["os"],
                    "capabilities": json.loads(r["capabilities_json"]),
                    "ssh_fingerprint": r["ssh_fingerprint"], "status": r["status"],
                    "last_seen": r["last_seen"], "metadata": json.loads(r["metadata_json"]),
                } for r in rows]})
            elif path == "/tasks":
                reap_expired(conn)
                status = query.get("status")
                if status and status not in TASK_STATUSES:
                    raise ValueError("invalid task status")
                if status:
                    rows = conn.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at", (status,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
                self._json(200, {"tasks": [row_task(r) for r in rows]})
            elif path == "/messages":
                node_id = query.get("node_id")
                if not node_id:
                    raise ValueError("node_id query parameter is required")
                task_id = query.get("task_id")
                sql = "SELECT * FROM messages WHERE acked_at IS NULL AND (to_node_id=? OR to_node_id IS NULL)"
                params = [node_id]
                if task_id:
                    sql += " AND task_id=?"
                    params.append(task_id)
                sql += " ORDER BY created_at"
                rows = conn.execute(sql, params).fetchall()
                self._json(200, {"messages": [{
                    "message_id": r["message_id"], "conversation_id": r["conversation_id"],
                    "task_id": r["task_id"], "from_node_id": r["from_node_id"],
                    "to_node_id": r["to_node_id"], "kind": r["kind"],
                    "body": r["body"], "created_at": r["created_at"],
                } for r in rows]})
            else:
                self._json(404, {"error": "not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        finally:
            conn.close()

    def do_POST(self):  # noqa: N802
        path, _ = self._path_query()
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._json(413 if "request body" in str(exc) else 400, {"error": str(exc)})
            return
        conn = self._with_db()
        try:
            if path == "/nodes/register":
                self._register(conn, payload)
            elif path == "/nodes/heartbeat":
                self._heartbeat(conn, payload)
            elif path == "/tasks":
                self._create_task(conn, payload)
            elif path == "/tasks/claim":
                self._claim(conn, payload)
            elif path.startswith("/tasks/") and path.endswith("/heartbeat"):
                self._task_heartbeat(conn, path.split("/")[2], payload)
            elif path.startswith("/tasks/") and path.endswith("/complete"):
                self._complete(conn, path.split("/")[2], payload)
            elif path.startswith("/tasks/") and path.endswith("/fail"):
                self._fail(conn, path.split("/")[2], payload)
            elif path == "/messages":
                self._message(conn, payload)
            elif path.startswith("/messages/") and path.endswith("/ack"):
                self._ack(conn, path.split("/")[2], payload)
            else:
                self._json(404, {"error": "not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except sqlite3.IntegrityError as exc:
            self._json(409, {"error": f"conflict: {exc}"})
        finally:
            conn.close()

    def _idempotent(self, conn, operation: str, key: str, payload: dict):
        digest = request_hash(payload)
        row = conn.execute("SELECT * FROM idempotency WHERE idempotency_key=?", (key,)).fetchone()
        if row:
            if row["operation"] != operation or row["request_hash"] != digest:
                raise ValueError("idempotency key already used with a different request")
            return row["response_code"], json.loads(row["response_json"])
        return None

    def _save_idempotent(self, conn, operation, key, payload, code, body):
        conn.execute("INSERT INTO idempotency VALUES (?,?,?,?,?,?)",
                     (key, operation, request_hash(payload), code,
                      json.dumps(body, ensure_ascii=False), utc_now()))

    def _register(self, conn, payload):
        node = validate_node(payload)
        now = time.time()
        stamp = utc_now()
        conn.execute("""INSERT INTO nodes(node_id,machine_name,platform,os,capabilities_json,
                     ssh_fingerprint,status,last_seen,metadata_json,updated_at)
                     VALUES (?,?,?,?,?,'online',?,?,?,?)
                     ON CONFLICT(node_id) DO UPDATE SET machine_name=excluded.machine_name,
                     platform=excluded.platform, os=excluded.os,
                     capabilities_json=excluded.capabilities_json, ssh_fingerprint=excluded.ssh_fingerprint,
                     status='online', last_seen=excluded.last_seen, metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                     (node["node_id"], node["machine_name"], node["platform"], node["os"],
                      json.dumps(node["capabilities"], ensure_ascii=False), node["ssh_fingerprint"],
                      now, json.dumps(node["metadata"], ensure_ascii=False), stamp))
        self._json(200, {"ok": True, "node_id": node["node_id"], "last_seen": now})

    def _heartbeat(self, conn, payload):
        node_id = require_text(payload, "node_id", 80)
        now = time.time()
        cur = conn.execute("UPDATE nodes SET status='online', last_seen=?, updated_at=? WHERE node_id=?",
                           (now, utc_now(), node_id))
        if not cur.rowcount:
            raise ValueError("node not registered")
        self._json(200, {"ok": True, "node_id": node_id, "last_seen": now})

    def _create_task(self, conn, payload):
        task = validate_task(payload)
        key = task["idempotency_key"]
        previous = self._idempotent(conn, "task.create", key, payload)
        if previous:
            self._json(*previous)
            return
        task_id = new_id("task")
        stamp = utc_now()
        body = {"task_id": task_id, "status": "pending"}
        conn.execute("""INSERT INTO tasks(task_id,parent_change_id,child_change_id,kind,summary,
                     scope_json,status,idempotency_key,request_hash,base_git_sha,created_at,updated_at)
                     VALUES (?,?,?,?,?,?, 'pending',?,?,?,?,?)""",
                     (task_id, task["parent_change_id"], task["child_change_id"], task["kind"],
                      task["summary"], json.dumps(task["scope"], ensure_ascii=False), key,
                      request_hash(payload), task["base_git_sha"], stamp, stamp))
        self._save_idempotent(conn, "task.create", key, payload, 201, body)
        self._json(201, body)

    def _claim(self, conn, payload):
        node_id = require_text(payload, "node_id", 80)
        key = require_text(payload, "idempotency_key", 200)
        lease = int(payload.get("lease_seconds", DEFAULT_LEASE_SECONDS))
        if lease < 1 or lease > MAX_LEASE_SECONDS:
            raise ValueError(f"lease_seconds must be 1..{MAX_LEASE_SECONDS}")
        previous = self._idempotent(conn, "task.claim", key, payload)
        if previous:
            self._json(*previous)
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            reap_expired(conn)
            row = conn.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                self._json(204, {})
                return
            if not conn.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone():
                conn.execute("ROLLBACK")
                raise ValueError("node not registered")
            token = secrets.token_urlsafe(32)
            expires = time.time() + lease
            stamp = utc_now()
            conn.execute("""UPDATE tasks SET status='claimed', owner_node_id=?, lease_token_hash=?,
                         lease_expires_at=?, attempt=attempt+1, updated_at=? WHERE task_id=? AND status='pending'""",
                         (node_id, token_hash(token), expires, stamp, row["task_id"]))
            body = {"task": row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()),
                    "lease_token": token}
            self._save_idempotent(conn, "task.claim", key, payload, 200, body)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        self._json(200, body)

    def _lease_update(self, conn, task_id, payload, new_status=None):
        node_id = require_text(payload, "node_id", 80)
        token = require_text(payload, "lease_token", 300)
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("task not found")
        if row["owner_node_id"] != node_id or row["lease_token_hash"] != token_hash(token):
            raise ValueError("invalid task owner or lease token")
        if not row["lease_expires_at"] or row["lease_expires_at"] < time.time():
            raise ValueError("lease expired")
        return row

    def _task_heartbeat(self, conn, task_id, payload):
        row = self._lease_update(conn, task_id, payload)
        lease = int(payload.get("lease_seconds", DEFAULT_LEASE_SECONDS))
        if lease < 1 or lease > MAX_LEASE_SECONDS:
            raise ValueError(f"lease_seconds must be 1..{MAX_LEASE_SECONDS}")
        expires = time.time() + lease
        conn.execute("UPDATE tasks SET status='running', lease_expires_at=?, updated_at=? WHERE task_id=?",
                     (expires, utc_now(), task_id))
        self._json(200, {"ok": True, "task_id": task_id, "status": "running", "lease_expires_at": expires})

    def _complete(self, conn, task_id, payload):
        self._lease_update(conn, task_id, payload)
        key = require_text(payload, "idempotency_key", 200)
        previous = self._idempotent(conn, "task.complete", key, payload)
        if previous:
            self._json(*previous)
            return
        result_sha = require_text(payload, "result_git_sha", 200)
        body = {"ok": True, "task_id": task_id, "status": "done", "result_git_sha": result_sha}
        conn.execute("""UPDATE tasks SET status='done', lease_token_hash=NULL, lease_expires_at=NULL,
                     result_git_sha=?, result_ref=?, result_json=?, updated_at=? WHERE task_id=?""",
                     (result_sha, payload.get("result_ref"), json.dumps(payload.get("result", {}), ensure_ascii=False), utc_now(), task_id))
        self._save_idempotent(conn, "task.complete", key, payload, 200, body)
        self._json(200, body)

    def _fail(self, conn, task_id, payload):
        self._lease_update(conn, task_id, payload)
        key = require_text(payload, "idempotency_key", 200)
        previous = self._idempotent(conn, "task.fail", key, payload)
        if previous:
            self._json(*previous)
            return
        status = payload.get("status", "failed")
        if status not in ("failed", "blocked"):
            raise ValueError("failure status must be failed or blocked")
        failure = {"kind": payload.get("kind", "error"), "summary": require_text(payload, "summary", 4000),
                   "evidence": payload.get("evidence", {})}
        body = {"ok": True, "task_id": task_id, "status": status}
        conn.execute("""UPDATE tasks SET status=?, lease_token_hash=NULL, lease_expires_at=NULL,
                     failure_json=?, updated_at=? WHERE task_id=?""",
                     (status, json.dumps(failure, ensure_ascii=False), utc_now(), task_id))
        self._save_idempotent(conn, "task.fail", key, payload, 200, body)
        self._json(200, body)

    def _message(self, conn, payload):
        key = require_text(payload, "idempotency_key", 200)
        previous = self._idempotent(conn, "message.create", key, payload)
        if previous:
            self._json(*previous)
            return
        kind = payload.get("kind")
        if kind not in MESSAGE_KINDS:
            raise ValueError("invalid message kind")
        from_node = require_text(payload, "from_node_id", 80)
        if not conn.execute("SELECT 1 FROM nodes WHERE node_id=?", (from_node,)).fetchone():
            raise ValueError("from_node_id not registered")
        to_node = payload.get("to_node_id")
        if to_node and not conn.execute("SELECT 1 FROM nodes WHERE node_id=?", (to_node,)).fetchone():
            raise ValueError("to_node_id not registered")
        body = {"message_id": new_id("msg"), "conversation_id": payload.get("conversation_id") or new_id("conv"),
                "task_id": payload.get("task_id"), "from_node_id": from_node, "to_node_id": to_node,
                "kind": kind, "body": require_text(payload, "body", 10000), "created_at": utc_now()}
        conn.execute("""INSERT INTO messages(message_id,conversation_id,task_id,from_node_id,to_node_id,
                     kind,body,idempotency_key,request_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (body["message_id"], body["conversation_id"], body["task_id"], from_node, to_node,
                      kind, body["body"], key, request_hash(payload), body["created_at"]))
        self._save_idempotent(conn, "message.create", key, payload, 201, body)
        self._json(201, body)

    def _ack(self, conn, message_id, payload):
        node_id = require_text(payload, "node_id", 80)
        row = conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
        if not row:
            raise ValueError("message not found")
        if row["to_node_id"] and row["to_node_id"] != node_id:
            raise ValueError("message is addressed to another node")
        conn.execute("UPDATE messages SET acked_at=? WHERE message_id=?", (utc_now(), message_id))
        self._json(200, {"ok": True, "message_id": message_id})


def main() -> int:
    ap = argparse.ArgumentParser(description="FSTDD internal coordination hub")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--db", default="./fstdd-hub.sqlite3")
    args = ap.parse_args()
    db = Path(args.db).resolve()
    init_db(db)
    HubHandler.db_path = db
    server = ThreadingHTTPServer((args.host, args.port), HubHandler)
    print(f"[fstdd-hub] listening on {args.host}:{args.port} -> {db}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
