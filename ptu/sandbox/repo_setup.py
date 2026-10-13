"""Runner-side materialisation of a task's real coding repository."""

from __future__ import annotations

from ptu.common.binaries import REAL_GIT
import re
import hashlib
import subprocess
import zlib
from pathlib import Path

import yaml

from ptu.apps.codehost.repos import build_repo


_SECRET = re.compile(r"\{\{secret:([a-z][a-z0-9_]*)\}\}")


def _loose_reachable_objects(work: Path, env: dict[str, str]) -> None:
    """Keep the no-fork ptu-git client independent of Git pack readers."""
    listing = subprocess.run([str(REAL_GIT), "rev-list", "--objects", "--all"],
                             cwd=work, env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=40, check=False)
    if listing.returncode:
        raise ValueError("E_WS_REPO")
    names = [line[:40] for line in listing.stdout.splitlines()]
    if len(names) > 5000 or any(not re.fullmatch(rb"[0-9a-f]{40}", name) for name in names):
        raise ValueError("E_WS_REPO")
    if not names:
        raise ValueError("E_WS_REPO")
    batch = subprocess.run([str(REAL_GIT), "cat-file", "--batch"], cwd=work,
                           env=env, input=b"\n".join(names) + b"\n",
                           capture_output=True, timeout=40, check=False)
    if batch.returncode or len(batch.stdout) > 64 * 1024 * 1024:
        raise ValueError("E_WS_REPO")
    data, offset = batch.stdout, 0
    root = work / ".git" / "objects"
    for name in names:
        end = data.find(b"\n", offset)
        if end < 0:
            raise ValueError("E_WS_REPO")
        parts = data[offset:end].split()
        if len(parts) != 3 or parts[0] != name or parts[1] not in {b"commit", b"tree", b"blob", b"tag"}:
            raise ValueError("E_WS_REPO")
        try:
            size = int(parts[2])
        except ValueError:
            raise ValueError("E_WS_REPO") from None
        offset = end + 1
        if size < 0 or offset + size >= len(data) or data[offset + size] != 10:
            raise ValueError("E_WS_REPO")
        raw = parts[1] + b" " + parts[2] + b"\0" + data[offset:offset + size]
        if hashlib.sha1(raw).hexdigest().encode() != name:
            raise ValueError("E_WS_REPO")
        target = root / name[:2].decode() / name[2:].decode()
        if not target.is_file():
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(zlib.compress(raw))
        offset += size + 1
    if offset != len(data):
        raise ValueError("E_WS_REPO")


def _ignored_file(task, work: Path, secrets: dict[str, str]) -> dict:
    patch_name = (task.metadata.get("world") or {}).get("patch")
    if not patch_name:
        return {}
    source = Path(task.source_path).resolve().parent
    patch_path = (source / patch_name).resolve()
    if not patch_path.is_relative_to(source) or not patch_path.is_file():
        raise ValueError("E_WS_PATCH")
    try:
        patch = yaml.safe_load(patch_path.read_text())
    except (OSError, yaml.YAMLError):
        raise ValueError("E_WS_PATCH") from None
    row = (patch or {}).get("workstation", {}).get("ignored_file")
    if not row:
        return patch or {}
    name = row.get("path")
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("E_WS_PATCH")
    target = (work / name).resolve()
    if not target.is_relative_to(work) or target.is_symlink():
        raise ValueError("E_WS_PATCH")
    template = row.get("content", "")
    if not isinstance(template, str):
        raise ValueError("E_WS_PATCH")

    def replace(match):
        ref = match.group(1)
        if ref not in secrets:
            raise ValueError("E_WS_SECRET_MISSING")
        return secrets[ref]

    content = _SECRET.sub(replace, template)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    target.chmod(0o600)
    return patch or {}


