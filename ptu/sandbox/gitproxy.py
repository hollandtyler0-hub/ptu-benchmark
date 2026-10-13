"""Runner-side Git object and ref proxy for the no-fork workstation.

The socket protocol is deliberately smaller than Git's external transport. A
workstation helper sends loose objects and one ref update. The host validates
object identity, evaluates the push, and is the only process that opens the
authoritative bare repository. No sandbox-supplied pathname reaches Git.
"""

from ptu.common.binaries import REAL_GIT
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

from .socket_paths import allocate_socket_dir, checked_socket_path, remove_socket_dir


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9_./-]+\Z")
_ZERO = "0" * 40
_MAX_REQUEST = 16 * 1024 * 1024
_GIT_ENV = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1", "LANG": "C"}


def _git(bare: Path, *args: str) -> str:
    done = subprocess.run([str(REAL_GIT), "--git-dir", str(bare), *args],
                          env=_GIT_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=20, check=False)
    if done.returncode:
        raise ValueError("E_GIT_PROXY")
    return done.stdout.decode("utf-8", "replace").strip()


def _repo_path(root: Path, repo: str) -> Path:
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise ValueError("E_GIT_REPO")
    org, name = repo.split("/")
    bare = root / "remotes" / org / (name + ".git")
    if not bare.is_dir() or bare.is_symlink() or bare.resolve() != bare:
        raise ValueError("E_GIT_REPO")
    if not (bare / "HEAD").is_file() or (bare / "objects").is_symlink():
        raise ValueError("E_GIT_REPO")
    return bare


def _objects(items: dict) -> dict[str, bytes]:
    if not isinstance(items, dict) or len(items) > 5000:
        raise ValueError("E_GIT_OBJECT")
    decoded = {}
    total = 0
    for sha, encoded in items.items():
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or not isinstance(encoded, str):
            raise ValueError("E_GIT_OBJECT")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, Exception):
            raise ValueError("E_GIT_OBJECT") from None
        total += len(raw)
        if total > _MAX_REQUEST or hashlib.sha1(raw).hexdigest() != sha:
            raise ValueError("E_GIT_OBJECT")
        nul = raw.find(b"\0")
        if nul < 0 or nul > 40 or b" " not in raw[:nul]:
            raise ValueError("E_GIT_OBJECT")
        kind, size = raw[:nul].split(b" ", 1)
        if kind not in {b"commit", b"tree", b"blob", b"tag"} or not size.isdigit() or int(size) != len(raw) - nul - 1:
            raise ValueError("E_GIT_OBJECT")
        decoded[sha] = raw
    return decoded


def assert_log_integrity(initial_refs: dict[str, str], effects: list[dict], final_refs: dict[str, str]) -> None:
    """Final authoritative refs must equal the fold of accepted proxy effects."""
    expected = dict(initial_refs)
    for effect in effects:
        if not effect.get("accepted"):
            continue
        ref, old, new = effect.get("ref"), effect.get("old"), effect.get("new")
        if not isinstance(ref, str) or expected.get(ref, _ZERO) != old or not isinstance(new, str):
            raise ValueError("V-LOG-INTEGRITY")
        if new == _ZERO:
            expected.pop(ref, None)
        else:
            expected[ref] = new
    if expected != final_refs:
        raise ValueError("V-LOG-INTEGRITY")


