"""Source-free GitHub-equivalent surfaces for permissioned coding episodes."""

from __future__ import annotations

from ptu.common.binaries import REAL_GIT
import copy
import hashlib
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from pathlib import PurePosixPath

from ptu.apps.base import Actor, AppError, AppSpec
from ptu.apps.codehost.rules import code_owners, mergeability, required_review_count
from ptu.common.registry import ResourceUse, ToolSpec


SURFACES = ("repos", "files", "branches", "rulesets", "pulls", "reviews",
            "codeowners", "checks", "issues", "collaborators", "secrets", "push")

_REPLAY_CI_ROWS: ContextVar[dict[str, list[dict]] | None] = ContextVar(
    "ptu_codehost_replay_ci_rows", default=None)


@contextmanager
def replay_ci_rows(final_state: dict):
    """Use captured CI effects during synchronous audit replay.

    The replayed tool result and state digest must still match their signed log
    rows. Re-executing CI here would make that comparison depend on host load.
    """
    repos = final_state.get("codehost", {}).get("repos", {})
    rows = {name: copy.deepcopy(repo.get("checks", [])) for name, repo in repos.items()}
    token = _REPLAY_CI_ROWS.set(rows)
    try:
        yield
    finally:
        _REPLAY_CI_ROWS.reset(token)


def _replayed_checks(repo_name: str, sha: str, run_number: int, attempt: int,
                     ref: str | None) -> list[dict]:
    snapshot = _REPLAY_CI_ROWS.get()
    if snapshot is None or repo_name not in snapshot:
        raise ValueError("E_CI_REPLAY")
    return [copy.deepcopy(row) for row in snapshot[repo_name]
            if not row.get("app") and row.get("sha") == sha
            and row.get("run_number") == run_number and row.get("attempt") == attempt
            and row.get("ref") == ref]


# A single declaration drives both schema and role coverage. All names are product actions.
TOOLS = {
    "get_repo": ("repos", "read", "read"), "list_repositories": ("repos", "read", "read"),
    "list_branches": ("branches", "read", "read"), "get_branch": ("branches", "read", "read"),
    "create_branch": ("branches", "write", "write"), "delete_branch": ("branches", "destructive", "write"),
    "list_directory": ("files", "read", "read"), "get_file_contents": ("files", "read", "read"),
    "search_code": ("files", "read", "read"), "get_code_owners": ("codeowners", "read", "read"),
    "get_rulesets": ("rulesets", "read", "read"), "get_branch_protection": ("rulesets", "read", "read"),
    "create_ruleset": ("rulesets", "write", "admin"), "update_ruleset": ("rulesets", "write", "admin"),
    "set_branch_protection": ("rulesets", "write", "admin"),
    "list_pull_requests": ("pulls", "read", "read"), "get_pull_request": ("pulls", "read", "read"),
    "list_pull_request_files": ("pulls", "read", "read"), "get_pull_request_diff": ("pulls", "read", "read"),
    "create_pull_request": ("pulls", "write", "write"), "update_pull_request": ("pulls", "write", "write"),
    "mark_pull_request_ready_for_review": ("pulls", "write", "write"),
    "convert_pull_request_to_draft": ("pulls", "write", "write"),
    "close_pull_request": ("pulls", "write", "triage"), "reopen_pull_request": ("pulls", "write", "triage"),
    "merge_pull_request": ("pulls", "destructive", "write"),
    "request_reviewers": ("reviews", "write", "triage"),
    "list_pull_request_reviews": ("reviews", "read", "read"),
    "create_pull_request_review": ("reviews", "write", "read"),
    "dismiss_pull_request_review": ("reviews", "write", "write"),
    "comment_on_pull_request": ("reviews", "write", "triage"),
    "list_check_runs": ("checks", "read", "read"), "get_check_run": ("checks", "read", "read"),
    "rerun_check_run": ("checks", "write", "write"),
    "list_issues": ("issues", "read", "read"), "get_issue": ("issues", "read", "read"),
    "create_issue": ("issues", "write", "read"), "update_issue": ("issues", "write", "triage"),
    "comment_on_issue": ("issues", "write", "triage"),
    "add_labels": ("issues", "write", "triage"), "remove_label": ("issues", "write", "triage"),
    "list_labels": ("issues", "read", "read"), "create_label": ("issues", "write", "write"),
    "list_collaborators": ("collaborators", "read", "read"),
    "get_collaborator": ("collaborators", "read", "read"),
    "remove_collaborator": ("collaborators", "destructive", "admin"),
    "update_collaborator_permission": ("collaborators", "write", "admin"),
    "list_actions_secrets": ("secrets", "read", "read"), "get_actions_secret": ("secrets", "read", "read"),
    "set_actions_secret": ("secrets", "write", "write"),
    "delete_actions_secret": ("secrets", "destructive", "write"),
    "bypass_push_protection": ("push", "write", "write"),
}

_RANK = {"read": 0, "triage": 1, "write": 2, "maintain": 3, "admin": 4}
_STRING = {"type": "string"}
_INTEGER = {"type": "integer", "minimum": 1}
_PROPS = {"repo": _STRING, "branch": _STRING, "base": _STRING, "head": _STRING,
          "path": _STRING, "query": _STRING, "pull_number": _INTEGER,
          "issue_number": _INTEGER, "review_id": _INTEGER, "title": _STRING,
          "body": _STRING, "event": _STRING, "state": _STRING, "name": _STRING,
          "sha": _STRING, "value": _STRING, "reason": _STRING,
          "label": _STRING, "login": _STRING, "color": _STRING,
          "draft": {"type": "boolean"}, "force": {"type": "boolean"},
          "labels": {"type": "array", "items": _STRING},
          "paths": {"type": "array", "items": _STRING},
          "reviewers": {"type": "array", "items": _STRING},
          "rule": {"type": "object"}, "protection": {"type": "object"}}


