"""Synthetic contracts for the hard-task sieve and classification packet boundary."""

import json
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

from ptu.harness import sieve as sieve_module
from ptu.harness.classify import packets, record_labels
from ptu.harness.cli import _parser, main
from ptu.tasks.loader import load_task


def test_sieve_counts_only_verdicts_and_respects_k(monkeypatch, tmp_path):
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    outcomes = iter([(True, "O-COMPLETE", []), (False, "O-GOAL-FAIL", ["V-TOOL-OOS"]),
                     (False, "O-ESCALATED", []), (True, "O-COMPLETE", [])])
    calls = []
    def fake_episode(task, profile, episode_dir, **kwargs):
        calls.append((profile.id, profile.effort, episode_dir))
        episode_dir.mkdir(parents=True)
        passed, outcome, codes = next(outcomes)
        path = episode_dir / "result.json"
        path.write_text(json.dumps({"pass": passed, "outcome": outcome,
                                    "violations": [{"code": code} for code in codes]}))
        (episode_dir / "usage.json").write_text('{"episode_spend_usd":0.1}')
        return SimpleNamespace(result_path=path)
    monkeypatch.setattr(sieve_module, "run_harness_episode", fake_episode)
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    result = sieve_module.sieve(task, {"id": "cc-haiku"}, 4, 3)
    assert result["k"] == 4 and result["passes"] == 2
    assert result["codes"] == {"V-TOOL-OOS": 1}
    assert len(calls) == 4 and all(item[:2] == ("cc-haiku", "medium") for item in calls)
    assert "secret" not in json.dumps(result).lower()


def test_record_sieve_preserves_every_other_yaml_field(tmp_path):
    source = Path("ptu/tasks/demo/PTU-TDW-PRR-001.yaml").read_text()
    source = source.replace("metadata:\n", "metadata:\n  difficulty:\n    tier: hard\n    levers: []\n    horizon:\n      compliant_calls: 0\n      first_hazard_decision_call: 0\n", 1)
    path = tmp_path / "task.yaml"
    path.write_text(source)
    before = yaml.safe_load(source)
    sieve_module.record_sieve(path, {"task": before["id"], "model": "cc-haiku", "k": 4,
                                     "passes": 1, "run_id": "synthetic-run", "spent_usd": 0.2})
    after = yaml.safe_load(path.read_text())
    sieve = after["metadata"]["difficulty"].pop("sieve")
    assert after == before
    assert sieve["passes"] == 1 and sieve["k"] == 4
    assert "spent_usd" not in sieve


def test_measurement_replaces_multiline_horizon_without_orphaned_rows(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("metadata:\n  difficulty:\n    tier: hard\n    horizon:\n"
                    "      compliant_calls: 40\n      first_hazard_decision_call: 25\n"
                    "    levers: [scale]\n  review: {status: draft}\n")
    sieve_module._update_difficulty(path, "horizon", {"compliant_calls": 44,
                                                       "first_hazard_decision_call": 31})
    data = yaml.safe_load(path.read_text())
    assert data["metadata"]["difficulty"]["horizon"] == {
        "compliant_calls": 44, "first_hazard_decision_call": 31}
    assert data["metadata"]["difficulty"]["levers"] == ["scale"]


def test_update_difficulty_replaces_multiline_horizon(tmp_path):
    source = Path("ptu/tasks/demo/PTU-TDW-PRR-001.yaml").read_text()
    source = source.replace("metadata:\n", "metadata:\n  difficulty:\n    tier: hard\n    levers: []\n    horizon:\n      compliant_calls: 0\n      first_hazard_decision_call: 0\n", 1)
    path = tmp_path / "task.yaml"
    path.write_text(source)
    before = yaml.safe_load(source)
    sieve_module._update_difficulty(path, "horizon", {
        "compliant_calls": 50, "first_hazard_decision_call": 30})
    after = yaml.safe_load(path.read_text())
    assert after["metadata"]["difficulty"].pop("horizon") == {
        "compliant_calls": 50, "first_hazard_decision_call": 30}
    before["metadata"]["difficulty"].pop("horizon")
    assert after == before


def test_packets_include_private_evidence_and_redact_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    episode = tmp_path / "run-a" / "episodes" / "TASK-1" / "haiku-4-5" / "0"
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({"pass": False, "outcome": "O-GOAL-FAIL",
        "goal": {"failed_assertions": [2, "outcome"]}, "audit": {"finish_called": False},
        "metrics": {"hazard_exposed": {"H1": True}},
        "violations": [{"code": "V-TOOL-OOS", "seq": 4, "detail": {"body": "synthetic secret"}}]}))
    (episode / "task.yaml").write_text(yaml.safe_dump({"checker": {"goal": [
        {"assert": "exists"}, {"assert": "missing"}, {"assert": "log_has", "tool": "slack.post_message"}]},
        "hazards": [{"id": "H1", "location": {"event": "E1"}}]}))
    (episode / "secrets.json").write_text(json.dumps({"demo": "synthetic secret"}))
    (episode / "transcript.jsonl").write_text(json.dumps({"request": {"messages": [
        {"role": "user", "content": [{"text": "synthetic seen message"}]}]},
        "response": {"output": {"message": {"content": [{"text": "synthetic reason"}]}}}}) + "\n")
    (episode / "log.jsonl").write_text(json.dumps({"seq": 4, "tool": "slack.post_message",
        "arguments": {"text": "synthetic secret"}}) + "\n")
    paths = packets("run-a", "TASK-1", tmp_path / "packets")
    assert len(paths) == 1
    packet = json.loads(paths[0].read_text())
    assert packet["tools"] == [{"seq": 4, "tool": "slack.post_message",
                                 "arguments": {"text": "<secret:demo>"}, "result_status": None}]
    assert packet["failed_assertions"] == [{"index": 2,
        "assertion": {"assert": "log_has", "tool": "slack.post_message"}},
        {"index": "outcome", "assertion": {"assert": "outcome", "value": "complete"}}]
    assert packet["seen_messages"] == ["synthetic seen message"]
    assert packet["agent_reason"] == "synthetic reason"
    assert packet["artefact_suspect"] is False
    assert packet["unexposed_location_hazards"] == []
    assert "synthetic secret" not in paths[0].read_text()
    raw = json.loads((episode / "result.json").read_text())
    raw["metrics"]["hazard_exposed"]["H1"] = False
    (episode / "result.json").write_text(json.dumps(raw))
    packets("run-a", "TASK-1", tmp_path / "packets")
    assert json.loads(paths[0].read_text())["unexposed_location_hazards"] == ["H1"]


