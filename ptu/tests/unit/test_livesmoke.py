"""The paid gate requires a current, evidenced live route smoke."""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ptu.harness import gate, livesmoke
from ptu.common.log import AuditLog
from ptu.tasks.loader import load_task


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_agent_acts_require_accepted_push_pr_and_finish():
    rows = [{"tool": "ws.exec", "result_status": "ok", "push_effects": [{"accepted": True}]},
            {"tool": "codehost.create_pull_request", "result_status": "ok"},
            {"tool": "control.finish", "result_status": "ok"}]
    assert livesmoke._agent_acts(rows) == {"ws_exec": True, "push": True, "pr": True, "finish": True}
    rows[0]["push_effects"][0]["accepted"] = False
    assert not livesmoke._agent_acts(rows)["push"]
    rows[0]["push_effects"][0]["accepted"] = True
    rows[1]["result_status"] = "app_error"
    assert not livesmoke._agent_acts(rows)["pr"]


def test_gate_refuses_missing_and_stale_livesmoke(monkeypatch, tmp_path):
    task_path = tmp_path / "task.yaml"
    task_path.write_text("id: XTR-1\nmetadata:\n  difficulty:\n    route: {passed: true}\n")
    task = SimpleNamespace(id="XTR-1", source_path=str(task_path),
                           metadata={"difficulty": {"route": {"passed": True}}})
    monkeypatch.setattr(gate, "_load_task", lambda task_id: task)
    monkeypatch.setattr(livesmoke, "_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="^E_LIVESMOKE_REQUIRED$"):
        gate.gate("XTR-1", "expert")
    with pytest.raises(ValueError, match="^E_LIVESMOKE_REQUIRED$"):
        gate.gate("XTR-1", "frontier")


def test_receipt_checks_task_hash_and_evidence(monkeypatch, tmp_path):
    task_path = tmp_path / "task.yaml"
    task_path.write_text("id: XTR-1\nmetadata: {difficulty: {route: {passed: true}}}\n")
    task = SimpleNamespace(id="XTR-1", source_path=str(task_path))
    monkeypatch.setattr(livesmoke, "_root", lambda: tmp_path)
    episode = tmp_path / "smoke" / "episodes" / "XTR-1" / "cc-sonnet" / "0"
    episode.mkdir(parents=True)
    result = episode / "result.json"
    result.write_text(json.dumps({"outcome": "O-COMPLETE"}))
    route = tmp_path / "smoke" / "route.json"
    route.write_text(json.dumps({"pass": True, "findings": 0}))
    log = episode / "log.jsonl"
    audit = AuditLog(log)
    for index, row in enumerate((
            {"tool": "ws.exec", "result_status": "ok", "push_effects": [{"accepted": True}]},
            {"tool": "codehost.create_pull_request", "result_status": "ok"},
            {"tool": "control.finish", "result_status": "ok"}), 1):
        audit.append({"seq": index, **row})
    route_log = tmp_path / "smoke" / "route-log.jsonl"
    route_log.write_text("synthetic route evidence\n")
    paths = {"agent_result": str(result), "agent_log": str(log),
             "route_result": str(route), "route_log": str(route_log)}
    receipt = {"status": "passed", "task_id": task.id,
               "task_sha256": gate._task_definition_hash(str(task_path)),
               "scripted": {"pass": True, "findings": 0},
               "agent": {"acts": {key: True for key in ("ws_exec", "push", "pr", "finish")}},
               "paths": paths, "evidence": {name: _digest(name) for name in paths.values()}}
    livesmoke._write_receipt(task, receipt)
    livesmoke.require_pass(task)
    livesmoke._write_receipt(task, {**receipt, "status": "blocked"})
    with pytest.raises(ValueError, match="^E_LIVESMOKE_REQUIRED$"):
        livesmoke.require_pass(task)
    livesmoke._write_receipt(task, receipt)
    task_path.write_text(task_path.read_text() + "prompt: {task: changed}\n")
    with pytest.raises(ValueError, match="^E_LIVESMOKE_STALE$"):
        livesmoke.require_pass(task)
    task_path.write_text(task_path.read_text().split("prompt:")[0])
    log.write_text("tampered\n")
    with pytest.raises(ValueError, match="^E_LIVESMOKE_STALE$"):
        livesmoke.require_pass(task)


def test_livesmoke_runs_one_bounded_agent_and_writes_gate_receipt(monkeypatch, tmp_path):
    task_path = tmp_path / "task.yaml"
    task_path.write_text("id: XTR-1\nmetadata: {difficulty: {route: {passed: true}}}\n")
    task = SimpleNamespace(id="XTR-1", source_path=str(task_path), schema_version="0.3",
                           workstation={"repo": {"codehost_repo": "acme/widget"}})
    monkeypatch.setattr(gate, "_load_task", lambda task_id: task)
    monkeypatch.setattr(livesmoke, "_root", lambda: tmp_path)
    monkeypatch.setattr(livesmoke, "_smoke_task", lambda selected: selected)
    monkeypatch.setattr(livesmoke, "check_subscription_windows", lambda: {
        "observed_at": "2026-10-02T12:00:00+00:00", "overall_weekly": 0.43,
        "fable_weekly": 0.51})

    async def scripted(selected, episode):
        episode.mkdir(parents=True)
        (episode / "route.json").write_text('{"pass":true,"findings":0}\n')
        (episode / "log.jsonl").write_text("synthetic route log\n")
        return {"pass": True, "findings": 0, "calls": 3, "tool_count": 4}

    monkeypatch.setattr(livesmoke, "_scripted_route", scripted)
    seen = []

    def agent(selected, profile, episode, *, wall_clock_seconds, usage_bootstrap, live):
        seen.append((selected.id, profile.id, wall_clock_seconds, usage_bootstrap, live))
        episode.mkdir(parents=True)
        result = episode / "result.json"
        result.write_text('{"outcome":"O-GOAL-FAIL","graded":true}\n')
        log = AuditLog(episode / "log.jsonl")
        for index, row in enumerate((
                {"tool": "ws.exec", "result_status": "ok", "push_effects": [{"accepted": True}]},
                {"tool": "codehost.create_pull_request", "result_status": "ok"},
                {"tool": "control.finish", "result_status": "ok"}), 1):
            log.append({"seq": index, **row})
        return SimpleNamespace(result_path=result)

    monkeypatch.setattr(livesmoke, "run_harness_episode", agent)
    result = livesmoke.run(task.id)
    assert seen == [(task.id, "cc-sonnet", 900, True, True)]
    assert result["scripted"]["findings"] == 0
    assert result["usage_windows"]["fable_weekly"] == 0.51
    livesmoke.require_pass(task)


@pytest.mark.parametrize("task_id,ticket", [
    ("PTU-TDW-XTR-221", "T-2200"),
    ("PTU-TDW-XTR-222", "T-2300"),
    ("PTU-TDW-XTR-241", "T-3000"),
])
def test_app_only_livesmoke_uses_real_store_shape_and_fake_agent(
        monkeypatch, tmp_path, task_id, ticket):
    task = load_task(f"ptu/tasks/extreme/{task_id}/task.yaml")
    monkeypatch.setattr(gate, "_load_task", lambda selected: task)
    monkeypatch.setattr(livesmoke, "_root", lambda: tmp_path)
    monkeypatch.setattr(livesmoke, "check_subscription_windows", lambda: {
        "observed_at": "2026-10-02T12:00:00+00:00", "overall_weekly": 0.43,
        "fable_weekly": 0.51})
    observed = []

    async def scripted(selected, episode):
        observed.append(("scripted", selected.id, episode))
        episode.mkdir(parents=True)
        (episode / "route.json").write_text('{"pass":true,"findings":0}\n')
        (episode / "log.jsonl").write_text("synthetic route log\n")
        return {"pass": True, "findings": 0, "calls": 9, "tool_count": 20}

    def agent(selected, profile, episode, *, wall_clock_seconds, usage_bootstrap, live):
        observed.append(("agent", selected, profile.id, episode, wall_clock_seconds,
                         usage_bootstrap, live))
        episode.mkdir(parents=True)
        assert ticket in selected.prompt["task"]
        assert "crm.create_note" in selected.prompt["task"]
        result = episode / "result.json"
        result.write_text(json.dumps({"outcome": "O-GOAL-FAIL", "graded": True,
            "harness": {"init_seen": True, "mcp_connected": True,
                        "tool_list_sha256": livesmoke._expected_tool_hash(task),
                        "mcp_tool_uses": 6, "log_calls": 6,
                        "oob_actions": 0, "mismatch": False}}))
        log = AuditLog(episode / "log.jsonl")
        for index, row in enumerate((
                {"tool": "codehost.get_file_contents", "effect": "read"},
                {"tool": "crm.get_ticket", "effect": "read"},
                {"tool": "crm.list_activities", "effect": "read"},
                {"tool": "crm.create_note", "effect": "write"},
                {"tool": "crm.list_activities", "effect": "read"},
                {"tool": "control.finish", "effect": "write"}), 1):
            log.append({"seq": index, "result_status": "ok", **row})
        return SimpleNamespace(result_path=result)

    monkeypatch.setattr(livesmoke, "_scripted_route", scripted)
    monkeypatch.setattr(livesmoke, "run_harness_episode", agent)
    result = livesmoke.run(task.id)
    assert result["agent"]["acts"] == {
        "app_reads": True, "app_writes": True, "min_app_calls": True,
        "finish": True, "tools_connected": True, "no_harness_errors": True}
    assert observed[0][2].is_relative_to(tmp_path)
    assert observed[0][2].parts[-2:] == ("scripted", task.id)
    assert observed[1][2] == "cc-sonnet"
    assert observed[1][4:] == (900, True, True)
    livesmoke.require_pass(task)
    receipt = json.loads(livesmoke._receipt_path(task.id).read_text())
    agent_path = Path(receipt["paths"]["agent_result"])
    agent_result = json.loads(agent_path.read_text())
    agent_result["harness"]["tool_list_sha256"] = "0" * 64
    agent_path.write_text(json.dumps(agent_result))
    receipt["evidence"][str(agent_path)] = _digest(agent_path)
    livesmoke._write_receipt(task, receipt)
    with pytest.raises(ValueError, match="^E_LIVESMOKE_STALE$"):
        livesmoke.require_pass(task)


def test_app_only_receipt_rejects_wrong_tool_set_and_harness_errors():
    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-221/task.yaml")
    rows = [{"tool": "codehost.get_file_contents", "effect": "read", "result_status": "ok"},
            {"tool": "crm.get_ticket", "effect": "read", "result_status": "ok"},
            {"tool": "crm.list_activities", "effect": "read", "result_status": "ok"},
            {"tool": "crm.create_note", "effect": "write", "result_status": "ok"},
            {"tool": "control.finish", "effect": "write", "result_status": "ok"}]
    ready = {"harness": {"init_seen": True, "mcp_connected": True,
             "tool_list_sha256": livesmoke._expected_tool_hash(task),
             "mcp_tool_uses": 5, "log_calls": 5, "oob_actions": 0, "mismatch": False}}
    assert all(livesmoke._agent_acts(rows, app_only=True,
                                     tool_ready=livesmoke._agent_tools_ready(task, ready)).values())
    bad = {"harness": {**ready["harness"], "tool_list_sha256": "0" * 64}}
    assert not livesmoke._agent_acts(rows, app_only=True,
                                    tool_ready=livesmoke._agent_tools_ready(task, bad))["tools_connected"]
    isolated = {**ready, "error": {"code": "E_ISOLATION"}}
    assert not livesmoke._agent_tools_ready(task, isolated)
    assert not livesmoke._agent_tools_ready(task, {"harness": {**ready["harness"],
        "mcp_tool_uses": 0, "log_calls": 0}})
    assert not livesmoke._agent_acts(rows[:3] + rows[-1:], app_only=True,
                                    tool_ready=True)["app_writes"]
    assert not livesmoke._agent_acts(rows[:3] + rows[-1:], app_only=True,
                                    tool_ready=True)["min_app_calls"]
    assert not livesmoke._agent_acts(rows[:-1], app_only=True,
                                    tool_ready=True)["finish"]
    rows.insert(-1, {"tool": "crm.get_ticket", "result_status": "tool_error"})
    assert not livesmoke._agent_acts(rows, app_only=True,
                                    tool_ready=True)["no_harness_errors"]
    event_only = rows[:3] + [{"kind": "event", "tool": "crm.create_note",
                              "effect": "write", "result_status": "ok"}, rows[-1]]
    assert not livesmoke._agent_acts(event_only, app_only=True,
                                    tool_ready=True)["app_writes"]


def test_reconcile_one_bootstrap_agent_after_independent_fable_reading(monkeypatch, tmp_path):
    from ptu.harness.run_store import RunStore
    from ptu.harness.usage_windows import record_manual_snapshot

    task_path = tmp_path / "task.yaml"
    task_path.write_text("id: XTR-1\nmetadata: {difficulty: {route: {passed: true}}}\n")
    task = SimpleNamespace(id="XTR-1", source_path=str(task_path))
    monkeypatch.setattr(gate, "_load_task", lambda task_id: task)
    monkeypatch.setattr(livesmoke, "_root", lambda: tmp_path)
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(tmp_path / "usage-snapshot.json"))
    store = RunStore(root=tmp_path, run_id="livesmoke-synthetic")
    route = store.path / "scripted" / task.id
    route.mkdir(parents=True)
    (route / "route.json").write_text('{"pass":true,"findings":0}\n')
    (route / "log.jsonl").write_text("synthetic route evidence\n")
    episode = store.episode_dir(task.id, "cc-sonnet", 0)
    (episode / "private").mkdir(parents=True)
    (episode / "private" / "task.yaml").write_text(task_path.read_text())
    (episode / "manifest.json").write_text(json.dumps({
        "model_id": "cc-sonnet", "harness": {"usage_bootstrap": True}}))
    result = {"outcome": "O-ERROR", "graded": False,
              "error": {"code": "E_USAGE_WINDOWS"},
              "harness": {"mcp_tool_uses": 3, "log_calls": 3, "oob_actions": 0,
                          "exit_code": 0, "mismatch": False, "stop_reason": "result"}}
    result_path = episode / "result.json"
    result_path.write_text(json.dumps(result))
    log = AuditLog(episode / "log.jsonl")
    for index, row in enumerate((
            {"tool": "ws.exec", "result_status": "ok", "push_effects": [{"accepted": True}]},
            {"tool": "codehost.create_pull_request", "result_status": "ok"},
            {"tool": "control.finish", "result_status": "ok"}), 1):
        log.append({"seq": index, **row})
    (episode / "usage.json").write_text(json.dumps({
        "rate_windows": {"seven_day": {"last": 0.4}}}))
    old = time.time() - 60
    os.utime(result_path, (old, old))
    (tmp_path / "usage-bootstrap.json").write_text(json.dumps({
        "task_id": task.id, "episode_dir": str(episode)}))
    observed = datetime.now(timezone.utc).isoformat()
    record_manual_snapshot("40", "47", "manual-claude.ai", observed)
    summary = livesmoke.reconcile_bootstrap(task.id)
    assert summary["status"] == "passed"
    receipt = json.loads(livesmoke._receipt_path(task.id).read_text())
    assert receipt["agent"]["usage_reconciled"] is True
    assert receipt["agent"]["outcome"] == "O-ERROR"
    livesmoke.require_pass(task)
    bad = {**result, "error": {"code": "E_MODEL"}}
    assert not livesmoke._recoverable_usage(bad, livesmoke.check_subscription_windows(),
                                           result_path)
