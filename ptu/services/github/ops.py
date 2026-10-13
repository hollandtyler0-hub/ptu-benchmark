"""Deterministic GitHub-like operations over one synthetic JSON document."""

import copy
import hashlib


def _repo(state: dict, args: dict) -> dict:
    try:
        return state["repos"][args["repo"]]
    except KeyError:
        raise ValueError("E_NO_REPO") from None


def _branch(repo: dict, name: str) -> dict:
    try:
        return repo["branches"][name]
    except KeyError:
        raise ValueError("E_NO_BRANCH") from None


def _file(repo: dict, branch: str, path: str) -> dict:
    _branch(repo, branch)
    try:
        return repo["files"][branch][path]
    except KeyError:
        raise ValueError("E_NO_FILE") from None


def _issue(repo: dict, number: int) -> dict:
    try:
        return repo["issues"][str(number)]
    except KeyError:
        raise ValueError("E_NO_ISSUE") from None


def _pull(repo: dict, number: int) -> dict:
    try:
        return repo["pulls"][str(number)]
    except KeyError:
        raise ValueError("E_NO_PULL") from None


def search_matches(repo: dict, branch: str, query: str) -> list[str]:
    _branch(repo, branch)
    return sorted(path for path, record in repo["files"][branch].items()
                  if not any(part.startswith(".env") or part.endswith(".pem") for part in path.split("/"))
                  and (query.lower() in path.lower() or query.lower() in record["content"].lower()))


def _pull_files(repo: dict, head: str, base: str) -> list[dict]:
    head_files = repo["files"][head]
    base_files = repo["files"][base]
    return [{"path": path, "status": "added" if path not in base_files else "deleted" if path not in head_files else "modified"}
            for path in sorted(head_files.keys() | base_files.keys()) if head_files.get(path) != base_files.get(path)]


def page_rows(rows: list, page: int, size: int) -> tuple[list, int | None]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("E_BAD_PAGE")
    start = (page - 1) * size
    return rows[start:start + size], page + 1 if start + size < len(rows) else None


def issue_rows(repo: dict, args: dict) -> list[dict]:
    wanted = args.get("state") or "all"
    labels = args.get("labels") or []
    return [row for _, row in sorted(repo["issues"].items(), key=lambda item: int(item[0]))
            if (wanted == "all" or row["state"] == wanted)
            and all(label in row.get("labels", []) for label in labels)]


def pull_rows(repo: dict, args: dict) -> list[dict]:
    wanted = args.get("state") or "open"
    return [row for _, row in sorted(repo["pulls"].items(), key=lambda item: int(item[0]))
            if wanted == "all" or row["state"] == wanted]


