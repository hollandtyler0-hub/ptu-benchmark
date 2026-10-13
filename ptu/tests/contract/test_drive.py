"""Drive contract over the real policy gateway and synthetic state."""

import json
from pathlib import Path

import jsonschema
import pytest

from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import load_policy
from ptu.common.registry import Registry
from ptu.common.state import StateStore
from ptu.services.drive.ops import execute
from ptu.services.drive.registry import external_check, refs_for_path, tool_specs


FIXTURE = Path(__file__).parents[1] / "fixtures/drive_min.json"


def state():
    return json.loads(FIXTURE.read_text())


def gateway(seed=None):
    policy = load_policy({"principal": "agent:test", "allowed_tools": ["drive.*"],
                          "resource_scopes": [{"pattern": "drive:folder/d-root", "access": "write"},
                                              {"pattern": "drive:folder/d-finance", "access": "write"}]})
    return Gateway(Registry(tool_specs("agent:test")), StateStore({"drive": seed or state()}), AuditLog(None),
                   policy, "observe", LogicalClock(), IdFactory(7), "agent:test")


CASES = {
    "list_folder": {"folder_id": "d-root", "page": 1},
    "get_file": {"file_id": "f-note"},
    "search_files": {"query": "Note", "folder_id": "d-root"},
    "get_permissions": {"file_id": "f-note"},
    "create_file": {"folder_id": "d-root", "name": "New.txt", "content": "Synthetic"},
    "update_file": {"file_id": "f-note", "content": "Updated"},
    "append_to_file": {"file_id": "f-note", "text": " more"},
    "comment_on_file": {"file_id": "f-note", "body": "Reviewed"},
    "move_file": {"file_id": "f-note", "folder_id": "d-finance"},
    "share_file": {"file_id": "f-note", "subject": "sam@vendorsync.example.net", "role": "viewer"},
    "set_link_sharing": {"file_id": "f-note", "mode": "anyone"},
    "unshare_file": {"file_id": "f-note", "subject": "dev@tidewater.example.com"},
    "trash_file": {"file_id": "f-note"},
}


@pytest.mark.parametrize("name,args", CASES.items())
def test_drive_contract_schema_effect_and_refs(name, args):
    spec = Registry(tool_specs()).get(f"drive.{name}")
    jsonschema.validate(args, spec.input_schema)
    gw = gateway()
    before = gw.state.digest()
    result = gw.call(f"drive.{name}", args)
    assert result["ok"] is True
    entry = gw.log.entries[-1]
    assert entry["effect"] == spec.effect
    assert entry["resources"]
    if spec.effect == "read":
        assert gw.state.digest() == before
    else:
        assert gw.state.digest() != before
        assert any(use["access"] == "write" for use in entry["resources"])


def test_drive_pagination_attributes_only_returned_files():
    seed = state()
    for number in range(30):
        file_id = f"f-{number:02d}"
        seed["folders"]["d-root"]["files"].append(file_id)
        seed["files"][file_id] = {**seed["files"]["f-note"], "name": file_id}
    gw = gateway(seed)
    first = gw.call("drive.list_folder", {"folder_id": "d-root", "page": 1})
    second = gw.call("drive.list_folder", {"folder_id": "d-root", "page": 2})
    assert len(first["result"]["files"]) == 25 and first["result"]["next_page"] == 2
    assert len(second["result"]["files"]) == 7 and second["result"]["next_page"] is None
    refs = {use["ref"] for use in gw.log.entries[-1]["resources"]}
    assert "drive:file/f-29" in refs and "drive:file/f-00" not in refs


def test_folder_read_grant_permits_contained_file_reads_without_findings():
    gw = gateway()
    assert gw.call("drive.list_folder", {"folder_id": "d-root"})["ok"]
    assert gw.log.entries[-1]["policy_verdict"]["allowed"]
    assert gw.call("drive.get_file", {"file_id": "f-note"})["ok"]
    assert gw.log.entries[-1]["policy_verdict"]["allowed"]


def test_external_share_and_link_sharing_are_visible_at_call_time():
    gw = gateway()
    ref = "drive:share/f-note/sam@vendorsync.example.net"
    assert external_check(ref, gw.state.docs, "tidewater.example.com")
    assert not external_check("drive:file/f-note", gw.state.docs, "tidewater.example.com")
    assert gw.call("drive.share_file", CASES["share_file"])["ok"]
    assert external_check("drive:file/f-note", gw.state.docs, "tidewater.example.com")
    gw = gateway()
    assert gw.call("drive.set_link_sharing", CASES["set_link_sharing"])["ok"]
    assert external_check("drive:file/f-note", gw.state.docs, "tidewater.example.com")


def test_drive_ref_mapper_and_errors_are_fixed():
    assert refs_for_path(["files", "f-note", "content"]) == "drive:file/f-note"
    assert refs_for_path(["folders", "d-root", "files"]) == "drive:folder/d-root"
    with pytest.raises(ValueError, match="^E_NO_FILE$"):
        execute("get_file", {"file_id": "missing"}, state(), LogicalClock(), IdFactory(7))
