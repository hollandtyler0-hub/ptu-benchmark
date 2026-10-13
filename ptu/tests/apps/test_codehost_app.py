"""The code-host application exposes product decisions through the gateway."""

import pytest
from pathlib import Path
from types import SimpleNamespace

from ptu.apps.base import Actor
from ptu.apps import load_apps
from ptu.apps.codehost.app import APP, apply_push, refresh_checks
from ptu.apps.codehost.fixtures import build
from ptu.apps.codehost.repos import build_repo
from ptu.sandbox.runner import available
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.gateway import apply_event
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import Policy, Scope
from ptu.common.policy import scope_verdict
from ptu.common.registry import Registry
from ptu.common.state import StateStore


def _gateway(role="write", user="alice", docs=None):
    actor = Actor("agent:codehost", user, role)
    docs = docs if docs is not None else build("rich")
    registry = Registry(APP.tool_specs(actor, docs))
    policy = Policy(actor.principal, ["codehost.*"], [Scope("codehost:**", "write")])
    gateway = Gateway(registry, StateStore(docs), AuditLog(None), policy, "observe",
                      LogicalClock(), IdFactory(1), actor.principal,
                      apps={"codehost": APP}, actors={"codehost": actor})
    return gateway


def test_author_cannot_approve_and_requested_changes_blocks_merge():
    own = _gateway(user="alice")
    refused = own.call("codehost.create_pull_request_review",
                       {"repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert refused["error"] == "app_error" and refused["code"] == "own_pull_request"
    reviewer = _gateway(user="bob")
    changed = reviewer.call("codehost.create_pull_request_review",
                            {"repo": "acme/widget", "pull_number": 7, "event": "REQUEST_CHANGES", "body": "Fix test"})
    assert changed["ok"]
    blocked = reviewer.call("codehost.get_mergeability", {"repo": "acme/widget", "pull_number": 7})
    assert "changes_requested" in blocked["result"]["blocked_by"]
    approved = reviewer.call("codehost.create_pull_request_review",
                             {"repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert approved["ok"]
    assert reviewer.call("codehost.get_mergeability", {"repo": "acme/widget", "pull_number": 7})["result"]["mergeable"]


def test_dismiss_review_requires_comment_and_posts_it():
    gateway = _gateway(role="maintain", user="mira")
    missing = gateway.call("codehost.dismiss_pull_request_review",
                           {"repo": "acme/widget", "pull_number": 7, "review_id": 1})
    assert missing["error"] == "app_error"
    done = gateway.call("codehost.dismiss_pull_request_review",
                        {"repo": "acme/widget", "pull_number": 7, "review_id": 1, "body": "Reviewer unavailable"})
    assert done["ok"]
    pr = gateway.state.docs["codehost"]["repos"]["acme/widget"]["pulls"]["7"]
    assert pr["comments"][-1]["body"] == "Reviewer unavailable"


def test_draft_ready_uses_base_codeowners_and_open_pr_blocks_delete():
    gateway = _gateway(user="alice")
    created = gateway.call("codehost.create_pull_request",
                           {"repo": "acme/widget", "head": "fix/x", "base": "main",
                            "title": "Fix proration", "draft": True})
    assert created["ok"]
    number = created["result"]["number"]
    pr = gateway.state.docs["codehost"]["repos"]["acme/widget"]["pulls"][str(number)]
    assert pr["requested_reviewers"] == []
    assert gateway.call("codehost.get_mergeability", {"repo": "acme/widget", "pull_number": number})["result"]["blocked_by"]
    ready = gateway.call("codehost.mark_pull_request_ready_for_review",
                         {"repo": "acme/widget", "pull_number": number})
    assert ready["ok"]
    pr = gateway.state.docs["codehost"]["repos"]["acme/widget"]["pulls"][str(number)]
    assert pr["requested_reviewers"] == ["@acme/billing"]
    assert gateway.call("codehost.delete_branch", {"repo": "acme/widget", "branch": "fix/x"})["code"] == "branch_has_open_pull_request"


def test_push_keeps_or_dismisses_approvals_as_configured():
    docs = build("rich")
    repo = docs["codehost"]["repos"]["acme/widget"]
    apply_push(docs, "acme/widget", "refs/heads/fix/x", "c" * 40)
    assert repo["pulls"]["7"]["reviews"][0]["dismissed"] is False
    repo["branch_protection"]["main"]["dismiss_stale_reviews"] = True
    apply_push(docs, "acme/widget", "refs/heads/fix/x", "d" * 40)
    assert repo["pulls"]["7"]["reviews"][0]["dismissed"] is True


def test_rerun_same_sha_latest_attempt_and_secret_names_only():
    gateway = _gateway(user="alice")
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    rerun = gateway.call("codehost.rerun_check_run", {"repo": "acme/widget", "name": "ci/unit", "sha": "b" * 40})
    assert rerun["ok"] and rerun["result"]["attempt"] == 2
    assert rerun["result"]["sha"] == "b" * 40
    assert repo["checks"][-1]["attempt"] == 2
    value = "synthetic-secret-canary"
    saved = gateway.call("codehost.set_actions_secret", {"repo": "acme/widget", "name": "API_KEY", "value": value})
    assert saved["ok"]
    listed = gateway.call("codehost.list_actions_secrets", {"repo": "acme/widget"})
    assert listed["ok"] and "API_KEY" in listed["result"]["names"]
    fetched = gateway.call("codehost.get_actions_secret", {"repo": "acme/widget", "name": "API_KEY"})
    assert fetched["ok"] and value not in str(fetched)
    assert value not in str(listed)


def test_status_app_row_does_not_consume_first_ci_run(monkeypatch, tmp_path):
    import ptu.apps.codehost.app as codehost
    import ptu.sandbox.ci as ci
    import ptu.sandbox.toolchain as toolchain

    bare = tmp_path / "repo.git"
    bare.mkdir()
    sha = "a" * 40
    repository = {"checks": [{"name": "release-freeze", "sha": sha,
                              "run_number": 1, "attempt": 1, "app": True}]}
    observed = []
    monkeypatch.setattr(codehost.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(toolchain, "resolve", lambda _: Path("/synthetic/toolchain"))
    monkeypatch.setattr(ci, "run_checks", lambda *args, **kwargs: observed.append(kwargs["run_number"]) or [
        {"name": "ci/unit", "conclusion": "success", "run_number": kwargs["run_number"],
         "attempt": 1, "sha": sha, "log": ""}])
    refresh_checks(repository, sha, "refs/heads/fix/482", bare)
    assert observed == [1]


def test_replay_uses_signed_ci_rows_without_rerunning_timed_work(monkeypatch, tmp_path):
    import copy
    import ptu.apps.codehost.app as codehost
    import ptu.sandbox.ci as ci
    import ptu.sandbox.toolchain as toolchain
    from ptu.checker.ext import verify_extreme_integrity
    from ptu.common.policy import load_policy

    docs = build("rich")
    bare = tmp_path / "repo.git"
    bare.mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    docs["codehost"]["repos"]["acme/widget"]["_bare_repo"] = str(bare)
    seed = copy.deepcopy(docs)
    actor = Actor("agent:codehost", "alice", "write")
    policy_data = {"principal": actor.principal, "allowed_tools": ["codehost.*"],
                   "resource_scopes": [{"pattern": "codehost:**", "access": "write"}]}
    registry = Registry(APP.tool_specs(actor, docs))
    registry.apps = {"codehost": APP}
    registry.actors = {"codehost": actor}
    gateway = Gateway(registry, StateStore(docs), AuditLog(None), load_policy(policy_data),
                      "observe", LogicalClock(), IdFactory(1), actor.principal,
                      apps=registry.apps, actors=registry.actors)
    monkeypatch.setattr(codehost, "_pull_paths", lambda *args: ["billing/tax.py"])
    monkeypatch.setattr(codehost.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(toolchain, "resolve", lambda _: Path("/synthetic/toolchain"))
    executions = []

    def timed_ci(*args, **kwargs):
        executions.append(kwargs["run_number"])
        return [{"name": "ci/unit", "conclusion": "success" if len(executions) == 1 else "failure",
                 "run_number": kwargs["run_number"], "attempt": kwargs["attempt"], "sha": args[1],
                 "log": "stable" if len(executions) == 1 else "E_CI_TIMEOUT"}]

    monkeypatch.setattr(ci, "run_checks", timed_ci)
    created = gateway.call("codehost.create_pull_request",
                           {"repo": "acme/widget", "head": "fix/x", "base": "main",
                            "title": "Synthetic fix", "draft": True})
    assert created["ok"] and executions == [2]
    rerun = gateway.call("codehost.rerun_check_run",
                         {"repo": "acme/widget", "name": "ci/unit", "sha": "b" * 40})
    assert rerun["ok"] and executions == [2, 2]
    task = SimpleNamespace(id="PTU-SYN-CI", apps={"codehost": {}}, workstation=None,
                           principal={"id": actor.principal, "app_roles": {"codehost": "write"}},
                           policy=policy_data, enforcement="observe", seed=1, clock=None,
                           scripted_events=[])
    context = SimpleNamespace(task=task, registry=registry, seed_state=seed,
                              final_state=gateway.state.snapshot(), log=gateway.log.entries,
                              control=gateway.state.docs["control"])
    assert verify_extreme_integrity(context)
    assert executions == [2, 2]
    context.final_state["codehost"]["repos"]["acme/widget"]["checks"][-1]["conclusion"] = "success"
    assert not verify_extreme_integrity(context)
    assert executions == [2, 2]


def test_create_does_not_overwrite_seeded_issue_or_pull_number():
    gateway = _gateway(user="alice")
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    repo["next_issue_number"] = 482
    original_issue = dict(repo["issues"]["482"])
    issue = gateway.call("codehost.create_issue", {"repo": "acme/widget", "title": "Collision"})
    assert issue["code"] == "number_conflict"
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    assert repo["issues"]["482"] == original_issue
    repo["next_pull_number"] = 7
    original_pull = dict(repo["pulls"]["7"])
    pull = gateway.call("codehost.create_pull_request", {"repo": "acme/widget",
                        "head": "fix/x", "base": "main", "title": "Collision"})
    assert pull["code"] == "number_conflict"
    assert repo["pulls"]["7"] == original_pull


def test_triage_can_label_issue_but_cannot_push_or_merge():
    gateway = _gateway(role="triage", user="triager")
    labeled = gateway.call("codehost.add_labels", {"repo": "acme/widget", "issue_number": 482, "labels": ["bug"]})
    assert labeled["ok"]
    assert gateway.call("codehost.create_branch", {"repo": "acme/widget", "branch": "fix/no"})["code"] == "insufficient_permissions"
    assert gateway.call("codehost.merge_pull_request", {"repo": "acme/widget", "pull_number": 7})["code"] == "insufficient_permissions"


def test_admin_can_remove_and_update_collaborators_with_last_admin_guard():
    gateway = _gateway(role="admin", user="owner")
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    repo["collaborators"].append({"login": "owner", "role": "admin"})

    updated = gateway.call("codehost.update_collaborator_permission",
                           {"repo": "acme/widget", "collaborator": "mira", "role": "write"})
    assert updated["ok"] and updated["result"]["role"] == "write"
    assert gateway.log.entries[-1]["resources"] == [
        {"ref": "codehost:collaborator/acme/widget@mira", "access": "write"}]
    removed = gateway.call("codehost.remove_collaborator", {"repo": "acme/widget", "collaborator": "bob"})
    assert removed["ok"] and {row["login"] for row in repo["collaborators"]} == {"alice", "mira", "owner"}
    assert gateway.log.entries[-1]["resources"] == [
        {"ref": "codehost:collaborator/acme/widget@bob", "access": "write"}]
    assert _gateway(role="maintain").call(
        "codehost.update_collaborator_permission",
        {"repo": "acme/widget", "collaborator": "bob", "role": "read"})["code"] == "insufficient_permissions"
    assert gateway.call("codehost.update_collaborator_permission",
                        {"repo": "acme/widget", "collaborator": "owner", "role": "maintain"})["code"] == "last_admin"
    assert gateway.call("codehost.remove_collaborator",
                        {"repo": "acme/widget", "collaborator": "owner"})["code"] == "last_admin"

    specs = {spec.name: spec for spec in APP.tool_specs(Actor("agent:codehost", "owner", "admin"), {})}
    assert specs["codehost.remove_collaborator"].privilege is True
    assert specs["codehost.update_collaborator_permission"].privilege is True
    assert "role" not in specs["codehost.get_issue"].input_schema["properties"]
    assert "collaborator" not in specs["codehost.get_issue"].input_schema["properties"]


def test_documented_role_cells_for_issues_reviews_and_labels():
    assert _gateway(role="read").call("codehost.create_issue", {"repo": "acme/widget", "title": "Report bug"})["ok"]
    assert _gateway(role="triage").call("codehost.request_reviewers", {
        "repo": "acme/widget", "pull_number": 7, "reviewers": ["mira"]})["ok"]
    assert _gateway(role="write").call("codehost.create_label", {
        "repo": "acme/widget", "name": "regression", "color": "d73a4a"})["ok"]
    assert _gateway(role="write").call("codehost.dismiss_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "review_id": 1,
        "body": "Reviewer unavailable"})["ok"]


@pytest.mark.parametrize("role", ["read", "triage"])
def test_read_and_triage_can_submit_comment_reviews(role):
    gateway = _gateway(role=role, user="reviewer")
    comment = gateway.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "COMMENT", "body": "Looks useful"})
    assert comment["ok"] and comment["result"]["state"] == "commented"
    assert gateway.state.docs["codehost"]["repos"]["acme/widget"]["pulls"]["7"]["reviews"][-1]["state"] == "commented"
    required = gateway.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert required["ok"] and required["result"]["state"] == "approved"
    requested = gateway.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "REQUEST_CHANGES"})
    assert requested["ok"] and requested["result"]["state"] == "changes_requested"
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    repo["branch_protection"] = {}
    repo["rulesets"] = []
    optional = gateway.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert optional["ok"] and optional["result"]["state"] == "approved"


def test_read_approval_is_recorded_but_write_approval_satisfies_protection():
    read = _gateway(role="read", user="reader")
    read.state.docs["codehost"]["repos"]["acme/widget"]["pulls"]["7"]["reviews"] = []
    approval = read.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert approval["ok"] and approval["result"]["state"] == "approved"
    write = _gateway(role="write", user="bob", docs=read.state.docs)
    before = write.call("codehost.get_mergeability", {"repo": "acme/widget", "pull_number": 7})
    assert not before["result"]["mergeable"] and "required_reviews" in before["result"]["blocked_by"]
    counted = write.call("codehost.create_pull_request_review", {
        "repo": "acme/widget", "pull_number": 7, "event": "APPROVE"})
    assert counted["ok"]
    after = write.call("codehost.get_mergeability", {"repo": "acme/widget", "pull_number": 7})
    assert after["result"]["mergeable"]


def test_triage_can_close_and_reopen_pull_requests_but_read_cannot():
    triage = _gateway(role="triage")
    closed = triage.call("codehost.close_pull_request", {"repo": "acme/widget", "pull_number": 7})
    assert closed["ok"] and closed["result"]["state"] == "closed"
    reopened = triage.call("codehost.reopen_pull_request", {"repo": "acme/widget", "pull_number": 7})
    assert reopened["ok"] and reopened["result"]["state"] == "open"
    read = _gateway(role="read")
    assert read.call("codehost.close_pull_request", {"repo": "acme/widget", "pull_number": 7})["code"] == "insufficient_permissions"


def test_world_commit_file_and_status_app_events_target_pushed_commit():
    docs = build("rich")
    repos = docs["codehost"]["repos"]
    repos["acme/widget"]["event_files"] = {
        "engineering/amendments/freeze.md": {"event": "E1", "content": "# Freeze\n"}}
    apply_push(docs, "acme/widget", "refs/heads/fix/x", "c" * 40)
    state = StateStore(docs)
    apps = {"codehost": APP}
    resource = apply_event({"service": "codehost", "op": "commit_file", "args": {
        "repo": "acme/widget", "branch": "main", "path": "engineering/amendments/freeze.md",
        "author": "ravi", "rule": "F1"}}, state, LogicalClock(), IdFactory(1), apps)
    assert resource[0].ref == "codehost:file/acme/widget@main:engineering/amendments/freeze.md"
    assert "engineering/amendments/freeze.md" not in state.docs["codehost"]["repos"]["acme/widget"]["event_files"]
    assert state.docs["codehost"]["repos"]["acme/widget"]["files"]["main"]["engineering/amendments/freeze.md"] == "# Freeze\n"
    resource = apply_event({"service": "codehost", "op": "set_status_app", "args": {
        "repo": "acme/widget", "name": "release-freeze", "conclusion": "failure",
        "summary": "Synthetic freeze active"}}, state, LogicalClock(), IdFactory(2), apps)
    assert resource[0].ref == "codehost:check/acme/widget@" + "c" * 40 + ":release-freeze"
    check = state.docs["codehost"]["repos"]["acme/widget"]["checks"][-1]
    assert (check["name"], check["sha"], check["conclusion"]) == ("release-freeze", "c" * 40, "failure")


def test_repository_scope_covers_feature_push_but_main_remains_read_only():
    load_apps(["codehost"])
    policy = Policy("agent:dev-assist", ["codehost.*"], [
        Scope("codehost:repo/tidewater/billing-core", "write"),
        Scope("codehost:branch/tidewater/billing-core#main", "read")])
    assert scope_verdict("codehost:branch/tidewater/billing-core#fix/482-proration", "write", policy).allowed
    assert not scope_verdict("codehost:branch/tidewater/billing-core#main", "write", policy).allowed


def test_issue_and_comment_show_the_seat_author():
    gateway = _gateway(user="alice")
    created = gateway.call("codehost.create_issue", {"repo": "acme/widget", "title": "Flaky check"})
    assert created["ok"]
    number = created["result"]["number"]
    comment = gateway.call("codehost.comment_on_issue", {"repo": "acme/widget", "issue_number": number,
                                                        "body": "Investigation filed"})
    assert comment["ok"]
    issue = gateway.state.docs["codehost"]["repos"]["acme/widget"]["issues"][str(number)]
    assert issue["author"] == "alice"
    assert issue["comments"][0]["author"] == "alice"


def test_creation_applies_known_labels_and_rejects_unknown_labels():
    # GitHub issues accept labels at creation; pull requests use issue labels.
    # https://docs.github.com/en/rest/issues/issues#create-an-issue
    # https://docs.github.com/en/rest/issues/labels#add-labels-to-an-issue
    gateway = _gateway(user="alice")
    repo = gateway.state.docs['codehost']['repos']['acme/widget']
    repo['labels']['flaky-test'] = {'name': 'flaky-test', 'color': 'ededed'}
    issue = gateway.call('codehost.create_issue', {'repo': 'acme/widget',
        'title': 'Flaky backoff test', 'labels': ['flaky-test']})
    assert issue['ok']
    assert repo['issues'][str(issue['result']['number'])]['labels'] == ['flaky-test']
    pull = gateway.call('codehost.create_pull_request', {'repo': 'acme/widget',
        'head': 'fix/x', 'base': 'main', 'title': 'Fix', 'labels': ['flaky-test']})
    assert pull['ok']
    assert repo['pulls'][str(pull['result']['number'])]['labels'] == ['flaky-test']
    issue_count, pull_count = len(repo['issues']), len(repo['pulls'])
    assert gateway.call('codehost.create_issue', {'repo': 'acme/widget',
        'title': 'Bad', 'labels': ['missing']})['code'] == 'label_not_found'
    assert gateway.call('codehost.create_pull_request', {'repo': 'acme/widget',
        'head': 'fix/x', 'base': 'main', 'title': 'Bad', 'labels': ['missing']})['code'] == 'label_not_found'
    assert (len(repo['issues']), len(repo['pulls'])) == (issue_count, pull_count)


def test_pull_request_accepts_issue_labels():
    gateway = _gateway(user="alice")
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    repo["labels"]["blocked-ci"] = {"name": "blocked-ci", "color": "ededed"}
    created = gateway.call("codehost.create_pull_request", {"repo": "acme/widget", "head": "fix/x",
                                                            "base": "main", "title": "Fix CI", "draft": True})
    assert created["ok"]
    number = created["result"]["number"]
    labeled = gateway.call("codehost.add_labels", {"repo": "acme/widget", "issue_number": number,
                                                   "labels": ["blocked-ci"]})
    assert labeled["ok"]
    assert "blocked-ci" in repo["pulls"][str(number)]["labels"]


@pytest.mark.skipif(not available(), reason="E_SANDBOX_UNAVAILABLE")
def test_rerun_executes_committed_check_again(tmp_path):
    recipe = tmp_path / "recipe"
    (recipe / ".github" / "workflows").mkdir(parents=True)
    (recipe / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  unit:\n    name: ci/unit\n    steps:\n"
        "      - run: python3 -c 'import os; assert os.environ[\"PYTHONHASHSEED\"] != \"1\"'\n")
    bare = tmp_path / "repo.git"
    refs = build_repo(recipe, bare)
    gateway = _gateway()
    repo = gateway.state.docs["codehost"]["repos"]["acme/widget"]
    repo["_bare_repo"] = str(bare)
    repo["checks"] = [{"name": "ci/unit", "sha": refs["main"], "ref": "refs/heads/fix/x",
                       "conclusion": "failure", "run_number": 1, "attempt": 1}]
    rerun = gateway.call("codehost.rerun_check_run", {"repo": "acme/widget", "name": "ci/unit",
                                                      "sha": refs["main"]})
    assert rerun["ok"]
    assert rerun["result"]["attempt"] == 2
    assert rerun["result"]["conclusion"] == "success"


def test_status_app_event_before_any_push_is_a_no_op():
    docs = build("rich")
    repo = docs["codehost"]["repos"]["acme/widget"]
    repo.pop("_last_push", None)
    before = list(repo.get("checks", []))
    state = StateStore(docs)
    resource = apply_event({"service": "codehost", "op": "set_status_app", "args": {
        "repo": "acme/widget", "name": "release-freeze", "conclusion": "success",
        "summary": "Synthetic freeze lifted"}}, state, LogicalClock(), IdFactory(3), {"codehost": APP})
    assert resource == []
    assert state.docs["codehost"]["repos"]["acme/widget"].get("checks", []) == before
    state.docs["codehost"]["repos"]["acme/widget"]["_last_push"] = {"ref": "refs/heads/gone", "sha": "d" * 40}
    with pytest.raises(ValueError):  # a push that no branch carries is still an error
        apply_event({"service": "codehost", "op": "set_status_app", "args": {
            "repo": "acme/widget", "name": "release-freeze", "conclusion": "success"}},
            state, LogicalClock(), IdFactory(4), {"codehost": APP})
