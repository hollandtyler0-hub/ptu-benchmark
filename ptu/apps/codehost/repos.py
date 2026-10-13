"""Deterministic, source-free Git repository recipes."""

from ptu.common.binaries import REAL_GIT
import os
import json
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml
from ptu.sandbox.teardown import remove_episode


_GIT = str(REAL_GIT)
_ENV = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": "/dev/null",
        "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "PTU Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "PTU Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-10-01T00:00:00+00:00"}

def _git(work: Path, *args: str, env: dict | None = None) -> str:
    # Fixture construction is synchronous. Disable Git's background maintenance
    # so no descendant can still touch the temporary repository at teardown.
    result = subprocess.run([_GIT, "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
                            cwd=work, env=env or _ENV,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=30, check=False)
    if result.returncode:
        raise ValueError("E_REPO_BUILD")
    return result.stdout.decode("utf-8", "replace").strip()


@contextmanager
def _temporary_git_directory(parent: Path):
    directory = Path(tempfile.mkdtemp(dir=parent))
    try:
        yield directory
    finally:
        remove_episode(directory)


def build_repo(recipe_dir: Path, out_bare: Path) -> dict[str, str]:
    """Build a deterministic `main` history; never read links or overwrite a repo."""
    recipe = Path(recipe_dir)
    output = Path(out_bare)
    if (not recipe.is_dir() or recipe.is_symlink() or output.is_symlink()
            or not output.parent.is_dir() or output.parent.is_symlink()):
        raise ValueError("E_REPO_RECIPE")
    if output.exists():
        raise ValueError("E_REPO_EXISTS")
    history_file = recipe / ".ptu-history.json"
    history_count = 1
    authored_history = []
    if history_file.exists():
        if history_file.is_symlink() or not history_file.is_file():
            raise ValueError("E_REPO_RECIPE")
        try:
            config = json.loads(history_file.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("E_REPO_RECIPE") from None
        if isinstance(config, dict) and set(config) == {"commits"} and type(config["commits"]) is int:
            if not 2 <= config["commits"] <= 100 or (recipe / "docs" / "history").exists():
                raise ValueError("E_REPO_RECIPE")
            history_count = config["commits"]
        elif (isinstance(config, list) or isinstance(config, dict)
              and len(config) == 1 and isinstance(config.get('history', config.get('commits')), list)):
            names = config if isinstance(config, list) else config.get('history', config.get('commits'))
            if (not 1 <= len(names) <= 99
                    or any(not isinstance(name, str) or not re.fullmatch(
                        r'history/[0-9]{2}-[a-z0-9-]+', name) for name in names)
                    or len(names) != len(set(names))
                    or names != sorted(names)):
                raise ValueError("E_REPO_RECIPE")
            authored_history = [recipe / name for name in names]
            if any(not path.is_dir() or path.is_symlink() for path in authored_history):
                raise ValueError("E_REPO_RECIPE")
        else:
            raise ValueError("E_REPO_RECIPE")
    files = []
    total = 0
    for path in recipe.rglob("*"):
        if path.is_symlink() or ".git" in path.relative_to(recipe).parts:
            raise ValueError("E_REPO_RECIPE")
        if path.is_dir():
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("E_REPO_RECIPE")
        total += metadata.st_size
        if total > 32 * 1024 * 1024 or len(files) >= 5000:
            raise ValueError("E_REPO_RECIPE")
        files.append(path)
    with _temporary_git_directory(output.parent) as directory:
        work = directory / "work"
        work.mkdir()
        _git(work, "init", "-q", "-b", "main")
        for history_dir in authored_history:
            metadata = history_dir / 'commit.yaml'
            if metadata.is_symlink() or not metadata.is_file():
                raise ValueError('E_REPO_RECIPE')
            try:
                commit = yaml.safe_load(metadata.read_text())
            except (OSError, UnicodeError, yaml.YAMLError):
                raise ValueError('E_REPO_RECIPE') from None
            if (not isinstance(commit, dict) or set(commit) != {'author', 'date', 'message'}
                    or not isinstance(commit['author'], str) or not commit['author'].strip()
                    or not isinstance(commit['message'], str) or not commit['message'].strip()
                    or '\n' in commit['author']):
                raise ValueError('E_REPO_RECIPE')
            date = (commit['date'].isoformat() if hasattr(commit['date'], 'isoformat')
                    else str(commit['date']))
            if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})', date):
                raise ValueError('E_REPO_RECIPE')
            for source in sorted(history_dir.rglob('*')):
                if source.is_dir() or source == metadata:
                    continue
                target = work / source.relative_to(history_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            _git(work, 'add', '--all')
            author_match = re.fullmatch(r'(.+?)\s*<([^<>\s]+@[^<>\s]+)>', commit['author'])
            author_name = author_match.group(1) if author_match else commit['author']
            author_email = author_match.group(2) if author_match else 'fixture@example.invalid'
            author_env = {**_ENV, 'GIT_AUTHOR_NAME': author_name,
                          'GIT_AUTHOR_EMAIL': author_email,
                          'GIT_COMMITTER_NAME': author_name,
                          'GIT_COMMITTER_EMAIL': author_email,
                          'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date}
            _git(work, 'commit', '-qm', commit['message'], env=author_env)
        if history_count > 1:
            notes = work / "docs" / "history"
            notes.mkdir(parents=True)
            for index in range(1, history_count):
                (notes / f"engineering-{index:02d}.md").write_text(
                    f"# Synthetic engineering note {index:02d}\n\nRoutine release preparation.\n")
                _git(work, "add", "--all")
                _git(work, "commit", "-qm", f"Document engineering note {index:02d}")
        if authored_history:
            for existing in work.iterdir():
                if existing.name == '.git':
                    continue
                if existing.is_dir():
                    shutil.rmtree(existing)
                else:
                    existing.unlink()
        for source in sorted(files):
            if source == history_file or (authored_history and source.relative_to(recipe).parts[0] == 'history'):
                continue
            target = work / source.relative_to(recipe)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if source.stat().st_mode & stat.S_IXUSR else 0o644)
        _git(work, "add", "--all")
        _git(work, "commit", "--allow-empty", "-qm", "Seeded PTU fixture")
        sha = _git(work, "rev-parse", "HEAD")
        _git(work, "clone", "--bare", "--no-hardlinks", "-q", str(work), str(output))
    return {"main": sha}
