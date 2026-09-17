#!/usr/bin/env python3
"""FSTDD 任务看板（只读）

经 SSH 读取控制面 hub 的状态，在终端渲染任务流转全景。
不写入任何数据，不修改 hub 行为，可安全反复执行。

用法:
    python tools/hub_board.py                 # 单次快照
    python tools/hub_board.py --watch 10      # 每 10 秒刷新
    python tools/hub_board.py --host myserver # 指定 ssh 别名
    python tools/hub_board.py --local         # 已自建端口转发时，直连 127.0.0.1:8788
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_HOST = "fstdd-hub"
DEFAULT_PORT = 8788
SEP = "@@FSTDD-BOARD@@"

# 有数据支撑的分组；其余维度在 NOTES 中说明
STATUS_GROUPS = [
    ("待领池", ("pending",)),
    ("进行中", ("claimed", "running")),
    ("已完成", ("done",)),
    ("异常/失败", ("failed", "blocked")),
]


def _http_get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_remote(host: str, port: int, paths: list[str]) -> dict[str, dict]:
    """一次 ssh 取回多个接口，减少连接开销。"""
    parts = []
    for p in paths:
        # curl 输出不带尾换行，故显式 echo 补一个，避免分隔符与 JSON 粘行
        parts.append(f"printf '{SEP}{p}\\n'")
        parts.append(f"curl -s --max-time 10 http://127.0.0.1:{port}{p}")
        parts.append("echo")
    remote = " ; ".join(parts)
    proc = subprocess.run(
        ["ssh", host, remote],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh 失败 (rc={proc.returncode}): {proc.stderr.strip()}")
    out: dict[str, dict] = {}
    # 按分隔符整体切分，不依赖逐行读取
    for chunk in proc.stdout.split(SEP)[1:]:
        marker, _, body = chunk.partition("\n")
        out[marker.strip()] = _safe_json(body)
    return out


def _safe_json(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:200]}


def _width(text: str) -> int:
    """终端显示宽度：CJK 字符占 2 列。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))


def _pad(text: str, width: int) -> str:
    """按显示宽度右填充，保证中英混排也能对齐。"""
    s = str(text)
    return s + " " * max(0, width - _width(s))


def _rel_time(epoch: float | None) -> str:
    if not epoch:
        return "-"
    delta = time.time() - float(epoch)
    if delta < 0:
        return "刚刚"
    if delta < 60:
        return f"{int(delta)} 秒前"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"


def _iso(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:19]
    return dt.astimezone().strftime("%m-%d %H:%M:%S")


def _lease_left(task: dict) -> str:
    exp = task.get("lease_expires_at")
    if not exp:
        return "-"
    left = float(exp) - time.time()
    if left <= 0:
        return "已过期"
    return f"{int(left)}s"


def collect(host: str, port: int, local: bool) -> dict:
    nodes_path = "/nodes"
    paths = ["/health", nodes_path, "/tasks"]

    if local:
        base = f"http://127.0.0.1:{port}"
        data = {p: _http_get(base + p) for p in paths}
    else:
        data = _fetch_remote(host, port, paths)

    nodes = data.get(nodes_path, {}).get("nodes", []) or []
    msg_paths = [f"/messages?node_id={n.get('node_id')}" for n in nodes]
    if msg_paths:
        if local:
            base = f"http://127.0.0.1:{port}"
            for p in msg_paths:
                data[p] = _http_get(base + p)
        else:
            data.update(_fetch_remote(host, port, msg_paths))

    return data


