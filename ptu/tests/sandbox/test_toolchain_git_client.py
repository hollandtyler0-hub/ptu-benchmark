"""The proxy push client is pinned and callable from the isolated shell."""

import tempfile
import threading
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ptu.sandbox import runner, toolchain


def test_pinned_git_client_runs_without_exposing_repository_source():
    if not runner.available():
        pytest.skip("E_SANDBOX_UNAVAILABLE")
    chain = toolchain.build("py314-stdlib")
    client = chain / "bin" / "ptu-git"
    assert client.is_file() and not client.is_symlink()
    assert client.read_bytes() == (Path(toolchain.__file__).parent / "bin" / "ptu-git").read_bytes()
    root = Path.home() / ".meter-harness" / "toolchain-git-client"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        ws = Path(directory) / "episode" / "ws"
        (ws / "work").mkdir(parents=True)
        result = runner.run("ptu-git invalid", ws_root=ws, toolchain=str(chain),
                            timeout_s=10, limits={})
        assert result.exit_code != 0
        assert "usage: ptu-git" in result.output


def test_manifest_keyed_toolchain_bootstraps_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "ROOT", tmp_path / "toolchains")
    chain = toolchain.resolve("py314-stdlib")
    assert chain.name == "py314-stdlib"
    assert chain.parent.name == toolchain._registered("py314-stdlib")["manifest_sha256"][:16]
    (chain / "bin" / "ptu-payments").write_text("tampered")
    with pytest.raises(ValueError, match="^E_TOOLCHAIN$"):
        toolchain.resolve("py314-stdlib")


def test_source_mismatch_never_mutates_shared_pinned_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "ROOT", tmp_path / "toolchains")
    copied = tmp_path / "ptu-payments"
    original = toolchain.COPIED["ptu-payments"].read_bytes()
    copied.write_bytes(original)
    monkeypatch.setitem(toolchain.COPIED, "ptu-payments", copied)
    pinned = toolchain._registered("py314-stdlib")
    monkeypatch.setattr(toolchain, "_registered", lambda _id: pinned)
    chain = toolchain.build("py314-stdlib")
    cached = (chain / "bin" / "ptu-payments").read_bytes()
    copied.write_bytes(original + b"\n# changed source\n")
    with pytest.raises(ValueError, match="^E_TOOLCHAIN$"):
        toolchain.build("py314-stdlib")
    assert (chain / "bin" / "ptu-payments").read_bytes() == cached
    copied.write_bytes(original)
    assert toolchain.resolve("py314-stdlib") == chain


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac pinned-cache concurrency regression")
def test_concurrent_cache_build_publishes_one_complete_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "ROOT", tmp_path / "toolchains")
    barrier = threading.Barrier(2)
    original_manifest = toolchain._source_manifest_sha256
    original_symlink = Path.symlink_to

    def synchronized_manifest():
        value = original_manifest()
        barrier.wait(timeout=5)
        return value

    def slow_first_link(self, target, *args, **kwargs):
        if self.name == "sh" and self.parent.name == "bin":
            time.sleep(0.05)
        return original_symlink(self, target, *args, **kwargs)

    monkeypatch.setattr(toolchain, "_source_manifest_sha256", synchronized_manifest)
    monkeypatch.setattr(Path, "symlink_to", slow_first_link)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(toolchain.resolve, "py314-stdlib")
        second = pool.submit(toolchain.resolve, "py314-stdlib")
        assert first.result(timeout=10) == second.result(timeout=10)
    monkeypatch.setattr(toolchain, "_source_manifest_sha256", original_manifest)
    assert toolchain.resolve("py314-stdlib").is_dir()
