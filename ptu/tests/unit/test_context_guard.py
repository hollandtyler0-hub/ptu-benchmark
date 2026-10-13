"""Bedrock context exhaustion is a bounded, graded stop without a provider call."""

import asyncio
import json

from ptu.harness.bedrock_runner import run_loop
from ptu.harness.budget import SpendGuard
from ptu.harness.episode import run_episode
from ptu.tasks.loader import load_task


class NoProviderCall:
    def converse(self, **kwargs):
        raise AssertionError("E_UNEXPECTED_MODEL_CALL")


def test_run_loop_stops_before_exceeding_window(tmp_path):
    row = {"id": "sonnet-4-6", "profile": "synthetic", "max_tokens": 64,
           "context_window_tokens": 128}
    result = asyncio.run(run_loop(NoProviderCall(), row, None, ["system"], "request",
                                  SpendGuard(1), {"max_model_turns": 3},
                                  tmp_path / "transcript.jsonl"))
    assert result["context_exhausted"] is True
    assert result["model_turns"] == 0


def test_episode_reports_context_separately_from_provider_error(tmp_path):
    task = load_task("ptu/tasks/demo/PTU-TDW-PRR-001.yaml")
    row = {"id": "sonnet-4-6", "profile": "synthetic", "vendor": "anthropic",
           "thinking": "default", "cache_points": False, "echo_reasoning": False,
           "max_tokens": 64, "context_window_tokens": 128}
    record = run_episode(task, row, tmp_path / "episode", "inprocess",
                         converse_client=NoProviderCall())
    result = json.loads(record.result_path.read_text())
    assert result["outcome"] == "O-CONTEXT"
    assert result["pass"] is False
    assert "error" not in result
    assert result["metrics"]["tool_calls"] == 0
