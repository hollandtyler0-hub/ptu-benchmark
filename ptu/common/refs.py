"""Canonical resource references and slash-aware scope globs."""

import re


def parse_ref(ref: str) -> tuple[str, str, str]:
    if not isinstance(ref, str):
        raise ValueError("E_BAD_REF")
    match = re.fullmatch(r"([a-z][a-z0-9_-]*):([a-z][a-z0-9_-]*)/([^\s]+)", ref)
    if not match:
        raise ValueError("E_BAD_REF")
    return match.group(1), match.group(2), match.group(3)


def ancestors(ref: str, state: dict | None = None) -> list[str]:
    service, kind, path = parse_ref(ref)
    from ptu.apps import registered_app
    app = registered_app(service)
    if app is not None:
        return app.ancestors(ref, state)
    if service == "github":
        repo_path = path.split("@", 1)[0].split("#", 1)[0]
        if kind == "file":
            if "@" not in path or ":" not in path.split("@", 1)[1]:
                raise ValueError("E_BAD_REF")
            branch = path.split("@", 1)[1].split(":", 1)[0]
            return [f"github:branch/{repo_path}#{branch}", f"github:repo/{repo_path}", f"github:org/{repo_path.split('/')[0]}"]
        if kind in {"branch", "pr", "issue", "collaborators"}:
            return [f"github:repo/{repo_path}", f"github:org/{repo_path.split('/')[0]}"]
        if kind == "repo":
            return [f"github:org/{repo_path.split('/')[0]}"]
    if service == "slack":
        if kind == "message":
            return [f"slack:channel/{path.rsplit('/', 1)[0]}"]
    if service == "linear":
        if kind == "comment":
            issue = path.split("/", 1)[0]
            return [f"linear:issue/{issue}", f"linear:team/{issue.split('-')[0]}"]
        if kind == "issue":
            return [f"linear:team/{path.split('-')[0]}"]
        if kind == "project":
            return [f"linear:team/{path.split('/')[0]}"]
    if service == "drive":
        if kind in {"share", "comment"}:
            file_ref = f"drive:file/{path.split('/', 1)[0]}"
            return [file_ref, *ancestors(file_ref, state)]
        if kind == "file" and state:
            drive = state.get("drive", state)
            folder = drive.get("files", {}).get(path, {}).get("folder")
            if folder:
                return [f"drive:folder/{folder}"]
    if service == "billing":
        if kind == "note":
            return [f"billing:customer/{path.split('/', 1)[0]}"]
        if kind == "refund":
            return [f"billing:invoice/{path.split('/', 1)[0]}"]
        if kind == "credit":
            return [f"billing:customer/{path.split('/', 1)[0]}"]
    return []


def glob_match(pattern: str, ref: str) -> bool:
    """Match whole refs; * cannot cross slash, ** can. Other punctuation is literal."""
    pieces = []
    pos = 0
    while pos < len(pattern):
        if pattern.startswith("**", pos):
            pieces.append(".*")
            pos += 2
        elif pattern[pos] == "*":
            # Branch names are one logical ref component even when they contain '/'.
            branch_component = pattern.rfind("@", 0, pos) > pattern.rfind(":", 0, pos) and ":" in pattern[pos:]
            pieces.append(".*" if branch_component else "[^/]*")
            pos += 1
        elif pattern[pos] == "?":
            pieces.append("[^/]")
            pos += 1
        else:
            pieces.append(re.escape(pattern[pos]))
            pos += 1
    return re.fullmatch("".join(pieces), ref) is not None


def specificity(pattern: str) -> int:
    return sum(char not in "*?" for char in pattern)
