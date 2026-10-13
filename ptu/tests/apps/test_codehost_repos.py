"""Recipe commits have stable, reviewable identities."""

import errno
import subprocess

import pytest

import ptu.apps.codehost.repos as repos
import ptu.sandbox.teardown as teardown
from ptu.apps.codehost.repos import build_repo


def _git(bare, *args):
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_recipe_builds_same_commit_in_two_locations(tmp_path):
    recipe = tmp_path / "recipe"
    (recipe / "billing_core").mkdir(parents=True)
    (recipe / "billing_core" / "__init__.py").write_text("")
    (recipe / "billing_core" / "proration.py").write_text("def prorate(months):\n    return months * 100\n")
    one = tmp_path / "one.git"
    two = tmp_path / "two.git"
    refs_one = build_repo(recipe, one)
    refs_two = build_repo(recipe, two)
    assert refs_one == refs_two
    assert refs_one["main"] == _git(one, "rev-parse", "refs/heads/main")
    assert _git(one, "show", "main:billing_core/proration.py").startswith("def prorate")
    with pytest.raises(ValueError, match="E_REPO_EXISTS"):
        build_repo(recipe, one)


def test_repo_build_retries_temporary_cleanup_after_git_failure(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / "file.txt").write_text("synthetic")
    original = teardown.shutil.rmtree
    attempts = 0

    def flaky(path, *, onexc):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            onexc(original, str(path), OSError(errno.ENOTEMPTY, "synthetic race"))
        return original(path, onexc=onexc)

    def failed_git(*args, **kwargs):
        raise ValueError("E_REPO_BUILD")

    with monkeypatch.context() as patch:
        patch.setattr(repos, "_git", failed_git)
        patch.setattr(teardown.shutil, "rmtree", flaky)
        with pytest.raises(ValueError, match="E_REPO_BUILD"):
            build_repo(recipe, tmp_path / "out.git")
    assert attempts == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == ["recipe"]


def test_recipe_git_never_starts_background_maintenance(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(repos.subprocess, "run", fake_run)
    repos._git(tmp_path, "status")
    assert calls == [[repos._GIT, "-c", "gc.auto=0", "-c",
                      "maintenance.auto=false", "status"]]


def test_recipe_symlink_is_refused(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("invented-canary")
    (recipe / "escape").symlink_to(outside)
    with pytest.raises(ValueError, match="E_REPO_RECIPE"):
        build_repo(recipe, tmp_path / "out.git")
    assert not (tmp_path / "out.git").exists()


def test_recipe_history_ends_with_seeded_bug(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / ".ptu-history.json").write_text('{"commits": 4}')
    (recipe / "faulty.py").write_text("def compute(): return 0\n")
    one, two = tmp_path / "one.git", tmp_path / "two.git"
    assert build_repo(recipe, one) == build_repo(recipe, two)
    assert _git(one, "rev-list", "--count", "main") == "4"
    assert _git(one, "show", "main:faulty.py") == "def compute(): return 0"
    assert _git(one, "ls-tree", "--name-only", "main~1") == "docs"
    assert ".ptu-history.json" not in _git(one, "ls-tree", "--name-only", "main")


def test_recipe_history_rejects_invalid_configuration(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / ".ptu-history.json").write_text('{"commits": 101}')
    with pytest.raises(ValueError, match="E_REPO_RECIPE"):
        build_repo(recipe, tmp_path / "out.git")


def test_recipe_replays_authored_history_before_seeded_head(tmp_path):
    recipe = tmp_path / "recipe"
    old = recipe / "history" / "01-annual-proration"
    old.mkdir(parents=True)
    (old / "commit.yaml").write_text("author: Noor\ndate: 2026-09-18T12:00:00+00:00\nmessage: Add annual proration\n")
    (old / "annual.py").write_text("def amount(): return 1\n")
    (recipe / ".ptu-history.json").write_text('{"history": ["history/01-annual-proration"]}')
    (recipe / "annual.py").write_text("def amount(): return 1\n")
    (recipe / "monthly.py").write_text("def amount(): return 0\n")
    one, two = tmp_path / "one.git", tmp_path / "two.git"
    assert build_repo(recipe, one) == build_repo(recipe, two)
    assert _git(one, "rev-list", "--count", "main") == "2"
    assert _git(one, "log", "-1", "--format=%an", "main~1") == "Noor"
    assert _git(one, "show", "main~1:annual.py") == "def amount(): return 1"
    assert "history" not in _git(one, "ls-tree", "--name-only", "main")


def test_world_event_commits_files_and_creates_review(tmp_path):
    from ptu.apps.codehost.app import APP
    from ptu.common.clock import LogicalClock
    from ptu.common.gateway import apply_event
    from ptu.common.ids import IdFactory
    from ptu.common.state import StateStore
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / "old.py").write_text("before\n")
    bare = tmp_path / "remote.git"
    head = build_repo(recipe, bare)["main"]
    repo = {"_bare_repo": str(bare), "branches": {"main": {"head_commit": head}},
            "files": {"main": {"old.py": "before\n"}},
            "pulls": {"7": {"number": 7, "reviews": []}},
            "collaborators": [{"login": "noor", "role": "write"}]}
    state = StateStore({"codehost": {"repos": {"tidewater/demo": repo}}})
    seed = state.snapshot()
    clock = LogicalClock()
    mutation = {"service": "codehost", "op": "commit_to_branch", "args": {
        "repo": "tidewater/demo", "branch": "main", "author": "Platform",
        "message": "Fix runner", "files": {"fixed.py": "after\n"},
        "delete": ["old.py"]}}
    apply_event(mutation, state, clock, IdFactory(1), {"codehost": APP},
        event_time="2026-10-06T08:30:00-06:00")
    repo = state.docs["codehost"]["repos"]["tidewater/demo"]
    committed = repo["branches"]["main"]["head_commit"]
    reproduced = StateStore(seed)
    apply_event(mutation, reproduced, LogicalClock(), IdFactory(1), {"codehost": APP},
                event_time="2026-10-06T08:30:00-06:00", replay=True)
    assert reproduced.snapshot() == state.snapshot()
    assert _git(bare, "rev-parse", "refs/heads/main") == committed
    assert _git(bare, "show", "main:fixed.py") == "after"
    assert _git(bare, "merge-base", "main~1", "main") == head
    assert _git(bare, "log", "-1", "--format=%aI", "main") == "2026-10-06T08:30:00-06:00"
    assert "old.py" not in repo["files"]["main"]
    apply_event({"service": "codehost", "op": "create_review", "args": {
        "repo": "tidewater/demo", "pull": 7, "author": "noor",
        "state": "changes_requested", "body": "Add a test"}},
        state, clock, IdFactory(1), {"codehost": APP})
    assert repo["pulls"]["7"]["reviews"][0]["author_role"] == "write"


def test_seeded_repository_keeps_engineering_document_verbatim(tmp_path):
    recipe = tmp_path / "recipe"
    (recipe / "docs").mkdir(parents=True)
    (recipe / "docs" / "ENGINEERING.md").write_text("# Engineering\n")
    bare = tmp_path / "out.git"
    build_repo(recipe, bare)
    manual = _git(bare, "show", "main:docs/ENGINEERING.md")
    assert manual == "# Engineering"
