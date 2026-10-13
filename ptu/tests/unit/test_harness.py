"""W12 contracts use synthetic state and a fake Converse client only."""

import asyncio
import json

import pytest

from ptu.common.log import AuditLog
from ptu.common.registry import Registry
from ptu.services.control.registry import tool_specs as control_specs


class FakeConverse:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.replies)


def reply(blocks, stop="tool_use", usage=None):
    return {"output": {"message": {"role": "assistant", "content": blocks}},
            "stopReason": stop, "usage": usage or {"inputTokens": 10, "outputTokens": 5}}


def test_converse_returns_all_results_in_one_message_and_finishes(tmp_path):
    from ptu.harness.bedrock_runner import run_loop
    from ptu.harness.mcp_client import InProcessTools
    from ptu.harness.budget import SpendGuard, PriceTable

    class Gateway:
        closed = False
        def __init__(self):
            self.calls = []
        def call(self, name, args):
            self.calls.append((name, args))
            if name == "control.finish":
                self.closed = True
            return {"ok": name != "github_get_repo", "error": "unknown_tool"}

    gateway = Gateway()
    fake = FakeConverse([
        reply([{"toolUse": {"toolUseId": "a", "name": "github_get_repo", "input": {}}},
               {"toolUse": {"toolUseId": "b", "name": "control__get_policy", "input": {}}}]),
        reply([{"toolUse": {"toolUseId": "c", "name": "control__finish", "input":
                            {"status": "complete", "summary": "done", "changes": [], "blocked_on": []}}}]),
    ])
    row = {"id": "haiku-4-5", "profile": "fake", "vendor": "anthropic", "thinking": "none",
           "cache_points": True, "echo_reasoning": True}
    tools = InProcessTools(gateway, Registry(control_specs({})))
    result = asyncio.run(run_loop(fake, row, tools, ["system"], "task", SpendGuard(1, PriceTable()),
                                  {"max_model_turns": 4, "max_tool_calls": 4}, tmp_path / "transcript.jsonl"))
    assert result["model_turns"] == 2
    assert len([block for block in fake.calls[1]["messages"][-1]["content"] if "toolResult" in block]) == 2
    assert fake.calls[1]["messages"][-1]["content"][0]["toolResult"]["status"] == "error"
    assert gateway.calls[0][0] == "github_get_repo"
    assert gateway.calls[-1][0] == "control.finish"
    assert all(len(call["messages"]) <= 4 for call in fake.calls)


def test_m9_single_finish_nudge_after_end_turn(tmp_path):
    from ptu.harness.bedrock_runner import run_loop
    from ptu.harness.budget import SpendGuard

    class Tools:
        closed = False
        async def call(self, name, arguments):
            if name == "control__finish":
                self.closed = True
            return {"ok": True}

    fake = FakeConverse([
        reply([{"text": "I have completed the requested work."}], "end_turn"),
        reply([{"toolUse": {"toolUseId": "f", "name": "control__finish", "input": {}}}]),
    ])
    row = {"id": "haiku-4-5", "profile": "fake", "thinking": "none",
           "cache_points": False, "echo_reasoning": True}
    outcome = asyncio.run(run_loop(fake, row, Tools(), ["system"], "task", SpendGuard(1),
                                   {"max_model_turns": 3, "max_tool_calls": 3},
                                   tmp_path / "transcript.jsonl", nudge_finish=True))
    assert outcome["finish_nudges"] == 1
    assert outcome["model_turns"] == 2
    assert any("control.finish" in block.get("text", "") for message in fake.calls[1]["messages"]
               if message["role"] == "user" for block in message["content"])


def test_guard_preflight_aborts_before_over_cap_call():
    from ptu.harness.budget import BudgetError, PriceTable, SpendGuard
    row = {"id": "gpt-6-sol"}
    guard = SpendGuard(0.00005, PriceTable())
    with pytest.raises(BudgetError) as error:
        guard.reserve(row, max_tokens=100, input_tokens_estimate=0)
    assert error.value.code == "E_BUDGET"
    assert guard.spent_usd == 0
    guard = SpendGuard(0.01, PriceTable())
    guard.reserve(row, max_tokens=100, input_tokens_estimate=100)
    guard.charge({"inputTokens": 100, "outputTokens": 50}, row)
    assert guard.spent_usd > 0


