"""A pinned, local toolchain. Its registration contains no runtime data."""

import hashlib
import fcntl
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import yaml

from ptu.common.binaries import REAL_GIT


REGISTRY = Path(__file__).with_name("toolchains.yaml")
ROOT = Path.home() / ".meter-harness" / "toolchains"
BINARIES = {
    "sh": Path("/bin/sh"), "cat": Path("/bin/cat"), "ls": Path("/bin/ls"),
    "head": Path("/usr/bin/head"), "grep": Path("/usr/bin/grep"),
    "sed": Path("/usr/bin/sed"),
    "sleep": Path("/bin/sleep"), "ps": Path("/bin/ps"),
    "ptu-real-git": REAL_GIT,
    "python3": Path(sys.executable).resolve(),
}
COPIED = {"git": Path(__file__).with_name("bin") / "git",
          "ptu-git": Path(__file__).with_name("bin") / "ptu-git",
          "ptu-payments": Path(__file__).with_name("bin") / "ptu-payments"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_manifest_sha256() -> str:
    """Bind a Linux slot to exact bytes at trusted host paths and copied stubs."""
    binaries = {name: _digest(path) for name, path in BINARIES.items()}
    binaries.update({name: _digest(path) for name, path in COPIED.items()})
    payload = {"id": "py314-stdlib", "python": "3.14", "binaries": binaries}
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def _registered(toolchain_id: str) -> dict:
    if toolchain_id != "py314-stdlib":
        raise ValueError("E_TOOLCHAIN")
    rows = yaml.safe_load(REGISTRY.read_text())
    row = rows.get(toolchain_id) if isinstance(rows, dict) else None
    if not isinstance(row, dict) or not isinstance(row.get("manifest_sha256"), str):
        raise ValueError("E_TOOLCHAIN")
    if sys.platform.startswith("linux") or os.environ.get("PTU_TOOLCHAIN_PIN") == "local":
        # GitHub runners have a different Python and Git build from the pinned
        # macOS workstation, and so does any other Mac; an operator opts a Mac
        # in explicitly. The platform path allowlist is fixed; exact bytes
        # still key and validate the local manifest on every resolve.
        row = {**row, "manifest_sha256": _source_manifest_sha256()}
    return row


def _root(toolchain_id: str, row: dict) -> Path:
    """A manifest keyed slot avoids collisions with parallel checkout builds."""
    parent = ROOT / row["manifest_sha256"][:16]
    if parent.is_symlink():
        raise ValueError("E_TOOLCHAIN")
    return parent / toolchain_id


def resolve(toolchain_id: str) -> Path:
    row = _registered(toolchain_id)
    root = _root(toolchain_id, row)
    if not root.exists():
        return build(toolchain_id)
    manifest = root / "manifest.json"
    if root.is_symlink() or not manifest.is_file() or _digest(manifest) != row["manifest_sha256"]:
        raise ValueError("E_TOOLCHAIN")
    data = json.loads(manifest.read_text())
    if data.get("id") != toolchain_id or data.get("python") != "3.14":
        raise ValueError("E_TOOLCHAIN")
    if {path.name for path in (root / "bin").iterdir()} != set(BINARIES) | set(COPIED):
        raise ValueError("E_TOOLCHAIN")
    for name, digest in data.get("binaries", {}).items():
        target = root / "bin" / name
        source = BINARIES.get(name)
        if source is not None:
            if (not target.is_symlink() or target.resolve() != source.resolve()
                    or _digest(target) != digest):
                raise ValueError("E_TOOLCHAIN")
        elif (name not in COPIED or target.is_symlink() or not target.is_file()
              or not (target.stat().st_mode & 0o111) or _digest(target) != digest
              or digest != _digest(COPIED[name])):
            raise ValueError("E_TOOLCHAIN")
    if set(data.get("binaries", {})) != set(BINARIES) | set(COPIED):
        raise ValueError("E_TOOLCHAIN")
    return root


def build(toolchain_id: str) -> Path:
    row = _registered(toolchain_id)
    if sys.version_info[:2] != (3, 14):
        raise ValueError("E_TOOLCHAIN")
    # A source edit in another checkout must not rewrite the shared pinned
    # slot before the manifest mismatch is discovered.
    if _source_manifest_sha256() != row["manifest_sha256"]:
        raise ValueError("E_TOOLCHAIN")
    root = _root(toolchain_id, row)
    if root.is_symlink():
        raise ValueError("E_TOOLCHAIN")
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink() or ROOT.is_symlink():
        raise ValueError("E_TOOLCHAIN")
    try:
        lock = os.open(parent / ".build.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise ValueError("E_TOOLCHAIN") from None
    try:
        metadata = os.fstat(lock)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("E_TOOLCHAIN")
        fcntl.flock(lock, fcntl.LOCK_EX)
        # A concurrent builder may have published the exact slot while this
        # process waited. An existing invalid slot must still fail closed.
        if root.exists() or root.is_symlink():
            return resolve(toolchain_id)
        with tempfile.TemporaryDirectory(prefix=f".{toolchain_id}-", dir=parent) as staging:
            candidate = Path(staging)
            bin_dir = candidate / "bin"
            bin_dir.mkdir(mode=0o700)
            digests = {}
            for name, source in BINARIES.items():
                target = bin_dir / name
                target.symlink_to(source)
                digests[name] = _digest(target)
            for name, source in COPIED.items():
                target = bin_dir / name
                target.write_bytes(source.read_bytes())
                target.chmod(0o755)
                digests[name] = _digest(target)
            payload = {"id": toolchain_id, "python": "3.14", "binaries": digests}
            encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if hashlib.sha256(encoded).hexdigest() != row["manifest_sha256"]:
                raise ValueError("E_TOOLCHAIN")
            (candidate / "manifest.json").write_bytes(encoded)
            candidate.replace(root)
        return resolve(toolchain_id)
    finally:
        os.close(lock)


if __name__ == "__main__":
    if sys.argv[1:] != ["--build", "py314-stdlib"]:
        raise SystemExit("E_TOOLCHAIN")
    build("py314-stdlib")
    print("toolchain: PASS")
