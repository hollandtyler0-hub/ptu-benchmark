"""Gate scheduling and task staleness with synthetic episode results only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ptu.harness import gate as gate_module


@pytest.fixture(autouse=True)
def _synthetic_livesmoke(monkeypatch):
    from ptu.harness import livesmoke
    monkeypatch.setattr(livesmoke, "require_pass", lambda task: None)


def test_gate_cli_prints_underlying_error_code(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ValueError("E_TASK_SCHEMA /source_path")
    monkeypatch.setattr(gate_module, "gate", fail)
    assert gate_module.main(["--task", "XTR-1", "--stage", "expert"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E_TASK_SCHEMA"


def test_gate_retries_use_distinct_episode_keys_and_stop_on_oob(monkeypatch):
    seen = []
    def attempt(task, profile, run_id, repeat, retry):
        seen.append((repeat, retry))
        return ({"outcome": "O-ERROR", "graded": False, "error": {"code": "E_MODEL"}}
                if retry < 2 else {"outcome": "O-COMPLETE", "graded": True, "pass": True})
    monkeypatch.setattr(gate_module, "_run_attempt", attempt)
    answer, errors = gate_module._graded_attempt(None, None, "synthetic", 4)
    assert answer["pass"] and errors == 2
    assert seen == [(4, 0), (4, 1), (4, 2)]
    seen.clear()
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args:
                        seen.append(args[-1]) or {"outcome": "O-INVALID-OOB", "graded": False})
    answer, errors = gate_module._graded_attempt(None, None, "synthetic", 4)
    assert answer is None and errors == 1 and seen == [0]


def test_gate_does_not_retry_finished_agent_with_grading_error(monkeypatch):
    seen = []
    def finished_ungraded(task, profile, run_id, repeat, retry):
        seen.append(retry)
        return {"outcome": "O-ERROR", "graded": False,
                "regrade_required": True, "error": {"code": "E_HIDDEN_RUN"}}
    monkeypatch.setattr(gate_module, "_run_attempt", finished_ungraded)
    answer, errors = gate_module._graded_attempt(None, None, "synthetic", 0)
    assert answer is None and errors == 1 and seen == [0]


def test_gate_does_not_retry_wall_clock_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args:
                        seen.append(args[-1]) or {"outcome": "O-ERROR", "graded": False,
                                                   "error": {"code": "E_TIMEOUT"}})
    answer, errors = gate_module._graded_attempt(None, None, "synthetic", 0)
    assert answer is None and errors == 1 and seen == [0]


def test_gate_keeps_unscorable_episode_without_retry(monkeypatch):
    seen = []
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args:
                        seen.append(args[-1]) or {"outcome": "O-UNSCORABLE", "graded": True,
                                                   "pass": None})
    answer, errors = gate_module._graded_attempt(None, None, "synthetic", 0)
    assert answer["outcome"] == "O-UNSCORABLE" and answer["pass"] is None
    assert errors == 0 and seen == [0]


def test_regrade_saved_episode_runs_only_grading_and_keeps_original(monkeypatch, tmp_path, capsys):
    from ptu.harness import regrade as regrade_module
    from ptu.common.log import AuditLog

    run = tmp_path / "gate-synthetic"
    episode = run / "episodes/XTR-SYN/cc-opus-5-5-expert/0.r0"
    for name in ("private", "seed_state", "final_state"):
        (episode / name).mkdir(parents=True)
    source = tmp_path / "task.yaml"
    source.write_text("id: XTR-SYN\n")
    (episode / "private/task.yaml").write_bytes(source.read_bytes())
    (episode / "private/secrets.json").write_text("{}")
    for name in ("seed_state", "final_state"):
        (episode / name / "control.json").write_text("{}")
        (episode / name / "codehost.json").write_text("{}")
    AuditLog(episode / "log.jsonl").bind("0" * 64, "1" * 64, "episode-syn")
    original = {"task_id": "XTR-SYN", "outcome": "O-ERROR", "graded": False,
                "error": {"code": "E_HIDDEN_RUN"}, "harness": {"mcp_connected": True}}
    (episode / "result.json").write_text(json.dumps(original))
    task = SimpleNamespace(id="XTR-SYN", source_path=str(source),
                           workstation={"repo": {"codehost_repo": "acme/billing"}},
                           hidden_tests={"suites": {"pass_to_pass": {}}},
                           checker={"goal": [{"id": "g1"}]})
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    monkeypatch.setattr(regrade_module, "registry_for", lambda task: object())
    monkeypatch.setattr(regrade_module, "run_task_suites", lambda task, episode, state:
                        {"pass_to_pass": {"test_contract.Case.test_one": "fail"}})
    monkeypatch.setattr(regrade_module, "check", lambda context: SimpleNamespace(to_json=lambda:
                        {"task_id": "XTR-SYN", "goal": {"ok": False, "failed_assertions": [0]},
                         "outcome": "O-GOAL-FAIL", "pass": False, "violations": []}))
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args: pytest.fail("agent was rerun"))
    assert gate_module.main(["--regrade", str(run)]) == 0
    result = json.loads((episode / "result.regraded.json").read_text())
    assert result["outcome"] == "O-GOAL-FAIL" and result["graded"] is True
    assert result["hidden_tests"]["pass_to_pass"]["failing_test_ids"] == [
        "test_contract.Case.test_one"]
    assert json.loads((episode / "result.json").read_text()) == original
    report = json.loads(capsys.readouterr().out)
    assert report["regraded"] == 1 and report["episodes"][0]["failed_assertions"] == ["g1"]
    (episode / "private/task.yaml").write_text("id: XTR-OTHER\n")
    assert gate_module.main(["--regrade", str(run)]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E_REGRADE_TASK_STALE"


def test_regrade_write_records_verified_expert_stage_without_agent(monkeypatch, tmp_path, capsys):
    from ptu.harness import regrade as regrade_module

    task = _task(tmp_path)
    fingerprint = gate_module._task_definition_hash(task.source_path)
    runs = tmp_path / "runs"
    run = runs / "gate-synthetic"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"task_id": task.id,
        "transport": "claude-code"}))
    rows = []
    for index, passed in enumerate((False, True, False)):
        episode = run / "episodes" / task.id / "cc-opus-5-5-expert" / f"{index}.r0"
        episode.mkdir(parents=True)
        (episode / "result.json").write_text(json.dumps({"task_id": task.id,
            "outcome": "O-GOAL-FAIL", "pass": False}))
        (episode / "result.regraded.json").write_text(json.dumps({"task_id": task.id,
            "outcome": "O-COMPLETE" if passed else "O-GOAL-FAIL", "pass": passed,
            "graded": True, "decision_points": {"total": 4, "passed": 4 if passed else 3,
                "failed": [] if passed else [{"dp": f"DP-{index}"}]}}))
        rows.append({"episode": f"{index}.r0", "status": "GRADED",
                     "task_id": task.id, "profile": "cc-opus-5-5-expert"})
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    monkeypatch.setattr(gate_module, "default_root", lambda: runs)
    monkeypatch.setattr(gate_module, "_checker_commit", lambda: "a" * 40)
    monkeypatch.setattr(regrade_module, "regrade_run", lambda path: {
        "run_dir": str(path), "regraded": 3, "pending": 0, "episodes": rows})
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args: pytest.fail("agent was rerun"))
    assert gate_module.main(["--regrade", str(run), "--write"]) == 0
    report = json.loads(capsys.readouterr().out)
    recorded = yaml.safe_load(Path(task.source_path).read_text())["metadata"]["difficulty"]["expert_solve"]
    assert report["stage"] == "expert" and report["stage_record"] == recorded
    assert recorded["attempts"] == 3 and recorded["passes"] == 1
    assert recorded["run_ids"] == [run.name] and recorded["task_sha256"] == fingerprint
    assert recorded["regraded"] is True and recorded["checker_commit"] == "a" * 40
    assert gate_module._task_definition_hash(task.source_path) == fingerprint
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args: {
        "outcome": "O-GOAL-FAIL", "pass": False,
        "decision_points": {"total": 4, "failed": [{"dp": "DP-1"}]}})
    assert gate_module.gate(task.id, "frontier")["k"] == 1


def test_regrade_write_refuses_pending_or_unverified_episode(monkeypatch, tmp_path, capsys):
    from ptu.harness import regrade as regrade_module

    task = _task(tmp_path)
    runs = tmp_path / "runs"
    run = runs / "gate-synthetic"
    episode = run / "episodes" / task.id / "cc-opus-5-5-expert" / "0.r0"
    episode.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"task_id": task.id,
        "transport": "claude-code"}))
    (episode / "result.json").write_text(json.dumps({"task_id": task.id}))
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    monkeypatch.setattr(gate_module, "default_root", lambda: runs)
    monkeypatch.setattr(regrade_module, "regrade_run", lambda path: {
        "run_dir": str(path), "regraded": 0, "pending": 1,
        "episodes": [{"episode": "0.r0", "status": "PENDING", "task_id": task.id,
                      "profile": "cc-opus-5-5-expert"}]})
    assert gate_module.main(["--regrade", str(run), "--write"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E_REGRADE_INCOMPLETE"


def test_three_forced_gate_failures_leave_three_episode_directories(monkeypatch, tmp_path):
    from ptu.harness.run_store import RunStore

    task = SimpleNamespace(id="XTR-SYN", limits={"wall_clock_seconds": 5})
    profile = SimpleNamespace(id="cc-sonnet")
    monkeypatch.setattr(gate_module, "RunStore", lambda run_id: RunStore(root=tmp_path, run_id=run_id))

    def fake_episode(task, profile, path, **kwargs):
        path.mkdir(parents=True)
        result = path / "result.json"
        result.write_text(json.dumps({"outcome": "O-ERROR", "graded": False,
                                      "error": {"code": "E_MODEL"}}))
        return SimpleNamespace(result_path=result)

    monkeypatch.setattr(gate_module, "run_harness_episode", fake_episode)
    answer, errors = gate_module._graded_attempt(task, profile, "synthetic", 2)
    assert answer is None and errors == 3
    parent = tmp_path / "synthetic" / "episodes" / "XTR-SYN" / "cc-sonnet"
    assert {path.name for path in parent.iterdir()} == {"2.r0", "2.r1", "2.r2"}
    outcomes = json.loads((tmp_path / "synthetic" / "manifest.json").read_text())["retry_outcomes"]
    assert set(outcomes) == {"cc-sonnet/2.r0", "cc-sonnet/2.r1", "cc-sonnet/2.r2"}


def test_hidden_grade_failure_keeps_one_episode_marked_for_regrade(monkeypatch, tmp_path):
    from ptu.harness.run_store import RunStore

    task = SimpleNamespace(id="XTR-SYN", limits={"wall_clock_seconds": 5})
    profile = SimpleNamespace(id="cc-sonnet")
    monkeypatch.setattr(gate_module, "RunStore", lambda run_id: RunStore(root=tmp_path, run_id=run_id))
    def fake_episode(task, profile, path, **kwargs):
        path.mkdir(parents=True)
        result = path / "result.json"
        result.write_text(json.dumps({"outcome": "O-ERROR", "graded": False,
                                      "regrade_required": True,
                                      "error": {"code": "E_HIDDEN_RUN"}}))
        return SimpleNamespace(result_path=result)
    monkeypatch.setattr(gate_module, "run_harness_episode", fake_episode)
    answer, errors = gate_module._graded_attempt(task, profile, "synthetic", 0)
    assert answer is None and errors == 1
    parent = tmp_path / "synthetic/episodes/XTR-SYN/cc-sonnet"
    assert {path.name for path in parent.iterdir()} == {"0.r0"}
    manifest = json.loads((tmp_path / "synthetic/manifest.json").read_text())
    assert manifest["retry_outcomes"]["cc-sonnet/0.r0"]["regrade_required"] is True


def _task(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("id: XTR-1\nmetadata:\n  difficulty:\n    route: {passed: true}\n"
                    "prompt: {task: synthetic request}\n")
    return SimpleNamespace(id="XTR-1", source_path=str(path),
                           limits={"wall_clock_seconds": 300, "max_model_turns": 900},
                           metadata=yaml.safe_load(path.read_text())["metadata"])


def test_frontier_second_episode_only_after_first_pass(monkeypatch, tmp_path):
    task = _task(tmp_path)
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    calls = []

    def failed(task, profile, run_id, repeat, retry):
        assert retry == 0
        calls.append((profile.id, repeat))
        return {"pass": False, "outcome": "O-GOAL-FAIL", "decision_points": {"total": 2, "failed": [{"dp": "DP-1"}]}}

    monkeypatch.setattr(gate_module, "_run_attempt", failed)
    first = gate_module.gate("XTR-1", "frontier", write=True)
    assert first["k"] == 1 and first["passes"] == 0
    assert calls == [("cc-fable-5-1-max", 0)]
    assert yaml.safe_load(Path(task.source_path).read_text())["metadata"]["difficulty"]["frontier_gate"]["passes"] == 0

    calls.clear()
    monkeypatch.setattr(gate_module, "_run_attempt", lambda task, profile, run_id, repeat, retry:
                        calls.append(repeat) or {"pass": True, "outcome": "O-COMPLETE",
                                                  "decision_points": {"total": 2, "failed": []}})
    second = gate_module.gate("XTR-1", "frontier")
    assert second["k"] == 2 and second["passes"] == 2 and calls == [0, 1]


def test_expert_stops_on_pass_and_combines_decision_point_coverage(monkeypatch, tmp_path):
    task = _task(tmp_path)
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    answers = iter([
        {"pass": False, "outcome": "O-GOAL-FAIL", "decision_points": {"total": 2,
            "failed": [{"dp": "DP-1"}]}},
        {"pass": True, "outcome": "O-COMPLETE", "decision_points": {"total": 2, "failed": []}},
    ])
    calls = []
    monkeypatch.setattr(gate_module, "_run_attempt", lambda task, profile, run_id, repeat, retry:
                        calls.append((profile.id, repeat)) or next(answers))
    result = gate_module.gate("XTR-1", "expert")
    assert result["attempts"] == 2 and result["passes"] == 1 and result["dp_coverage"] == 1.0
    assert result["attempt_cap"] == 3 and result["stopped_early"] is True
    assert calls == [("cc-opus-5-5-expert", 0), ("cc-opus-5-5-expert", 1)]


def test_gate_coverage_does_not_count_unread_hazard_as_exercised():
    assert gate_module._coverage([{"pass": True, "decision_points": {
        "total": 3, "passed": 2, "failed": [], "unexercised": ["DP-3"]}}]) == 2 / 3


def test_route_required_and_confirmation_refuses_changed_task(monkeypatch, tmp_path):
    task = _task(tmp_path)
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    monkeypatch.setattr(gate_module, "_run_attempt", lambda *args: {"pass": False,
                        "outcome": "O-GOAL-FAIL", "decision_points": {"total": 1, "failed": []}})
    task.metadata["difficulty"]["route"] = {"passed": False}
    with pytest.raises(ValueError, match="E_ROUTE_REQUIRED"):
        gate_module.gate("XTR-1", "expert")
    task.metadata["difficulty"]["route"] = {"passed": True}
    gate_module.gate("XTR-1", "frontier", write=True)
    path = Path(task.source_path)
    path.write_text(path.read_text().replace("synthetic request", "changed request"))
    task.metadata = yaml.safe_load(path.read_text())["metadata"]
    with pytest.raises(ValueError, match="E_GATE_STALE"):
        gate_module.gate("XTR-1", "confirm")


def test_reference_passes_sixteen_dollar_episode_cap(monkeypatch, tmp_path):
    task = _task(tmp_path)
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    seen = []
    monkeypatch.setattr(gate_module, "_run_reference", lambda task, cap_usd:
                        seen.append(cap_usd) or {"task": task.id, "model": "cc-sonnet",
                                                  "k": 2, "completed": 2, "graded_episodes": 2,
                                                  "passes": 0, "run_id": "synthetic", "spent_usd": 0})
    result = gate_module.gate("XTR-1", "reference")
    assert seen == [16] and result["k"] == 2


def test_reference_uses_subscription_sonnet_and_usage_stop_has_no_retry(monkeypatch, tmp_path):
    task = _task(tmp_path)
    monkeypatch.setattr(gate_module, "_load_task", lambda task_id: task)
    seen = []
    monkeypatch.setattr(gate_module, "refsolve", lambda selected, row, **kwargs:
        seen.append((row["id"], kwargs["cap_usd"], kwargs["per_episode"])) or
        {"model": "cc-sonnet", "k": 2, "graded_episodes": 0, "passes": 0,
         "ungraded_code": "E_USAGE_STOP"})
    result = gate_module.gate("XTR-1", "reference")
    assert seen == [("cc-sonnet", 16, True)]
    assert result["status"] == "UNGRADED" and result["error_code"] == "E_USAGE_STOP"
    original_gate = gate_module.gate
    monkeypatch.setattr(gate_module, "gate", lambda *args, **kwargs: result)
    assert gate_module.main(["--task", "XTR-1", "--stage", "reference"]) == 1
    monkeypatch.setattr(gate_module, "gate", original_gate)
    calls = []
    def stopped(*args):
        calls.append(1)
        return {"outcome": "O-ERROR", "error": {"code": "E_USAGE_STOP"}, "graded": False}
    monkeypatch.setattr(gate_module, "_run_attempt", stopped)
    with pytest.raises(ValueError, match="^E_USAGE_STOP$"):
        gate_module.gate("XTR-1", "frontier")
    assert calls == [1]


def test_ptu_cli_dispatches_gate_arguments(monkeypatch, capsys):
    from ptu.harness import cli

    called = []
    monkeypatch.setattr(gate_module, "gate", lambda task_id, stage, *, write:
                        called.append((task_id, stage, write)) or {"k": 1, "passes": 0})
    assert cli.main(["gate", "--task", "XTR-1", "--stage", "frontier", "--write"]) == 0
    assert called == [("XTR-1", "frontier", True)]
    assert '"passes": 0' in capsys.readouterr().out
