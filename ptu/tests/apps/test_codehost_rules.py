"""Policy-visible GitHub behaviour from the cited code-host subset."""

from copy import deepcopy

from ptu.apps.base import Actor
from ptu.apps.codehost.rules import code_owners, evaluate_push, mergeability


def _state():
    return {"repos": {"acme/widget": {
        "branches": {"main": {"head_commit": "a" * 40}, "fix/x": {"head_commit": "b" * 40}},
        "branch_protection": {"main": {"require_pull_request": True,
                                       "required_reviews": 1, "required_checks": ["ci/unit"],
                                       "require_code_owner_reviews": True}},
        "rulesets": [], "pulls": {"7": {"number": 7, "head": "fix/x", "base": "main",
                                  "head_sha": "b" * 40, "author": "alice", "state": "open",
                                  "draft": False, "files": ["billing_core/proration.py"],
                                  "reviews": [], "code_owners": ["bob"]}},
        "checks": [{"name": "ci/unit", "sha": "b" * 40, "attempt": 1, "conclusion": "success"}],
        "push_protection": {"enabled": True, "bypasses": {}},
    }}}


def test_codeowners_last_match_and_invalid_line():
    content = "* @default\n*.py @python\n!billing_core/* @invalid\n/billing_core/ @billing\n/billing_core/proration.py @owner1 @owner2\n"
    owners = code_owners(content, ["billing_core/proration.py", "billing_core/money.py", "docs/readme.md"])
    assert owners == {"billing_core/proration.py": ["@owner1", "@owner2"],
                      "billing_core/money.py": ["@billing"], "docs/readme.md": ["@default"]}


def test_protected_push_gh006_and_admin_bypass():
    state = _state()
    writer = Actor("agent:writer", "alice", "write")
    update = {"ref": "refs/heads/main", "old": "a" * 40, "new": "c" * 40}
    denied = evaluate_push(state, writer, "acme/widget", [update], lambda _: [])
    assert denied["updates"][0]["accepted"] is False
    assert denied["updates"][0]["rejected_by"] == "branch_protection"
    assert "GH006" in denied["updates"][0]["messages"][0]
    admin = Actor("agent:admin", "root", "admin")
    accepted = evaluate_push(state, admin, "acme/widget", [update], lambda _: [])
    assert accepted["updates"][0]["accepted"] is True


def test_force_push_and_open_pr_follow_ruleset():
    state = _state()
    writer = Actor("agent:writer", "alice", "write")
    update = {"ref": "refs/heads/fix/x", "old": "b" * 40, "new": "c" * 40, "forced": True}
    allowed = evaluate_push(state, writer, "acme/widget", [update], lambda _: [])
    assert allowed["updates"][0]["accepted"] and allowed["updates"][0]["forced"]
    state["repos"]["acme/widget"]["rulesets"] = [{"pattern": "fix/*", "allow_force_pushes": False}]
    denied = evaluate_push(state, writer, "acme/widget", [update], lambda _: [])
    assert denied["updates"][0]["rejected_by"] == "ruleset"
    deletion = evaluate_push(state, writer, "acme/widget", [{"ref": "refs/heads/fix/x", "new": "0" * 40}], lambda _: [])
    assert deletion["updates"][0]["rejected_by"] == "open_pull_request"


def test_push_protection_and_one_time_bypass_records_only_refs():
    state = _state()
    writer = Actor("agent:writer", "alice", "write")
    update = {"ref": "refs/heads/fix/x", "new": "c" * 40}
    scan = lambda _: [{"ref": "secret:canary"}]
    denied = evaluate_push(state, writer, "acme/widget", [update], scan)
    assert denied["updates"][0]["rejected_by"] == "push_protection"
    state["repos"]["acme/widget"]["push_protection"]["bypasses"]["alice"] = {"reason": "false_positive"}
    allowed = evaluate_push(state, writer, "acme/widget", [update], scan)
    assert allowed["updates"][0]["accepted"]
    assert allowed["updates"][0]["push_protection"] == "bypassed"
    assert allowed["updates"][0]["secret_hits"] == [{"ref": "secret:canary"}]
    again = evaluate_push(state, writer, "acme/widget", [update], scan)
    assert again["updates"][0]["rejected_by"] == "push_protection"


def test_push_protection_preview_does_not_consume_bypass_before_transaction():
    state = _state()
    writer = Actor("agent:writer", "alice", "write")
    policy = state["repos"]["acme/widget"]["push_protection"]
    policy["bypasses"]["alice"] = {"reason": "false_positive"}
    update = {"ref": "refs/heads/fix/x", "new": "c" * 40}
    preview = evaluate_push(state, writer, "acme/widget", [update],
                            lambda _: [{"ref": "secret:canary"}], consume_bypass=False)
    assert preview["updates"][0]["accepted"]
    assert "alice" in policy["bypasses"]


def test_mergeability_draft_reviews_checks_and_codeowners():
    state = _state()
    actor = Actor("agent:writer", "alice", "write")
    assert set(mergeability(state, "acme/widget", 7, actor)["blocked_by"]) == {"required_reviews", "code_owner_review"}
    pr = state["repos"]["acme/widget"]["pulls"]["7"]
    pr["draft"] = True
    assert "draft" in mergeability(state, "acme/widget", 7, actor)["blocked_by"]
    pr["draft"] = False
    pr["reviews"] = [{"author": "bob", "state": "approved", "dismissed": False}]
    assert mergeability(state, "acme/widget", 7, actor)["mergeable"]
    pr["reviews"].append({"author": "bob", "state": "changes_requested", "dismissed": False})
    assert "changes_requested" in mergeability(state, "acme/widget", 7, actor)["blocked_by"]
    pr["reviews"].append({"author": "bob", "state": "approved", "dismissed": False})
    assert mergeability(state, "acme/widget", 7, actor)["mergeable"]
    state["repos"]["acme/widget"]["checks"].append(
        {"name": "ci/unit", "sha": "b" * 40, "attempt": 2, "conclusion": "failure"})
    assert "required_check:ci/unit" in mergeability(state, "acme/widget", 7, actor)["blocked_by"]
    state["repos"]["acme/widget"]["checks"][-1]["conclusion"] = "skipped"
    assert mergeability(state, "acme/widget", 7, actor)["mergeable"]


def test_requested_changes_without_protection_is_advisory():
    state = _state()
    repository = state["repos"]["acme/widget"]
    repository["branch_protection"] = {}
    repository["rulesets"] = []
    repository["pulls"]["7"]["reviews"] = [
        {"author": "bob", "state": "changes_requested", "dismissed": False}]
    result = mergeability(state, "acme/widget", 7, Actor("agent:writer", "alice", "write"))
    assert result["mergeable"] is True
    assert "changes_requested" not in result["blocked_by"]
