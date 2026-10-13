"""Regression tests for the proxied host-Git confinement.

Every marker is a benign empty file written with the shell builtin `: >`
(the host profile cannot exec /usr/bin/touch, so a `touch` payload proves
nothing). Two markers per case:
- `outside` lives under pytest tmp_path (outside the episode, so the host
  profile would block it even if the command ran), as the house style does;
- `inside` lives in the checkout, which the host profile CAN write, so it is
  the witness that tells "neutralised by code" apart from "contained by the
  profile".
"""

import errno
import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ptu.sandbox import runner
from ptu.sandbox.gitproxy import GitProxy
from ptu.tests.sandbox.test_gitproxy import _call, _git, episode  # noqa: F401

ACTOR = {"id": "writer", "role": "write"}
ID = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")


def _profile_with_shell(profile, work=None):
    """Test-only control: restore shell execution without changing file scope."""
    assert '(literal "/bin/sh")' not in profile
    from ptu.sandbox.profile import _quoted
    extra = ('(allow file-read* (literal "/bin/sh") (literal "/bin/bash") '
             '(literal "/private/var/select/sh"))\n'
             '(allow process-exec (literal "/bin/sh") (literal "/bin/bash"))\n')
    if work is not None:
        extra += f'(allow process-exec (subpath {_quoted(work / ".git" / "hooks")}))\n'
    return profile + extra


def _need_sandbox():
    if not runner.available():
        pytest.skip("E_SANDBOX_UNAVAILABLE")


def _markers(tmp_path, work):
    return tmp_path / "canary", work / "inside-canary"


def _origin_ahead(work):
    """origin/main one commit ahead of HEAD, fast-forwardable."""
    _git("remote", "add", "origin", "ptu://acme/widget", cwd=work)
    base = _git("rev-parse", "HEAD", cwd=work)
    (work / "added.txt").write_text("safe\n")
    _git("add", "added.txt", cwd=work)
    _git(*ID, "commit", "-qm", "add", cwd=work)
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=work)
    _git("reset", "--hard", "-q", base, cwd=work)


# --- config classes not in the existing parametrize --------------------------

@pytest.mark.parametrize("entry", [
    "diff.canary.textconv", "diff.external", "core.fsmonitor", "core.sshCommand",
    "core.editor", "sequence.editor", "core.hooksPath", "core.pager",
    "includeIf.gitdir:/.path", "gpg.program", "hook.canary.command",
    "core.attributesFile", "core.worktree",
])
def test_rejects_more_executing_config(episode, tmp_path, entry):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", entry, "touch " + str(inside), cwd=work)
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        rejected = _call(root, proxy.socket_path, "git merge origin/main")
    assert rejected.exit_code != 0 and "E_GIT_REQUEST" in rejected.output
    assert not outside.exists() and not inside.exists()


# --- hooks: a witness the profile does not block ------------------------------

@pytest.mark.parametrize("command,hook", [
    ("git merge origin/main", "post-merge"),
    ("git merge origin/main", "reference-transaction"),
    ("git rebase origin/main", "post-checkout"),
    ("git stash push -u", "reference-transaction"),
])
def test_hooks_do_not_run_even_when_target_is_writable(episode, tmp_path, command, hook):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.invalid", cwd=work)
    (work / "dirty.txt").write_text("x\n")
    path = work / ".git" / "hooks" / hook
    path.write_text(f"#!/bin/sh\n: > '{inside}'\n: > '{outside}'\n")
    path.chmod(0o700)
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        done = _call(root, proxy.socket_path, command)
    assert done.exit_code == 0, done.output
    assert not outside.exists() and not inside.exists()


@pytest.mark.parametrize("command,hook", [
    ("git merge origin/main", "post-merge"),
    ("git merge origin/main", "reference-transaction"),
    ("git rebase origin/main", "post-checkout"),
    ("git stash push -u", "reference-transaction"),
])
def test_control_hook_fires_without_override(episode, tmp_path, command, hook, monkeypatch):
    """Sensitivity control: with shell enabled, removing hooksPath runs the hook."""
    _need_sandbox()
    from ptu.sandbox import git_local
    profile_original = git_local.host_git_profile
    monkeypatch.setattr(git_local, 'host_git_profile',
                        lambda work, private: _profile_with_shell(profile_original(work, private), work))
    original = git_local._host_command
    def without_hooks_override(profile, *args, identity=None):
        argv = original(profile, *args, identity=identity)
        i = argv.index("core.hooksPath=/dev/null")
        return argv[:i - 1] + argv[i + 1:]
    monkeypatch.setattr(git_local, "_host_command", without_hooks_override)
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.invalid", cwd=work)
    (work / "dirty.txt").write_text("x\n")
    path = work / ".git" / "hooks" / hook
    path.write_text(f"#!/bin/sh\n: > '{inside}'\n: > '{outside}'\n")
    path.chmod(0o700)
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        _call(root, proxy.socket_path, command)
    assert inside.exists(), "control: hook did not fire even without the override"
    assert not outside.exists()


