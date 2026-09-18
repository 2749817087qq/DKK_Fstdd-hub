#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git isolation and serial integration helpers for FSTDD tasks.

The helpers are intentionally conservative: task worktrees must live outside
any checkout, task branches are separate refs, and integration is only allowed
from a clean target branch.  A conflict is reported to the caller; this module
never force-pushes, silently resets, or resolves a conflict on behalf of an
integrator.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")


class GitIsolationError(RuntimeError):
    """A task violates worktree, branch, or integration safety rules."""


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), text=True,
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode:
        raise GitIsolationError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result


def repo_root(path: str | Path) -> Path:
    path = Path(path).resolve()
    result = _run(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise GitIsolationError("task_id contains invalid characters")
    return task_id


def task_branch(task_id: str) -> str:
    return "task/" + validate_task_id(task_id)


def validate_external_worktree(repo: str | Path, worktree: str | Path) -> tuple[Path, Path]:
    root = repo_root(repo)
    worktree_path = Path(worktree).resolve()
    if _inside(worktree_path, root):
        raise GitIsolationError("worktree must be outside the main Git checkout")
    if worktree_path == root:
        raise GitIsolationError("worktree cannot equal the main checkout")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    return root, worktree_path


def create_task_worktree(repo: str | Path, worktree: str | Path,
                         task_id: str, base_sha: str = "HEAD") -> dict:
    """Create an isolated task branch/worktree from a fixed base SHA.

    Existing worktrees or branches are never overwritten.  Repeating the same
    request is safe only when both the branch and worktree already point at the
    requested base; otherwise it fails loudly.
    """
    root, worktree_path = validate_external_worktree(repo, worktree)
    branch = task_branch(task_id)
    base = _run(root, "rev-parse", base_sha).stdout.strip()
    branch_exists = _run(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0
    if worktree_path.exists() and any(worktree_path.iterdir()):
        existing_root = _run(worktree_path, "rev-parse", "--show-toplevel", check=False)
        if existing_root.returncode:
            raise GitIsolationError(f"worktree path is not an empty checkout: {worktree_path}")
        existing_head = _run(worktree_path, "rev-parse", "HEAD").stdout.strip()
        if existing_head != base:
            raise GitIsolationError("existing worktree HEAD differs from requested base SHA")
        return {"branch": branch, "base_sha": base, "worktree": str(worktree_path), "reused": True}
    if branch_exists:
        branch_head = _run(root, "rev-parse", branch).stdout.strip()
        if branch_head != base:
            raise GitIsolationError("existing task branch differs from requested base SHA")
        _run(root, "worktree", "add", str(worktree_path), branch)
    else:
        _run(root, "worktree", "add", "-b", branch, str(worktree_path), base)
    return {"branch": branch, "base_sha": base, "worktree": str(worktree_path), "reused": False}


def _literal_prefix(pattern: str) -> str:
    chars = []
    for char in pattern.replace("\\", "/"):
        if char in "*?[":
            break
        chars.append(char)
    return "".join(chars).rstrip("/")


def patterns_overlap(left: str, right: str) -> bool:
    """Return True when two path globs might overlap.

    False negatives are unsafe, so wildcard patterns with a shared literal
    directory prefix are treated as overlapping.  Exact paths and ancestor
    paths also overlap.
    """
    left = left.replace("\\", "/").strip("/")
    right = right.replace("\\", "/").strip("/")
    if not left or not right:
        return True
    if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    if fnmatch.fnmatchcase(left, right) or fnmatch.fnmatchcase(right, left):
        return True
    lp, rp = _literal_prefix(left), _literal_prefix(right)
    if lp and rp and (lp.startswith(rp + "/") or rp.startswith(lp + "/")):
        return True
    if lp and rp and lp == rp:
        return True
    return False


def scope_conflicts(left: dict, right: dict) -> list[dict]:
    """Conservatively report allowed/frozen path conflicts between tasks."""
    left_allowed = [str(v) for v in left.get("allowed", [])]
    right_allowed = [str(v) for v in right.get("allowed", [])]
    left_frozen = [str(v) for v in left.get("frozen", [])]
    right_frozen = [str(v) for v in right.get("frozen", [])]
    conflicts = []
    for a in left_allowed:
        for b in right_allowed:
            if patterns_overlap(a, b):
                conflicts.append({"kind": "allowed/allowed", "left": a, "right": b})
    for a in left_allowed:
        for b in right_frozen:
            if patterns_overlap(a, b):
                conflicts.append({"kind": "left-allowed/right-frozen", "left": a, "right": b})
    for a in right_allowed:
        for b in left_frozen:
            if patterns_overlap(a, b):
                conflicts.append({"kind": "right-allowed/left-frozen", "left": a, "right": b})
    return conflicts


def ensure_target_up_to_date(root: Path, target_branch: str = "master") -> None:
    """确认本地 target 分支不落后于远端，否则集成后必然推不上去。

    ⚠️ 多机场景下「本地干净」**不等于**「可以集成」：
    ensure_clean_target 只检查本地工作区，而别的机器可能已经推送了新提交。
    本地看着干净 -> ff-only 合并成功 -> 推送被拒 -> **本地 master 与远端分叉**，
    必须手动 rebase 才能重来。这个错误在**推送**时才暴露，发现得太晚。

    实测（双 clone 模拟两台机器）：B 不 fetch 时 ensure_clean_target 一路绿灯，
    集成本地成功，推送报 `! [rejected] master -> master (fetch first)`。

    没有 upstream 的纯本地仓库无从比较，直接放行（不破坏离线/单机使用）。
    """
    up = _run(root, "rev-parse", "--abbrev-ref", f"{target_branch}@{{upstream}}", check=False)
    if up.returncode != 0 or not up.stdout.strip():
        return  # 没有上游（纯本地仓库），无从比较
    remote_ref = up.stdout.strip()
    remote = remote_ref.split("/", 1)[0]
    # GIT_TERMINAL_PROMPT=0：远端不可达时立刻失败，不要卡在交互式凭据输入上
    # （本机经代理访问 GitHub，代理时段性故障时很容易挂住）。
    env = dict(__import__("os").environ, GIT_TERMINAL_PROMPT="0")
    fetch = subprocess.run(["git", "fetch", "--quiet", remote], cwd=str(root), text=True,
                           capture_output=True, encoding="utf-8", errors="replace", env=env)
    if fetch.returncode != 0:
        raise GitIsolationError(
            f"cannot fetch {remote}; refusing to integrate because the remote state is "
            f"unknown. If you are certain nobody else has pushed, re-run with "
            f"check_remote=False")
    behind = _run(root, "rev-list", "--count", f"{target_branch}..{remote_ref}").stdout.strip()
    if behind and behind != "0":
        raise GitIsolationError(
            f"target branch {target_branch} is {behind} commit(s) behind {remote_ref}; "
            f"pull or rebase before integrating, otherwise the merge succeeds locally "
            f"but the push is rejected and your branch diverges")


def ensure_clean_target(repo: str | Path, target_branch: str = "master",
                        check_remote: bool = True) -> Path:
    root = repo_root(repo)
    branch = _run(root, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if branch != target_branch:
        raise GitIsolationError(f"integration must run on {target_branch}, currently on {branch}")
    status = _run(root, "status", "--porcelain").stdout.strip()
    if status:
        raise GitIsolationError("integration target has uncommitted changes")
    if check_remote:
        ensure_target_up_to_date(root, target_branch)
    return root


def integrate_task_branch(repo: str | Path, task_branch_name: str,
                          target_branch: str = "master",
                          check_remote: bool = True) -> dict:
    """Merge a task branch serially into a clean target branch.

    The default fast-forward-only policy prevents an unexpected merge commit
    from hiding a divergent base.  Conflicts are returned as an exception and
    must be recorded as BLOCKED by the control plane.
    """
    root = ensure_clean_target(repo, target_branch, check_remote=check_remote)
    if not task_branch_name.startswith("task/"):
        raise GitIsolationError("only task/* branches may be integrated")
    task_sha = _run(root, "rev-parse", task_branch_name).stdout.strip()
    before = _run(root, "rev-parse", target_branch).stdout.strip()
    _run(root, "merge", "--ff-only", task_branch_name)
    after = _run(root, "rev-parse", target_branch).stdout.strip()
    return {"target_branch": target_branch, "task_branch": task_branch_name,
            "before_sha": before, "task_sha": task_sha, "after_sha": after}


def _main() -> int:
    parser = argparse.ArgumentParser(description="FSTDD Git task isolation helpers")
    subs = parser.add_subparsers(dest="command", required=True)
    p_worktree = subs.add_parser("create-worktree")
    p_worktree.add_argument("repo")
    p_worktree.add_argument("worktree")
    p_worktree.add_argument("task_id")
    p_worktree.add_argument("--base-sha", default="HEAD")
    p_scope = subs.add_parser("scope-conflicts")
    p_scope.add_argument("left_json")
    p_scope.add_argument("right_json")
    p_merge = subs.add_parser("integrate")
    p_merge.add_argument("repo")
    p_merge.add_argument("task_branch")
    p_merge.add_argument("--target", default="master")
    p_merge.add_argument("--no-check-remote", action="store_true",
                         help="跳过「本地是否落后远端」检查（离线且确认无人推送时才用）")
    args = parser.parse_args()
    try:
        if args.command == "create-worktree":
            result = create_task_worktree(args.repo, args.worktree, args.task_id, args.base_sha)
        elif args.command == "scope-conflicts":
            result = scope_conflicts(json.loads(args.left_json), json.loads(args.right_json))
        else:
            result = integrate_task_branch(args.repo, args.task_branch, args.target,
                                          check_remote=not args.no_check_remote)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GitIsolationError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
