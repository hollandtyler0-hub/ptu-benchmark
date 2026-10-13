"""Fail-closed subscription-window guard for live harness episodes."""

import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


LIMIT = 0.75
MAX_AGE_SECONDS = 6 * 3600
FABLE_KEYS = ("seven_day_fable", "fable_seven_day", "fable_weekly")


def _snapshot_path() -> Path:
    return Path(os.environ.get("PTU_USAGE_WINDOWS_FILE",
                               str(Path.home() / "meter-runs" / "ptu" / "usage-snapshot.json")))


def _bootstrap_claim_path() -> Path:
    if "PTU_USAGE_WINDOWS_FILE" in os.environ:
        return _snapshot_path().with_name("usage-bootstrap.json")
    # Keep the first bootstrap's claim when the snapshot moves to the run store.
    return Path.home() / ".meter-harness" / "usage-bootstrap.json"


def _fraction(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("E_USAGE_WINDOWS")
    return float(value)


def _observed(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("E_USAGE_WINDOWS") from None
    if parsed.tzinfo is None:
        raise ValueError("E_USAGE_WINDOWS")
    return parsed


def _write_snapshot(snapshot: dict) -> None:
    path = _snapshot_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(snapshot, stream, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _percent(value: str) -> float:
    try:
        number = float(str(value).strip().removesuffix("%"))
    except (TypeError, ValueError):
        raise ValueError("E_USAGE_WINDOWS") from None
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError("E_USAGE_WINDOWS")
    return number / 100


def record_manual_snapshot(overall_pct: str, fable_pct: str, source: str,
                           observed_at: str, *, now: datetime | None = None) -> dict:
    """Record independently read subscription percentages outside Git."""
    if source != "manual-claude.ai":
        raise ValueError("E_USAGE_WINDOWS")
    timestamp = _observed(observed_at)
    age = ((now or datetime.now(timezone.utc)) - timestamp).total_seconds()
    if not 0 <= age <= MAX_AGE_SECONDS:
        raise ValueError("E_USAGE_WINDOWS")
    observed = timestamp.isoformat()
    snapshot = {"observed_at": observed, "overall_observed_at": observed,
                "fable_observed_at": observed, "overall_weekly": _percent(overall_pct),
                "fable_weekly": _percent(fable_pct), "source": source,
                "overall_source": source, "fable_source": source}
    _write_snapshot(snapshot)
    return snapshot


def update_overall_from_stream(windows: dict, *, now: datetime | None = None) -> bool:
    """Refresh only the stream-observed overall reading; retain Fable's age."""
    try:
        value = _fraction(windows["seven_day"]["last"])
        path = _snapshot_path()
        snapshot = json.loads(path.read_text())
        _fraction(snapshot["fable_weekly"])
        _observed(snapshot.get("fable_observed_at", snapshot["observed_at"]))
    except (OSError, KeyError, TypeError, ValueError):
        return False
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("E_USAGE_WINDOWS")
    snapshot["overall_weekly"] = value
    snapshot["overall_observed_at"] = observed.isoformat()
    snapshot["overall_source"] = "claude-code-rate-limit-stream"
    _write_snapshot(snapshot)
    return True


def claim_bootstrap(task_id: str, episode_dir: Path, *, now: datetime | None = None) -> dict:
    """Atomically reserve the one unsnapshotted live episode for this seat."""
    path = _bootstrap_claim_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"task_id": task_id, "episode_dir": str(episode_dir),
              "claimed_at": (now or datetime.now(timezone.utc)).isoformat()}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("E_USAGE_BOOTSTRAP_USED") from None
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
    return record


def bootstrap_snapshot_from_stream(windows: dict, *, now: datetime | None = None) -> dict:
    """Persist only independent overall and Fable weekly stream readings."""
    try:
        overall = _fraction(windows["seven_day"]["last"])
        fable = next(_fraction(windows[key]["last"]) for key in FABLE_KEYS if key in windows)
    except (KeyError, TypeError, StopIteration):
        raise ValueError("E_USAGE_WINDOWS") from None
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("E_USAGE_WINDOWS")
    snapshot = {"observed_at": observed.isoformat(),
                "overall_observed_at": observed.isoformat(),
                "fable_observed_at": observed.isoformat(),
                "overall_weekly": overall, "fable_weekly": fable,
                "source": "claude-code-rate-limit-stream",
                "overall_source": "claude-code-rate-limit-stream",
                "fable_source": "claude-code-rate-limit-stream"}
    _write_snapshot(snapshot)
    if overall >= LIMIT or fable >= LIMIT:
        raise ValueError("E_USAGE_STOP")
    return snapshot


def check_subscription_windows(now: datetime | None = None) -> dict:
    """Require recent independently observed overall and Fable weekly readings."""
    path = _snapshot_path()
    try:
        data = json.loads(path.read_text())
        observed = _observed(data["observed_at"])
        overall_observed = _observed(data.get("overall_observed_at", data["observed_at"]))
        fable_observed = _observed(data.get("fable_observed_at", data["observed_at"]))
        current = now or datetime.now(timezone.utc)
        overall_age = (current - overall_observed).total_seconds()
        fable_age = (current - fable_observed).total_seconds()
        overall = _fraction(data["overall_weekly"])
        fable = _fraction(data["fable_weekly"])
        if not 0 <= overall_age <= MAX_AGE_SECONDS or not 0 <= fable_age <= MAX_AGE_SECONDS:
            raise ValueError("E_USAGE_WINDOWS")
    except (OSError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "E_USAGE_WINDOWS":
            raise
        raise ValueError("E_USAGE_WINDOWS") from None
    if overall >= LIMIT or fable >= LIMIT:
        raise ValueError("E_USAGE_STOP")
    return {"observed_at": observed.isoformat(),
            "overall_observed_at": overall_observed.isoformat(),
            "fable_observed_at": fable_observed.isoformat(),
            "overall_weekly": overall, "fable_weekly": fable,
            "overall_source": data.get("overall_source", data.get("source")),
            "fable_source": data.get("fable_source", data.get("source"))}


def stream_window_stop(windows: dict) -> bool:
    """Stop immediately on any weekly reading at the limit."""
    if not isinstance(windows, dict):
        return False
    for key in ("seven_day", *FABLE_KEYS):
        row = windows.get(key)
        if isinstance(row, dict):
            value = row.get("utilization", row.get("last"))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= LIMIT:
                return True
    return False


def latest_readings(usage: dict) -> dict:
    windows = usage.get("rate_windows", {}) if isinstance(usage, dict) else {}
    return {name: row.get("last") for name, row in windows.items()
            if isinstance(row, dict) and isinstance(row.get("last"), (int, float))}
