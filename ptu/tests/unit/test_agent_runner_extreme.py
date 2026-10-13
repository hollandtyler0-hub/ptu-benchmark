"""Long episode contracts without a provider or subscription call."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import ptu.harness.agent_runner as agent_runner

from ptu.harness.agent_runner import (HarnessProfile, _prepare_episode, episode_limits,
                                      load_profile, parse_stream, run_harness_episode)
from ptu.harness.mcp_client import registry_for
from ptu.common.policy import load_policy
from ptu.common.gateway import runtime_policy_dict
from ptu.common.registry import Registry
from ptu.harness.classify import packets
from ptu.tasks.loader import load_task
from ptu.sandbox.runner import available
from ptu.sandbox.teardown import remove_episode


def test_extreme_profiles_and_task_bounded_limits():
    expert = load_profile("cc-opus-5-5-expert")
    assert expert.id == "cc-opus-5-5-expert"
    assert expert.expert and expert.effort == "max" and expert.max_turns == 1200
    assert load_profile("cc-fable-5-1-max").budget_usd == 80
    with pytest.raises(ValueError, match="E_HARNESS_PROFILE"):
        load_profile("missing")
    task = SimpleNamespace(limits={"max_model_turns": 900, "wall_clock_seconds": 14400},
                           metadata={"difficulty": {"horizon": {"expert_calls": 214}}})
    assert episode_limits(task, expert) == {"max_turns": 672, "budget_usd": 60,
                                             "wall_clock_seconds": 14400}
    task.limits["max_model_turns"] = 500
    assert episode_limits(task, expert)["max_turns"] == 500


def test_expert_prompt_changes_only_by_fixed_paragraph(tmp_path):
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    normal = HarnessProfile("claude-code", "opus", "max", "mcp_only", "append", 12, 2)
    expert = HarnessProfile("claude-code", "opus", "max", "mcp_only", "append", 12, 2,
                            id="cc-opus-5-5-expert", expert=True)
    default_dir, expert_dir = tmp_path / "default", tmp_path / "expert"
    default_dir.mkdir()
    expert_dir.mkdir()
    _prepare_episode(task, normal, default_dir)
    _prepare_episode(task, expert, expert_dir)
    default_prompt = (default_dir / "system.md").read_text()
    expert_prompt = (expert_dir / "system.md").read_text()
    assert expert_prompt.startswith(default_prompt)
    assert "written ledger" in expert_prompt[len(default_prompt):]
    assert "re-read policy changes" in expert_prompt[len(default_prompt):]
    assert (expert_dir / "private" / "task.yaml").exists()
    for name in ("ws", "remotes", "sock"):
        assert (expert_dir / name).is_dir()
    assert not (expert_dir / "ws" / "private").exists()
    config = json.loads((expert_dir / "mcp.json").read_text())
    assert config["mcpServers"]["ptu"]["timeout"] >= 120000


def test_coding_episode_materializes_real_checkout_and_ignored_credential(tmp_path, monkeypatch):
    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    rendered = tmp_path / "rendered"
    state = rendered / "state"
    state.mkdir(parents=True)
    for name, doc in {
        "codehost": {"repos": {"tidewater/billing-core": {"default_branch": "main"}}},
        "chat": {"channels": {}},
        "control": {"policy_text": "", "confirmations": [], "escalations": [], "finish": None},
    }.items():
        (state / f"{name}.json").write_text(json.dumps(doc))
    episode = tmp_path / "episode"
    episode.mkdir()
    monkeypatch.setattr(agent_runner, "render_prompt", lambda _: ("synthetic system", "synthetic user"))
    profile = HarnessProfile("claude-code", "opus", "max", "mcp_only", "append", 12, 2)
    _prepare_episode(task, profile, episode,
                     render_fn=lambda *args: (rendered, {"payments_sandbox_key": "synthetic-key"}))
    work = episode / "ws" / "work"
    assert (work / ".git").is_dir()
    assert (work / "docs" / "PRORATION.md").is_file()
    assert (work / "config" / "local.env").read_text().endswith("synthetic-key\n")
    repo_state = json.loads((episode / "seed_state" / "codehost.json").read_text())["repos"]["tidewater/billing-core"]
    assert Path(repo_state["_bare_repo"]).is_dir()


@pytest.mark.skipif(not available(), reason="E_SANDBOX_UNAVAILABLE")
def test_real_ws_exec_works_from_deep_episode_path():
    import tempfile
    from ptu.harness.mcp_client import _server

    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    home = Path.home() / ".meter-harness" / "isolation-tests"
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ptu-deep-" + "x" * 80, dir=home) as name:
        episode = Path(name)
        assert len(os.fsencode(episode)) >= 140
        _prepare_episode(task, load_profile("cc-sonnet-5"), episode)
        server = _server(episode, harness=True)
        endpoint = server._tool_manager.get_tool("ws__exec").fn
        response = endpoint(command="pwd")
        assert response["ok"]
        assert response["result"]["exit_code"] == 0


@pytest.mark.skipif(not available(), reason="E_SANDBOX_UNAVAILABLE")
def test_real_ws_origin_push_and_virtual_paths():
    import tempfile
    from ptu.harness.mcp_client import _server

    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    home = Path.home() / ".meter-harness" / "isolation-tests"
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as name:
        episode = Path(name)
        _prepare_episode(task, load_profile("cc-sonnet-5"), episode)
        server = _server(episode, harness=True)
        endpoint = server._tool_manager.get_tool("ws__exec").fn
        remote = endpoint(command="git remote -v")
        assert remote["ok"] and remote["result"]["exit_code"] == 0
        assert str(episode) not in remote["result"]["output"]
        assert "ptu://tidewater/billing-core" in remote["result"]["output"]
        config = endpoint(command="cat .git/config; cat .git/logs/HEAD")
        assert config["ok"] and config["result"]["exit_code"] == 0
        assert str(episode) not in config["result"]["output"]
        location = endpoint(command="pwd; git status 2>&1")
        assert location["ok"] and location["result"]["exit_code"] == 0
        assert str(episode) not in location["result"]["output"]
        read = server._tool_manager.get_tool("ws__read_file").fn
        assert read(path="docs/PRORATION.md")["ok"]
        missing = read(path="docs/missing.md")
        assert not missing["ok"] and missing["code"] == "E_WS_NOT_FOUND"
        pushed = endpoint(command="git checkout -b fix/smoke-route && git -c user.name=Test -c user.email=test@example.invalid commit --allow-empty -m smoke && git push -u origin HEAD")
        assert pushed["ok"] and pushed["result"]["exit_code"] == 0
        entries = list(map(json.loads, (episode / "log.jsonl").read_text().splitlines()))
        assert any(effect["accepted"] for row in entries
                   for effect in row.get("push_effects", []))
        assert any(row.get("arguments", {}).get("event") == "E1" for row in entries)


def test_preflight_marks_broken_workstation_infrastructure_before_agent(tmp_path, monkeypatch):
    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    seen = []

    class Fake:
        def __init__(self, *args, **kwargs):
            seen.append("spawn")

    def broken(*args):
        raise ValueError("E_GIT_PROXY_START")

    # This unit test injects the preflight failure. Building a real Git fixture
    # adds background Git cleanup without exercising the assertion under test.
    monkeypatch.setattr("ptu.sandbox.repo_setup.prepare_repo", lambda *args: None)
    monkeypatch.setattr(agent_runner, "_preflight_ws", broken)
    episode = tmp_path / "episode"
    try:
        record = run_harness_episode(task, load_profile("cc-sonnet-5"), episode,
                                     wall_clock_seconds=5, spawn=Fake, preflight=True)
        assert seen == []
        result = json.loads(record.result_path.read_text())
        assert result["outcome"] == "O-ERROR"
        assert result["error"]["code"] == "E_INFRA"
    finally:
        remove_episode(episode)
    assert not episode.exists()


@pytest.mark.skipif(not available(), reason="E_SANDBOX_UNAVAILABLE")
def test_app_livesmoke_fake_agent_passes_real_runner_preflight():
    import tempfile

    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-222/task.yaml")
    exposed = registry_for(task).tools_for(task.services, task.tool_exposure,
                                           load_policy(runtime_policy_dict(task.policy)))
    wires = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in exposed]
    spawned = []

    class FakeAgent:
        returncode = 0

        def __init__(self, _argv, **kwargs):
            spawned.append(True)
            kwargs["stdout"].write(json.dumps({"type": "system", "subtype": "init",
                "tools": wires, "skills": [], "slash_commands": [],
                "mcp_servers": [{"name": "ptu", "status": "connected"}]}) + "\n")
            kwargs["stdout"].write(json.dumps({"type": "result", "subtype": "success",
                "num_turns": 1, "is_error": False}) + "\n")
            kwargs["stdout"].flush()

        def wait(self, timeout=None):
            return 0

    home = Path.home() / ".meter-harness" / "agent-runner-tests"
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as directory:
        record = run_harness_episode(task, load_profile("cc-sonnet"), Path(directory) / "episode",
                                     wall_clock_seconds=10, spawn=FakeAgent, preflight=True)
        result = json.loads(record.result_path.read_text())
        assert spawned == [True]
        assert (record.path / "transcript.jsonl").is_file()
        assert result["harness"]["init_seen"] is True
        assert result["harness"]["mcp_connected"] is True
        assert isinstance(result["pass"], bool)
        assert result["outcome"] != "O-ERROR"


@pytest.mark.asyncio
async def test_extreme_task_starts_real_harness_mcp_and_lists_tools(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    episode = tmp_path / "episode"
    episode.mkdir()
    _prepare_episode(task, load_profile("cc-sonnet-5"), episode)
    assert (episode / "private" / "task.yaml").read_bytes() == Path(task.source_path).read_bytes()
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "ptu.harness.mcp_client", "--serve", str(episode), "--harness"],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(agent_runner.REPO),
             "PYTHONDONTWRITEBYTECODE": "1"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
    names = {item.name for item in tools.tools}
    assert "control__finish" in names
    assert "ws__exec" in names
    assert "gateway__unknown" not in names


@pytest.mark.parametrize("status,tools", [("failed", []), ("connected", []),
                                          ("connected", ["Bash"])])
def test_bad_init_kills_agent_before_waiting_for_result(tmp_path, status, tools):
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    events = []

    class Fake:
        returncode = None
        def __init__(self, argv, **kwargs):
            events.append("spawn")
            kwargs["stdout"].write(json.dumps({"type": "system", "subtype": "init",
                "tools": tools, "skills": [], "slash_commands": [],
                "mcp_servers": [{"name": "ptu", "status": status}]}) + "\n")
            kwargs["stdout"].flush()
        def wait(self, timeout=None):
            events.append("wait")
            if self.returncode is None:
                raise TimeoutError()
            return self.returncode
        def terminate(self):
            events.append("kill")
            self.returncode = -15

    record = run_harness_episode(task, load_profile("cc-sonnet-5"), tmp_path / "episode",
                                 wall_clock_seconds=5, spawn=Fake)
    result = json.loads(record.result_path.read_text())
    assert events.count("spawn") == 1
    assert events.index("kill") < events.index("wait") if "wait" in events else True
    assert result["outcome"] == "O-ERROR"
    assert result["error"]["code"] == "E_ISOLATION"


def test_extreme_episode_reaches_spawn_with_runtime_policy(tmp_path):
    task = load_task("ptu/tasks/extreme/PTU-TDW-XTR-201/task.yaml")
    seen = []
    class Fake:
        returncode = 0
        def __init__(self, argv, **kwargs):
            seen.append("spawn")
            kwargs["stdout"].write(json.dumps({"type": "system", "subtype": "init",
                "tools": [], "skills": [], "slash_commands": [],
                "mcp_servers": [{"name": "ptu", "status": "failed"}]}) + "\n")
            kwargs["stdout"].flush()
        def wait(self, timeout=None):
            return 0
        def terminate(self):
            seen.append("kill")
    record = run_harness_episode(task, load_profile("cc-sonnet-5"), tmp_path / "episode",
                                 wall_clock_seconds=5, spawn=Fake)
    assert seen == ["spawn", "kill"]
    assert json.loads(record.result_path.read_text())["error"]["code"] == "E_ISOLATION"


def test_rate_windows_capture_first_and_last_events():
    def event(value):
        return json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "unifiedWindows": {"five_hour": {"utilization": value},
                               "seven_day": {"utilization": value / 2}}}})
    summary = parse_stream("claude-code", [event(0.12), event(0.2), event(0.32)])
    assert summary.usage_windows == {
        "five_hour": {"first": 0.12, "last": 0.32},
        "seven_day": {"first": 0.06, "last": 0.16}}


def test_runner_does_not_retry_disconnected_startup(tmp_path):
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    policy = load_policy(task.policy)
    exposed = registry_for(task).tools_for(task.services, task.tool_exposure, policy)
    wires = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in exposed]
    attempts = []

    class Fake:
        returncode = 0

        def __init__(self, argv, **kwargs):
            attempts.append(argv)
            status = "failed" if len(attempts) < 3 else "connected"
            kwargs["stdout"].write(json.dumps({"type": "system", "subtype": "init",
                "tools": wires if status == "connected" else [], "skills": [],
                "slash_commands": [], "mcp_servers": [{"name": "ptu", "status": status}]}) + "\n")
            kwargs["stdout"].write(json.dumps({"type": "rate_limit_event", "rate_limit_info": {
                "unifiedWindows": {"five_hour": {"utilization": 0.2}}}}) + "\n")
            kwargs["stdout"].write(json.dumps({"type": "result", "subtype": "success",
                "num_turns": 1}) + "\n")

        def wait(self, timeout=None):
            return 0

    profile = HarnessProfile("claude-code", "opus", "max", "mcp_only", "append", 1200, 40)
    record = run_harness_episode(task, profile, tmp_path / "episode",
                                 wall_clock_seconds=10, spawn=Fake)
    assert len(attempts) == 1
    manifest = json.loads((record.path / "manifest.json").read_text())
    assert manifest["harness"]["startup_retries"] == 0
    assert json.loads(record.result_path.read_text())["error"]["code"] == "E_ISOLATION"
    usage = json.loads((record.path / "usage.json").read_text())
    assert usage["rate_windows"]["five_hour"] == {"first": 0.2, "last": 0.2}


def test_claude_packet_maps_decision_point_and_redacts_private_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    episode = tmp_path / "sample" / "episodes" / "XTR-1" / "cc-opus-5-5-expert" / "0"
    (episode / "private").mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({
        "pass": False, "outcome": "O-GOAL-FAIL", "goal": {"failed_assertions": [0]},
        "decision_points": {"failed": [{"dp": "DP-1", "assertions": ["g1"], "codes": []}]},
        "metrics": {"hazard_exposed": {"H1": True}}, "violations": []}))
    (episode / "private" / "task.yaml").write_text(
        "checker:\n  goal:\n    - {id: g1, dp: DP-1, assert: exists}\n"
        "hazards:\n  - {id: H1, location: {event: E1}}\n")
    (episode / "private" / "secrets.json").write_text(json.dumps({"canary": "synthetic private"}))
    (episode / "log.jsonl").write_text(json.dumps({"seq": 1, "tool": "codehost.get_pull_request",
        "arguments": {"repo": "acme/widget", "note": "synthetic private"},
        "result_status": "ok"}) + "\n")
    (episode / "transcript.jsonl").write_text("\n".join(map(json.dumps, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I should read the policy first."},
            {"type": "tool_use", "id": "t1", "name": "mcp__ptu__codehost__get_pull_request",
             "input": {"repo": "acme/widget", "note": "synthetic private"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
            "content": [{"type": "text", "text": "The policy was announced."}]}]}},
    ])) + "\n")
    output = packets("sample", "XTR-1", tmp_path / "packets")
    assert len(output) == 1
    packet = json.loads(output[0].read_text())
    assert packet["failed_assertions"][0]["dp"] == "DP-1"
    assert packet["agent_reason"] == "I should read the policy first."
    assert packet["agent_tools"][0]["arguments"]["note"] == "<secret:canary>"
    assert "The policy was announced." in packet["seen_messages"]
    assert "synthetic private" not in output[0].read_text()


def test_turn_exhaustion_is_infrastructure_below_three_times_route(tmp_path):
    base = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    task = replace(base, metadata={**base.metadata,
        "difficulty": {"horizon": {"expert_calls": 5}}})
    exposed = registry_for(task).tools_for(task.services, task.tool_exposure,
                                           load_policy(task.policy))
    wires = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in exposed]

    class Exhausted:
        returncode = 1

        def __init__(self, argv, **kwargs):
            kwargs["stdout"].write(json.dumps({"type": "system", "subtype": "init",
                "tools": wires, "skills": [], "slash_commands": [],
                "mcp_servers": [{"name": "ptu", "status": "connected"}]}) + "\n")
            kwargs["stdout"].write(json.dumps({"type": "result", "subtype": "error_max_turns",
                "num_turns": 8, "is_error": True}) + "\n")

        def wait(self, timeout=None):
            return 1

    low = HarnessProfile("claude-code", "opus", "max", "mcp_only", "append", 10, 40)
    high = replace(low, max_turns=30)
    low_result = run_harness_episode(task, low, tmp_path / "low", wall_clock_seconds=10,
                                     spawn=Exhausted)
    high_result = run_harness_episode(task, high, tmp_path / "high", wall_clock_seconds=10,
                                      spawn=Exhausted)
    assert json.loads(low_result.result_path.read_text())["outcome"] == "O-ERROR"
    high_payload = json.loads(high_result.result_path.read_text())
    assert high_payload["outcome"] != "O-ERROR" and high_payload["pass"] is False
    from ptu.harness.livesmoke import _expected_tool_hash
    assert high_payload["harness"]["init_seen"] is True
    assert high_payload["harness"]["mcp_connected"] is True
    assert high_payload["harness"]["tool_list_sha256"] == _expected_tool_hash(task)
