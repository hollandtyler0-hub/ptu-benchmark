"""A fresh episode may not have a workstation directory yet."""

from ptu.common.binaries import REAL_GIT
from types import SimpleNamespace
import subprocess

import pytest

from ptu.common.state import StateStore
from ptu.sandbox.repo_setup import prepare_repo
from ptu.sandbox.runner import available


def test_prepare_repo_creates_work_parent_before_cloning(tmp_path):
    recipe = tmp_path / "task" / "repo"
    recipe.mkdir(parents=True)
    (recipe / "README.md").write_text("Synthetic coding recipe\n")
    (recipe / ".ptu-history.json").write_text('{"commits": 40}\n')
    task = SimpleNamespace(
        source_path=str(recipe.parent / "task.yaml"), metadata={},
        workstation={"kind": "repo", "repo": {
            "codehost_repo": "tidewater/billing-core", "recipe": "repo", "checkout": "main"}})
    state = StateStore({"codehost": {"repos": {"tidewater/billing-core": {
        "branches": {"main": {"head_commit": "a" * 64}},
        "checks": [{"name": "ci/unit", "sha": "a" * 64, "conclusion": "failure"}],
    }}}})
    episode = tmp_path / "episode"
    prepare_repo(task, episode, state, {})
    assert (episode / "ws" / "work" / "README.md").read_text() == "Synthetic coding recipe\n"
    assert state.docs["codehost"]["repos"]["tidewater/billing-core"]["_bare_repo"]
    repo = state.docs["codehost"]["repos"]["tidewater/billing-core"]
    head = subprocess.run([str(REAL_GIT), "rev-parse", "HEAD"],
                          cwd=episode / "ws" / "work", capture_output=True,
                          text=True, check=True).stdout.strip()
    assert repo["branches"]["main"]["head_commit"] == head
    assert repo["checks"][0]["sha"] == head
    work = episode / "ws" / "work"
    rows = subprocess.run([str(REAL_GIT), "rev-list", "--objects", "--all"],
                          cwd=work, capture_output=True, text=True, check=True).stdout.splitlines()
    objects = work / ".git" / "objects"
    assert all((objects / row[:2] / row[2:40]).is_file() for row in rows)


def test_plural_main_commit_event_keeps_engineering_document_verbatim(tmp_path):
    recipe = tmp_path / 'task' / 'repo'
    (recipe / 'docs').mkdir(parents=True)
    (recipe / 'docs' / 'ENGINEERING.md').write_text('# Engineering\n')
    task = SimpleNamespace(source_path=str(recipe.parent / 'task.yaml'), metadata={},
        scripted_events=[{'mutations': [{'service': 'codehost', 'op': 'commit_to_branch',
                          'args': {'branch': 'main'}}]}],
        workstation={'kind': 'repo', 'repo': {'codehost_repo': 'tidewater/billing-core',
                     'recipe': 'repo', 'checkout': 'main'}})
    state = StateStore({'codehost': {'repos': {'tidewater/billing-core': {
        'branches': {'main': {'head_commit': 'a' * 64}},
        'files': {'main': {'docs/ENGINEERING.md': '# Engineering\n'}},
        'checks': []}}}})
    episode = tmp_path / 'episode'
    prepare_repo(task, episode, state, {})
    document = (episode / 'ws' / 'work' / 'docs' / 'ENGINEERING.md').read_text()
    assert document == '# Engineering\n'
    assert state.docs['codehost']['repos']['tidewater/billing-core']['files']['main'][
        'docs/ENGINEERING.md'] == document


@pytest.mark.skipif(not available(), reason="E_SANDBOX_UNAVAILABLE")
def test_real_extreme_seeded_ci_checks_have_diagnostic_logs():
    import tempfile
    from pathlib import Path
    from ptu.harness.agent_runner import _prepare_episode, load_profile
    from ptu.tasks.loader import load_task

    home = Path.home() / ".meter-harness" / "isolation-tests"
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as name:
        episode = Path(name)
        task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-202/task.yaml")
        _prepare_episode(task, load_profile("cc-sonnet-5"), episode)
        repo = StateStore.load(episode / "seed_state").docs["codehost"]["repos"]["tidewater/ledger-export"]
        checks = {row["name"]: row for row in repo["checks"] if row.get("ref") in {"main", "refs/heads/main"}}
        assert "ruff" in checks["ci/lint"]["log"].lower()
        assert checks["ci/unit"]["log"]
        archive = episode / "ws" / "work" / "ci" / "checks"
        assert (archive / "main-unit.log").read_text() == checks["ci/unit"]["log"]
        assert (archive / "main-lint.log").read_text() == checks["ci/lint"]["log"]
