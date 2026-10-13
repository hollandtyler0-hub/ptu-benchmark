"""Order episode process shutdown before removing its private filesystem."""

from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path


_lock = threading.Lock()
_children: dict[Path, set[subprocess.Popen]] = {}


def track_child(episode: Path, proc: subprocess.Popen) -> None:
    with _lock:
        _children.setdefault(Path(episode).resolve(), set()).add(proc)


def untrack_child(episode: Path, proc: subprocess.Popen) -> None:
    with _lock:
        children = _children.get(Path(episode).resolve())
        if children is not None:
            children.discard(proc)
            if not children:
                _children.pop(Path(episode).resolve(), None)


def _stop_children(episode: Path) -> None:
    with _lock:
        children = tuple(_children.get(episode, ()))
    for proc in children:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
        else:
            proc.wait()
        untrack_child(episode, proc)


def remove_episode(episode: Path) -> None:
    """Stop tracked workstation children, then tolerate transient delete races."""
    episode = Path(episode).resolve()
    _stop_children(episode)

    def onexc(_function, _path, error):
        if isinstance(error, FileNotFoundError) or getattr(error, "errno", None) == errno.ENOENT:
            return
        raise error

    for attempt in range(6):
        try:
            shutil.rmtree(episode, onexc=onexc)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            if error.errno not in {errno.ENOTEMPTY, errno.EBUSY} or attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))