@pytest.mark.parametrize("command,hook", [
    ("git merge origin/main", "post-merge"),
    ("git merge origin/main", "reference-transaction"),
    ("git rebase origin/main", "post-checkout"),
    ("git stash push -u", "reference-transaction"),
])
def test_hook_override_blocks_writable_witness_with_shell_enabled(episode, tmp_path, command, hook, monkeypatch):
    _need_sandbox()
    from ptu.sandbox import git_local
    profile_original = git_local.host_git_profile
    monkeypatch.setattr(git_local, 'host_git_profile',
                        lambda work, private: _profile_with_shell(profile_original(work, private), work))
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.invalid", cwd=work)
    (work / "dirty.txt").write_text("x\n")
    path = work / ".git" / "hooks" / hook
    path.write_text(f"#!/bin/sh\n: > '{inside}'\n: > '{outside}'\n")
    path.chmod(0o700)
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        done = _call(root, proxy.socket_path, command)
    assert done.exit_code == 0
    assert not outside.exists() and not inside.exists()


# --- rebase state files are agent-writable and carry `exec` -------------------

@pytest.mark.parametrize("state_name", ["git-rebase-todo", "done"])
def test_forged_rebase_todo_exec_does_not_run(episode, tmp_path, state_name):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.invalid", cwd=work)
    (work / "mine.txt").write_text("mine\n")
    _git("add", "mine.txt", cwd=work)
    _git("commit", "-qm", "mine", cwd=work)
    # Stop the rebase at `edit`, as an agent could by writing these files itself.
    env = dict(os.environ, GIT_SEQUENCE_EDITOR="sed -i '' '1s/^pick/edit/'")
    subprocess.run(["git", "rebase", "-i", "origin/main"], cwd=work, env=env,
                   check=True, capture_output=True)
    todo = work / ".git" / "rebase-merge" / state_name
    todo.write_text(f"exec : > '{inside}'; : > '{outside}'\n")
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        rejected = _call(root, proxy.socket_path, "git rebase --continue")
    assert rejected.exit_code != 0 and "E_GIT_REQUEST" in rejected.output
    assert not outside.exists()
    assert not inside.exists(), "host git ran an agent-forged rebase `exec` line"


def test_forked_child_does_not_outlive_proxy_call(episode, tmp_path):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.invalid", cwd=work)
    (work / "mine.txt").write_text("mine\n")
    _git("add", "mine.txt", cwd=work)
    _git("commit", "-qm", "mine", cwd=work)
    env = dict(os.environ, GIT_SEQUENCE_EDITOR="sed -i '' '1s/^pick/edit/'")
    subprocess.run(["git", "rebase", "-i", "origin/main"], cwd=work, env=env,
                   check=True, capture_output=True)
    todo = work / ".git" / "rebase-merge" / "git-rebase-todo"
    late = work / "late-canary"
    spin = "i=0; while [ $i -lt 150000 ]; do i=$((i+1)); done"
    todo.write_text(f"exec ({spin}; : > '{late}') </dev/null >/dev/null 2>&1 &\n")
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        _call(root, proxy.socket_path, "git rebase --continue")
    at_return = late.exists()
    returned = time.monotonic()
    while time.monotonic() - returned < 15 and not late.exists():
        time.sleep(0.2)
    assert not outside.exists()
    assert not (not at_return and late.exists()), (
        "a host-side child wrote into the checkout after the proxy call returned")


