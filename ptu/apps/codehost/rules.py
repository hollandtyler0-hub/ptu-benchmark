"""GitHub-like branch, review, check, and CODEOWNERS decisions."""

from ptu.common.binaries import REAL_GIT
import fnmatch
import re
import subprocess
from pathlib import PurePosixPath


_ZERO = "0" * 40
_OWNER = re.compile(r"(?:@[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?|[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+)\Z")


def _repos(state: dict) -> dict:
    root = state.get("codehost", state)
    return root.get("repos", root.get("repositories", {}))


def _user(actor) -> str:
    return (actor.get("acting_for") or actor.get("id") or actor.get("principal")) if isinstance(actor, dict) else (
        actor.acting_for or actor.principal)


def _role(actor) -> str:
    return actor.get("role", "read") if isinstance(actor, dict) else actor.role


def _pattern_matches(pattern: str, path: str) -> bool:
    if pattern == "*":
        return True
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        prefix = pattern.rstrip("/")
        return (path.startswith(prefix + "/") if anchored else
                any(path[index:].startswith(prefix + "/") for index in [0, *[i + 1 for i, c in enumerate(path) if c == "/"]]))
    if "/" not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern)
    expression = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    if anchored or "/" in pattern:
        return re.fullmatch(expression, path) is not None
    return re.search(expression, path) is not None


def code_owners(text: str, paths: list[str]) -> dict[str, list[str]]:
    """Return the owners on the last valid matching line for each path."""
    lines = []
    for raw in text.splitlines():
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        parts = content.split()
        if len(parts) < 2:
            continue
        pattern, owners = parts[0], parts[1:]
        if pattern.startswith(("!", "\\#")) or any(char in pattern for char in "[]"):
            continue
        if any(_OWNER.fullmatch(owner) is None for owner in owners):
            continue
        lines.append((pattern, owners))
    result = {}
    for path in paths:
        result[path] = []
        for pattern, owners in lines:
            if _pattern_matches(pattern, path):
                result[path] = list(owners)
    return result


def _active_rules(repository: dict, branch: str) -> list[dict]:
    return [row for row in repository.get("rulesets", [])
            if row.get("active", True) and fnmatch.fnmatchcase(branch, row.get("pattern", ""))]


def _protection(repository: dict, branch: str) -> dict:
    return repository.get("branch_protection", {}).get(branch, {})


def required_review_count(repository: dict, branch: str) -> int:
    protection = _protection(repository, branch)
    rulesets = _active_rules(repository, branch)
    return max([protection.get("required_reviews", 0),
                int(protection.get("require_code_owner_reviews", False)),
                *[rule.get("required_reviews", 0) for rule in rulesets],
                *[int(rule.get("require_code_owner_reviews", False)) for rule in rulesets]])


def evaluate_push(state: dict, actor, repo: str, updates: list[dict], scan,
                  *, consume_bypass: bool = True) -> dict:
    """Evaluate each ref independently; never include secret values in effects."""
    repository = _repos(state).get(repo)
    if repository is None:
        raise ValueError("E_CODEHOST_REPO")
    role = _role(actor)
    user = _user(actor)
    results = []
    for update in updates:
        ref = update.get("ref", "")
        if not isinstance(ref, str) or not ref.startswith("refs/heads/") or ".." in ref:
            raise ValueError("E_CODEHOST_REF")
        branch = ref.removeprefix("refs/heads/")
        old = update.get("old", repository.get("branches", {}).get(branch, {}).get("head_commit", _ZERO))
        new = update.get("new", _ZERO)
        forced = bool(update.get("forced", update.get("force", False)))
        deletion = new == _ZERO or update.get("delete") is True
        protection = _protection(repository, branch)
        rulesets = _active_rules(repository, branch)
        classic_bypass = role == "admin" and not protection.get("enforce_admins", False)
        bypass_rules = all(user in row.get("bypass_actors", []) for row in rulesets)
        rejected = None
        if role not in {"write", "maintain", "admin"}:
            rejected = "permission"
        elif deletion and any(pr.get("state") == "open" and pr.get("head") == branch
                              for pr in repository.get("pulls", {}).values()):
            rejected = "open_pull_request"
        elif protection and not classic_bypass:
            if deletion and not protection.get("allow_deletions", False):
                rejected = "branch_protection"
            elif forced and not protection.get("allow_force_pushes", False):
                rejected = "branch_protection"
            elif protection.get("require_pull_request") and not update.get("via_pull_request"):
                rejected = "branch_protection"
        if not rejected and rulesets and not bypass_rules:
            if any(row.get("require_pull_request") and not update.get("via_pull_request")
                   for row in rulesets):
                rejected = "ruleset"
            elif forced and any(not row.get("allow_force_pushes", False) for row in rulesets):
                rejected = "ruleset"
            elif deletion and any(not row.get("allow_deletions", False) for row in rulesets):
                rejected = "ruleset"
        hits = scan(update) if callable(scan) and not deletion else []
        hits = [{"ref": hit["ref"]} for hit in hits if isinstance(hit, dict) and isinstance(hit.get("ref"), str)]
        protection_state = repository.get("push_protection", {})
        bypasses = protection_state.get("bypasses", {})
        bypass = bypasses.get(user) if isinstance(bypasses, dict) else None
        push_protection = None
        if not rejected and hits and protection_state.get("enabled"):
            if role in {"write", "maintain", "admin"} and isinstance(bypass, dict) and bypass.get("reason"):
                push_protection = "bypassed"
                if consume_bypass:
                    bypasses.pop(user, None)
            else:
                rejected = "push_protection"
                push_protection = "blocked"
        message = (f"remote: error: GH006: Protected branch update failed for {ref}."
                   if rejected in {"branch_protection", "ruleset"} else
                   f"remote: push rejected: {rejected}" if rejected else "")
        results.append({"repo": repo, "ref": ref, "old": old, "new": new,
                        "accepted": rejected is None, "messages": [message] if message else [],
                        "forced": forced, "rejected_by": rejected,
                        "push_protection": push_protection, "secret_hits": hits})
    return {"updates": results}


