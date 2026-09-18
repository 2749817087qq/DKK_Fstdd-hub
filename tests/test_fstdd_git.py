"""Tests for FSTDD Git isolation and serial integration helpers."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "fstdd_git.py"
_SPEC = importlib.util.spec_from_file_location("fstdd_git", _TOOL)
fstdd_git = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(fstdd_git)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "FSTDD Test")
    (repo / "README.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


def test_create_worktree_is_outside_repo_and_reuses_same_request(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "work" / "task-a"
    first = fstdd_git.create_task_worktree(repo, worktree, "task-a")
    assert first["branch"] == "task/task-a"
    assert Path(first["worktree"]).resolve() == worktree.resolve()
    assert (worktree / ".git").exists()
    second = fstdd_git.create_task_worktree(repo, worktree, "task-a")
    assert second["reused"] is True
    assert second["base_sha"] == first["base_sha"]


def test_worktree_inside_checkout_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(fstdd_git.GitIsolationError, match="outside"):
        fstdd_git.create_task_worktree(repo, repo / "nested", "task-b")


def test_existing_branch_with_different_base_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "work" / "task-c"
    fstdd_git.create_task_worktree(repo, worktree, "task-c")
    (worktree / "README.txt").write_text("changed\n", encoding="utf-8")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "task change")
    with pytest.raises(fstdd_git.GitIsolationError, match="differs"):
        fstdd_git.create_task_worktree(repo, tmp_path / "other", "task-c")


def test_scope_conflicts_are_conservative(tmp_path):
    left = {"allowed": ["tools/fstdd_hub.py"], "frozen": ["upstream/tests/"]}
    right = {"allowed": ["tools/*.py"], "frozen": ["tools/fstdd_hub.py"]}
    conflicts = fstdd_git.scope_conflicts(left, right)
    assert len(conflicts) >= 2
    assert fstdd_git.scope_conflicts({"allowed": ["docs/a.md"]}, {"allowed": ["tools/b.py"]}) == []


def test_integrate_task_branch_is_fast_forward_only(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "work" / "task-d"
    fstdd_git.create_task_worktree(repo, worktree, "task-d")
    (worktree / "README.txt").write_text("task\n", encoding="utf-8")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "task change")
    result = fstdd_git.integrate_task_branch(repo, "task/task-d")
    assert result["after_sha"] == result["task_sha"]
    assert (repo / "README.txt").read_text(encoding="utf-8") == "task\n"


def test_integration_rejects_dirty_target_and_non_task_branch(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "README.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(fstdd_git.GitIsolationError, match="uncommitted"):
        fstdd_git.integrate_task_branch(repo, "task/nope")
    git(repo, "restore", "README.txt")
    with pytest.raises(fstdd_git.GitIsolationError, match="task"):
        fstdd_git.integrate_task_branch(repo, "feature/nope")


def make_remote_pair(tmp_path):
    """建一个裸库 + 两个 clone，模拟**两台机器**。

    返回 (origin, machineA, machineB)。两边初始同步于同一个提交。
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(origin)],
                   text=True, capture_output=True, check=True)
    machines = []
    for name in ("machineA", "machineB"):
        target = tmp_path / name
        subprocess.run(["git", "clone", str(origin), str(target)],
                       text=True, capture_output=True, check=True)
        git(target, "config", "user.email", "test@example.invalid")
        git(target, "config", "user.name", "FSTDD Test")
        machines.append(target)
    machine_a, machine_b = machines
    (machine_a / "README.txt").write_text("base\n", encoding="utf-8")
    git(machine_a, "add", ".")
    git(machine_a, "commit", "-m", "base")
    git(machine_a, "push", "origin", "master")
    git(machine_b, "pull", "--ff-only")
    return origin, machine_a, machine_b


def test_integration_is_rejected_when_local_target_is_behind_remote(tmp_path):
    """本地干净 ≠ 可以集成 —— 别的机器可能已经推送了。

    旧行为：ensure_clean_target 只查本地工作区，一路绿灯 -> ff-only 合并本地成功
    -> **推送**才被拒（fetch first）-> 本地 master 已与远端分叉，必须手动 rebase。
    错误在推送时才暴露，发现得太晚。
    """
    _, machine_a, machine_b = make_remote_pair(tmp_path)

    # A：在 task 分支上干活，集成并推送成功
    git(machine_a, "checkout", "-b", "task/a")
    (machine_a / "a.txt").write_text("A\n", encoding="utf-8")
    git(machine_a, "add", ".")
    git(machine_a, "commit", "-m", "A work")
    git(machine_a, "checkout", "master")
    fstdd_git.integrate_task_branch(machine_a, "task/a")
    git(machine_a, "push", "origin", "master")

    # B：基于旧 master 干活。B 本地工作区是干净的，但已经落后远端
    git(machine_b, "checkout", "-b", "task/b")
    (machine_b / "b.txt").write_text("B\n", encoding="utf-8")
    git(machine_b, "add", ".")
    git(machine_b, "commit", "-m", "B work")
    git(machine_b, "checkout", "master")

    # 注意：不能拿 master 与 origin/master 比 —— 检查过程本身会 fetch，
    # fetch 之后 origin/master 已经前进，两者本来就不相等。
    # 要断言的是「master 自己没被改动」，即没有产生需要 rebase 才能收拾的分叉。
    master_before = git(machine_b, "rev-parse", "master").stdout.strip()

    with pytest.raises(fstdd_git.GitIsolationError) as exc:
        fstdd_git.integrate_task_branch(machine_b, "task/b")
    msg = str(exc.value)
    assert "behind" in msg, msg
    assert "origin/master" in msg, msg

    assert git(machine_b, "rev-parse", "master").stdout.strip() == master_before


def test_check_remote_false_allows_offline_integration(tmp_path):
    """离线且确认无人推送时可显式绕过（不破坏单机/离线使用）。"""
    _, machine_a, machine_b = make_remote_pair(tmp_path)

    git(machine_a, "checkout", "-b", "task/a")
    (machine_a / "a.txt").write_text("A\n", encoding="utf-8")
    git(machine_a, "add", ".")
    git(machine_a, "commit", "-m", "A work")
    git(machine_a, "checkout", "master")
    fstdd_git.integrate_task_branch(machine_a, "task/a")
    git(machine_a, "push", "origin", "master")

    git(machine_b, "checkout", "-b", "task/b")
    (machine_b / "b.txt").write_text("B\n", encoding="utf-8")
    git(machine_b, "add", ".")
    git(machine_b, "commit", "-m", "B work")
    git(machine_b, "checkout", "master")

    result = fstdd_git.integrate_task_branch(machine_b, "task/b", check_remote=False)
    assert result["after_sha"] != result["before_sha"]


def test_repo_without_upstream_is_not_blocked(tmp_path):
    """纯本地仓库没有上游，无从比较 —— 必须放行，不能因为遥测不到远端就拒绝集成。"""
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-b", "task/local")
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "local work")
    git(repo, "checkout", "master")

    result = fstdd_git.integrate_task_branch(repo, "task/local")
    assert result["after_sha"] != result["before_sha"]
