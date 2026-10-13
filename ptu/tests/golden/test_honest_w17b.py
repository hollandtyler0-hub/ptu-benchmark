"""Exercise the audit reports that use one wire-name entry per write call."""

from pathlib import Path

import pytest

from ptu.checker.golden_runner import run_golden
from ptu.tasks.loader import iter_tasks


GOLDENS = Path(__file__).resolve().parents[2] / "tasks" / "golden"
TASKS = list(iter_tasks("demo"))


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_honest_report_passes(task):
    assert run_golden(task, GOLDENS / f"{task.id}.honest.json").passed