def test_estimate_uses_measured_token_profile_and_opus_proxy():
    from ptu.harness.budget import PriceTable
    table = PriceTable()
    haiku = table.estimate({"id": "haiku-4-5", "cache_points": True}, 320)
    opus = table.estimate({"id": "opus-4-6", "cache_points": True}, 160)
    assert haiku["basis"] == "measured:haiku-4-5"
    assert opus["basis"] == "measured_proxy:sonnet-4-6"
    assert haiku["cached_usd"] == pytest.approx(
        table.data["measured"]["haiku-4-5"]["mean_episode_cost_usd"] * 320, rel=0.001)
    assert opus["cached_usd"] < 30


def test_run_store_resume_skips_finished(tmp_path):
    from ptu.harness.run_store import RunStore
    store = RunStore(tmp_path, "sample")
    path = store.episode_dir("PTU-ONE", "haiku-4-5", 0)
    assert not store.finished("PTU-ONE", "haiku-4-5", 0)
    path.mkdir(parents=True)
    (path / "result.json").write_text('{"pass":true}')
    (path / "usage.json").write_text('{"episode_spend_usd":0.25}')
    assert store.finished("PTU-ONE", "haiku-4-5", 0)
    assert store.spent_usd() == 0.25
    (path / "result.json").write_text('{"error":{"code":"E_BUDGET"}}')
    assert not store.finished("PTU-ONE", "haiku-4-5", 0)


def test_episode_uses_render_contract_and_writes_artifacts(tmp_path):
    from ptu.harness.episode import run_episode
    from ptu.tasks.loader import load_task
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    seed = tmp_path / "rendered"
    (seed / "state").mkdir(parents=True)
    for service in ("github", "slack", "linear"):
        (seed / "state" / f"{service}.json").write_text("{}")
    (seed / "state" / "control.json").write_text(json.dumps({"policy_text": "policy", "confirmations": [],
                                                                "escalations": [], "finish": None}))
    fake = FakeConverse([reply([{"toolUse": {"toolUseId": "f", "name": "control__finish", "input":
                                                   {"status": "complete", "summary": "done", "changes": [],
                                                    "blocked_on": []}}}])])
    row = {"id": "haiku-4-5", "profile": "fake", "vendor": "anthropic", "thinking": "none",
           "cache_points": True, "echo_reasoning": True}
    record = run_episode(task, row, tmp_path / "run", "inprocess", converse_client=fake,
                         render_fn=lambda workspace, seed_number, task_obj: (seed, {}))
    assert record.result_path.exists()
    assert (tmp_path / "run" / "usage.json").exists()
    assert json.loads((tmp_path / "run" / "usage.json").read_text())["inputTokens"] == 10
    assert (tmp_path / "run" / "control.json").exists()
    assert (tmp_path / "run" / "final_state" / "control.json").exists()
    assert AuditLog(tmp_path / "run" / "log.jsonl").entries[-1]["tool"] == "control.finish"
    assert fake.calls[0]["modelId"] == "fake"


def test_probe_never_prints_credentials(monkeypatch, capsys):
    from ptu.harness.bedrock_runner import probe_models
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "FAKE-SECRET-FOR-TEST")
    rows = [{"id": "haiku-4-5", "profile": "fake"}]
    result = probe_models(rows, FakeConverse([reply([{"text": "ok"}], "end_turn")]))
    print(json.dumps(result))
    assert result["haiku-4-5"]["status"] == "available"
    assert "FAKE-SECRET-FOR-TEST" not in capsys.readouterr().out


def test_cli_prints_redacted_lines(capsys):
    from ptu.harness.cli import safe_print
    secret = "ghp_" + "A" * 36
    safe_print({"message": secret}, {"test": secret})
    assert secret not in capsys.readouterr().out


def test_tool_specs_use_wire_names():
    from ptu.harness.mcp_client import tool_specs
    result = tool_specs(Registry(control_specs({})))
    assert any(row["toolSpec"]["name"] == "control__finish" for row in result)
    assert all("json" in row["toolSpec"]["inputSchema"] for row in result)