def test_private_packets_cannot_be_written_into_repository():
    with pytest.raises(ValueError, match="^E_CLASSIFY_OUTDIR$"):
        packets("run-a", "TASK-1", Path("ptu/tasks/reviews"))


def test_packets_use_regraded_result_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    episode = tmp_path / "run-b" / "episodes" / "TASK-1" / "haiku-4-5" / "0"
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({"pass": False, "outcome": "O-GOAL-FAIL",
        "goal": {"failed_assertions": [0]}, "violations": [{"code": "V-AUDIT-STATUS-OPTIMISTIC"}]}))
    (episode / "result.regraded.json").write_text(json.dumps({"pass": False, "outcome": "O-COMPLETE",
        "goal": {"failed_assertions": []}, "violations": [{"code": "V-TOOL-OOS"}]}))
    (episode / "task.yaml").write_text(yaml.safe_dump({"checker": {"goal": [{"assert": "exists"}]}}))
    (episode / "log.jsonl").write_text("")
    paths = packets("run-b", "TASK-1", tmp_path / "packets")
    packet = json.loads(paths[0].read_text())
    assert packet["outcome"] == "O-COMPLETE"
    assert packet["failed_assertions"] == []
    assert [row["code"] for row in packet["codes"]] == ["V-TOOL-OOS"]


def test_packet_redaction_preserves_json_unicode_escapes(monkeypatch, tmp_path):
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    episode = tmp_path / "run-c" / "episodes" / "TASK-1" / "haiku-4-5" / "0"
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({"pass": False, "outcome": "O-GOAL-FAIL",
        "goal": {"failed_assertions": []}, "violations": []}))
    (episode / "task.yaml").write_text(yaml.safe_dump({"checker": {"goal": []}}))
    (episode / "log.jsonl").write_text("")
    (episode / "secrets.json").write_text(json.dumps({"demo": "00e9"}))
    (episode / "transcript.jsonl").write_text(json.dumps({"request": {"messages": [
        {"content": [{"text": "café"}]}]}}) + "\n")
    paths = packets("run-c", "TASK-1", tmp_path / "packets")
    assert json.loads(paths[0].read_text())["seen_messages"] == ["café"]


def test_record_labels_counts_principled_and_artefact(tmp_path):
    source = Path("ptu/tasks/demo/PTU-TDW-PRR-001.yaml").read_text()
    source = source.replace("metadata:\n", "metadata:\n  difficulty:\n    tier: hard\n    levers: []\n    horizon:\n      compliant_calls: 0\n      first_hazard_decision_call: 0\n", 1)
    path = tmp_path / "task.yaml"
    path.write_text(source)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"run_id": "run-a", "labels": [
        {"episode": 0, "label": "policy_miss"}, {"episode": 1, "label": "ambiguity"}]}))
    record_labels(path, labels)
    data = yaml.safe_load(path.read_text())
    assert data["metadata"]["difficulty"]["classified_failures"]["principled"] == 1
    assert data["metadata"]["difficulty"]["classified_failures"]["artefact"] == 1
    from ptu.tasks.loader import load_task
    assert load_task(path).metadata["difficulty"]["classified_failures"]["date"]


def test_cli_exposes_bounded_sieve_and_classification_flags(monkeypatch, tmp_path, capsys):
    parsed = _parser().parse_args(["sieve", "--task", "PTU-TDW-HRD-101", "--model", "cc-haiku",
                                   "--k", "4", "--max-spend-usd", "3", "--write"])
    assert (parsed.task, parsed.model, parsed.k, parsed.max_spend_usd, parsed.write) == (
        "PTU-TDW-HRD-101", "cc-haiku", 4, 3, True)
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    episode = tmp_path / "run-a" / "episodes" / "TASK-1" / "haiku-4-5" / "0"
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({"pass": False, "outcome": "O-GOAL-FAIL",
                                                       "violations": [], "goal": {}, "audit": {}, "metrics": {}}))
    (episode / "log.jsonl").write_text("{}\n")
    assert main(["classify", "run-a", "--task", "TASK-1", "--out", str(tmp_path / "packets")]) == 0
    assert json.loads(capsys.readouterr().out) == {"code": "OK", "packets": 1}
