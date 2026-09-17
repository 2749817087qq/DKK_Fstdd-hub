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
