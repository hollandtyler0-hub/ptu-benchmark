from copy import deepcopy
from pathlib import Path

import pytest

import ptu.apps
from ptu.apps import load_apps
from ptu.apps.base import Actor
from ptu.apps import fidelity


FIXTURE_APPS = Path(__file__).parents[1] / "fixtures" / "apps"


@pytest.fixture(autouse=True)
def notes_on_app_path(monkeypatch):
    monkeypatch.setattr(ptu.apps, "__path__", [*ptu.apps.__path__, str(FIXTURE_APPS)])


def test_notes_fidelity_cases_and_coverage():
    app = load_apps(["notes"])["notes"]
    document = fidelity.load_fidelity("notes")
    assert document["fidelity_version"] == app.fidelity_version
    assert fidelity.coverage(app)["errors"] == []
    assert all(spec.ui_path in spec.description
               for spec in app.tool_specs(Actor("agent:fidelity", None, "editor"), {}))
    cases = [case for behaviour in document["behaviours"] for case in behaviour["cases"]]
    assert len(cases) == 10
    assert all(fidelity.run_case(app, case) == {"ok": True, "diff": []} for case in cases)


def test_coverage_reports_uncovered_absent_role_and_dialog(monkeypatch):
    app = load_apps(["notes"])["notes"]
    source = fidelity.load_fidelity("notes")
    changed = deepcopy(source)
    changed["behaviours"] = [row for row in changed["behaviours"] if "notes.get_note" not in row["tools"]]
    monkeypatch.setattr(fidelity, "load_fidelity", lambda app_id: changed)
    assert "E_FID_TOOL_UNCOVERED" in fidelity.coverage(app)["errors"]
    changed["surfaces"][0]["status"] = "absent"
    assert "E_FID_ABSENT_SURFACE_TOOL" in fidelity.coverage(app)["errors"]
    changed["behaviours"][0]["cases"] = []
    assert "E_FID_ROLE_MATRIX" in fidelity.coverage(app)["errors"]
    changed["behaviours"] = [row for row in changed["behaviours"] if row["category"] != "DIALOG"]
    assert "E_FID_DIALOG" in fidelity.coverage(app)["errors"]


def test_quote_is_required_by_schema():
    document = fidelity.load_fidelity("notes")
    document["behaviours"][0]["source"].pop("quote")
    with pytest.raises(ValueError, match="^E_FID_SCHEMA "):
        fidelity.validate_fidelity(document)


def test_fidelity_cli_report(capsys):
    assert fidelity.main(["--app", "notes", "--report"]) == 0
    assert '"failed": 0' in capsys.readouterr().out
