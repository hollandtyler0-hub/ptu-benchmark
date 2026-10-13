"""Episode cleanup waits for live workstation resources."""

import errno
import subprocess
import sys
import tempfile
from pathlib import Path

import ptu.sandbox.teardown as teardown
from ptu.sandbox.teardown import remove_episode, track_child


def test_teardown_stops_child_holding_socket_before_removing_episode():
    with tempfile.TemporaryDirectory(prefix="ptu-teardown-test-", dir=Path.home()) as parent:
        episode = Path(parent) / "ptu-route-race"
        (episode / "ws" / "work").mkdir(parents=True)
        sockets = episode / "sockets"
        sockets.mkdir()
        child = subprocess.Popen(
            [sys.executable, "-c",
             "import socket,sys,time; "
             "sock=socket.socket(socket.AF_UNIX); sock.bind(sys.argv[1]); "
             "sock.listen(); print('READY', flush=True); time.sleep(30)",
             str(sockets / "live.sock")],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True)
        try:
            assert child.stdout.readline().strip() == "READY"
            assert (sockets / "live.sock").exists()
            track_child(episode, child)
            remove_episode(episode)
            assert child.poll() is not None
            assert not episode.exists()
            remove_episode(episode)  # ENOENT after a concurrent cleanup is harmless.
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()


def test_teardown_retries_transient_nonempty_directory(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="ptu-teardown-test-", dir=Path.home()) as parent:
        episode = Path(parent) / "ptu-route-race"
        episode.mkdir()
        (episode / "race").write_text("synthetic")
        original = teardown.shutil.rmtree
        attempts = 0

        def flaky(path, *, onexc):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError(errno.ENOTEMPTY, "synthetic race")
            return original(path, onexc=onexc)

        with monkeypatch.context() as patch:
            patch.setattr(teardown.shutil, "rmtree", flaky)
            remove_episode(episode)
        assert attempts == 2
        assert not episode.exists()


def test_teardown_retries_nonempty_from_rmtree_onexc(monkeypatch, tmp_path):
    episode = tmp_path / "episode"
    episode.mkdir()
    (episode / "race").write_text("synthetic")
    original = teardown.shutil.rmtree
    attempts = 0

    def flaky(path, *, onexc):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            onexc(original, str(path), OSError(errno.ENOTEMPTY, "synthetic race"))
        return original(path, onexc=onexc)

    with monkeypatch.context() as patch:
        patch.setattr(teardown.shutil, "rmtree", flaky)
        remove_episode(episode)
    assert attempts == 2
    assert not episode.exists()