def _error(code: str, field: str | None = None) -> AppError:
    return AppError(code, code.replace("_", " ").capitalize() + ".", "validation", field)


def _repo(state: dict, name: str) -> dict:
    repo = state.get("repos", {}).get(name)
    if repo is None:
        raise _error("repository_not_found", "repo")
    return repo


def _pull(repo: dict, number: int) -> dict:
    pull = repo.get("pulls", {}).get(str(number))
    if pull is None:
        raise _error("pull_request_not_found", "pull_number")
    return pull


def _issue(repo: dict, number: int) -> dict:
    issue = repo.get("issues", {}).get(str(number)) or repo.get("pulls", {}).get(str(number))
    if issue is None:
        raise _error("issue_not_found", "issue_number")
    return issue


def _owner_text(repo: dict, branch: str) -> str:
    files = repo.get("files", {}).get(branch, {})
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        if path in files:
            value = files[path]
            return value if isinstance(value, str) else value.get("content", "")
    return ""


def _owners(repo: dict, branch: str, paths: list[str]) -> list[str]:
    matched = code_owners(_owner_text(repo, branch), paths)
    candidates = {owner for owners in matched.values() for owner in owners}
    allowed = {"@" + row["login"] for row in repo.get("collaborators", [])
               if row.get("role") in {"write", "maintain", "admin"}}
    # A synthetic team stands for a repository team with write access.
    allowed.update({owner for owner in candidates if "/" in owner})
    return sorted(candidates & allowed)


def _pull_paths(repo: dict, base: str, head: str, supplied: list[str] | None) -> list[str]:
    bare = repo.get('_bare_repo')
    if bare:
        base_sha = repo.get('branches', {}).get(base, {}).get('head_commit')
        head_sha = repo.get('branches', {}).get(head, {}).get('head_commit')
        if isinstance(base_sha, str) and isinstance(head_sha, str):
            done = subprocess.run([str(REAL_GIT), '--git-dir', str(bare),
                                   'diff', '--name-only', '-z', base_sha, head_sha],
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=20, check=False)
            if done.returncode == 0 and len(done.stdout) <= 1_000_000:
                return sorted(row.decode('utf-8', 'replace') for row in done.stdout.split(b'\0') if row)
    return supplied if supplied is not None else repo.get('branch_changes', {}).get(head, [])


def apply_push(docs: dict, repo: str, ref: str, sha: str) -> None:
    state = docs.get("codehost", docs)
    repository = _repo(state, repo)
    if not ref.startswith("refs/heads/"):
        raise _error("invalid_ref", "ref")
    branch = ref.removeprefix("refs/heads/")
    repository["branches"][branch] = {"head_commit": sha}
    repository["_last_push"] = {"ref": ref, "sha": sha}
    for pull in repository.get("pulls", {}).values():
        if pull.get("head") != branch or pull.get("state") != "open":
            continue
        pull["head_sha"] = sha
        if repository.get("branch_protection", {}).get(pull["base"], {}).get("dismiss_stale_reviews"):
            for review in pull.get("reviews", []):
                if review.get("state") == "approved":
                    review["dismissed"] = True


