"""Page boundaries and item-level resource attribution."""

from ptu.common.clock import LogicalClock
from ptu.common.ids import IdFactory
from ptu.services.github.ops import execute as github_execute
from ptu.services.github.registry import get_specs
from ptu.services.linear.ops import list_issues as linear_list
from ptu.services.linear.registry import tool_specs as linear_specs
from ptu.services.slack.ops import read_channel_history


def test_github_issue_page_refs_are_only_returned_items():
    repo = {"issues": {str(n): {"number": n, "title": f"Item {n}", "labels": [], "author": "a",
                                   "state": "open"} for n in range(1, 61)},
            "pulls": {}, "branches": {"main": {"head_commit": "abc", "protected": True}},
            "default_branch": "main"}
    state = {"repos": {"tidewater/web": repo}}
    args = {"repo": "tidewater/web", "page": 2}
    result = github_execute("list_issues", args, state, LogicalClock(), IdFactory(1))
    spec = next(s for s in get_specs() if s.name == "github.list_issues")
    refs = [item.ref for item in spec.resources_fn(args, state)]
    assert [item["number"] for item in result["issues"]] == list(range(26, 51))
    assert result["next_page"] == 3
    assert len(refs) == 26
    assert "github:issue/tidewater/web#1" not in refs
    assert "github:issue/tidewater/web#26" in refs


def test_pull_list_and_detail_keep_files_and_reviews_in_their_own_tools():
    pull = {"number": 419, "title": "Release check", "body": "Review the deploy gate.",
            "author": "dev", "head": "feature/release", "base": "main", "state": "open",
            "updated_at": "2026-09-30T10:00:00Z", "reviews": [{"state": "APPROVED"}],
            "files": [{"path": "deploy/config.yaml"}], "diff": "synthetic diff"}
    state = {"repos": {"tidewater/web": {"pulls": {"419": pull}}}}
    clock, ids = LogicalClock(), IdFactory(1)
    listed = github_execute("list_pull_requests", {"repo": "tidewater/web", "state": "open"}, state, clock, ids)
    detail = github_execute("get_pull_request", {"repo": "tidewater/web", "pull_number": 419}, state, clock, ids)
    files = github_execute("list_pull_request_files", {"repo": "tidewater/web", "pull_number": 419}, state, clock, ids)
    reviews = github_execute("list_pull_request_reviews", {"repo": "tidewater/web", "pull_number": 419}, state, clock, ids)
    assert listed["pulls"] == [{"number": 419, "title": "Release check", "author": "dev",
                                "head": "feature/release", "base": "main", "state": "open",
                                "updated_at": "2026-09-30T10:00:00Z"}]
    assert detail["body"] == "Review the deploy gate."
    assert "files" not in detail and "reviews" not in detail
    assert files["files"] == pull["files"] and reviews["reviews"] == pull["reviews"]


def test_linear_and_slack_pages():
    linear = {"teams": {"ENG": {"projects": {}}},
              "issues": {f"ENG-{n}": {"identifier": f"ENG-{n}", "team": "ENG"} for n in range(60)}}
    args = {"team": "ENG", "page": 2}
    result = linear_list(args, linear, LogicalClock(), IdFactory(1), "agent:test")
    spec = next(s for s in linear_specs() if s.name == "linear.list_issues")
    assert len(result["issues"]) == 30 and result["next_page"] is None
    assert len(spec.resources_fn(args, linear)) == 31
    slack = {"channels": {"#eng-web": {"messages": {f"{n:010d}.000000": {"text": str(n)}
                                                   for n in range(120)}}}}
    result = read_channel_history({"channel": "#eng-web", "page": 2}, slack, LogicalClock(), IdFactory(1), "agent:test")
    assert len(result["messages"]) == 50 and result["next_page"] == 3


def test_large_create_issue_uses_project_container():
    state = {"world_scale": "large", "teams": {"ENG": {"projects": {"web-q4": {}},
                                                    "next_issue_number": 501}}, "issues": {}}
    spec = next(s for s in linear_specs() if s.name == "linear.create_issue")
    args = {"team": "ENG", "project": "web-q4", "title": "Synthetic issue"}
    assert [(use.ref, use.access) for use in spec.resources_fn(args, state)] == [
        ("linear:project/ENG/web-q4", "write"), ("linear:team/ENG", "read")]


def test_branch_listing_exposes_names_at_repository_scope():
    state = {"repos": {"tidewater/web": {"branches": {"main": {"protected": True},
                                                        "feature/cart-1": {"protected": False}},
                                         "issues": {}, "pulls": {}}}}
    specs = {s.name: s for s in get_specs()}
    assert [(use.ref, use.access) for use in specs["github.list_branches"].resources_fn(
        {"repo": "tidewater/web", "page": 1}, state)] == [("github:repo/tidewater/web", "read")]
    assert specs["github.get_branch"].resources_fn({"repo": "tidewater/web", "branch": "main"}, state)[0].ref == (
        "github:branch/tidewater/web#main")