def mergeability(state: dict, repo: str, number: int, actor) -> dict:
    repository = _repos(state).get(repo)
    if repository is None:
        raise ValueError("E_CODEHOST_REPO")
    pr = repository.get("pulls", {}).get(str(number), repository.get("pulls", {}).get(number))
    if pr is None:
        raise ValueError("E_CODEHOST_PULL")
    protection = _protection(repository, pr["base"])
    rulesets = _active_rules(repository, pr["base"])
    required_reviews = required_review_count(repository, pr["base"])
    required_checks = set(protection.get("required_checks", []))
    for rule in rulesets:
        required_checks.update(rule.get("required_checks", []))
    code_owner_required = (protection.get("require_code_owner_reviews", False)
                           or any(rule.get("require_code_owner_reviews", False) for rule in rulesets))
    blocked = []
    if pr.get("state") != "open":
        blocked.append("closed")
    if pr.get("draft"):
        blocked.append("draft")
    if protection.get('require_up_to_date'):
        base_sha = repository.get('branches', {}).get(pr['base'], {}).get('head_commit')
        head_sha = pr.get('head_sha')
        bare = repository.get('_bare_repo')
        if not isinstance(base_sha, str) or not isinstance(head_sha, str) or not bare:
            blocked.append('stale_branch')
        else:
            result = subprocess.run([str(REAL_GIT), '--git-dir', str(bare),
                                     'merge-base', '--is-ancestor', base_sha, head_sha],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=20, check=False)
            if result.returncode:
                blocked.append('stale_branch')
    latest_reviews = {}
    for review in pr.get("reviews", []):
        if not review.get("dismissed"):
            latest_reviews[review.get("author")] = review
    if (protection.get("require_pull_request") or any(rule.get("require_pull_request") for rule in rulesets)) and any(
            review.get("state") == "changes_requested" for review in latest_reviews.values()):
        blocked.append("changes_requested")
    collaborator_roles = {row.get("login"): row.get("role")
                          for row in repository.get("collaborators", [])}
    approvals = {name for name, review in latest_reviews.items()
                 if name != pr.get("author") and review.get("state") == "approved"
                 and (review.get("author_role", collaborator_roles.get(name)) in {"write", "maintain", "admin"}
                      or code_owner_required and name in pr.get("code_owners", []))}
    if len(approvals) < required_reviews:
        blocked.append("required_reviews")
    if code_owner_required and pr.get("code_owners") and not approvals.intersection(pr["code_owners"]):
        blocked.append("code_owner_review")
    sha = pr.get("head_sha", repository.get("branches", {}).get(pr.get("head"), {}).get("head_commit"))
    for name in sorted(required_checks):
        rows = [check for check in repository.get("checks", [])
                if check.get("name") == name and check.get("sha") == sha]
        current = max(rows, key=lambda row: (row.get("run_number", 0), row.get("attempt", 0)), default=None)
        if current is None or current.get("conclusion") not in {"success", "skipped", "neutral"}:
            blocked.append("required_check:" + name)
    if _role(actor) not in {"write", "maintain", "admin"}:
        blocked.append("permission")
    return {"mergeable": not blocked, "blocked_by": blocked,
            "required_checks": sorted(required_checks), "approvals": sorted(approvals)}
