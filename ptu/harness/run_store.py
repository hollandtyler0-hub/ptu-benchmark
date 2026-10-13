"""Untracked run directory layout and idempotent episode keys."""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def default_root() -> Path:
    return Path(os.environ.get("PTU_RUN_ROOT", "~/meter-runs/ptu")).expanduser()


def _safe(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError("E_RUN_KEY")
    return value


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class EpisodeRecord:
    task_id: str
    model_id: str
    repeat: int
    path: Path
    result_path: Path
    skipped: bool = False


class RunStore:
    def __init__(self, root: Path | None = None, run_id: str | None = None):
        self.root = Path(root) if root is not None else default_root()
        self.run_id = _safe(run_id or new_run_id())
        self.path = self.root / self.run_id

    def episode_dir(self, task_id: str, model_id: str, repeat: int) -> Path:
        if repeat < 0:
            raise ValueError("E_RUN_KEY")
        return self.path / "episodes" / _safe(task_id) / _safe(model_id) / str(repeat)

    def finished(self, task_id: str, model_id: str, repeat: int) -> bool:
        path = self.episode_dir(task_id, model_id, repeat) / "result.json"
        if not path.is_file():
            return False
        try:
            result = json.loads(path.read_text())
            return isinstance(result, dict) and "error" not in result
        except (OSError, ValueError):
            return False

    def write_manifest(self, data: dict) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        path = self.path / "manifest.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
        temp.replace(path)

    def spent_usd(self) -> float:
        total = self.probe_spent_usd()
        for path in (self.path / "episodes").glob("*/*/*/usage.json"):
            try:
                total += float(json.loads(path.read_text()).get("episode_spend_usd", 0))
            except (OSError, ValueError, TypeError):
                raise ValueError("E_RUN_USAGE") from None
        return total

    def probe_spent_usd(self) -> float:
        try:
            return float(json.loads((self.path / "manifest.json").read_text()).get("probe_spent_usd", 0))
        except FileNotFoundError:
            return 0.0
        except (OSError, ValueError, TypeError):
            raise ValueError("E_RUN_MANIFEST") from None
