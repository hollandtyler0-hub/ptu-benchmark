"""Task time, separate from the logical counter that orders audit entries."""

from contextlib import contextmanager
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SimClock:
    def __init__(self, config: dict):
        try:
            self.zone = ZoneInfo(config["timezone"])
            start = datetime.fromisoformat(config["start"])
            if start.tzinfo is None:
                raise ValueError
            self._now = start.astimezone(self.zone)
            self.start = self._now
            self.open_time, self.close_time = (time.fromisoformat(value) for value in config["business_hours"])
            if self.open_time >= self.close_time:
                raise ValueError
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValueError("E_SIMCLOCK_CONFIG") from None

    def now(self) -> str:
        return self._now.isoformat(timespec="seconds")

    def is_business_time(self) -> bool:
        return (self._now.weekday() < 5 and
                self.open_time <= self._now.timetz().replace(tzinfo=None) < self.close_time)

    def next_business_day(self) -> str:
        day = self._now.date() + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return datetime.combine(day, self.open_time, self.zone).isoformat(timespec="seconds")

    def advance_to(self, iso: str) -> None:
        try:
            target = datetime.fromisoformat(iso)
            if target.tzinfo is None:
                raise ValueError
            target = target.astimezone(self.zone)
        except (TypeError, ValueError):
            raise ValueError("E_SIMCLOCK_TIME") from None
        if target < self._now:
            raise ValueError("E_SIMCLOCK_BACKWARDS")
        self._now = target

    @contextmanager
    def at_scheduled_time(self, instant: datetime, *, started: datetime):
        """Stamp a due world event at its time within a completed wait."""
        end = self._now
        event_time = instant.astimezone(self.zone)
        if not started <= event_time <= end:
            raise ValueError("E_SIMCLOCK_TIME")
        self._now = event_time
        try:
            yield
        finally:
            self._now = end
