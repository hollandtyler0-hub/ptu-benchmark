"""Short, private socket locations independent of the episode store depth."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


_ROOT = Path.home() / ".mh" / "s"
_SUN_PATH_LIMIT = 104


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("E_SOCKET_DIR")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("E_SOCKET_DIR")


def allocate_socket_dir() -> Path:
    parent = _ROOT.parent
    parent.mkdir(mode=0o700, exist_ok=True)
    _private_directory(parent)
    _ROOT.mkdir(mode=0o700, exist_ok=True)
    _private_directory(_ROOT)
    for _ in range(20):
        directory = _ROOT / secrets.token_hex(4)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        _private_directory(directory)
        return directory
    raise ValueError("E_SOCKET_DIR")


def registered_socket_dir(path: Path) -> Path:
    path = Path(path)
    if path.parent != _ROOT or len(path.name) != 8 or any(c not in "0123456789abcdef" for c in path.name):
        raise ValueError("E_SOCKET_DIR")
    for directory in (_ROOT.parent, _ROOT, path):
        _private_directory(directory)
    return path


def checked_socket_path(directory: Path, name: str) -> Path:
    directory = registered_socket_dir(directory)
    path = directory / name
    if len(os.fsencode(path)) >= _SUN_PATH_LIMIT:
        raise ValueError("E_SOCKET_PATH_TOO_LONG")
    return path


def remove_socket_dir(directory: Path) -> None:
    directory = registered_socket_dir(directory)
    if any(directory.iterdir()):
        raise ValueError("E_SOCKET_DIR_NOT_EMPTY")
    directory.rmdir()
