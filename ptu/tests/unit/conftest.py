"""Synthetic vendor executables for stream-only harness tests on CI Linux."""

import sys

import pytest


_STREAM_MODULES = {
    "test_agent_runner", "test_agent_runner_extreme", "test_agent_runner_h2b",
    "test_usage_windows",
}


@pytest.fixture(autouse=True)
def fake_vendor_binaries_on_linux(request, monkeypatch, tmp_path):
    if not sys.platform.startswith("linux") or request.module.__name__.rsplit(".", 1)[-1] not in _STREAM_MODULES:
        return
    import ptu.harness.agent_runner as runner

    binaries = {}
    for kind, version in (("claude-code", "2.1.286 (Claude Code)"),
                          ("codex", "codex-cli 0.158.0")):
        binary = tmp_path / kind
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
        binaries[kind] = (binary, version)
    monkeypatch.setattr(runner, "_pin", lambda kind, model=None: binaries[kind])