def render(data: dict, width: int = 78) -> str:
    L: list[str] = []
    bar = "=" * width
    L.append(bar)
    L.append(f" FSTDD 任务看板    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}    (只读)")
    L.append(bar)

    health = data.get("/health", {})
    nodes = data.get("/nodes", {}).get("nodes", []) or []
    tasks = data.get("/tasks", {}).get("tasks", []) or []

    counts = {name: sum(1 for t in tasks if t.get("status") in grp) for name, grp in STATUS_GROUPS}

    L.append("")
    L.append(" 总览")
    ok = "ok" if health.get("ok") else "DOWN"
    L.append(
        f"   健康: {ok}    节点: {len(nodes)}    任务: {len(tasks)}"
        f"  (待领 {counts['待领池']} / 进行 {counts['进行中']}"
        f" / 完成 {counts['已完成']} / 异常 {counts['异常/失败']})"
    )

    L.append("")
    L.append(f" 节点 ({len(nodes)})")
    if not nodes:
        L.append("   （无）")
    else:
        L.append(
            "   " + _pad("ID", 22) + _pad("机器", 14) + _pad("平台", 10)
            + _pad("状态", 10) + "心跳"
        )
        for n in nodes:
            L.append(
                "   " + _pad(n.get("node_id", "-"), 22)
                + _pad(n.get("machine_name", "-"), 14)
                + _pad(n.get("platform", "-"), 10)
                + _pad(n.get("status", "-"), 10)
                + _rel_time(n.get("last_seen"))
            )
            caps = n.get("capabilities") or []
            if caps:
                L.append(f"      能力: {', '.join(map(str, caps))}")

    # 汇总全部消息（各节点 /messages 去重后合并）
    msgs: list[dict] = []
    seen: set[str] = set()
    for key, val in data.items():
        if key.startswith("/messages?"):
            for m in (val.get("messages") or []):
                mid = str(m.get("message_id"))
                if mid not in seen:
                    seen.add(mid)
                    msgs.append(m)
    msgs.sort(key=lambda m: str(m.get("created_at") or ""))

    # 按任务归集留言 -> BBS 线程
    # 注意：服务端过滤含 acked_at IS NULL，被 ack 的消息不会出现在这里。
    #       因此留言板上的讨论**绝不可 ack**，否则讨论串会凭空消失。
    thread: dict[str, list[dict]] = {}
    for m in msgs:
        mtid = m.get("task_id")
        if mtid:
            thread.setdefault(str(mtid), []).append(m)

    for name, grp in STATUS_GROUPS:
        rows = [t for t in tasks if t.get("status") in grp]
        L.append("")
        L.append(f" {name} ({len(rows)})")
        if not rows:
            L.append("   （空）")
            continue
        for t in rows:
            tid = str(t.get("task_id", "-"))
            owner = t.get("owner_node_id") or "-"
            base = str(t.get("base_git_sha", "-"))[:7]
            result = str(t.get("result_git_sha", "-"))[:7] if t.get("result_git_sha") else "-"
            L.append(f"   {tid}")
            L.append(f"      {str(t.get('summary', ''))[:60]}")
            L.append(
                f"      类型 {t.get('kind', '-')}  领取者 {owner}"
                f"  尝试 {t.get('attempt', 0)}  更新 {_iso(t.get('updated_at'))}"
            )
            L.append(f"      基线 {base} -> 结果 {result}   租约剩余 {_lease_left(t)}")
            if t.get("failure_json"):
                try:
                    fj = json.loads(t["failure_json"]) if isinstance(t["failure_json"], str) else t["failure_json"]
                    L.append(f"      失败: {str(fj.get('summary', fj))[:60]}")
                except (ValueError, AttributeError):
                    L.append(f"      失败: {str(t.get('failure_json'))[:60]}")

            # 任务留言线程（BBS）
            tl = thread.get(tid, [])
            if tl:
                L.append(f"      留言 ({len(tl)})")
                for m in tl:
                    L.append(
                        f"         - {_iso(m.get('created_at'))} "
                        f"{str(m.get('from_node_id', '-'))} [{str(m.get('kind', '-'))}]"
                    )
                    body_line = str(m.get("body", "")).replace(chr(10), " ")
                    L.append("           " + body_line[:68])
            else:
                L.append("      留言 (0)   （暂无留言）")

    L.append("")
    L.append(f" 消息 ({len(msgs)})")
    if not msgs:
        L.append("   （空）")
    else:
        for m in msgs[-15:]:
            L.append(
                f"   [{_iso(m.get('created_at'))}] {str(m.get('kind', '-')):<9}"
                f"{str(m.get('from_node_id', '-'))} -> {str(m.get('to_node_id') or '广播')}"
            )
            body = str(m.get("body", "")).replace("\n", " ")
            L.append(f"      {body[:64]}")
            if m.get("acked_at"):
                L.append(f"      已确认 {_iso(m.get('acked_at'))}")

    by_node: dict[str, list[dict]] = {}
    for t in tasks:
        owner = t.get("owner_node_id")
        if owner:
            by_node.setdefault(str(owner), []).append(t)

    L.append("")
    L.append(" agent 任务历史")
    if not by_node:
        L.append("   （暂无任何领取记录）")
    else:
        for node, items in sorted(by_node.items()):
            done = sum(1 for i in items if i.get("status") == "done")
            fail = sum(1 for i in items if i.get("status") in ("failed", "blocked"))
            live = sum(1 for i in items if i.get("status") in ("claimed", "running"))
            L.append(f"   {node}: 共 {len(items)}  (完成 {done} / 失败 {fail} / 进行 {live})")
            for i in items:
                L.append(f"      - {i.get('task_id')} [{i.get('status')}] {str(i.get('summary', ''))[:44]}")

    L.append("")
    L.append(" 说明")
    L.append("   * 已领取 / agent 历史 / 实时状态：由 hub 现有字段直接支撑")
    L.append("   * 完成评分、合成情况、开发阶段：hub 无对应字段，需先打通链路")
    L.append("   * 无「已分配」概念：claim 由服务端自选最早 pending，中枢不派单")
    L.append(bar)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="FSTDD 任务看板（只读）")
    ap.add_argument("--host", default=DEFAULT_HOST, help="ssh 别名或主机（默认 fstdd-hub）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="hub 端口（默认 8788）")
    ap.add_argument("--local", action="store_true", help="直连本机 127.0.0.1（需已建端口转发）")
    ap.add_argument("--watch", type=int, metavar="SEC", help="按秒间隔持续刷新，0 或省略则单次")
    args = ap.parse_args()

    interval = args.watch or 0
    while True:
        try:
            data = collect(args.host, args.port, args.local)
        except urllib.error.URLError as exc:
            print(f"[FAIL] 无法连接 hub: {exc}", file=sys.stderr)
            print("提示：先用 ssh -L 8788:127.0.0.1:8788 -N fstdd-hub 建转发，再跑 --local", file=sys.stderr)
            return 1
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            print(f"[FAIL] 读取失败: {exc}", file=sys.stderr)
            return 1

        if interval:
            subprocess.run(["cls"] if sys.platform == "win32" else ["clear"], shell=True)
        print(render(data))

        if not interval:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
