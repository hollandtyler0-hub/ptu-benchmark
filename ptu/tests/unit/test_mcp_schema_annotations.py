from pathlib import Path

from ptu.common.policy import load_policy
from ptu.harness.mcp_client import _annotation_for, registry_for
from ptu.tasks.loader import load_task

TASKS = Path(__file__).resolve().parents[2] / "tasks"


def test_union_type_uses_first_non_null_member():
    assert _annotation_for({"type": ["string", "null"]}) is str
    assert _annotation_for({"type": ["null", "integer"]}) is int
    assert _annotation_for({"type": ["null"]}) is str
    assert _annotation_for({"type": "array"}) is list
    assert _annotation_for({}) is str


def test_every_exposed_tool_argument_has_a_valid_annotation():
    # Regression: harnessed runs on hard tasks failed to start the MCP server
    # because linear.update_issue declares assignee as ["string", "null"].
    checked = 0
    for path in sorted(TASKS.glob("*/PTU-TDW-*.yaml")):
        task = load_task(path)
        registry = registry_for(task)
        for spec in registry.tools_for(task.services, task.tool_exposure, load_policy(task.policy)):
            for field in spec.input_schema.get("properties", {}).values():
                assert _annotation_for(field) in (str, int, bool, list, dict)
                checked += 1
    assert checked > 0


def test_harnessed_schemas_have_no_top_level_combinators():
    # Regression: Claude Code silently dropped linear.list_issues and
    # linear.update_issue (top-level anyOf), so the isolation assertion failed.
    from ptu.harness.mcp_client import _converse_schema
    for path in sorted(TASKS.glob("*/PTU-TDW-*.yaml")):
        task = load_task(path)
        registry = registry_for(task)
        for spec in registry.tools_for(task.services, task.tool_exposure, load_policy(task.policy)):
            lowered = _converse_schema(spec.input_schema)
            assert not {"anyOf", "oneOf", "allOf"} & set(lowered), spec.name
            assert lowered.get("type") == "object", spec.name
