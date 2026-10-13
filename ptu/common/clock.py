"""Episode-local logical time; no wall clock enters mock state."""

from datetime import datetime, timedelta, timezone


class LogicalClock:
    def __init__(self, start: int = 0):
        if start < 0:
            raise ValueError("E_BAD_CLOCK")
        self._value = start

    def tick(self) -> int:
        self._value += 1
        return self._value

    def now(self) -> int:
        return self._value

    def iso(self) -> str:
        instant = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self._value)
        return instant.isoformat(timespec="seconds").replace("+00:00", "Z")
