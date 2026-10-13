"""Executable Slack citation and coverage contract."""

from copy import deepcopy

from ptu.apps.chat.app import APP
from ptu.apps.fidelity import coverage, load_fidelity, run_case


def test_chat_fidelity_corpus_and_cases():
    document = load_fidelity("chat")
    assert len(document["behaviours"]) >= 15
    assert sum(row["status"] == "verified" for row in document["behaviours"]) >= 15
    assert all(row["status"] != "contradicted" for row in document["behaviours"])
    assert coverage(APP)["errors"] == []
    cases = [case for row in document["behaviours"] for case in row["cases"]]
    assert len(cases) >= 50
    assert all(run_case(APP, case)["ok"] for case in cases)


def test_unread_case_requires_new_messages():
    row = next(row for row in load_fidelity("chat")["behaviours"] if row["id"] == "CHAT-25")
    case = deepcopy(row["cases"][1])
    assert run_case(APP, case)["ok"]
    case["given"]["events"] = []
    assert "E_FID_UNREAD" in run_case(APP, case)["diff"]


def test_unread_quote_uses_verbatim_sidebar_wording():
    row = next(row for row in load_fidelity("chat")["behaviours"] if row["id"] == "CHAT-25")
    assert "will appear bold in your sidebar" in row["source"]["quote"]