def test_stdio_unknown_name_is_logged_by_gateway(tmp_path):
    from ptu.harness.episode import run_episode
    from ptu.tasks.loader import load_task
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    seed = tmp_path / "rendered"
    (seed / "state").mkdir(parents=True)
    for service in ("github", "slack", "linear"):
        (seed / "state" / f"{service}.json").write_text("{}")
    (seed / "state" / "control.json").write_text(json.dumps({"policy_text": "policy", "confirmations": [],
                                                                "escalations": [], "finish": None}))
    fake = FakeConverse([
        reply([{"toolUse": {"toolUseId": "u", "name": "github__not_a_tool", "input": {}}}]),
        reply([{"toolUse": {"toolUseId": "f", "name": "control__finish", "input":
                            {"status": "complete", "summary": "done", "changes": [], "blocked_on": []}}}]),
    ])
    row = {"id": "haiku-4-5", "profile": "fake", "vendor": "anthropic", "thinking": "none",
           "cache_points": True, "echo_reasoning": True}
    run_dir = tmp_path / "stdio"
    run_episode(task, row, run_dir, "stdio", converse_client=fake,
                render_fn=lambda workspace, seed_number, task_obj: (seed, {}))
    entries = AuditLog(run_dir / "log.jsonl").entries
    assert entries[0]["kind"] == "genesis"
    assert [entry["tool"] for entry in entries if entry.get("kind") != "genesis"] == ["github.not_a_tool", "control.finish"]
    result = json.loads((run_dir / "result.json").read_text())
    assert "V-LOG-INTEGRITY" not in {finding["code"] for finding in result["violations"]}
    assert fake.calls[1]["messages"][-1]["content"][0]["toolResult"]["status"] == "error"
    assert json.loads((run_dir / "final_state" / "control.json").read_text())["finish"] is not None


@pytest.mark.parametrize(("provider_code", "expected"), [
    ("AccessDeniedException", "E_ACCESS"),
    ("ThrottlingException", "E_THROTTLE"),
    ("ValidationException", "E_MODEL"),
])
def test_probe_maps_blocked_without_raw_exception_text(provider_code, expected):
    from ptu.harness.bedrock_runner import probe_models

    class Denied:
        def converse(self, **kwargs):
            error = RuntimeError("fake secret should not surface")
            error.response = {"Error": {"Code": provider_code},
                              "ResponseMetadata": {"HTTPStatusCode": 403}}
            raise error

    result = probe_models([{"id": "haiku-4-5", "profile": "fake"}], Denied())
    assert result["haiku-4-5"] == {"status": "blocked", "code": expected, "model_id": "haiku-4-5",
                                     "task_id_sha256": "", "http_status": 403}


