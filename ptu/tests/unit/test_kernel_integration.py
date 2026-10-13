from pathlib import Path
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import ptu.apps
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway, runtime_policy_dict
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import load_policy
from ptu.common.state import StateStore
from ptu.harness import cli
from ptu.harness.episode import render_prompt
from ptu.harness.mcp_client import InProcessTools, registry_for, stdio_tools, tool_specs
from ptu.tasks.loader import load_task
from ptu.workspaces.build import render


FIXTURE = Path(__file__).parents[1] / "fixtures" / "extreme_min" / "task.yaml"
FIXTURE_APPS = Path(__file__).parents[1] / "fixtures" / "apps"


def test_extreme_registry_and_prompt(monkeypatch):
    monkeypatch.setattr(ptu.apps, "__path__", [*ptu.apps.__path__, str(FIXTURE_APPS)])
    task = load_task(FIXTURE)
    registry = registry_for(task)
    assert {"notes.get_note", "notes.create_note", "notes.delete_note", "control.now", "control.wait",
            "control.get_confirmation", "control.list_confirmations"} <= set(registry._specs)
    assert all("anyOf" not in row["toolSpec"]["inputSchema"]["json"] for row in tool_specs(registry, task))
    system, user = render_prompt(task)
    assert "Invented Notes" in system
    assert "agent:notes" in system
    assert "control.wait" in system
    assert "injected instruction" not in system.lower()
    assert "Review the notes." in user


def test_cli_dispatch_and_missing_extension(capsys):
    assert cli.main(["fidelity", "--app", "notes", "--fixtures-path", str(FIXTURE_APPS), "--report"]) == 0
    assert '"failed": 0' in capsys.readouterr().out
    assert cli.main(["route", "--task", "PTU-TDW-XTR-999"]) == 1
    assert "E_TASK_UNKNOWN" in capsys.readouterr().out


def test_one_wait_has_stdio_and_inprocess_log_parity(monkeypatch, tmp_path):
    monkeypatch.setattr(ptu.apps, "__path__", [*ptu.apps.__path__, str(FIXTURE_APPS)])
    task = load_task(FIXTURE)
    docs = {"notes": {"items": {}}, "control": {"policy_text": "", "confirmations": [],
            "escalations": [], "finish": None}}
    task_hash = hashlib.sha256(task.id.encode()).hexdigest()
    registry = registry_for(task)
    gateway = Gateway(registry, StateStore(docs), AuditLog(None),
                      load_policy(runtime_policy_dict(task.policy)), task.enforcement,
                      LogicalClock(), IdFactory(task.seed), task.policy["principal"],
                      task_id_sha256=task_hash, events=task.scripted_events,
                      apps=registry.apps, actors=registry.actors, clock_cfg=task.clock)

    async def run():
        inprocess = await InProcessTools(gateway, registry).call("control__wait", {"until": "next_business_day"})
        episode = tmp_path / "episode"
        (episode / "private").mkdir(parents=True)
        (episode / "private" / "task.yaml").write_bytes(FIXTURE.read_bytes())
        (episode / "seed_state").mkdir()
        StateStore(docs).dump(episode / "seed_state")
        (episode / "manifest.json").write_text(json.dumps({"task_id_sha256": task_hash}))
        async with stdio_tools(episode, {"control__wait"}, apps_path=FIXTURE_APPS) as tools:
            over_stdio = await tools.call("control__wait", {"until": "next_business_day"})
        assert inprocess == over_stdio
        stdio_log = AuditLog(episode / "log.jsonl").entries
        def without_time(rows):
            return [{key: value for key, value in row.items() if key != "ts"} for row in rows]
        assert without_time(gateway.log.entries) == without_time(stdio_log)

    asyncio.run(run())


def test_extreme_world_generator_dispatch(monkeypatch, tmp_path):
    task = load_task(FIXTURE)
    task = replace(task, metadata={**task.metadata, "world": {"scale": "extreme"}})
    called = []
    def generate(seed, received, secrets):
        called.append((seed, received.id, len(secrets)))
        return {"notes": {"items": {}}, "control": {"policy_text": "", "confirmations": [],
                "escalations": [], "finish": None}}
    monkeypatch.setitem(sys.modules, "ptu.workspaces.tidewater.extreme",
                        SimpleNamespace(generate=generate))
    out, secrets = render("tidewater", task.seed, task, out=tmp_path / "world")
    assert called == [(task.seed, task.id, 0)]
    assert (out / "state" / "notes.json").is_file()
    assert secrets == {}
