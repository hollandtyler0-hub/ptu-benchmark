"""Run each workstation command in a fresh Seatbelt process group."""

import os
import platform
import resource
import signal
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .profile import seatbelt_profile, zoneinfo_path
from .socket_paths import registered_socket_dir
from .teardown import track_child, untrack_child


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: str
    truncated: bool
    full_output_path: str | None
    timed_out: bool
    duration_ms: int


def available() -> bool:
    return platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").is_file()


def workspace_root(ws_root: Path, *, scan_symlinks: bool = True) -> Path:
    """Accept only an episode ws under the user's home, outside this checkout."""
    raw_root = Path(ws_root)
    if raw_root.is_symlink():
        raise ValueError("E_SANDBOX_PATH")
    try:
        root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("E_SANDBOX_PATH") from None
    if not root.is_dir():
        raise ValueError("E_SANDBOX_PATH")
    home_root = Path.home().resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if (root.name != "ws" or root.parent == home_root or home_root not in root.parents
            or repository_root in root.parents):
        raise ValueError("E_SANDBOX_PATH")
    if scan_symlinks:
        try:
            for item in root.rglob("*"):
                if item.is_symlink() and root not in item.resolve().parents:
                    raise ValueError("E_WS_SYMLINK")
        except (OSError, RuntimeError):
            raise ValueError("E_WS_SYMLINK") from None
    return root


def _inside(root: Path, path: str) -> Path:
    if not isinstance(path, str) or not path or Path(path).is_absolute():
        raise ValueError("E_WS_PATH")
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("E_WS_PATH")
    return target


def _limits(limits: dict):
    def apply():
        cpu = int(limits.get("cpu_s", 90))
        file_mb = int(limits.get("file_size_mb", 64))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_mb * 1024 * 1024, file_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def _validate_limits(limits: dict) -> tuple[int, int, int]:
    if not isinstance(limits, dict):
        raise ValueError("E_WS_LIMIT")
    try:
        cpu = int(limits.get("cpu_s", 90))
        file_mb = int(limits.get("file_size_mb", 64))
        cap = int(limits.get("max_output_bytes", 24000))
        processes = int(limits.get("processes", 64))
    except (TypeError, ValueError):
        raise ValueError("E_WS_LIMIT") from None
    if not (1 <= cpu <= 120 and 1 <= file_mb <= 1024 and 1 <= cap <= 1_000_000
            and 1 <= processes <= 1024):
        raise ValueError("E_WS_LIMIT")
    return cpu, file_mb, cap


def _sequence(command: str) -> list[tuple[str, str]]:
    """Split sequential shell commands outside quotes; never invoke a pipeline."""
    result = []
    quote = None
    escaped = False
    start = 0
    index = 0
    mode = "always"
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char == quote:
            quote = None
        elif char in {"'", '"'} and quote is None:
            quote = char
        elif quote is None:
            if command[index:index + 2] == "&&":
                segment = command[start:index].strip()
                if not segment:
                    raise ValueError("E_WS_SHELL_UNSUPPORTED")
                result.append((_without_stderr_merge(segment), mode))
                mode = "success"
                index += 1
                start = index + 1
            elif char == ";":
                segment = command[start:index].strip()
                if not segment:
                    raise ValueError("E_WS_SHELL_UNSUPPORTED")
                result.append((_without_stderr_merge(segment), mode))
                mode = "always"
                start = index + 1
            elif char == "&" and command[max(0, index - 2):index + 2] == "2>&1":
                pass
            elif char in {"&", "|"}:
                raise ValueError("E_WS_SHELL_UNSUPPORTED")
        index += 1
    segment = command[start:].strip()
    if quote is not None or not segment:
        raise ValueError("E_WS_SHELL_UNSUPPORTED")
    result.append((_without_stderr_merge(segment), mode))
    return result


def _without_stderr_merge(segment: str) -> str:
    """The runner already merges stderr into stdout; avoid a shell redirection fork."""
    out = []
    quote = None
    escaped = False
    index = 0
    while index < len(segment):
        char = segment[index]
        if not escaped and quote is None and segment.startswith("2>&1", index) and (
                index == 0 or segment[index - 1].isspace()) and (
                index + 4 == len(segment) or segment[index + 4].isspace()):
            index += 4
            continue
        out.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char == quote:
            quote = None
        elif char in {"'", '"'} and quote is None:
            quote = char
        index += 1
    return "".join(out).strip()