def test_run_loop_retries_transient_throttle_without_an_error_episode(tmp_path, monkeypatch):
    from ptu.harness import bedrock_runner
    from ptu.harness.budget import SpendGuard
    waits = []
    monkeypatch.setattr(bedrock_runner.time, "sleep", waits.append)

    class Throttle(Exception):
        response = {"Error": {"Code": "ThrottlingException"},
                    "ResponseMetadata": {"HTTPStatusCode": 429}}

    class Client:
        calls = 0
        def converse(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise Throttle()
            return reply([{"text": "done"}], "end_turn")

    client = Client()
    row = {"id": "haiku-4-5", "profile": "fake", "thinking": "none", "cache_points": False}
    result = asyncio.run(bedrock_runner.run_loop(client, row, None, ["system"], "task",
                                                 SpendGuard(1), {"max_model_turns": 1},
                                                 tmp_path / "transcript.jsonl"))
    assert client.calls == 3
    assert len(waits) == 2 and 1 <= waits[0] <= 3 and 2 <= waits[1] <= 6
    assert result["model_turns"] == 1


def test_retry_after_is_honoured_and_throttle_budget_is_bounded(tmp_path, monkeypatch):
    from ptu.harness import bedrock_runner
    from ptu.harness.budget import SpendGuard
    waits = []
    monkeypatch.setattr(bedrock_runner.time, "sleep", waits.append)
    monkeypatch.setattr(bedrock_runner.random, "uniform", lambda a, b: 1.0)
    class Throttle(Exception):
        response = {"Error": {"Code": "ThrottlingException"},
                    "ResponseMetadata": {"HTTPStatusCode": 429,
                                         "HTTPHeaders": {"retry-after": "17"}}}
    class Client:
        calls = 0
        def converse(self, **kwargs):
            self.calls += 1
            raise Throttle()
    row = {"id": "opus-4-6", "profile": "fake", "thinking": "none", "cache_points": False}
    client = Client()
    with pytest.raises(bedrock_runner.ModelError, match="E_THROTTLE"):
        asyncio.run(bedrock_runner.run_loop(client, row, None, ["system"], "task",
                                            SpendGuard(1), {"max_model_turns": 1},
                                            tmp_path / "transcript.jsonl"))
    assert client.calls > 6
    assert waits[0] >= 17
    assert 290 <= sum(waits) <= 300


def test_model_concurrency_and_three_throttle_circuit_breaker(tmp_path):
    from ptu.harness.cli import _model_workers, _run_model_queue, model_rows
    from ptu.harness.run_store import EpisodeRecord
    rows = {row["id"]: row for row in model_rows()}
    assert _model_workers(rows["opus-4-6"], 3) == 2
    assert _model_workers(rows["sonnet-4-6"], 3) == 3
    assert _model_workers(rows["haiku-4-5"], 3) == 3
    calls = []
    def execute(job):
        calls.append(job)
        path = tmp_path / str(job)
        path.mkdir()
        result = path / "result.json"
        result.write_text('{"error":{"code":"E_THROTTLE"}}')
        return EpisodeRecord("task", "opus-4-6", job, path, result)
    records, paused = _run_model_queue(list(range(8)), execute, max_workers=1)
    assert paused and len(records) == 3 and calls == [0, 1, 2]


@pytest.mark.parametrize("provider_code,expected", [("ThrottlingException", "E_THROTTLE"),
                                                  ("ValidationException", "E_MODEL"),
                                                  ("AccessDeniedException", "E_ACCESS")])
def test_provider_error_is_ungraded_and_resumable(tmp_path, monkeypatch, provider_code, expected):
    from ptu.harness import bedrock_runner
    from ptu.harness.episode import run_episode
    from ptu.harness.run_store import RunStore
    from ptu.harness.cli import model_rows
    from ptu.tasks.loader import load_task
    monkeypatch.setattr(bedrock_runner.time, "sleep", lambda seconds: None)
    class Failing:
        def converse(self, **kwargs):
            error = RuntimeError("synthetic provider failure")
            error.response = {"Error": {"Code": provider_code},
                              "ResponseMetadata": {"HTTPStatusCode": 429 if expected == "E_THROTTLE" else 400}}
            raise error
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    row = {**model_rows()[0], "profile": "fake"}
    store = RunStore(run_id="errors")
    record = run_episode(task, row, store.episode_dir(task.id, row["id"], 0), "inprocess",
                         converse_client=Failing())
    result = json.loads(record.result_path.read_text())
    assert result["outcome"] == "O-ERROR"
    assert result["pass"] is None and result["graded"] is False
    assert result["violations"] == []
    assert result["error"]["code"] == expected
    assert store.finished(task.id, row["id"], 0) is False
    import jsonschema
    from pathlib import Path
    schema = json.loads((Path(__file__).parents[2] / "schemas/result.schema.json").read_text())
    jsonschema.validate(result, schema)


def test_preflight_does_not_call_fake_model(tmp_path):
    from ptu.harness.bedrock_runner import run_loop
    from ptu.harness.budget import BudgetError, PriceTable, SpendGuard
    from ptu.harness.mcp_client import InProcessTools
    fake = FakeConverse([])
    row = {"id": "gpt-6-sol", "profile": "fake", "thinking": "default", "cache_points": False,
           "echo_reasoning": False}
    with pytest.raises(BudgetError):
        asyncio.run(run_loop(fake, row, InProcessTools(None, Registry(control_specs({}))),
                             ["system"], "task", SpendGuard(0.00001, PriceTable()),
                             {"max_model_turns": 1}, tmp_path / "transcript.jsonl", max_tokens=100))
    assert fake.calls == []


def test_cli_probe_fake_client_and_no_access_key(monkeypatch, tmp_path, capsys):
    from ptu.harness.cli import main
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "FAKE-ACCESS-KEY-FOR-TEST")
    fake = FakeConverse([])
    assert main(["models", "probe"], client_factory=lambda profile: fake) == 0
    output = capsys.readouterr().out
    assert "FAKE-ACCESS-KEY-FOR-TEST" not in output
    assert '"unavailable"' in output
    assert "E_BEDROCK_UNAVAILABLE" in output
    assert fake.calls == []


