"""Resolve host Git from fixed platform locations, independent of the caller's PATH."""

import os
import shutil
import sys
from pathlib import Path


_GIT_DIRS = {
    "darwin": (Path("/opt/homebrew/bin"), Path("/usr/bin")),
    "linux": (Path("/usr/bin"), Path("/bin")),
}
_GIT_REAL_ROOTS = {
    "darwin": (Path("/opt/homebrew/Cellar"), Path("/opt/homebrew/bin"), Path("/usr/bin")),
    "linux": (Path("/usr/bin"), Path("/bin")),
}


def trusted_git() -> Path:
    platform = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else ""
    directories = _GIT_DIRS.get(platform)
    if directories is None:
        raise ValueError("E_HOST_BINARY")
    candidate = shutil.which("git", path=os.pathsep.join(map(str, directories)))
    if candidate is None:
        raise ValueError("E_HOST_BINARY")
    path = Path(candidate)
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("E_HOST_BINARY") from None
    if (path.parent not in directories or not real.is_file() or not os.access(real, os.X_OK)
            or not any(real == root or root in real.parents for root in _GIT_REAL_ROOTS[platform])):
        raise ValueError("E_HOST_BINARY")
    return real


REAL_GIT = trusted_git()