def _segments(command: str) -> list[str]:
    return [segment for segment, _ in _sequence(command)]


def run(command: str, *, ws_root: Path, cwd: str = "work", toolchain: str,
        timeout_s: int, limits: dict, sockets: Sequence[Path] = (),
        env_extra: dict | None = None) -> ExecResult:
    if not available():
        raise ValueError("E_SANDBOX_UNAVAILABLE")
    root = workspace_root(ws_root)
    work = _inside(root, cwd)
    if not work.is_dir():
        raise ValueError("E_WS_PATH")
    from .toolchain import resolve
    try:
        chain = Path(toolchain).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("E_TOOLCHAIN") from None
    if chain != resolve(chain.name):
        raise ValueError("E_TOOLCHAIN")
    if not isinstance(command, str) or not command or not isinstance(timeout_s, int) or not 0 < timeout_s < 120:
        raise ValueError("E_WS_EXEC")
    cpu, file_mb, cap = _validate_limits(limits)
    profile = seatbelt_profile(root, chain, sockets)
    segments = _sequence(command)
    home = root / "home"
    temp = root / "tmp"
    if home.is_symlink() or temp.is_symlink():
        raise ValueError("E_SANDBOX_PATH")
    home.mkdir(mode=0o700, exist_ok=True)
    temp.mkdir(mode=0o700, exist_ok=True)
    env = {"PATH": str(chain / "bin"), "HOME": str(home), "TMPDIR": str(temp),
           "LANG": "C", "PYTHONHASHSEED": "0", "PYTHONTZPATH": str(zoneinfo_path())}
    short_dirs = {Path(path).parent for path in sockets if Path(path).parent != root.parent / "sockets"}
    if short_dirs:
        if len(short_dirs) != 1:
            raise ValueError("E_SANDBOX_SOCKET")
        env["PTU_SOCKET_DIR"] = str(registered_socket_dir(short_dirs.pop()))
    for key, value in (env_extra or {}).items():
        if key not in {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}:
            raise ValueError("E_WS_ENV")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("E_WS_ENV")
        env[key] = value
    filename = "exec-" + uuid.uuid4().hex + ".output"
    path = temp / filename
    started = time.monotonic()
    timed_out = False
    exit_code = 0
    try:
        temp_fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ValueError("E_SANDBOX_START") from None
    try:
        fd = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                     0o600, dir_fd=temp_fd)
    finally:
        os.close(temp_fd)
    with os.fdopen(fd, "w+b") as output:
        try:
            for segment, mode in segments:
                if mode == "success" and exit_code != 0:
                    continue
                proc = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", profile,
                                         str(chain / "bin" / "sh"), "-c", segment],
                                        cwd=work, env=env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT,
                                        start_new_session=True,
                                        preexec_fn=_limits({"cpu_s": cpu, "file_size_mb": file_mb}))
                track_child(root.parent, proc)
                try:
                    remaining = timeout_s - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                    else:
                        try:
                            proc.wait(timeout=remaining)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                finally:
                    if timed_out:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    try:
                        proc.wait()
                    finally:
                        untrack_child(root.parent, proc)
                exit_code = proc.returncode
                if timed_out:
                    break
        except OSError:
            path.unlink(missing_ok=True)
            raise ValueError("E_SANDBOX_START") from None
        original = os.fstat(output.fileno())
        try:
            current = path.lstat()
        except OSError:
            raise ValueError("E_WS_OUTPUT_TAMPER") from None
        if (not stat.S_ISREG(current.st_mode) or current.st_dev != original.st_dev
                or current.st_ino != original.st_ino):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ValueError("E_WS_OUTPUT_TAMPER")
        size = original.st_size
        truncated = size > cap
        output.seek(0)
        output_text = output.read(cap).decode("utf-8", "replace")
        output_text = (output_text.replace(str(root.parent), "/workstation")
                       .replace(str(chain), "/tools")
                       .replace(str(Path.home()), "/home"))
    relative = str(path.relative_to(root)) if truncated else None
    if truncated:
        output_text += f"\n[output truncated; full output: {relative}]\n"
    else:
        path.unlink(missing_ok=True)
    return ExecResult(exit_code, output_text, truncated, relative, timed_out,
                      round((time.monotonic() - started) * 1000))
