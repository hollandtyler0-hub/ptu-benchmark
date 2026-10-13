"""Frozen decision tables agree with an independent task oracle."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ptu.tasks.decisions import generate, load_decisions
from ptu.tasks.loader import load_task


FIXTURE = Path(__file__).parents[1] / "fixtures/extreme_min/task.yaml"


def test_oracle_plugin_matches_each_frozen_decision(tmp_path):
    data = yaml.safe_load(FIXTURE.read_text())
    data["decision_points"]["oracle"] = "oracle.py"
    (tmp_path / "task.yaml").write_text(yaml.safe_dump(data))
    points = [{"id": "DP-1", "item": "notes:note/n1", "kind": "act", "rules": ["R1"],
               "evidence": ["notes:note/n1"], "expect": {"eligible": True},
               "graded_by": ["g1"]}]
    (tmp_path / "decisions.json").write_text(json.dumps(points))
    (tmp_path / "oracle.py").write_text("def generate(task, seed_state):\n"
                                        "    return {'DP-1': {'eligible': seed_state['eligible']}}\n")
    task = load_task(tmp_path / "task.yaml")
    assert load_decisions(task) == points
    assert generate(task, {"eligible": True})["mismatched"] == []
    assert generate(task, {"eligible": False})["mismatched"] == ["DP-1"]
    with pytest.raises(ValueError, match="^E_DECISIONS_ORACLE$"):
        generate(task, {})


def test_decision_path_must_remain_inside_task(tmp_path):
    (tmp_path / "task.yaml").write_bytes(FIXTURE.read_bytes())
    task = load_task(tmp_path / "task.yaml")
    task = replace(task, decision_points={"from": "../outside.json", "cross_cutting": []})
    with pytest.raises(ValueError, match="^E_DECISIONS_PATH$"):
        load_decisions(task)