def prepare_repo(task, episode: Path, state, secrets: dict[str, str]) -> None:
    """Build the authoritative bare repo and an isolated workstation clone."""
    config = task.workstation or {}
    if config.get("kind") != "repo":
        return
    repo_cfg = config.get("repo") or {}
    name = repo_cfg.get("codehost_repo", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ValueError("E_WS_REPO")
    source = Path(task.source_path).resolve().parent
    recipe = (source / repo_cfg.get("recipe", "")).resolve()
    if not recipe.is_relative_to(source) or not recipe.is_dir():
        raise ValueError("E_WS_REPO")
    org, repo = name.split("/")
    bare = episode / "remotes" / org / (repo + ".git")
    bare.parent.mkdir(parents=True, exist_ok=True)
    built_refs = build_repo(recipe, bare)
    work = episode / "ws" / "work"
    if work.parent.is_symlink():
        raise ValueError("E_WS_REPO")
    work.parent.mkdir(parents=True, exist_ok=True)
    if work.is_symlink() or (work.exists() and (not work.is_dir() or any(work.iterdir()))):
        raise ValueError("E_WS_REPO")
    env = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(episode / "empty_home"),
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C"}
    done = subprocess.run([str(REAL_GIT), "clone", "--quiet", "--branch",
                           repo_cfg.get("checkout", "main"), str(bare), str(work)],
                          env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=40, check=False)
    if done.returncode:
        raise ValueError("E_WS_REPO")
    changed = subprocess.run([str(REAL_GIT), "remote", "set-url", "origin",
                              "ptu://" + name], cwd=work, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=10, check=False)
    if changed.returncode:
        raise ValueError("E_WS_REPO")
    for log in (work / ".git" / "logs").rglob("*"):
        if log.is_file() and not log.is_symlink():
            data = log.read_bytes()
            log.write_bytes(data.replace(str(bare).encode(), ("ptu://" + name).encode()))
    _loose_reachable_objects(work, env)
    world_patch = _ignored_file(task, work, secrets)
    repository = state.docs.get("codehost", {}).get("repos", {}).get(name)
    if repository is not None:
        # The rendered catalog is provisional until the real Git recipe is built.
        # Keep checker refs and seeded CI statuses pinned to that same commit.
        branch = repo_cfg.get("checkout", "main")
        previous = repository.get("branches", {}).get(branch, {}).get("head_commit")
        head = built_refs.get(branch)
        if not isinstance(head, str):
            raise ValueError("E_WS_REPO")
        repository.setdefault("branches", {}).setdefault(branch, {})["head_commit"] = head
        for check in repository.get("checks", []):
            if check.get("sha") == previous:
                check["sha"] = head
        seeded = [check for check in repository.get("checks", [])
                  if check.get("sha") == head and check.get("ref") in {branch, "refs/heads/" + branch}
                  and check.get("name") in {"ci/unit", "ci/lint"} and not check.get("log")]
        if seeded:
            from ptu.sandbox.ci import run_checks
            from ptu.sandbox.toolchain import resolve
            actual = {row["name"]: row for row in run_checks(
                bare, head, run_number=1, attempt=1,
                toolchain=str(resolve(config.get("toolchain", "py314-stdlib"))),
                ci_seed_schedule=repository.get("ci_seed_schedule"))}
            for check in seeded:
                if check["name"] not in actual or not actual[check["name"]].get("log"):
                    raise ValueError("E_WS_REPO")
                check["log"] = actual[check["name"]]["log"]
            archive = work / "ci" / "checks"
            if archive.is_symlink() or archive.parent.is_symlink():
                raise ValueError("E_WS_REPO")
            archive.mkdir(parents=True, exist_ok=True)
            for check in seeded:
                leaf = check["name"].removeprefix("ci/")
                path = archive / ("main-" + leaf + ".log")
                if path.is_symlink() or path.exists() and path.read_text() != check["log"]:
                    raise ValueError("E_WS_REPO")
                if not path.exists():
                    path.write_text(check["log"])
        patterns = (world_patch.get("repository", {}).get("protection", {})
                    .get("push_protection_patterns"))
        if patterns is not None:
            if (not isinstance(patterns, list) or len(patterns) > 16 or
                    any(not isinstance(value, str) or not value or len(value) > 128
                        for value in patterns)):
                raise ValueError("E_WS_PATCH")
            repository.setdefault("push_protection", {})["patterns"] = patterns
        repository["_bare_repo"] = str(bare)
