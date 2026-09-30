"""Clock adapter.

``SystemClock.now`` is the only place in FreshCal that reads the system clock;
ruff ``TID251`` bans the clock-reading APIs everywhere else (see
``[tool.ruff.lint.per-file-ignores]`` in ``pyproject.toml``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from freshcal.core.ports import Clock

__all__ = ["FixedClock", "SystemClock"]


class SystemClock(Clock):
    """Returns the current instant as an aware UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FixedClock(Clock):
    """Returns a fixed instant; used by ``--now`` and by tests."""

    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError(f"FixedClock instant must be timezone-aware, got {self.instant!r}")

    def now(self) -> datetime:
        return self.instant.astimezone(UTC)
