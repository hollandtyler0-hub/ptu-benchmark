"""Synthetic retry-incident facts and independent decision coverage."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from ptu.workspaces.tidewater.extreme import generate


TASK = Path(__file__).resolve().parents[2] / "tasks/extreme/PTU-TDW-XTR-222"


def _seed():
    data = yaml.safe_load((TASK / "task.yaml").read_text())
    task = SimpleNamespace(**data, source_path=str(TASK / "task.yaml"))
    return task, generate(task.seed, task, {})


def test_retry_incident_has_sixty_distinct_candidates_and_reconciled_seed():
    _, seed = _seed()
    tickets = [row for ident, row in seed["crm"]["tickets"].items()
               if ident.startswith("T-23") and ident != "T-2300"]
    assert len(tickets) == 60
    assert len({row["payment"] or row["invoice"] for row in tickets}) == 60
    assert sum(bool(row["invoice"]) for row in tickets) == 10
    assert sum(bool(row["payment"]) for row in tickets) == 50
    assert all(row["incident"] == "INC-2300" for row in tickets)


def test_retry_incident_hard_tier_scope_and_escalation_definition():
    task, _ = _seed()
    scopes = {row["pattern"]: row["access"] for row in task.policy["resource_scopes"]}
    assert scopes["crm:company/*"] == "read"

    rules = {row["id"]: row["text"] for row in task.policy["rules"]}
    assert "control.escalate" in rules["R9"]
    assert "approvals are not escalations" in rules["R9"]
    assert task.metadata["difficulty_target"] == "hard"
    assert task.metadata["difficulty"]["tier"] == "hard"


def test_retry_incident_oracle_agrees_with_frozen_hand_table():
    task, seed = _seed()
    spec = importlib.util.spec_from_file_location("incident_oracle", TASK / "oracle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hand = json.loads((TASK / "decisions.json").read_text())
    decisions = module.generate(task, seed)
    expected = {row["id"]: row["expect"] for row in hand["decision_points"]}
    expected.update({row["id"]: row.get("expect", {}) for row in task.decision_points["cross_cutting"]})
    assert decisions == expected
    assert len(decisions) == 64

    task.decision_points["cross_cutting"][0]["expect"] = {"corrupt": True}
    assert module.generate(task, seed) == expected
