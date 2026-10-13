"""Synthetic subscription-window inputs; no account access."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ptu.harness.usage_windows import (bootstrap_snapshot_from_stream, check_subscription_windows,
                                       claim_bootstrap, record_manual_snapshot,
                                       stream_window_stop, update_overall_from_stream)


def test_weekly_snapshot_requires_both_fresh_windows(monkeypatch, tmp_path):
    path = tmp_path / "windows.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(path))
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    data = {"observed_at": (now - timedelta(minutes=5)).isoformat(),
            "overall_weekly": 0.6, "fable_weekly": 0.7}
    path.write_text(json.dumps(data))
    assert check_subscription_windows(now)["fable_weekly"] == 0.7
    for key in ("overall_weekly", "fable_weekly"):
        stopped = dict(data, **{key: 0.75})
        path.write_text(json.dumps(stopped))
        with pytest.raises(ValueError, match="^E_USAGE_STOP$"):
            check_subscription_windows(now)
    path.write_text(json.dumps({key: value for key, value in data.items() if key != "fable_weekly"}))
    with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
        check_subscription_windows(now)
    path.write_text(json.dumps(dict(data, observed_at=(now - timedelta(hours=6, seconds=1)).isoformat())))
    with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
        check_subscription_windows(now)


def test_one_bootstrap_requires_both_stream_windows_and_writes_six_hour_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "windows.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(path))
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    claim_bootstrap("PTU-TDW-XTR-201", tmp_path / "episode", now=now)
    with pytest.raises(ValueError, match="^E_USAGE_BOOTSTRAP_USED$"):
        claim_bootstrap("PTU-TDW-XTR-202", tmp_path / "second", now=now)
    with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
        bootstrap_snapshot_from_stream({"seven_day": {"last": 0.43}}, now=now)
    assert not path.exists()
    captured = bootstrap_snapshot_from_stream({
        "seven_day": {"last": 0.43}, "seven_day_fable": {"last": 0.51}}, now=now)
    assert captured["overall_weekly"] == 0.43 and captured["fable_weekly"] == 0.51
    assert check_subscription_windows(now + timedelta(hours=5, minutes=59))["fable_weekly"] == 0.51
    with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
        check_subscription_windows(now + timedelta(hours=6, seconds=1))


def test_stream_weekly_stop_recognizes_overall_and_fable():
    assert stream_window_stop({"seven_day": {"utilization": 0.75}})
    assert stream_window_stop({"seven_day_fable": {"utilization": 0.8}})
    assert not stream_window_stop({"five_hour": {"utilization": 0.9},
                                   "seven_day": {"utilization": 0.7}})


def test_manual_snapshot_and_stream_overall_keep_independent_expiry(monkeypatch, tmp_path):
    path = tmp_path / "usage-snapshot.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(path))
    observed = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    snapshot = record_manual_snapshot("40%", "41", "manual-claude.ai", observed.isoformat(),
                                      now=observed)
    assert snapshot["overall_weekly"] == 0.40
    assert snapshot["fable_weekly"] == 0.41
    assert check_subscription_windows(observed)["fable_weekly"] == 0.41
    later = observed + timedelta(hours=5)
    assert update_overall_from_stream({"seven_day": {"last": 0.52}}, now=later)
    updated = json.loads(path.read_text())
    assert updated["overall_weekly"] == 0.52
    assert updated["overall_source"] == "claude-code-rate-limit-stream"
    assert updated["fable_observed_at"] == observed.isoformat()
    assert updated["fable_weekly"] == 0.41
    with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
        check_subscription_windows(observed + timedelta(hours=6, seconds=1))


def test_manual_snapshot_rejects_bad_inputs_and_threshold_stops(monkeypatch, tmp_path):
    path = tmp_path / "usage-snapshot.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(path))
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    for overall, fable, source, timestamp in (
            ("101", "20", "manual-claude.ai", now.isoformat()),
            ("40", "20", "unknown", now.isoformat()),
            ("40", "20", "manual-claude.ai", "2026-10-02T12:00:00"),
            ("40", "20", "manual-claude.ai",
             (now - timedelta(hours=6, seconds=1)).isoformat())):
        with pytest.raises(ValueError, match="^E_USAGE_WINDOWS$"):
            record_manual_snapshot(overall, fable, source, timestamp, now=now)
    assert not path.exists()
    record_manual_snapshot("75", "41", "manual-claude.ai", now.isoformat(), now=now)
    with pytest.raises(ValueError, match="^E_USAGE_STOP$"):
        check_subscription_windows(now)


def test_usage_snapshot_cli_dispatch(monkeypatch, tmp_path, capsys):
    from ptu.harness import cli

    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(tmp_path / "usage-snapshot.json"))
    observed = datetime.now(timezone.utc).isoformat()
    assert cli.main(["usage-snapshot", "--overall", "40", "--fable", "41",
                     "--source", "manual-claude.ai", "--observed-at", observed]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["overall_weekly"] == 0.4 and output["fable_weekly"] == 0.41


def test_live_stream_threshold_kills_before_result(tmp_path):
    from ptu.common.gateway import runtime_policy_dict
    from ptu.common.policy import load_policy
    from ptu.common.registry import Registry
    from ptu.harness.agent_runner import load_profile, run_harness_episode
    from ptu.harness.mcp_client import registry_for
    from ptu.tasks.loader import load_task

    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    registry = registry_for(task)
    tools = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in registry.tools_for(
        task.services, task.tool_exposure, load_policy(runtime_policy_dict(task.policy)))]
    killed = []
    class Fake:
        returncode = None
        def __init__(self, argv, **kwargs):
            for event in ({"type": "system", "subtype": "init", "tools": tools,
                           "model": "claude-haiku-4-5-exact-synthetic",
                           "skills": [], "slash_commands": [],
                           "mcp_servers": [{"name": "ptu", "status": "connected"}]},
                          {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
                              "seven_day_fable": {"utilization": 0.76}}}}):
                kwargs["stdout"].write(json.dumps(event) + "\n")
            kwargs["stdout"].flush()
        def wait(self, timeout=None):
            if self.returncode is None:
                raise TimeoutError()
            return self.returncode
        def terminate(self):
            killed.append(True)
            self.returncode = -15
    record = run_harness_episode(task, load_profile("cc-sonnet-5"), tmp_path / "episode",
                                 wall_clock_seconds=5, spawn=Fake)
    assert killed == [True]
    assert json.loads(record.result_path.read_text())["error"]["code"] == "E_USAGE_STOP"
    manifest = json.loads((record.path / "manifest.json").read_text())
    assert manifest["harness"]["reported_model"] == "claude-haiku-4-5-exact-synthetic"


@pytest.mark.parametrize("fable_present", [False, True])
def test_livesmoke_runner_bootstrap_is_one_episode_and_requires_fable(monkeypatch, tmp_path,
                                                                       fable_present):
    from ptu.common.gateway import runtime_policy_dict
    from ptu.common.policy import load_policy
    from ptu.common.registry import Registry
    from ptu.harness.agent_runner import load_profile, run_harness_episode
    from ptu.harness.mcp_client import registry_for
    from ptu.tasks.loader import load_task

    snapshot = tmp_path / "windows.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(snapshot))
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    registry = registry_for(task)
    tools = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in registry.tools_for(
        task.services, task.tool_exposure, load_policy(runtime_policy_dict(task.policy)))]
    windows = {"seven_day": {"utilization": 0.43}}
    if fable_present:
        windows["seven_day_fable"] = {"utilization": 0.51}

    class Fake:
        returncode = 0
        def __init__(self, argv, **kwargs):
            for event in ({"type": "system", "subtype": "init", "tools": tools,
                           "model": "claude-sonnet-synthetic", "skills": [], "slash_commands": [],
                           "mcp_servers": [{"name": "ptu", "status": "connected"}]},
                          {"type": "rate_limit_event", "rate_limit_info": {
                              "unifiedWindows": windows}}):
                kwargs["stdout"].write(json.dumps(event) + "\n")
            kwargs["stdout"].flush()
        def wait(self, timeout=None):
            return self.returncode

    episode = tmp_path / "episode"
    record = run_harness_episode(task, load_profile("cc-sonnet"), episode,
                                 wall_clock_seconds=5, spawn=Fake, usage_bootstrap=True)
    manifest = json.loads((episode / "manifest.json").read_text())
    assert manifest["harness"]["usage_bootstrap"] is True
    assert (tmp_path / "usage-bootstrap.json").exists()
    if fable_present:
        assert json.loads(snapshot.read_text())["fable_weekly"] == 0.51
        assert manifest["harness"]["bootstrap_snapshot"]["overall_weekly"] == 0.43
    else:
        assert not snapshot.exists()
        assert json.loads(record.result_path.read_text())["error"]["code"] == "E_USAGE_WINDOWS"


def test_runner_stream_refreshes_only_overall_after_manual_snapshot(monkeypatch, tmp_path):
    from ptu.common.gateway import runtime_policy_dict
    from ptu.common.policy import load_policy
    from ptu.common.registry import Registry
    from ptu.harness.agent_runner import load_profile, run_harness_episode
    from ptu.harness.mcp_client import registry_for
    from ptu.tasks.loader import load_task

    path = tmp_path / "usage-snapshot.json"
    monkeypatch.setenv("PTU_USAGE_WINDOWS_FILE", str(path))
    observed = datetime.now(timezone.utc) - timedelta(minutes=10)
    record_manual_snapshot("40", "41", "manual-claude.ai", observed.isoformat())
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    registry = registry_for(task)
    tools = ["mcp__ptu__" + Registry.wire_name(spec.name) for spec in registry.tools_for(
        task.services, task.tool_exposure, load_policy(runtime_policy_dict(task.policy)))]

    class Fake:
        returncode = 0
        def __init__(self, argv, **kwargs):
            for event in ({"type": "system", "subtype": "init", "tools": tools,
                           "model": "claude-sonnet-synthetic", "skills": [], "slash_commands": [],
                           "mcp_servers": [{"name": "ptu", "status": "connected"}]},
                          {"type": "rate_limit_event", "rate_limit_info": {
                              "unifiedWindows": {"seven_day": {"utilization": 0.52}}}}):
                kwargs["stdout"].write(json.dumps(event) + "\n")
            kwargs["stdout"].flush()
        def wait(self, timeout=None):
            return self.returncode

    episode = tmp_path / "episode"
    run_harness_episode(task, load_profile("cc-sonnet"), episode,
                        wall_clock_seconds=5, spawn=Fake, usage_bootstrap=True)
    manifest = json.loads((episode / "manifest.json").read_text())
    snapshot = json.loads(path.read_text())
    assert manifest["harness"]["usage_bootstrap"] is False
    assert manifest["harness"]["stream_overall_updated"] is True
    assert snapshot["overall_weekly"] == 0.52
    assert snapshot["fable_weekly"] == 0.41
    assert snapshot["fable_observed_at"] == observed.isoformat()
    assert not (tmp_path / "usage-bootstrap.json").exists()