def execute(name: str, args: dict, state: dict, clock, ids) -> dict:
    """Apply one named tool operation. Errors carry only fixed E_* codes."""
    repo = _repo(state, args)
    if name == "get_repo":
        return {"repo": args["repo"], "default_branch": repo["default_branch"],
                "branches": sorted(repo["branches"]), "open_pulls": sum(p["state"] == "open" for p in repo["pulls"].values())}
    if name == "list_collaborators":
        return {"collaborators": copy.deepcopy(repo["collaborators"])}
    if name == "list_branches":
        rows, next_page = page_rows(sorted(repo["branches"].items()),
                                    1 if args.get("page") is None else args["page"], 20)
        return {"branches": [{"name": key} for key, _ in rows], "next_page": next_page}
    if name == "get_branch":
        branch = args["branch"]
        row = _branch(repo, branch)
        return {"name": branch, "head_commit": row["head_commit"], "protected": row["protected"],
                "merged": row.get("merged", False), "merged_at": row.get("merged_at"),
                "last_commit_at": row.get("last_commit_at"),
                "open_pull": any(p["state"] == "open" and p["head"] == branch for p in repo["pulls"].values())}
    if name == "list_labels":
        return {"labels": sorted(set(repo.get("labels", [])) | {label for row in repo["issues"].values()
                         for label in row.get("labels", [])})}
    if name == "list_directory":
        branch = args["branch"]
        _branch(repo, branch)
        prefix = args["path"].strip("/")
        if prefix == ".":
            prefix = ""
        prefix = f"{prefix}/" if prefix else ""
        paths = sorted(path for path in repo["files"][branch] if path.startswith(prefix))
        return {"paths": paths}
    if name == "get_file_contents":
        return {"path": args["path"], **copy.deepcopy(_file(repo, args["branch"], args["path"]))}
    if name == "search_code":
        branch = args.get("branch", repo["default_branch"])
        return {"matches": [{"path": path, "branch": branch, "content": repo["files"][branch][path]["content"]}
                            for path in search_matches(repo, branch, args["query"])]}
    if name == "list_issues":
        rows, next_page = page_rows(issue_rows(repo, args), 1 if args.get("page") is None else args["page"], 25)
        return {"issues": [{key: copy.deepcopy(row.get(key)) for key in ("number", "title", "labels", "author")}
                           for row in rows], "next_page": next_page}
    if name == "get_issue":
        return copy.deepcopy(_issue(repo, args["issue_number"]))
    if name == "list_issue_comments":
        return {"comments": copy.deepcopy(_issue(repo, args["issue_number"])["comments"])}
    if name == "list_pull_requests":
        rows, next_page = page_rows(pull_rows(repo, args), 1 if args.get("page") is None else args["page"], 20)
        summary = ("number", "title", "author", "head", "base", "state", "updated_at", "merged_at")
        return {"pulls": [{key: copy.deepcopy(row[key]) for key in summary if key in row}
                          for row in rows], "next_page": next_page}
    if name == "get_pull_request":
        return {key: copy.deepcopy(value) for key, value in _pull(repo, args["pull_number"]).items()
                if key not in {"files", "reviews"}}
    if name == "list_pull_request_files":
        return {"files": copy.deepcopy(_pull(repo, args["pull_number"])["files"])}
    if name == "get_pull_request_diff":
        pull = _pull(repo, args["pull_number"])
        return {"diff": pull.get("diff", "\n".join(item["path"] for item in pull["files"]))}
    if name == "list_pull_request_reviews":
        return {"reviews": copy.deepcopy(_pull(repo, args["pull_number"])["reviews"])}
    if name == "create_branch":
        source = args.get("from_branch", repo["default_branch"])
        source_data = _branch(repo, source)
        target = args["branch"]
        if target in repo["branches"]:
            raise ValueError("E_BRANCH_EXISTS")
        repo["branches"][target] = {"head_commit": source_data["head_commit"], "protected": False}
        repo["files"][target] = copy.deepcopy(repo["files"][source])
        return {"branch": target, "from_branch": source}
    if name == "create_or_update_file":
        branch = args["branch"]
        branch_data = _branch(repo, branch)
        path = args["path"]
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("E_BAD_PATH")
        old_commit = branch_data["head_commit"]
        commit = ids.next("commit")
        content = args["content"]
        sha = hashlib.sha256(content.encode()).hexdigest()
        repo["files"].setdefault(branch, {})[path] = {"content": content, "sha": sha}
        repo["commits"][commit] = {"message": args.get("message", f"Update {path}"), "parents": [old_commit]}
        branch_data["head_commit"] = commit
        return {"path": path, "sha": sha, "commit": commit}
    if name == "create_issue":
        number = repo["next_issue_number"]
        repo["next_issue_number"] = number + 1
        issue = {"number": number, "title": args["title"], "body": args.get("body", ""),
                 "author": state.get("acting_principal", "agent:release-bot"), "state": "open", "labels": [], "comments": []}
        repo["issues"][str(number)] = issue
        return {"number": number}
    if name == "comment_on_issue":
        issue = _issue(repo, args["issue_number"])
        comment = {"id": ids.next("comment"), "author": state.get("acting_principal", "agent:release-bot"),
                   "body": args["body"], "created_at": clock.iso()}
        issue["comments"].append(comment)
        return {"id": comment["id"]}
    if name == "update_issue":
        issue = _issue(repo, args["issue_number"])
        updates = {key: args[key] for key in ("title", "body", "state", "labels") if key in args}
        if not updates:
            raise ValueError("E_NO_CHANGE")
        issue.update(updates)
        return {"number": issue["number"], "updated": sorted(updates)}
    if name == "create_pull_request":
        head, base = args["head"], args["base"]
        _branch(repo, head)
        _branch(repo, base)
        number = repo["next_pull_number"]
        repo["next_pull_number"] = number + 1
        pull = {"number": number, "title": args["title"], "body": args["body"],
                "author": state.get("acting_principal", "agent:release-bot"), "head": head, "base": base,
                "state": "open", "reviews": [], "comments": [], "files": _pull_files(repo, head, base)}
        repo["pulls"][str(number)] = pull
        return {"number": number}
    if name == "create_pull_request_review":
        pull = _pull(repo, args["pull_number"])
        event = args["event"]
        if event not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
            raise ValueError("E_BAD_REVIEW")
        review = {"id": ids.next("review"), "author": state.get("acting_principal", "agent:release-bot"),
                  "state": {"COMMENT": "COMMENTED", "APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[event],
                  "body": args["body"], "created_at": clock.iso()}
        pull["reviews"].append(review)
        return {"id": review["id"], "state": review["state"]}
    if name == "comment_on_pull_request":
        pull = _pull(repo, args["pull_number"])
        comment = {"id": ids.next("comment"), "author": state.get("acting_principal", "agent:release-bot"),
                   "body": args["body"], "created_at": clock.iso()}
        pull["comments"].append(comment)
        return {"id": comment["id"]}
    if name == "merge_pull_request":
        pull = _pull(repo, args["pull_number"])
        if pull["state"] != "open":
            raise ValueError("E_PULL_CLOSED")
        head, base = pull["head"], pull["base"]
        _branch(repo, head)
        base_data = _branch(repo, base)
        commit = ids.next("commit")
        repo["files"][base].update(copy.deepcopy(repo["files"][head]))
        repo["commits"][commit] = {"message": f"Merge PR #{pull['number']}",
                                   "parents": [base_data["head_commit"], repo["branches"][head]["head_commit"]]}
        base_data["head_commit"] = commit
        pull["state"] = "merged"
        return {"number": pull["number"], "state": "merged", "commit": commit}
    if name == "close_pull_request":
        pull = _pull(repo, args["pull_number"])
        if pull["state"] != "open":
            raise ValueError("E_PULL_CLOSED")
        pull["state"] = "closed"
        return {"number": pull["number"], "state": "closed"}
    if name == "delete_branch":
        branch = args["branch"]
        if branch == repo["default_branch"]:
            raise ValueError("E_DEFAULT_BRANCH")
        _branch(repo, branch)
        del repo["branches"][branch]
        repo["files"].pop(branch, None)
        return {"branch": branch, "deleted": True}
    if name == "add_collaborator":
        user = args["user"]
        if user not in repo["collaborators"]:
            repo["collaborators"].append(user)
            repo["collaborators"].sort()
        return {"user": user, "permission": args.get("permission", "write")}
    if name == "remove_collaborator":
        user = args["user"]
        if user not in repo["collaborators"]:
            raise ValueError("E_NO_COLLABORATOR")
        repo["collaborators"].remove(user)
        return {"user": user, "removed": True}
    raise ValueError("E_UNKNOWN_OPERATION")
