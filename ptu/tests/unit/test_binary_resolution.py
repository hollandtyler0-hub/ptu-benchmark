"""Host binaries come only from platform-specific trusted locations."""

from pathlib import Path

import pytest

from ptu.common import binaries
from ptu.harness.agent_runner import build_command, load_profile
from ptu.sandbox import toolchain


def test_linux_git_ignores_untrusted_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(binaries.sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(binaries.shutil, "which", lambda name, path: calls.append((name, path)) or "/usr/bin/git")
    assert binaries.trusted_git() == Path("/usr/bin/git").resolve()
    assert calls == [("git", "/usr/bin:/bin")]


def test_git_rejects_lookup_outside_trusted_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(binaries.sys, "platform", "linux")
    monkeypatch.setattr(binaries.shutil, "which", lambda name, path: str(tmp_path / "git"))
    (tmp_path / "git").write_text("synthetic")
    with pytest.raises(ValueError, match="E_HOST_BINARY"):
        binaries.trusted_git()


def test_linux_toolchain_manifest_is_keyed_by_local_trusted_binary_bytes(monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    assert toolchain._registered("py314-stdlib")["manifest_sha256"] == toolchain._source_manifest_sha256()


def test_toolchain_accepts_approved_system_symlink_target(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    monkeypatch.setattr(toolchain, "ROOT", tmp_path / "toolchains")
    alias = tmp_path / "cat-link"
    alias.symlink_to(toolchain.BINARIES["cat"])
    monkeypatch.setattr(toolchain, "BINARIES", {**toolchain.BINARIES, "cat": alias})
    chain = toolchain.build("py314-stdlib")
    assert (chain / "bin" / "cat").resolve() == alias.resolve()


def test_harness_binary_override_cannot_follow_path(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    profile = load_profile("cc-sonnet")
    profile.binary = str(fake)
    with pytest.raises(ValueError, match="E_HARNESS_BINARY"):
        build_command(profile, tmp_path / "episode", tmp_path / "scratch",
                      {"system": tmp_path / "system.md", "user": "synthetic"})


def test_harness_binary_resolves_home_and_explicit_override(tmp_path, monkeypatch):
    from ptu.harness import agent_runner

    monkeypatch.delenv("PTU_CLAUDE_BINARY", raising=False)
    monkeypatch.delenv("PTU_CLAUDE_CLI_VERSION", raising=False)
    row = {"kind": "claude-code", "binary": "~/.local/bin/claude", "cli_version": "1.0 (Claude Code)"}
    assert agent_runner.profile_binary(row) == Path.home() / ".local/bin/claude"
    assert agent_runner.profile_version(row) == "1.0 (Claude Code)"
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PTU_CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("PTU_CLAUDE_CLI_VERSION", "2.0 (Claude Code)")
    assert agent_runner.profile_binary(row) == fake
    assert agent_runner.profile_version(row) == "2.0 (Claude Code)"
    assert agent_runner._pin("claude-code", "sonnet") == (fake, "2.0 (Claude Code)")
    assert load_profile("cc-sonnet").binary == str(fake)


def test_harness_binary_override_must_be_absolute(tmp_path, monkeypatch):
    from ptu.harness import agent_runner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "claude").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PTU_CLAUDE_BINARY", "claude")
    with pytest.raises(ValueError, match="E_HARNESS_BINARY"):
        agent_runner._pin("claude-code", "sonnet")


def test_darwin_toolchain_stays_pinned_unless_operator_opts_in(monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    monkeypatch.delenv("PTU_TOOLCHAIN_PIN", raising=False)
    pinned = toolchain._registered("py314-stdlib")["manifest_sha256"]
    assert pinned == toolchain.yaml.safe_load(toolchain.REGISTRY.read_text())["py314-stdlib"]["manifest_sha256"]
    monkeypatch.setenv("PTU_TOOLCHAIN_PIN", "local")
    assert toolchain._registered("py314-stdlib")["manifest_sha256"] == toolchain._source_manifest_sha256()
