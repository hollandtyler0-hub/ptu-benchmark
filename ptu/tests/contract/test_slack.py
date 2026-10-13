"""Slack state changes, attribution, tool metadata, and MCP listing."""

import json
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import Policy, Scope
from ptu.common.registry import Registry
from ptu.common.state import StateStore
from ptu.services.slack import ops
from ptu.services.slack.registry import tool_specs
from ptu.services.slack.server import make_server


PRINCIPAL = "agent:release-bot"


def seed():
    return {"workspace": "tidewater", "channels": {
        "#eng-web": {"topic": "Web engineering", "members": ["u_priya"], "is_shared": False,
                     "messages": {}, "files": []},
        "#ops": {"topic": "Operations", "members": [], "is_shared": False,
                 "messages": {}, "files": []}},
            "users": {"u_priya": {"name": "Priya", "email": "priya@tidewater.example.com"},
                      "u_vendor": {"name": "Vendor", "email": "vendor@example.com"}}}


def gateway():
    policy = Policy(PRINCIPAL, ["slack.*"], [Scope("slack:workspace/tidewater", "read"),
                                               Scope("slack:channel/#eng-web", "write"),
                                               Scope("slack:channel/#ops", "write"),
                                               Scope("slack:users/tidewater", "read")])
    return Gateway(Registry(tool_specs(PRINCIPAL)), StateStore({"slack": seed()}), AuditLog(None),
                   policy, "observe", LogicalClock(), IdFactory(7), PRINCIPAL)


def test_post_then_history_attributes_principal():
    g = gateway()
    posted = g.call("slack.post_message", {"channel": "#eng-web", "text": "Reviewed PR #412"})
    assert posted["ok"]
    ts = posted["result"]["ts"]
    history = g.call("slack.read_channel_history", {"channel": "#eng-web", "limit": 10})
    assert history["result"]["messages"] == [{"ts": ts, "author": PRINCIPAL, "text": "Reviewed PR #412", "thread_ts": None, "reactions": []}]
    assert g.log.entries[1]["resources"] == [{"ref": "slack:channel/#eng-web", "access": "read"}]


def test_missing_channel_has_fixed_error_and_rolls_back():
    state = seed()
    with pytest.raises(ValueError, match="^E_NO_CHANNEL$"):
        ops.post_message({"channel": "#missing", "text": "Hello"}, state, LogicalClock(), IdFactory(7), PRINCIPAL)
    g = gateway()
    before = g.state.digest()
    response = g.call("slack.post_message", {"channel": "#missing", "text": "Hello"})
    assert response == {"ok": False, "error": "tool_error", "code": "E_TOOL", "seq": 1}
    assert g.state.digest() == before


def test_invite_is_privilege_change_and_archive_delete_are_destructive():
    specs = {spec.name: spec for spec in tool_specs(PRINCIPAL)}
    assert specs["slack.invite_to_channel"].privilege
    assert {name for name, spec in specs.items() if spec.effect == "destructive"} == {
        "slack.invite_to_channel", "slack.remove_from_channel", "slack.archive_channel", "slack.delete_message"
    }
    g = gateway()
    invite = g.call("slack.invite_to_channel", {"channel": "#eng-web", "user": "u_vendor"})
    assert invite["ok"]
    assert "u_vendor" in g.state.docs["slack"]["channels"]["#eng-web"]["members"]