def test_cli_report_emits_only_outcomes_codes_counts_and_costs(monkeypatch, tmp_path, capsys):
    from ptu.harness.cli import main
    from ptu.harness.run_store import RunStore

    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    store = RunStore(run_id="calibration")
    store.write_manifest({"probe_spent_usd": 0.0001})
    episode = store.episode_dir("PTU-DEMO-001", "haiku-4-5", 0)
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({
        "pass": False, "outcome": "O-GOAL-FAIL", "goal": {"ok": False},
        "violations": [{"code": "V-TOOL-OOS", "detail": "private source text"}],
    }))
    (episode / "usage.json").write_text(json.dumps({
        "inputTokens": 12, "outputTokens": 8, "cacheReadInputTokens": 5,
        "cacheWriteInputTokens": 3, "episode_spend_usd": 0.002,
    }))

    assert main(["report", "calibration"], client_factory=lambda profile: pytest.fail("client constructed")) == 0
    output = capsys.readouterr().out
    assert "PTU-DEMO-001" in output
    assert "O-GOAL-FAIL" in output
    assert "V-TOOL-OOS" in output
    assert "private source text" not in output
    assert '"spent_usd": 0.0021' in output


def test_cli_report_counts_errors_without_treating_them_as_failures(monkeypatch, tmp_path):
    from ptu.harness.cli import _report
    from ptu.harness.run_store import RunStore
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    store = RunStore(run_id="errors")
    store.write_manifest({"probe_spent_usd": 0})
    path = store.episode_dir("PTU-SYN", "opus-4-6", 0)
    path.mkdir(parents=True)
    (path / "result.json").write_text('{"outcome":"O-ERROR","pass":null,"error":{"code":"E_THROTTLE"},"violations":[]}')
    (path / "usage.json").write_text('{"episode_spend_usd":0.01}')
    report = _report("errors")
    assert report["episodes"] == 0
    assert report["error_episodes"] == 1
    assert report["passed"] == 0
    assert report["spent_usd"] == 0.01


def test_cli_resume_skips_finished_without_constructing_client(monkeypatch, tmp_path, capsys):
    from ptu.harness import cli
    from ptu.harness.run_store import RunStore
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path))
    store = RunStore(run_id="existing")
    tasks = cli._tasks("demo", None)
    for task in tasks:
        path = store.episode_dir(task.id, "haiku-4-5", 0)
        path.mkdir(parents=True)
        (path / "result.json").write_text('{"pass":true}')
        (path / "usage.json").write_text('{"episode_spend_usd":0.001}')
    assert cli.main(["run", "--models", "haiku-4-5", "--k", "1", "--resume", "existing"],
                    client_factory=lambda profile: pytest.fail("client constructed for finished run")) == 0
    assert f'"skipped": {len(tasks)}' in capsys.readouterr().out


def test_cli_run_uses_fake_probe_and_fake_episodes(monkeypatch, tmp_path, capsys):
    from ptu.harness import cli
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path / "runs"))
    calls = []

    class DynamicFake:
        def converse(self, **kwargs):
            calls.append(kwargs)
            if kwargs["inferenceConfig"]["maxTokens"] == 8:
                return reply([{"text": "OK"}], "end_turn")
            return reply([{"toolUse": {"toolUseId": "f", "name": "control__finish", "input":
                                        {"status": "complete", "summary": "done", "changes": [],
                                         "blocked_on": []}}}])

    code = cli.main(["run", "--task-set", "demo", "--models", "haiku-4-5", "--k", "2",
                     "--concurrency", "2", "--tasks", "PTU-TDW-PRR-001"],
                    client_factory=lambda profile: DynamicFake())
    assert code == 0
    assert '"completed": 2' in capsys.readouterr().out
    assert len(calls) == 3  # one probe, two episodes
    assert len(list((tmp_path / "runs").glob("*/episodes/*/*/*/result.json"))) == 2


def test_cli_mixed_k_runs_models_in_order_and_records_manifest(monkeypatch, tmp_path):
    from ptu.harness import cli
    from ptu.harness.run_store import EpisodeRecord
    monkeypatch.setenv("PTU_RUN_ROOT", str(tmp_path / "runs"))
    calls = []

    def episode(task, model, path, transport, **kwargs):
        calls.append((model["id"], kwargs["repeat"]))
        path.mkdir(parents=True, exist_ok=True)
        (path / "result.json").write_text('{"pass":true,"goal":{"ok":true},"violations":[]}')
        (path / "usage.json").write_text('{"episode_spend_usd":0}')
        return EpisodeRecord(task.id, model["id"], kwargs["repeat"], path, path / "result.json")

    class Probe:
        def converse(self, **kwargs):
            return reply([{"text": "OK"}], "end_turn")

    monkeypatch.setattr(cli, "run_episode", episode)
    assert cli.main(["run", "--models", "haiku-4-5,sonnet-4-6", "--k", "1",
                     "--k-override", "haiku-4-5=2", "--concurrency", "1",
                     "--tasks", "PTU-TDW-PRR-001"], client_factory=lambda profile: Probe()) == 0
    assert calls == [("haiku-4-5", 0), ("haiku-4-5", 1), ("sonnet-4-6", 0)]
    manifest = json.loads(next((tmp_path / "runs").glob("*/manifest.json")).read_text())
    assert manifest["k_override"] == {"haiku-4-5": 2}
    assert manifest["task_ids"] == ["PTU-TDW-PRR-001"]


