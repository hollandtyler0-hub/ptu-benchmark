"""Bounded Claude Code Haiku sieve; returns only counts and fixed codes."""

import json
import uuid
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ptu.harness.agent_runner import load_profile, run_harness_episode
from ptu.harness.run_store import RunStore
from ptu.harness.usage_windows import latest_readings, stream_window_stop


def _update_difficulty(path: Path, key: str, value: dict) -> None:
    """Replace one difficulty row without reformatting the rest of a task."""
    import yaml

    path = Path(path)
    original = path.read_text()
    rows = original.splitlines(keepends=True)
    metadata = next((i for i, row in enumerate(rows) if row.rstrip() == "metadata:"), None)
    if metadata is None:
        raise ValueError("E_TASK_METADATA")
    limit = next((i for i in range(metadata + 1, len(rows))
                  if rows[i].strip() and not rows[i].startswith(" ")), len(rows))
    difficulty = next((i for i in range(metadata + 1, limit)
                       if rows[i].rstrip() == "  difficulty:"), None)
    if difficulty is None:
        rows.insert(metadata + 1, "  difficulty:\n")
        difficulty = metadata + 1
        limit += 1
    end = next((i for i in range(difficulty + 1, limit)
                if rows[i].strip() and not rows[i].startswith("    ")), limit)
    prefix = f"    {key}:"
    existing = next((i for i in range(difficulty + 1, end) if rows[i].startswith(prefix)), None)
    if existing is not None:
        tail = next((i for i in range(existing + 1, end)
                     if rows[i].strip() and not rows[i].startswith("      ")), end)
        del rows[existing:tail]
        end -= tail - existing
    rows.insert(end, f"{prefix} {json.dumps(value, sort_keys=True, separators=(',', ':'))}\n")
    updated = "".join(rows)
    parsed = yaml.safe_load(updated)
    if parsed["metadata"]["difficulty"].get(key) != value:
        raise ValueError("E_TASK_METADATA")
    path.write_text(updated)


def sieve(task, model_row: dict | None, k: int, max_spend_usd: float) -> dict:
    if k < 1 or max_spend_usd <= 0 or (model_row is not None and model_row.get("id") != "cc-haiku"):
        raise ValueError("E_SIEVE_CONFIG")
    profile = load_profile("cc-haiku")
    store = RunStore(run_id="sieve-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                     + "-" + uuid.uuid4().hex[:8])
    manifest = {"task_ids": [task.id], "profile": profile.id, "model_alias": profile.model,
                "effort": profile.effort, "k": k, "max_spend_usd": max_spend_usd,
                "transport": "claude-code", "usage_windows": {}}
    store.write_manifest(manifest)
    outcomes = Counter()
    codes = Counter()
    passes = 0
    completed = 0
    spent = 0.0
    ungraded_code = None
    for repeat in range(k):
        remaining = max_spend_usd - spent
        if remaining <= 0:
            ungraded_code = "E_BUDGET"
            break
        path = store.episode_dir(task.id, profile.id, repeat)
        try:
            record = run_harness_episode(task, replace(profile, budget_usd=min(profile.budget_usd, remaining)),
                                         path, wall_clock_seconds=int(task.limits["wall_clock_seconds"]),
                                         repeat=repeat, live=True)
        except ValueError as error:
            code = str(error)
            ungraded_code = code if code.startswith("E_") and code.replace("_", "").isalnum() else "E_MODEL"
            break
        result = json.loads(record.result_path.read_text())
        usage = json.loads((path / "usage.json").read_text())
        manifest["usage_windows"][str(repeat)] = latest_readings(usage)
        store.write_manifest(manifest)
        spent += float(usage.get("episode_spend_usd", 0))
        completed += 1
        outcomes[result["outcome"]] += 1
        if stream_window_stop(usage.get("rate_windows", {})):
            ungraded_code = "E_USAGE_STOP"
            break
        if result.get("error") or result["outcome"] in {"O-ERROR", "O-INVALID-OOB"}:
            ungraded_code = result.get("error", {}).get("code") or "E_ISOLATION"
            break
        codes.update({row["code"] for row in result.get("violations", [])})
        passes += result.get("pass") is True
    return {"task": task.id, "model": profile.id, "k": k, "completed": completed,
            "passes": passes, "codes": dict(sorted(codes.items())),
            "outcomes": dict(sorted(outcomes.items())), "run_id": store.run_id,
            "spent_usd": round(spent, 9), "ungraded_code": ungraded_code}


def record_sieve(task_path: Path, result: dict) -> None:
    import yaml

    task_id = yaml.safe_load(Path(task_path).read_text())["id"]
    if (result.get("task") != task_id or result.get("model") != "cc-haiku"
            or result.get("completed", result.get("k")) != result.get("k")
            or result.get("ungraded_code") is not None
            or result.get("passes", -1) < 0 or result["passes"] > result.get("k", 0)):
        raise ValueError("E_SIEVE_INCOMPLETE")
    value = {"model": result["model"], "k": result["k"], "passes": result["passes"],
             "run_id": result["run_id"], "date": datetime.now(timezone.utc).date().isoformat()}
    _update_difficulty(Path(task_path), "sieve", value)