def test_finished_git_leader_cannot_leave_a_background_group_child(tmp_path):
    from ptu.sandbox.git_local import _kill_and_verify_group
    marker = tmp_path / "canary"
    ready = tmp_path / "child-ready"
    gate = tmp_path / "child-gate"
    os.mkfifo(gate)
    command = (f"( : > {shlex.quote(str(ready))}; "
               f"read _ < {shlex.quote(str(gate))}; "
               f": > {shlex.quote(str(marker))} ) </dev/null >/dev/null 2>&1 &")
    proc = subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        ready_deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() >= ready_deadline:
                pytest.fail("E_CHILD_READY_TIMEOUT", pytrace=False)
            time.sleep(0.01)
        try:
            leader_exit = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("E_LEADER_WAIT_TIMEOUT", pytrace=False)
        assert leader_exit == 0, "E_LEADER_EXIT"
        try:
            _kill_and_verify_group(proc)
        except ValueError as error:
            if str(error) == "E_GIT_LOCAL":
                pytest.fail("E_GIT_LOCAL_GROUP_CLEANUP", pytrace=False)
            raise
        assert not marker.exists(), "E_CANARY_CREATED"
        try:
            writer = os.open(gate, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
        else:
            os.close(writer)
            pytest.fail("E_BACKGROUND_CHILD_SURVIVED", pytrace=False)
    finally:
        try:
            os.killpg(proc.pid, 9)
        except OSError as error:
            # Darwin can briefly answer EPERM for an already-killed group.
            # The assertion above remains the authority on cleanup success.
            if error.errno not in (errno.ESRCH, errno.EPERM):
                raise
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("E_LEADER_REAP_TIMEOUT", pytrace=False)
        marker.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
        gate.unlink(missing_ok=True)


@pytest.mark.parametrize("reaped_at,expected_error", [(4, False), (None, True)])
def test_group_verification_waits_for_slow_reap_and_fails_closed(
        monkeypatch, reaped_at, expected_error):
    from ptu.sandbox import git_local
    elapsed = [0.0]

    def killpg(pid, sig):
        assert pid == 43210
        if sig != signal.SIGKILL and reaped_at is not None and elapsed[0] >= reaped_at:
            raise ProcessLookupError

    monkeypatch.setattr(git_local, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(git_local, "time", SimpleNamespace(
        monotonic=lambda: elapsed[0], sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)))
    proc = SimpleNamespace(pid=43210, stdout=None, wait=lambda timeout: 0)
    if expected_error:
        with pytest.raises(ValueError, match="^E_GIT_LOCAL$"):
            git_local._kill_and_verify_group(proc)
        assert elapsed[0] >= 15
    else:
        git_local._kill_and_verify_group(proc)
        assert 4 <= elapsed[0] < 15


@pytest.mark.parametrize("eventually_gone", [True, False])
def test_group_verification_retries_transient_eperm_but_fails_closed(
        monkeypatch, eventually_gone):
    from ptu.sandbox import git_local
    elapsed = [0.0]
    signals = []

    def killpg(pid, sig):
        assert pid == 43210
        signals.append(sig)
        if sig == signal.SIGKILL:
            return
        if eventually_gone and elapsed[0] >= 0.1:
            raise ProcessLookupError
        raise PermissionError(errno.EPERM, "group temporarily inaccessible")

    monkeypatch.setattr(git_local, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(git_local, "time", SimpleNamespace(
        monotonic=lambda: elapsed[0],
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)))
    proc = SimpleNamespace(pid=43210, stdout=None, wait=lambda timeout: 0)
    if eventually_gone:
        git_local._kill_and_verify_group(proc)
        assert 0.1 <= elapsed[0] < 15
    else:
        with pytest.raises(ValueError, match="^E_GIT_LOCAL$"):
            git_local._kill_and_verify_group(proc)
        assert elapsed[0] >= 15
    assert signals[0] == signal.SIGKILL


# --- alternates / commondir / config.worktree / symlinks ----------------------

@pytest.mark.parametrize("relative", ["objects/info/alternates", "commondir", "config.worktree"])
def test_rejects_redirecting_git_files(episode, tmp_path, relative):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    target = work / ".git" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(tmp_path) + "\n")
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        rejected = _call(root, proxy.socket_path, "git merge origin/main")
    assert rejected.exit_code != 0 and "E_GIT_REQUEST" in rejected.output
    assert not outside.exists() and not inside.exists()


@pytest.mark.parametrize("relative", ["link", ".git/refs/heads/link", ".git/objects/info/alternates"])
def test_rejects_symlink_present_before_validation(episode, tmp_path, relative):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    link = work / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "missing-target")  # dangling: Path.exists() is False
    # The outer runner already refuses ws symlinks that resolve outside ws, so
    # exercise the host-side check itself.
    from ptu.sandbox.git_local import _validated_worktree
    with pytest.raises(ValueError, match="E_GIT_WORKTREE"):
        _validated_worktree(root, "acme/widget")
    assert not outside.exists() and not inside.exists()


