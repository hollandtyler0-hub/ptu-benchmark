"""Serialize workstation shell calls for one episode, including proxied Git."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


@contextmanager
def episode_ws_lock(episode: Path):
    path = Path(episode) / ".ws-exec.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