def test_model_adapters_shape_converse_requests(tmp_path):
    from ptu.harness.bedrock_runner import run_loop
    from ptu.harness.budget import SpendGuard
    from ptu.harness.mcp_client import InProcessTools

    class Gateway:
        closed = False
        def call(self, name, args):
            return {"ok": False, "error": "unknown_tool"}

    openai = {"id": "gpt-6-sol", "profile": "fake", "thinking": "default", "cache_points": False,
              "echo_reasoning": False}
    fake = FakeConverse([
        reply([{"reasoningContent": {"reasoningText": {"text": "private"}}},
               {"toolUse": {"toolUseId": "x", "name": "unknown", "input": {}}}]),
        reply([{"text": "done"}], "end_turn"),
    ])
    asyncio.run(run_loop(fake, openai, InProcessTools(Gateway(), Registry(control_specs({}))),
                         ["system"], "task", SpendGuard(1), {"max_model_turns": 3},
                         tmp_path / "openai.jsonl"))
    assert "additionalModelRequestFields" not in fake.calls[0]
    assert all("cachePoint" not in block for block in fake.calls[0]["system"])
    assert all("reasoningContent" not in block for block in fake.calls[1]["messages"][-2]["content"])

    anthropic = {"id": "sonnet-4-6", "profile": "fake", "thinking": "adaptive", "cache_points": True,
                 "echo_reasoning": True}
    fake = FakeConverse([reply([{"text": "done"}], "end_turn")])
    asyncio.run(run_loop(fake, anthropic, InProcessTools(Gateway(), Registry(control_specs({}))),
                         ["system"], "task", SpendGuard(1), {"max_model_turns": 1},
                         tmp_path / "anthropic.jsonl", tool_config=[{"toolSpec": {"name": "x"}}]))
    request = fake.calls[0]
    assert request["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}
    assert request["system"][-1] == {"cachePoint": {"type": "default"}}
    assert request["toolConfig"]["tools"][-1] == {"cachePoint": {"type": "default"}}


def test_episode_integrates_real_w9_render_with_fake_converse(tmp_path):
    from ptu.harness.episode import run_episode
    from ptu.harness.cli import model_rows
    from ptu.tasks.loader import load_task
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    row = {**model_rows()[0], "profile": "fake"}
    fake = FakeConverse([reply([{"toolUse": {"toolUseId": "f", "name": "control__finish", "input":
                                          {"status": "complete", "summary": "done", "changes": [],
                                           "blocked_on": []}}}])])
    record = run_episode(task, row, tmp_path / "episode", "inprocess", converse_client=fake)
    assert record.result_path.is_file()
    state = json.loads((record.path / "seed_state" / "github.json").read_text())
    assert "tidewater/web" in state["repos"]
    assert json.loads((record.path / "usage.json").read_text())["inputTokens"] == 10
    assert fake.calls[0]["inferenceConfig"]["maxTokens"] == 4096
    assert json.loads((record.path / "manifest.json").read_text())["settings"]["max_tokens"] == 4096


def test_model_rows_define_output_limits():
    from ptu.harness.cli import model_rows
    limits = {row["id"]: row["max_tokens"] for row in model_rows()}
    assert limits["haiku-4-5"] == 4096
    assert all(limits[name] == 16000 for name in (
        "sonnet-4-6", "opus-4-6", "sonnet-5", "opus-5-5", "fable-5-1"))


def test_converse_tool_specs_lower_nullable_and_anyof_schema():
    from ptu.harness.mcp_client import registry_for, tool_specs
    from ptu.tasks.loader import load_task
    task = load_task("ptu/tasks/demo/PTU-TDW-DEP-014.yaml")
    tools = tool_specs(registry_for(task), task)
    encoded = json.dumps(tools)
    assert "anyOf" not in encoded
    assert '"type": [' not in encoded
    assert '"null"' not in encoded