# --- argument allowlist --------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git rebase --exec 'touch {inside}' origin/main",
    "git rebase -x 'touch {inside}' origin/main",
    "git rebase -i origin/main",
    "git merge -s canary origin/main",
    "git merge --no-edit -X theirs origin/main",
    "git stash push -- added.txt",
    "git stash push -p",
])
def test_rejects_unlisted_arguments(episode, tmp_path, command):
    _need_sandbox()
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    with GitProxy(root, lambda: {}, ACTOR) as proxy:
        rejected = _call(root, proxy.socket_path, command.format(inside=inside))
    assert rejected.exit_code != 0 and "E_GIT_REQUEST" in rejected.output
    assert not outside.exists() and not inside.exists()


# --- environment scrubbing ----------------------------------------------------

def test_harness_environment_does_not_reach_host_git(episode, tmp_path, monkeypatch):
    _need_sandbox()
    from ptu.sandbox.git_local import run_local_git
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    _origin_ahead(work)
    hooks = work / "hooks-canary"
    hooks.mkdir()
    (hooks / "post-merge").write_text(f"#!/bin/sh\n: > '{inside}'\n")
    (hooks / "post-merge").chmod(0o700)
    trace = work / "trace-canary"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hookspath'='{hooks}'")
    monkeypatch.setenv("GIT_TRACE", str(trace))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "touch " + str(inside))
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    done = run_local_git(root, "acme/widget", "merge", ["origin/main"])
    assert done["exit_code"] == 0, done["output"]
    assert not inside.exists() and not trace.exists() and not outside.exists()


# --- host-side profile: read scope and hard links -----------------------------

def test_host_profile_denies_reads_outside_checkout(episode, tmp_path):
    """Shell builtins only: the profile cannot exec /bin/cat, so a cat probe is insensitive."""
    _need_sandbox()
    from ptu.sandbox.git_local import host_git_profile
    root, _, work = episode
    outside, _ = _markers(tmp_path, work)
    secret = Path(root) / "harmless-episode-file.txt"   # same episode, outside work
    secret.write_text("canary-content\n")
    (work / "inside.txt").write_text("inside-content\n")
    def sh(script):
        with tempfile.TemporaryDirectory(dir=root) as private:
            profile = _profile_with_shell(host_git_profile(work, Path(private)))
            return subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", script],
                                  cwd=work, capture_output=True, text=True, timeout=10)
    control = sh("read l < inside.txt; echo \"$l\"")
    assert "inside-content" in control.stdout          # sensitivity control
    episode_read = sh(f"read l < '{secret}'; echo \"$l\"")
    home_list = sh(f"for f in '{Path.home()}'/*; do echo \"$f\"; done")
    tmp_read = sh(f"read l < '{tmp_path}'/nothing 2>&1; echo done")
    assert "canary-content" not in episode_read.stdout
    assert str(Path.home() / "") + "Library" not in home_list.stdout
    assert not outside.exists()


def test_host_profile_denies_hardlink_into_checkout(episode, tmp_path):
    """No binary that can link() starts under the host profile (python fails at
    realpath; ln/touch are unreadable), so a behavioural probe is insensitive.
    Assert the rule the workstation profile already carries instead."""
    _need_sandbox()
    from ptu.sandbox.git_local import host_git_profile
    root, _, work = episode
    outside, _ = _markers(tmp_path, work)
    with tempfile.TemporaryDirectory(dir=root) as private:
        profile = host_git_profile(work, Path(private))
    assert "(deny file-link)" in profile, "host profile lacks the workstation's explicit (deny file-link)"
    assert not outside.exists()


def test_host_profile_cannot_exec_shell(episode, tmp_path):
    _need_sandbox()
    from ptu.sandbox.git_local import host_git_profile
    root, _, work = episode
    outside, inside = _markers(tmp_path, work)
    with tempfile.TemporaryDirectory(dir=root) as private:
        profile = host_git_profile(work, Path(private))
        denied = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile,
                                 "/bin/sh", "-c", f": > '{inside}'"],
                                cwd=work, capture_output=True, timeout=10)
    assert denied.returncode != 0
    assert not inside.exists() and not outside.exists()
