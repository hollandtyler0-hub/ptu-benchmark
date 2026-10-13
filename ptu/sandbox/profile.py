"""Generate a narrow macOS Seatbelt profile for an episode workstation."""

from pathlib import Path
import stat
from typing import Sequence

from .toolchain import BINARIES, REAL_GIT
from .socket_paths import registered_socket_dir


def zoneinfo_path() -> Path:
    """Use the macOS system database through its real, versioned path."""
    try:
        target = Path('/usr/share/zoneinfo').resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError('E_SANDBOX_TZDATA') from None
    root = Path('/private/var/db/timezone/tz')
    if root not in target.parents or not (target / 'America' / 'Denver').is_file():
        raise ValueError('E_SANDBOX_TZDATA')
    return target


def _quoted(path: Path) -> str:
    value = str(path)
    if not value.startswith("/") or any(c in value for c in '"\\\n\r'):
        raise ValueError("E_SANDBOX_PATH")
    return '"' + value + '"'


def seatbelt_profile(ws_root: Path, toolchain: Path, sockets: Sequence[Path]) -> str:
    """Deny all operations except local tools, system runtime and workstation I/O.

    Parent metadata grants let dyld and getcwd traverse to the granted roots.
    The literal root and Homebrew directories remain visible for runtime lookup.
    """
    if Path(ws_root).is_symlink() or Path(toolchain).is_symlink():
        raise ValueError("E_SANDBOX_PATH")
    try:
        ws = Path(ws_root).resolve(strict=True)
        chain = Path(toolchain).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("E_SANDBOX_PATH") from None
    if not ws.is_dir() or not chain.is_dir() or ws == chain or ws in chain.parents or chain in ws.parents:
        raise ValueError("E_SANDBOX_PATH")
    socket_paths = []
    for raw in sockets:
        try:
            path = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("E_SANDBOX_SOCKET") from None
        if path.parent != ws.parent / "sockets":
            try:
                registered_socket_dir(path.parent)
            except (OSError, ValueError):
                raise ValueError("E_SANDBOX_SOCKET") from None
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise ValueError("E_SANDBOX_SOCKET")
        socket_paths.append(path)
    parents = {Path("/")}
    for root in (ws, chain):
        parents.update(root.parents)
    parents.update(Path('/private/var/db/timezone/tz').parents)
    parents.add(Path('/usr/share/zoneinfo'))
    for path in socket_paths:
        parents.update(path.parents)
    parents.update(Path(p) for p in (
        '/opt', '/opt/homebrew', '/opt/homebrew/Cellar', '/opt/homebrew/opt', '/opt/homebrew/etc'))
    metadata = " ".join(f"(literal {_quoted(p)})" for p in sorted(parents, key=str))
    system = [
        '/System', '/usr/lib', '/usr/share', '/private/var/db/timezone/tz',
        '/Library/Frameworks/Python.framework/Versions/3.14',
        '/Library/Developer/CommandLineTools', '/dev/fd',
        str(REAL_GIT.parent.parent),
        str(Path('/opt/homebrew/opt/pcre2').resolve()),
        str(Path('/opt/homebrew/opt/gettext').resolve()),
    ]
    system_reads = " ".join(f"(subpath {_quoted(Path(p))})" for p in system)
    # Some system executables use the macOS-selected shell path at launch.
    literal_reads = " ".join(f"(literal {_quoted(Path(p))})" for p in (
        '/', '/bin/sh', '/bin/bash', '/private/var/select/sh', '/usr/bin/git', '/usr/bin/ps',
        '/usr/bin/env', '/private/etc/hosts', '/private/etc/resolv.conf',
        '/dev', '/dev/null', '/dev/zero', '/dev/random', '/dev/urandom',
        '/opt', '/opt/homebrew', '/opt/homebrew/opt/pcre2', '/opt/homebrew/opt/gettext',
        '/opt/homebrew/opt/git', '/opt/homebrew/etc/gitconfig',
    ) + tuple(str(p) for p in BINARIES.values()) + (str(REAL_GIT),))
    socket_rules = [f'(allow network-outbound (remote unix-socket (path-literal {_quoted(p)})))'
                    for p in socket_paths]
    return "\n".join((
        '(version 1)', '(deny default)',
        f'(allow file-read-metadata {metadata})',
        f'(allow file-read* {literal_reads} {system_reads} (subpath {_quoted(chain)}) (subpath {_quoted(ws)}))',
        f'(allow file-write* (subpath {_quoted(ws)}) (literal "/dev/null"))',
        '(deny file-link)',
        '(allow process-exec*)',
        # TCP/IP, DNS, sysctl, process-info and signals retain the default deny.
        *socket_rules,
    )) + "\n"
