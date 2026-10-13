"""Probe-only expansion must preserve accepted demo and hard renders byte for byte."""

import csv
import json
from dataclasses import replace
from hashlib import sha256
from io import StringIO
from pathlib import Path

import yaml

from ptu.tasks.loader import LEGACY_VALIDATOR, load_task
from ptu.workspaces.build import render


TASK_ROOT = Path(__file__).resolve().parents[2] / "tasks"
BASELINES = {
    "demo/PTU-TDW-PRR-001.yaml": "7075ed8281dd8482ef61f88c65d4a6791cf936dbfcd99436a0357ca73644c7d1",
    "hard/PTU-TDW-HRD-101.yaml": "22d3a41978e8efd400ee8e45ff3f0f3187b9e9609bd31a08877eb00963f2f468",
    "hard/PTU-TDW-HRD-141.yaml": "923a278865b3d86225a3595d6ce07186ebaa9a9389be293bbff75ef543137049",
}


def _state(task, path):
    root, _ = render(task.workspace, task.seed, task, out=path)
    return {file.stem: json.loads(file.read_text()) for file in (root / "state").glob("*.json")}


def test_legacy_probe_split_is_additive():
    data = yaml.safe_load((TASK_ROOT / "hard/PTU-TDW-HRD-101.yaml").read_text())
    data["id"] = "PTU-TDW-PRB-002"
    data["split"] = "probe"
    assert LEGACY_VALIDATOR.is_valid(data)
    data["split"] = "unknown"
    assert not LEGACY_VALIDATOR.is_valid(data)


def test_probe_world_has_required_volumes(tmp_path):
    refund = load_task(TASK_ROOT / "hard/PTU-TDW-HRD-141.yaml")
    refund = replace(refund, id="PTU-TDW-PRB-001", split="probe",
                     hazards=[row for row in refund.hazards
                              if row.get("location", {}).get("ref") != "drive:file/f-rr09"])
    state = _state(refund, tmp_path / "refund")
    rows = list(csv.DictReader(StringIO(state["drive"]["files"]["f-rr09"]["content"])))
    assert len(rows) == 36
    assert len({row["request_id"] for row in rows}) == 36
    assert "f-refunds-amend" in state["drive"]["files"]
    extra = rows[9:]
    assert len({row["customer_id"] for row in extra}) == 9
    assert all(sum(other["customer_id"] == row["customer_id"] for other in extra) == 3
               for row in extra)
    assert all(state["billing"]["invoices"][row["invoice_id"]]["customer"] == row["customer_id"]
               for row in extra)
    assert all(row["requested_at"] == "2026-10-01" for row in extra)

    triage = load_task(TASK_ROOT / "hard/PTU-TDW-HRD-101.yaml")
    triage = replace(triage, id="PTU-TDW-PRB-002", split="probe",
                     metadata={**triage.metadata, "world": {"scale": "large"}})
    state = _state(triage, tmp_path / "triage")
    candidates = [issue for issue in state["github"]["repos"]["tidewater/web"]["issues"].values()
                  if "needs-triage" in issue["labels"]]
    assert len(candidates) == 30
    assert len([issue for issue in candidates if issue["number"] >= 113]) == 18


def test_demo_and_hard_renders_remain_byte_identical(tmp_path):
    for index, (name, expected) in enumerate(BASELINES.items()):
        task = load_task(TASK_ROOT / name)
        root, _ = render(task.workspace, task.seed, task, out=tmp_path / str(index))
        digest = sha256()
        for file in sorted((root / "state").glob("*.json")):
            digest.update(file.name.encode() + b"\0" + file.read_bytes())
        assert digest.hexdigest() == expected
