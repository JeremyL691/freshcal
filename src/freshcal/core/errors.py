"""Issue catalog and error types (BLUEPRINT.md §4.6, §5.3).

Every diagnostic FreshCal emits is an :class:`Issue` with a code from
``ISSUE_CODES``. Errors raise one of the :class:`FreshCalError` subclasses; the
application layer turns them into statuses and exit codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "ISSUE_CODES",
    "CalendarError",
    "ConfigError",
    "FreshCalError",
    "Issue",
    "QueryError",
]


@dataclass(frozen=True, slots=True)
class Issue:
    """A coded message. ``message`` already includes the location prefix."""

    code: str  # "E202", "W002"
    message: str
    location: str | None = None


class FreshCalError(Exception):
    """Base class for errors that carry an :class:`Issue`."""

    issue: Issue

    def __init__(self, issue: Issue) -> None:
        super().__init__(f"{issue.code} {issue.message}")
        self.issue = issue


class ConfigError(FreshCalError):
    """Configuration or usage error: codes E1xx-E4xx (exit 2)."""


class CalendarError(ConfigError):
    """Holiday data problem: E405 (out of range), E407 (roll failed)."""


class QueryError(FreshCalError):
    """Warehouse error: codes E501-E505 (exit 3)."""


#: Every active issue code (BLUEPRINT.md §4.6) mapped to its condition.
#: Retired code W001 ("naive value interpreted as UTC") is deliberately absent.
ISSUE_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {
        # Configuration and usage errors (exit 2)
        "E100": "YAML syntax error",
        "E101": "Unknown field",
        "E102": "Missing required field",
        "E103": "Wrong type",
        "E104": "Value not allowed",
        "E105": "Secret in config",
        "E106": "Format or range",
        "E110": "Config file missing",
        "E201": "Unknown time zone",
        "E202": "Invalid cron",
        "E203": "Duration too long",
        "E204": "Bad business_day",
        "E205": "Policy on wrong kind",
        "E206": "Duplicate source ID",
        "E207": "Unknown calendar name",
        "E208": "Required field unresolved",
        "E209": "No release within horizon",
        "E210": "Selection empty",
        "E211": "Bad CLI instant",
        "E212": "Unknown source in explain",
        "E213": "Connection missing",
        "E214": "Naive value without zone",
        "E215": "Undecidable (stale beyond horizon)",
        "E301": "Unsupported manifest version",
        "E302": "Manifest unreadable",
        "E303": "No loaded_at_field",
        "E304": "Forbidden field in meta.freshcal",
        "E401": "Unknown country, subdivision, or category",
        "E402": "Unknown financial market",
        "E403": "Date conflict",
        "E404": "Override file problem",
        "E405": "Holiday data out of range",
        "E406": "Weekend too large",
        "E407": "Roll failed",
        "E408": "Calendar expired",
        # Runtime errors (exit 3)
        "E501": "Connection failed",
        "E502": "Query failed",
        "E503": "Unsupported value type",
        "E504": "DSN env var missing",
        "E505": "Optional dependency missing",
        "E599": "Unexpected internal error",
        # Warnings (never change the exit code)
        "W002": "observed_timezone set but value aware",
        "W003": "Observed in the future",
        "W004": "Source in config and manifest",
        "W005": "Calendar consulted past valid_until",
        "W006": "Overrides without valid_until",
    }
)