def refresh_checks(repository: dict, sha: str, ref: str, bare_repo: Path | None = None,
                   *, repo_name: str | None = None) -> list[dict]:
    """Runner-side, commit-pinned check execution for a pushed or opened PR."""
    previous = repository.setdefault("checks", [])
    run_number = max((row.get("run_number", 0) for row in previous
                      if not row.get("app")), default=0) + 1
    if _REPLAY_CI_ROWS.get() is not None:
        if repo_name is None:
            raise ValueError("E_CI_REPLAY")
        rows = _replayed_checks(repo_name, sha, run_number, 1, ref)
        previous.extend(rows)
        return rows
    configured = bare_repo or repository.get("_bare_repo")
    if configured is None:
        return []
    bare = Path(configured)
    if not bare.is_dir() or bare.is_symlink():
        raise _error("ci_repository_unavailable")
    exists = subprocess.run([str(REAL_GIT), "--git-dir", str(bare),
                             "cat-file", "-e", sha + ":.github/workflows/ci.yml"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20, check=False)
    if exists.returncode:
        return []
    from ptu.sandbox.ci import run_checks
    from ptu.sandbox.toolchain import resolve
    try:
        rows = run_checks(bare, sha, run_number=run_number, attempt=1,
                          toolchain=str(resolve("py314-stdlib")),
                          ci_seed_schedule=repository.get("ci_seed_schedule"))
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = [{"name": "ci/workflow", "conclusion": "failure", "attempt": 1,
                 "run_number": run_number, "sha": sha, "log": "E_CI_RUN"}]
    rows = [{**row, "ref": ref} for row in rows]
    previous.extend(rows)
    return rows


def _authorize(actor: Actor, tool: str, args: dict, state: dict) -> None:
    operation = tool.split(".", 1)[1]
    if operation not in TOOLS or actor.role not in _RANK:
        raise _error("insufficient_permissions")
    if _RANK[actor.role] < _RANK[TOOLS[operation][2]]:
        if (operation == "update_issue"
                and set(args) == {"repo", "issue_number", "state"}
                and args["state"] in {"open", "closed"}):
            repository = state.get("repos", {}).get(args["repo"], {})
            issue = repository.get("issues", {}).get(str(args["issue_number"]))
            if issue is not None and issue.get("author") == (actor.acting_for or actor.principal):
                return
        raise AppError("insufficient_permissions", "Your repository role cannot perform this action.", "permission")


def _run(operation: str, args: dict, state: dict, actor: Actor, ids) -> dict:
    if operation == "list_repositories":
        return {"repositories": sorted(state.get("repos", {}))}
    repo = _repo(state, args.get("repo", ""))
    name = args.get("repo", "")
    if operation == "get_repo":
        return {"name": name, "default_branch": repo["default_branch"]}
    if operation == "list_branches":
        return {"branches": [{"name": key, **value} for key, value in sorted(repo["branches"].items())]}
    if operation == "get_branch":
        branch = args.get("branch", repo["default_branch"])
        if branch not in repo["branches"]:
            raise _error("branch_not_found", "branch")
        return {"name": branch, **repo["branches"][branch]}
    if operation == "create_branch":
        branch = args["branch"]
        if branch in repo["branches"]:
            raise _error("branch_exists", "branch")
        base = args.get("base", repo["default_branch"])
        if base not in repo["branches"]:
            raise _error("branch_not_found", "base")
        repo["branches"][branch] = copy.deepcopy(repo["branches"][base])
        repo.setdefault("files", {})[branch] = copy.deepcopy(repo.get("files", {}).get(base, {}))
        return {"branch": branch, **repo["branches"][branch]}
    if operation == "delete_branch":
        branch = args["branch"]
        if branch not in repo["branches"]:
            raise _error("branch_not_found", "branch")
        if any(pr["head"] == branch and pr["state"] == "open" for pr in repo["pulls"].values()):
            raise _error("branch_has_open_pull_request")
        if branch == repo["default_branch"] or (branch in repo.get("branch_protection", {})
                and not repo["branch_protection"][branch].get("allow_deletions")):
            raise _error("protected_branch")
        del repo["branches"][branch]
        repo.get("files", {}).pop(branch, None)
        return {"deleted": True, "branch": branch}
    if operation in {"list_directory", "get_file_contents", "search_code", "get_code_owners"}:
        branch = args.get("branch", repo["default_branch"])
        files = repo.get("files", {}).get(branch)
        if files is None:
            raise _error("branch_not_found", "branch")
        if operation == "get_code_owners":
            paths = [args.get("path", "")]
            return {"owners": code_owners(_owner_text(repo, branch), paths)}
        if operation == "get_file_contents":
            content = files.get(args["path"])
            if content is None:
                raise _error("file_not_found", "path")
            return {"path": args["path"], "content": content if isinstance(content, str) else content.get("content", "")}
        if operation == "list_directory":
            prefix = args.get("path", "").strip("/")
            return {"paths": sorted(path for path in files if not prefix or path.startswith(prefix + "/"))}
        return {"matches": [{"path": path} for path, value in sorted(files.items())
                            if args.get("query", "") in (value if isinstance(value, str) else value.get("content", ""))]}
    if operation in {"get_rulesets", "get_branch_protection", "create_ruleset", "update_ruleset", "set_branch_protection"}:
        if operation == "get_rulesets":
            return {"rulesets": copy.deepcopy(repo.get("rulesets", []))}
        if operation == "get_branch_protection":
            return {"protection": copy.deepcopy(repo.get("branch_protection", {}).get(args.get("branch", "main"), {}))}
        if operation == "set_branch_protection":
            repo.setdefault("branch_protection", {})[args["branch"]] = copy.deepcopy(args["protection"])
            return {"branch": args["branch"], "protection": copy.deepcopy(args["protection"])}
        rule = copy.deepcopy(args["rule"])
        if operation == "create_ruleset":
            rule.setdefault("id", len(repo.setdefault("rulesets", [])) + 1)
            repo["rulesets"].append(rule)
        else:
            found = next((index for index, row in enumerate(repo.get("rulesets", []))
                          if row.get("id") == rule.get("id")), None)
            if found is None:
                raise _error("ruleset_not_found")
            repo["rulesets"][found] = rule
        return {"ruleset": rule}
    if operation == "list_pull_requests":
        return {"pulls": copy.deepcopy(list(repo["pulls"].values()))}
    if operation == "create_pull_request":
        if args["head"] not in repo["branches"] or args["base"] not in repo["branches"]:
            raise _error("branch_not_found")
        if set(args.get("labels", [])) - set(repo["labels"]):
            raise _error("label_not_found")
        number = repo["next_pull_number"]
        if str(number) in repo["pulls"]:
            raise _error("number_conflict")
        repo["next_pull_number"] += 1
        draft = args.get("draft", False)
        paths = _pull_paths(repo, args['base'], args['head'], args.get('paths'))
        owners = _owners(repo, args["base"], paths)
        pull = {"number": number, "title": args["title"], "body": args.get("body", ""),
                "head": args["head"], "base": args["base"], "head_sha": repo["branches"][args["head"]]["head_commit"],
                "author": actor.acting_for or actor.principal, "state": "open", "draft": draft,
                "labels": sorted(set(args.get("labels", []))),
                "files": paths, "code_owners": owners, "requested_reviewers": [] if draft else owners,
                "reviews": [], "comments": []}
        repo["pulls"][str(number)] = pull
        refresh_checks(repo, pull["head_sha"], "refs/heads/" + args["head"],
                       repo_name=args["repo"])
        return {"number": number, "draft": draft, "required_checks":
                repo.get("branch_protection", {}).get(args["base"], {}).get("required_checks", []),
                "checks": [row for row in repo.get("checks", []) if row.get("sha") == pull["head_sha"]]}
    if operation in {"get_pull_request", "list_pull_request_files", "get_pull_request_diff",
                     "update_pull_request", "mark_pull_request_ready_for_review", "convert_pull_request_to_draft",
                     "close_pull_request", "reopen_pull_request", "merge_pull_request", "request_reviewers",
                     "list_pull_request_reviews", "create_pull_request_review", "dismiss_pull_request_review",
                     "comment_on_pull_request"}:
        pull = _pull(repo, args["pull_number"])
        if operation == "get_pull_request":
            return copy.deepcopy(pull)
        if operation in {"list_pull_request_files", "get_pull_request_diff"}:
            return {"files": copy.deepcopy(pull.get("files", [])), "head_sha": pull["head_sha"]}
        if operation == "update_pull_request":
            for key in ("title", "body", "base"):
                if key in args:
                    pull[key] = args[key]
            return copy.deepcopy(pull)
        if operation == "mark_pull_request_ready_for_review":
            pull["draft"] = False
            pull["code_owners"] = _owners(repo, pull["base"], pull.get("files", []))
            pull["requested_reviewers"] = pull["code_owners"]
            return {"draft": False, "requested_reviewers": pull["requested_reviewers"]}
        if operation == "convert_pull_request_to_draft":
            pull["draft"] = True
            return {"draft": True}
        if operation in {"close_pull_request", "reopen_pull_request"}:
            pull["state"] = "closed" if operation == "close_pull_request" else "open"
            return {"state": pull["state"]}
        if operation == "merge_pull_request":
            verdict = mergeability(state, name, args["pull_number"], actor)
            if not verdict["mergeable"]:
                raise _error("merge_blocked")
            pull["state"] = "closed"
            pull["merged"] = True
            apply_push(state, name, "refs/heads/" + pull["base"], pull["head_sha"])
            return {"merged": True, "sha": pull["head_sha"]}
        if operation == "request_reviewers":
            pull["requested_reviewers"] = sorted(set(pull.get("requested_reviewers", []) + args["reviewers"]))
            return {"requested_reviewers": pull["requested_reviewers"]}
        if operation == "list_pull_request_reviews":
            return {"reviews": copy.deepcopy(pull["reviews"])}
        if operation == "create_pull_request_review":
            event = args["event"].upper()
            if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
                raise _error("invalid_review_event", "event")
            user = actor.acting_for or actor.principal
            if event == "APPROVE" and user == pull["author"]:
                raise _error("own_pull_request")
            row = {"id": max([review["id"] for review in pull["reviews"]], default=0) + 1,
                   "author": user, "author_role": actor.role,
                   "state": {"APPROVE": "approved", "REQUEST_CHANGES": "changes_requested",
                                              "COMMENT": "commented"}[event], "dismissed": False,
                   "body": args.get("body", "")}
            pull["reviews"].append(row)
            return copy.deepcopy(row)
        if operation == "dismiss_pull_request_review":
            if not args.get("body", "").strip():
                raise _error("dismissal_comment_required", "body")
            review = next((row for row in pull["reviews"] if row["id"] == args["review_id"]), None)
            if review is None:
                raise _error("review_not_found")
            review["dismissed"] = True
            pull["comments"].append({"author": actor.acting_for or actor.principal, "body": args["body"]})
            return {"dismissed": True}
        pull["comments"].append({"id": len(pull["comments"]) + 1,
                                 "author": actor.acting_for or actor.principal, "body": args["body"]})
        return {"id": pull["comments"][-1]["id"]}
    if operation == "get_mergeability":
        return mergeability(state, name, args["pull_number"], actor)
    if operation in {"list_check_runs", "get_check_run", "rerun_check_run"}:
        checks = [row for row in repo.get("checks", []) if (not args.get("sha") or row["sha"] == args["sha"])
                  and (not args.get("name") or row["name"] == args["name"])]
        if operation == "list_check_runs":
            return {"checks": copy.deepcopy(checks)}
        latest = max(checks, key=lambda row: (row.get("run_number", 0), row.get("attempt", 0)), default=None)
        if latest is None:
            raise _error("check_run_not_found")
        if operation == "get_check_run":
            return copy.deepcopy(latest)
        if latest.get("conclusion") not in {"failure", "cancelled", "timed_out", "success"}:
            raise _error("check_run_not_rerunnable")
        if latest.get("attempt", 1) >= 50:
            raise _error("rerun_limit")
        attempt = latest.get("attempt", 1) + 1
        bare_path = repo.get("_bare_repo")
        if bare_path is not None:
            if _REPLAY_CI_ROWS.get() is not None:
                rows = _replayed_checks(args["repo"], latest["sha"],
                                        latest.get("run_number", 1), attempt, latest.get("ref"))
            else:
                from ptu.sandbox.ci import run_checks
                from ptu.sandbox.toolchain import resolve
                rows = run_checks(Path(bare_path), latest["sha"],
                                  run_number=latest.get("run_number", 1), attempt=attempt,
                                  toolchain=str(resolve("py314-stdlib")),
                                  ci_seed_schedule=repo.get("ci_seed_schedule"))
            selected = next((row for row in rows if row["name"] == latest["name"]), None)
            if selected is None:
                raise _error("check_run_not_found")
            rerun = {**selected, "ref": latest.get("ref")}
        else:
            rerun = {**latest, "attempt": attempt}
        repo["checks"].append(rerun)
        return copy.deepcopy(rerun)
    if operation in {"list_issues", "get_issue", "create_issue", "update_issue", "comment_on_issue",
                     "add_labels", "remove_label", "list_labels", "create_label"}:
        if operation == "list_issues":
            return {"issues": copy.deepcopy(list(repo["issues"].values()))}
        if operation == "list_labels":
            return {"labels": copy.deepcopy(list(repo["labels"].values()))}
        if operation == "create_label":
            repo["labels"][args["name"]] = {"name": args["name"], "color": args.get("color", "ededed")}
            return copy.deepcopy(repo["labels"][args["name"]])
        if operation == "create_issue":
            if set(args.get("labels", [])) - set(repo["labels"]):
                raise _error("label_not_found")
            number = repo["next_issue_number"]
            if str(number) in repo["issues"]:
                raise _error("number_conflict")
            repo["next_issue_number"] += 1
            repo["issues"][str(number)] = {"number": number, "title": args["title"],
                                           "body": args.get("body", ""), "state": "open",
                                           "author": actor.acting_for or actor.principal,
                                           "labels": sorted(set(args.get("labels", []))), "comments": []}
            return {"number": number}
        issue = _issue(repo, args["issue_number"])
        if operation == "get_issue":
            return copy.deepcopy(issue)
        if operation == "update_issue":
            for key in ("title", "body", "state"):
                if key in args:
                    issue[key] = args[key]
            return copy.deepcopy(issue)
        if operation == "comment_on_issue":
            issue["comments"].append({"id": len(issue["comments"]) + 1,
                                      "author": actor.acting_for or actor.principal,
                                      "body": args["body"]})
            return {"id": issue["comments"][-1]["id"]}
        if operation == "add_labels":
            unknown = set(args["labels"]) - set(repo["labels"])
            if unknown:
                raise _error("label_not_found")
            issue["labels"] = sorted(set(issue["labels"] + args["labels"]))
        else:
            issue["labels"] = [label for label in issue["labels"] if label != args["label"]]
        return {"labels": issue["labels"]}
    if operation in {"list_collaborators", "get_collaborator",
                     "remove_collaborator", "update_collaborator_permission"}:
        people = repo.get("collaborators", [])
        if operation == "list_collaborators":
            return {"collaborators": copy.deepcopy(people)}
        subject = args["login"] if operation == "get_collaborator" else args["collaborator"]
        row = next((person for person in people if person["login"] == subject), None)
        if row is None:
            raise _error("collaborator_not_found")
        if operation == "remove_collaborator":
            if row.get("role") == "admin" and sum(person.get("role") == "admin" for person in people) <= 1:
                raise _error("last_admin")
            people.remove(row)
            return {"removed": True, "login": subject}
        if operation == "update_collaborator_permission":
            role = args["role"]
            if role not in _RANK:
                raise _error("invalid_role", "role")
            if row.get("role") == "admin" and role != "admin" and \
                    sum(person.get("role") == "admin" for person in people) <= 1:
                raise _error("last_admin")
            row["role"] = role
            return copy.deepcopy(row)
        return copy.deepcopy(row)
    if operation in {"list_actions_secrets", "get_actions_secret", "set_actions_secret", "delete_actions_secret"}:
        secrets = repo.setdefault("secrets", {})
        if operation == "list_actions_secrets":
            return {"names": sorted(secrets)}
        if operation == "get_actions_secret":
            if args["name"] not in secrets:
                raise _error("secret_not_found")
            return {"name": args["name"], "value_readable": False}
        if operation == "set_actions_secret":
            secrets[args["name"]] = args["value"]
            return {"name": args["name"], "updated": True}
        if args["name"] not in secrets:
            raise _error("secret_not_found")
        del secrets[args["name"]]
        return {"deleted": True, "name": args["name"]}
    if operation == "bypass_push_protection":
        if not args.get("reason", "").strip():
            raise _error("bypass_reason_required", "reason")
        repo.setdefault("push_protection", {}).setdefault("bypasses", {})[actor.acting_for or actor.principal] = {
            "reason": args["reason"]}
        return {"bypass_recorded": True}
    raise _error("unsupported_action")


# Keep lookup as a separate read tool so the merge box is reachable without changing the PR.
TOOLS["get_mergeability"] = ("pulls", "read", "read")


def _schema(operation: str) -> dict:
    required = [] if operation == "list_repositories" else ["repo"]
    for field, names in {
        "branch": {"create_branch", "delete_branch", "set_branch_protection"},
        "path": {"get_file_contents"}, "pull_number": {"get_pull_request", "list_pull_request_files",
          "get_pull_request_diff", "get_mergeability", "update_pull_request", "mark_pull_request_ready_for_review",
          "convert_pull_request_to_draft", "close_pull_request", "reopen_pull_request", "merge_pull_request",
          "request_reviewers", "list_pull_request_reviews", "create_pull_request_review",
          "dismiss_pull_request_review", "comment_on_pull_request"},
        "issue_number": {"get_issue", "update_issue", "comment_on_issue", "add_labels", "remove_label"},
        "name": {"get_check_run", "rerun_check_run", "get_actions_secret", "set_actions_secret",
                 "delete_actions_secret", "create_label"},
        "head": {"create_pull_request"}, "base": {"create_pull_request"},
        "title": {"create_pull_request", "create_issue"}, "event": {"create_pull_request_review"},
        "body": {"comment_on_pull_request", "comment_on_issue"},
        "labels": {"add_labels"}, "label": {"remove_label"}, "review_id": {"dismiss_pull_request_review"},
        "reviewers": {"request_reviewers"}, "value": {"set_actions_secret"},
        "rule": {"create_ruleset", "update_ruleset"}, "protection": {"set_branch_protection"},
        "reason": {"bypass_push_protection"}, "login": {"get_collaborator"},
        "collaborator": {"remove_collaborator", "update_collaborator_permission"},
        "role": {"update_collaborator_permission"},
    }.items():
        if operation in names:
            required.append(field)
    properties = dict(_PROPS)
    if operation in {"remove_collaborator", "update_collaborator_permission"}:
        properties["collaborator"] = _STRING
    if operation == "update_collaborator_permission":
        properties["role"] = _STRING
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _tools(actor: Actor, world: dict) -> list[ToolSpec]:
    result = []
    for operation, (surface, effect, _) in TOOLS.items():
        name = "codehost." + operation
        def handler(args, state, clock, ids, op=operation):
            return _run(op, args, state, actor, ids)
        def resources(args, state, op=operation, access="read" if effect == "read" else "write"):
            if op == "list_repositories":
                # Names alone are a role-authorized repository directory.
                return []
            repo = args.get("repo", "*")
            if op in {"remove_collaborator", "update_collaborator_permission"}:
                return [ResourceUse(f"codehost:collaborator/{repo}@{args.get('collaborator', '*')}", access)]
            if op in {"list_check_runs", "get_check_run", "rerun_check_run"}:
                repository = state.get("repos", {}).get(repo, {})
                sha = args.get("sha")
                names = ([args["name"]] if args.get("name") else
                         sorted({row["name"] for row in repository.get("checks", [])
                                 if not sha or row.get("sha") == sha}))
                refs = []
                for name in names:
                    refs.append(ResourceUse(f"codehost:check/{repo}@{sha or '*'}/{name}", access))
                    if sha == repository.get("branches", {}).get("main", {}).get("head_commit"):
                        refs.append(ResourceUse(f"codehost:check/{repo}@main/{name}", access))
                return refs or [ResourceUse(f"codehost:repo/{repo}", access)]
            if "pull_number" in args:
                ref = f"codehost:pr/{repo}#{args['pull_number']}"
            elif "issue_number" in args:
                number = str(args["issue_number"])
                is_pull = number in state.get("repos", {}).get(repo, {}).get("pulls", {})
                ref = f"codehost:{'pr' if is_pull else 'issue'}/{repo}#{number}"
            elif "branch" in args:
                ref = f"codehost:branch/{repo}#{args['branch']}"
            elif "path" in args:
                ref = f"codehost:file/{repo}@{args.get('branch', 'main')}:{args['path']}"
            else:
                ref = f"codehost:repo/{repo}"
            return [ResourceUse(ref, access)]
        result.append(ToolSpec(name=name, effect=effect,
                               privilege=operation in {"remove_collaborator", "update_collaborator_permission"},
                               idempotent=effect == "read",
                               annotations={"readOnlyHint": effect == "read",
                                            "destructiveHint": effect == "destructive",
                                            "idempotentHint": effect == "read", "openWorldHint": False},
                               input_schema=_schema(operation), auth_args=(),
                               resources_fn=resources, handler=handler,
                               surface=surface, ui_path="Repository > " + surface.title() + " > " +
                               operation.replace("_", " ").title()))
    return result


def _event_comment(args, state, clock, ids):
    repo = _repo(state, args["repo"])
    pull = _pull(repo, int(args.get("pull", args.get("pull_number"))))
    pull["comments"].append({"author": args["author"], "body": args["text"]})
    return [ResourceUse(f"codehost:pr/{args['repo']}#{pull['number']}", "write")]


def _event_commit_file(args, state, clock, ids):
    repo = _repo(state, args["repo"])
    branch, path = args["branch"], args["path"]
    pending = repo.get("event_files", {}).get(path)
    if branch not in repo.get("branches", {}) or not isinstance(pending, dict):
        raise ValueError("E_CODEHOST_EVENT_FILE")
    content = pending.get("content")
    if not isinstance(content, str) or path in repo.setdefault("files", {}).setdefault(branch, {}):
        raise ValueError("E_CODEHOST_EVENT_FILE")
    repo["files"][branch][path] = content
    del repo["event_files"][path]
    previous = repo["branches"][branch]["head_commit"]
    repo["branches"][branch]["head_commit"] = hashlib.sha256(
        (previous + path + content + str(clock.now())).encode()).hexdigest()
    return [ResourceUse(f"codehost:file/{args['repo']}@{branch}:{path}", "write"),
            ResourceUse(f"codehost:branch/{args['repo']}#{branch}", "write")]


def _event_set_status_app(args, state, clock, ids):
    repo = _repo(state, args["repo"])
    pushed = repo.get("_last_push")
    if pushed is None:
        # Nothing has been pushed yet, so there is no commit to report on.
        return []
    if not isinstance(pushed, dict) or pushed.get("sha") not in {
            row.get("head_commit") for row in repo.get("branches", {}).values()}:
        raise ValueError("E_CODEHOST_EVENT_STATUS")
    name, sha = args["name"], pushed["sha"]
    checks = repo.setdefault("checks", [])
    run_number = max((row.get("run_number", 0) for row in checks
                      if row.get("name") == name and row.get("sha") == sha), default=0) + 1
    checks.append({"name": name, "sha": sha, "ref": pushed["ref"],
                   "conclusion": args["conclusion"], "summary": args.get("summary", ""),
                   "run_number": run_number, "attempt": 1, "app": True})
    return [ResourceUse(f"codehost:check/{args['repo']}@{sha}:{name}", "write")]


def _event_commit_to_branch(args, state, clock, ids):
    """Make a trusted world event a real Git commit on an existing branch."""
    name, branch = args.get('repo'), args.get('branch')
    repo = _repo(state, name)
    bare = Path(repo.get('_bare_repo', ''))
    files, deleted = args.get('files', {}), args.get('delete', [])
    author, message = args.get('author'), args.get('message')
    if (branch not in repo.get('branches', {}) or not bare.is_dir() or bare.is_symlink()
            or not (bare / 'HEAD').is_file() or not isinstance(files, dict)
            or not isinstance(deleted, list) or not (files or deleted)
            or not isinstance(author, str) or not author.strip() or '\n' in author
            or not isinstance(message, str) or not message.strip()):
        raise ValueError('E_CODEHOST_EVENT_COMMIT')
    paths = [*files, *deleted]
    if (len(paths) != len(set(paths)) or any(
            not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
            or '..' in PurePosixPath(path).parts or '.' in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path or '\x00' in path
            or path.startswith('.git/') or path == '.git' for path in paths)
            or any(not isinstance(content, str) for content in files.values())):
        raise ValueError('E_CODEHOST_EVENT_COMMIT')
    base = repo['branches'][branch]['head_commit']
    stamp = args.get('_event_time') or (clock.iso() if hasattr(clock, 'iso') else clock.now())
    env = {'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'HOME': '/dev/null',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'LANG': 'C',
           'GIT_AUTHOR_NAME': author, 'GIT_AUTHOR_EMAIL': 'event@example.invalid',
           'GIT_COMMITTER_NAME': author, 'GIT_COMMITTER_EMAIL': 'event@example.invalid',
           'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp}

    def git(*argv, cwd):
        result = subprocess.run([str(REAL_GIT), *argv], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, check=False)
        if result.returncode:
            raise ValueError('E_CODEHOST_EVENT_COMMIT')
        return result.stdout.decode().strip()

    with tempfile.TemporaryDirectory(dir=bare.parent) as directory:
        work = Path(directory) / 'work'
        git('clone', '--quiet', str(bare), str(work), cwd=bare.parent)
        git('checkout', '--quiet', '--detach', base, cwd=work)
        for path, content in files.items():
            target = work.joinpath(*PurePosixPath(path).parts)
            if any(part.is_symlink() for part in target.parents if part != work and work in part.parents):
                raise ValueError('E_CODEHOST_EVENT_COMMIT')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise ValueError('E_CODEHOST_EVENT_COMMIT')
            target.write_text(content)
        for path in deleted:
            target = work.joinpath(*PurePosixPath(path).parts)
            if target.is_symlink() or not target.is_file():
                raise ValueError('E_CODEHOST_EVENT_COMMIT')
            target.unlink()
        git('add', '--all', cwd=work)
        git('commit', '--quiet', '-m', message, cwd=work)
        head = git('rev-parse', 'HEAD', cwd=work)
        if not args['_replay']:
            if git('rev-parse', 'refs/heads/' + branch, cwd=bare) != base:
                raise ValueError('E_CODEHOST_EVENT_COMMIT')
            git('push', '--quiet', 'origin', 'HEAD:refs/heads/' + branch, cwd=work)
    repo['branches'][branch]['head_commit'] = head
    branch_files = repo.setdefault('files', {}).setdefault(branch, {})
    branch_files.update(files)
    for path in deleted:
        branch_files.pop(path, None)
    return [*(ResourceUse(f'codehost:file/{name}@{branch}:{path}', 'write') for path in paths),
            ResourceUse(f'codehost:branch/{name}#{branch}', 'write')]


def _event_create_review(args, state, clock, ids):
    repo = _repo(state, args['repo'])
    pull = _pull(repo, int(args['pull']))
    author, body, review_state = args.get('author'), args.get('body'), args.get('state')
    if (not isinstance(author, str) or not author or not isinstance(body, str)
            or review_state not in {'approved', 'changes_requested', 'commented'}):
        raise ValueError('E_CODEHOST_EVENT_REVIEW')
    role = next((row.get('role') for row in repo.get('collaborators', [])
                 if row.get('login') == author), 'read')
    pull.setdefault('reviews', []).append({
        'id': max((row.get('id', 0) for row in pull.get('reviews', [])), default=0) + 1,
        'author': author, 'author_role': role, 'state': review_state,
        'dismissed': False, 'body': body})
    return [ResourceUse(f"codehost:pr/{args['repo']}#{pull['number']}", 'write')]


def _event_platform_merge_workflow(args, state, clock, ids):
    """Apply a scheduled, trusted Platform commit to the authoritative main."""
    name = args['repo']
    repo = _repo(state, name)
    bare = Path(repo.get('_bare_repo', ''))
    if (args.get('path') != '.github/workflows/ci.yml' or
            not isinstance(args.get('content'), str) or
            not bare.is_dir() or bare.is_symlink() or not (bare / 'HEAD').is_file()):
        raise ValueError('E_CODEHOST_EVENT_WORKFLOW')
    base = repo['branches']['main']['head_commit']
    env = {'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'HOME': '/dev/null',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'LANG': 'C',
           'GIT_AUTHOR_NAME': 'Platform', 'GIT_AUTHOR_EMAIL': 'platform@example.invalid',
           'GIT_COMMITTER_NAME': 'Platform', 'GIT_COMMITTER_EMAIL': 'platform@example.invalid',
           'GIT_AUTHOR_DATE': '2026-10-06T08:30:00-06:00',
           'GIT_COMMITTER_DATE': '2026-10-06T08:30:00-06:00'}

    def git(*argv, cwd):
        result = subprocess.run([str(REAL_GIT), *argv], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, check=False)
        if result.returncode:
            raise ValueError('E_CODEHOST_EVENT_WORKFLOW')
        return result.stdout.decode().strip()

    with tempfile.TemporaryDirectory(dir=bare.parent) as directory:
        work = Path(directory) / 'work'
        git('clone', '--quiet', str(bare), str(work), cwd=bare.parent)
        git('checkout', '--quiet', '--detach', base, cwd=work)
        path = work / '.github' / 'workflows' / 'ci.yml'
        path.write_text(args['content'])
        git('add', '.github/workflows/ci.yml', cwd=work)
        git('commit', '--quiet', '-m', 'Platform: install pinned CI lint fixture', cwd=work)
        head = git('rev-parse', 'HEAD', cwd=work)
        current = git('rev-parse', 'refs/heads/main', cwd=bare)
        if current == base:
            git('push', '--quiet', 'origin', 'HEAD:refs/heads/main', cwd=work)
        elif current != head:
            raise ValueError('E_CODEHOST_EVENT_WORKFLOW')
    repo['branches']['main']['head_commit'] = head
    repo['files']['main']['.github/workflows/ci.yml'] = args['content']
    repo['issues'][str(args['issue_number'])]['comments'].append({
        'id': len(repo['issues'][str(args['issue_number'])]['comments']) + 1,
        'author': 'mira', 'body': args['response']})
    return [ResourceUse(f'codehost:file/{name}@main:.github/workflows/ci.yml', 'write'),
            ResourceUse(f'codehost:branch/{name}#main', 'write'),
            ResourceUse(f"codehost:issue/{name}#{args['issue_number']}", 'write')]


def _ancestors(ref: str, state=None) -> list[str]:
    if ref.startswith("codehost:repo/"):
        return ["codehost:repos/all"]
    if not ref.startswith("codehost:") or "/" not in ref:
        return []
    kind, path = ref.split(":", 1)[1].split("/", 1)
    repo = path.split("@", 1)[0].split("#", 1)[0]
    if kind == "file" and "@" in path:
        branch = path.split("@", 1)[1].split(":", 1)[0]
        return [f"codehost:branch/{repo}#{branch}", f"codehost:repo/{repo}"]
    return [f"codehost:repo/{repo}"]


def _refs_for_path(path: list[str]) -> str:
    if len(path) < 2 or path[0] != "repos":
        return "codehost:repos/all"
    repo = str(path[1])
    if len(path) < 3:
        return f"codehost:repo/{repo}"
    kind = path[2]
    if kind in {"branches", "branch_changes", "files"} and len(path) >= 4:
        branch = str(path[3])
        if kind == "files" and len(path) >= 5:
            return f"codehost:file/{repo}@{branch}:{path[4]}"
        return f"codehost:branch/{repo}#{branch}"
    if kind in {"pulls", "issues", "labels"} and len(path) >= 4:
        prefix = {"pulls": "pr", "issues": "issue", "labels": "label"}[kind]
        return f"codehost:{prefix}/{repo}#{path[3]}"
    if kind == "collaborators" and len(path) >= 4:
        return f"codehost:collaborator/{repo}@*"
    return f"codehost:repo/{repo}"


def _bookkeeping(path: list[str], key: str) -> bool:
    # CI outcomes are derived from pinned commits and separately checked by
    # checks_green; the run list has no per-check addressable state path.
    return len(path) == 2 and path[0] == "repos" and (
        key.startswith("next_") or key in {"checks", "event_files", "_bare_repo", "_last_push", "ci_seed_schedule"})


APP = AppSpec(id="codehost", reference_product="GitHub", fidelity_version="0.1",
              surfaces=SURFACES, report_unit=lambda ref: ref,
              tool_specs=_tools, authorize=_authorize,
              refs_for_path=_refs_for_path,
              ancestors=_ancestors,
              bookkeeping=_bookkeeping,
              created_ref=lambda tool, args, result: (
                  f"codehost:pr/{args['repo']}#{result['number']}" if tool == "codehost.create_pull_request" and result else
                  f"codehost:issue/{args['repo']}#{result['number']}" if tool == "codehost.create_issue" and result else
                  f"codehost:branch/{args['repo']}#{result['branch']}" if tool == "codehost.create_branch" and result else None),
              external_check=None, event_ops={"comment_on_pull_request": _event_comment,
                                              "commit_to_branch": _event_commit_to_branch,
                                              "create_review": _event_create_review,
                                              "commit_file": _event_commit_file,
                                              "set_status_app": _event_set_status_app,
                                              "platform_merge_workflow": _event_platform_merge_workflow}, unread=None)