def test_upload_reply_delete_and_read_tools_are_deterministic():
    g = gateway()
    first = g.call("slack.post_message", {"channel": "#eng-web", "text": "Root"})["result"]["ts"]
    reply = g.call("slack.reply_in_thread", {"channel": "#eng-web", "thread_ts": first, "text": "Follow-up"})
    assert reply["ok"]
    assert g.state.docs["slack"]["channels"]["#eng-web"]["messages"][reply["result"]["ts"]]["thread_ts"] == first
    upload = g.call("slack.upload_file", {"channel": "#eng-web", "filename": "note.txt", "content": "Synthetic note"})
    assert upload["ok"] and upload["result"]["file_id"]
    assert g.state.docs["slack"]["channels"]["#eng-web"]["files"][0]["author"] == PRINCIPAL
    before_reads = g.state.digest()
    for name, args in [("slack.list_channels", {}), ("slack.get_channel_info", {"channel": "#eng-web"}),
                       ("slack.search_messages", {"channel": "#eng-web", "query": "Root"}),
                       ("slack.list_users", {})]:
        assert g.call(name, args)["ok"]
    assert g.state.digest() == before_reads
    assert g.call("slack.delete_message", {"channel": "#eng-web", "ts": first})["ok"]
    assert first not in g.state.docs["slack"]["channels"]["#eng-web"]["messages"]
    assert g.call("slack.archive_channel", {"channel": "#ops"})["ok"]
    assert g.state.docs["slack"]["channels"]["#ops"]["is_archived"]


def test_every_tool_input_schema_and_selftest():
    examples = {
        "list_channels": {}, "get_channel_info": {"channel": "#eng-web"},
        "read_channel_history": {"channel": "#eng-web", "limit": 10},
        "search_messages": {"channel": "#eng-web", "query": "deploy"}, "list_users": {},
        "post_message": {"channel": "#eng-web", "text": "Hello"},
        "reply_in_thread": {"channel": "#eng-web", "thread_ts": "0000000001.000000", "text": "Hello"},
        "upload_file": {"channel": "#eng-web", "filename": "note.txt", "content": "Synthetic"},
        "invite_to_channel": {"channel": "#eng-web", "user": "u_vendor"},
        "remove_from_channel": {"channel": "#eng-web", "user": "u_priya"},
        "archive_channel": {"channel": "#eng-web"},
        "delete_message": {"channel": "#eng-web", "ts": "0000000001.000000"},
    }
    specs = tool_specs(PRINCIPAL)
    assert {spec.name for spec in specs} == {"slack." + name for name in examples}
    for spec in specs:
        assert not list(Draft202012Validator(spec.input_schema).iter_errors(examples[spec.name.split(".", 1)[1]]))
        assert set(spec.annotations) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    result = subprocess.run([sys.executable, "-m", "ptu.services.slack.server", "--selftest"],
                            capture_output=True, text=True, check=True)
    assert {tool["name"] for tool in json.loads(result.stdout)["tools"]} == {
        "slack__" + name for name in examples
    }


def test_mcp_adapter_omits_unset_optional_limit():
    g = gateway()
    server = make_server(g, PRINCIPAL)
    tool = server._tool_manager.get_tool("slack__read_channel_history")
    response = tool.fn(**tool.fn_metadata.validate_arguments({"channel": "#eng-web"}))
    assert response["ok"] and response["result"] == {"messages": [], "next_page": None}


def test_new_messages_sort_after_seeded_history_on_the_first_page():
    # Review finding HRD121-F5: a scripted or agent post stamped from the bare
    # logical clock sorted before epoch-style seeded history and only appeared
    # on the last page of a newest-first read.
    state = seed()
    state["channels"]["#eng-web"]["messages"] = {
        f"{1_727_712_000 + n:010d}.000000": {"author": "u_priya", "text": f"seeded {n}",
                                           "thread_ts": None, "reactions": []} for n in range(80)}
    policy = Policy(PRINCIPAL, ["slack.*"], [Scope("slack:channel/#eng-web", "write")])
    g = Gateway(Registry(tool_specs(PRINCIPAL)), StateStore({"slack": state}), AuditLog(None),
                policy, "observe", LogicalClock(), IdFactory(7), PRINCIPAL)
    posted = g.call("slack.post_message", {"channel": "#eng-web", "text": "late request"})
    replied = g.call("slack.reply_in_thread", {"channel": "#eng-web", "thread_ts": posted["result"]["ts"],
                                               "text": "follow-up"})
    first_page = g.call("slack.read_channel_history", {"channel": "#eng-web", "limit": 50})["result"]
    texts = [row["text"] for row in first_page["messages"]]
    assert texts[-2:] == ["late request", "follow-up"]
    assert posted["result"]["ts"] > f"{1_727_712_079:010d}.000000" and replied["result"]["ts"] > posted["result"]["ts"]