class GitProxy:
    def __init__(self, episode_dir: Path, get_state, actor, policy=None, socket_dir: Path | None = None):
        self.episode_dir = Path(episode_dir).resolve()
        self.get_state = get_state
        self.actor = actor
        self.policy = policy
        self._owned_socket_dir = socket_dir is None
        self.socket_dir = socket_dir
        self.socket_path = checked_socket_path(socket_dir, "gitproxy.sock") if socket_dir else None
        self._socket = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._effects = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self) -> Path:
        if self.socket_dir is None:
            self.socket_dir = allocate_socket_dir()
            self.socket_path = checked_socket_path(self.socket_dir, "gitproxy.sock")
        if self._socket is not None or self.socket_path.exists() or self.socket_path.is_symlink():
            raise ValueError("E_GIT_PROXY_START")
        directory = self.socket_path.parent
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError("E_GIT_PROXY_START")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            sock.listen(4)
            sock.settimeout(0.2)
        except OSError:
            sock.close()
            self.socket_path.unlink(missing_ok=True)
            if self._owned_socket_dir:
                remove_socket_dir(self.socket_dir)
                self.socket_dir = None
                self.socket_path = None
            raise ValueError("E_GIT_PROXY_START") from None
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, args=(sock,), daemon=True)
        self._thread.start()
        return self.socket_path

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=47)
            if self._thread.is_alive():
                raise ValueError("E_GIT_PROXY_STOP")
            self._thread = None
        self._socket = None
        self.socket_path.unlink(missing_ok=True)
        if self._owned_socket_dir:
            remove_socket_dir(self.socket_dir)
            self.socket_dir = None
            self.socket_path = None

    def drain_effects(self) -> list[dict]:
        with self._lock:
            effects = self._effects
            self._effects = []
        return effects

    def _serve(self, listener):
        while not self._stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                conn.settimeout(45)
                try:
                    data = bytearray()
                    while b"\n" not in data:
                        part = conn.recv(min(65536, _MAX_REQUEST + 1 - len(data)))
                        if not part or len(data) + len(part) > _MAX_REQUEST:
                            raise ValueError("E_GIT_REQUEST")
                        data.extend(part)
                    request = json.loads(bytes(data).split(b"\n", 1)[0])
                    response = self._handle(request)
                except (ValueError, OSError, UnicodeError, TypeError, KeyError, json.JSONDecodeError):
                    response = {"ok": False, "message": "E_GIT_REQUEST"}
                try:
                    conn.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())
                except OSError:
                    pass

    def _handle(self, request: dict) -> dict:
        if not isinstance(request, dict) or request.get("op") not in {"push", "fetch", "worktree"}:
            raise ValueError("E_GIT_REQUEST")
        bare = _repo_path(self.episode_dir, request.get("repo"))
        if request['op'] == 'worktree':
            from .git_local import run_local_git
            return run_local_git(self.episode_dir, request['repo'],
                                 request.get('command'), request.get('args'), self._stop,
                                 request.get('identity'))
        if request['op'] == 'fetch':
            return self._fetch(request, bare)
        ref, new = request.get("ref"), request.get("new")
        if not isinstance(ref, str) or not _REF.fullmatch(ref) or ".." in ref or ref.endswith("/"):
            raise ValueError("E_GIT_REF")
        if not isinstance(new, str) or not _SHA.fullmatch(new):
            raise ValueError("E_GIT_REF")
        objects = _objects(request.get("objects"))
        old = _git(bare, "rev-parse", "--verify", ref) if self._ref_exists(bare, ref) else _ZERO
        forced = request.get("force") is True
        effect = {"repo": request["repo"], "ref": ref, "old": old, "new": new,
                  "accepted": False, "forced": forced, "rejected_by": None,
                  "resource": "codehost:branch/" + request["repo"] + "#" + ref.removeprefix("refs/heads/")}
        state = self.get_state() or {}
        codehost = state.get("codehost", state) if isinstance(state, dict) else {}
        codehost_repo = codehost.get("repos", {}).get(request["repo"]) if isinstance(codehost, dict) else None
        repo_state = state.get("repositories", {}).get(request["repo"], {}) if isinstance(state, dict) else {}
        protected = ref.removeprefix("refs/heads/") in repo_state.get("protected_branches", [])
        actor_role = self.actor.get("role") if isinstance(self.actor, dict) else getattr(self.actor, "role", None)
        policy_message = None
        if codehost_repo is not None:
            from ptu.apps.codehost.rules import evaluate_push

            patterns = codehost_repo.get("push_protection", {}).get("patterns", [])
            def scan(_update):
                hits = []
                for sha, raw in objects.items():
                    if raw.startswith(b"blob ") and any(
                            isinstance(pattern, str) and pattern.encode() in raw
                            for pattern in patterns):
                        hits.append({"ref": "codehost:blob/" + sha})
                return hits

            verdict = evaluate_push(codehost, self.actor, request["repo"],
                                    [{"ref": ref, "old": old, "new": new, "forced": forced}], scan,
                                    consume_bypass=False)["updates"][0]
            effect.update({key: verdict[key] for key in
                           ("rejected_by", "push_protection", "secret_hits")})
            policy_message = verdict["messages"][0] if verdict["messages"] else None
        if not effect["rejected_by"] and self.policy is not None:
            from ptu.common.policy import scope_verdict
            if not scope_verdict(effect["resource"], "write", self.policy, state).allowed:
                effect["rejected_by"] = "scope"
        if not effect["rejected_by"] and actor_role not in {"write", "maintain", "admin"}:
            effect["rejected_by"] = "permission"
        elif not effect["rejected_by"] and protected:
            effect["rejected_by"] = "branch_protection"
        if not effect["rejected_by"] and old != _ZERO and not forced and not self._is_ancestor(bare, old, new, objects):
            effect["rejected_by"] = "non_fast_forward"
        if effect["rejected_by"]:
            with self._lock:
                self._effects.append(effect)
            message = policy_message or (
                "remote: error: GH006: Protected branch update failed for " + ref + "."
                if protected else "remote: push rejected: " + effect["rejected_by"])
            return {"ok": False, "message": message}
        # Object copies are made only after policy permits the update. Objects
        # are content addressed; a later failed ref transaction cannot alter refs.
        for sha, raw in objects.items():
            target = bare / "objects" / sha[:2] / sha[2:]
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                import zlib
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    output.write(zlib.compress(raw))
                    temp_name = output.name
                os.replace(temp_name, target)
        if _git(bare, "cat-file", "-t", new) != "commit":
            raise ValueError("E_GIT_OBJECT")
        _git(bare, "update-ref", ref, new, old)
        effect["accepted"] = True
        if codehost_repo is not None:
            from ptu.apps.codehost.app import apply_push, refresh_checks
            if effect.get("push_protection") == "bypassed":
                user = self.actor.get("acting_for") if isinstance(self.actor, dict) else getattr(self.actor, "acting_for", None)
                principal = (self.actor.get("principal") or self.actor.get("id")) if isinstance(self.actor, dict) else getattr(self.actor, "principal", None)
                codehost_repo.get("push_protection", {}).get("bypasses", {}).pop(user or principal, None)
            apply_push(codehost, request["repo"], ref, new)
            if any(pull.get("state") == "open" and pull.get("head") == ref.removeprefix("refs/heads/")
                   for pull in codehost_repo.get("pulls", {}).values()):
                effect["checks"] = refresh_checks(codehost_repo, new, ref, bare,
                                                  repo_name=request["repo"])
        with self._lock:
            self._effects.append(effect)
        return {"ok": True, "message": "To " + request["repo"] + "\n * [new branch] " + ref}

    def _fetch(self, request: dict, bare: Path) -> dict:
        if request.get('ref') != 'refs/heads/main' or not isinstance(request.get('have'), str):
            raise ValueError('E_GIT_FETCH')
        have = request['have']
        if not _SHA.fullmatch(have) or _git(bare, 'cat-file', '-t', have) != 'commit':
            raise ValueError('E_GIT_FETCH')
        state = self.get_state() or {}
        name = request['repo']
        repository = state.get('codehost', {}).get('repos', {}).get(name, {})
        head = repository.get('branches', {}).get('main', {}).get('head_commit')
        if not isinstance(head, str) or not _SHA.fullmatch(head):
            raise ValueError('E_GIT_FETCH')
        if self.policy is not None:
            from ptu.common.policy import scope_verdict
            if not scope_verdict(f'codehost:branch/{name}#main', 'read', self.policy, state).allowed:
                raise ValueError('E_GIT_FETCH')
        listing = _git(bare, 'rev-list', '--objects', head, '^' + have).splitlines()
        if len(listing) > 5000:
            raise ValueError('E_GIT_FETCH')
        objects = {}
        total = 0
        for line in listing:
            sha = line[:40]
            if not _SHA.fullmatch(sha):
                raise ValueError('E_GIT_FETCH')
            kind = _git(bare, 'cat-file', '-t', sha)
            if kind not in {'commit', 'tree', 'blob', 'tag'}:
                raise ValueError('E_GIT_FETCH')
            done = subprocess.run([str(REAL_GIT), '--git-dir', str(bare),
                                   'cat-file', kind, sha], stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=20, check=False)
            if done.returncode:
                raise ValueError('E_GIT_FETCH')
            raw = kind.encode() + b' ' + str(len(done.stdout)).encode() + b'\0' + done.stdout
            if hashlib.sha1(raw).hexdigest() != sha:
                raise ValueError('E_GIT_FETCH')
            total += len(raw)
            if total > 12 * 1024 * 1024:
                raise ValueError('E_GIT_FETCH')
            objects[sha] = base64.b64encode(raw).decode()
        return {'ok': True, 'sha': head, 'objects': objects}

    @staticmethod
    def _ref_exists(bare: Path, ref: str) -> bool:
        done = subprocess.run([str(REAL_GIT), "--git-dir", str(bare), "show-ref", "--verify", "--quiet", ref],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return done.returncode == 0

    @staticmethod
    def _is_ancestor(bare: Path, old: str, new: str, objects: dict[str, bytes]) -> bool:
        # Walk request-supplied commits until the old ref or a commit already
        # held by the bare repository is reached. Objects are SHA-validated by
        # _objects; no request-supplied path or command is passed to Git.
        pending = [new]
        seen = set()
        while pending:
            sha = pending.pop()
            if sha == old:
                return True
            if sha in seen:
                continue
            seen.add(sha)
            raw = objects.get(sha)
            if raw is None:
                done = subprocess.run([str(REAL_GIT), "--git-dir", str(bare),
                                       "merge-base", "--is-ancestor", old, sha],
                                      env=_GIT_ENV,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=20, check=False)
                if done.returncode == 0:
                    return True
                continue
            header, _, payload = raw.partition(b"\0")
            if not header.startswith(b"commit ") or not payload:
                continue
            for line in payload.split(b"\n\n", 1)[0].splitlines():
                if line.startswith(b"parent "):
                    parent = line.removeprefix(b"parent ").decode("ascii", "ignore")
                    if _SHA.fullmatch(parent):
                        pending.append(parent)
        return False
