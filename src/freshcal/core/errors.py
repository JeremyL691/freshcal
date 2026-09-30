"""Issue catalog and error types (BLUEPRINT.md §4.6, §5.3).

Every diagnostic FreshCal emits is an :class:`Issue` with a code from
``ISSUE_CODES``. Errors raise one of the :class:`FreshCalError` subclasses; the
application layer turns them into statuses and exit codes.

The two rendering helpers keep a message's size independent of its input: a YAML alias
bomb is 380 bytes and expands to hundreds of megabytes, so a value that reaches a
message is always rendered through :func:`render_value` (``reprlib``, bounded) and any
other interpolated text through :func:`truncate` (CFG-13).
"""

from __future__ import annotations

import reprlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "ISSUE_CODES",
    "MAX_RENDER_LENGTH",
    "CalendarError",
    "ConfigError",
    "FreshCalError",
    "Issue",
    "QueryError",
    "format_issue",
    "read_failure_reason",
    "render_value",
    "truncate",
]

#: Longest rendering of one value in a message (CFG-13).
MAX_RENDER_LENGTH: Final[int] = 80

_RENDERER: Final[reprlib.Repr] = reprlib.Repr()
_RENDERER.maxlevel = 4
_RENDERER.maxstring = 60
_RENDERER.maxother = 60
_RENDERER.maxlist = 6
_RENDERER.maxtuple = 6
_RENDERER.maxdict = 6
_RENDERER.maxset = 6
_RENDERER.maxfrozenset = 6
_RENDERER.maxdeque = 6
_RENDERER.maxarray = 6


def truncate(text: str, limit: int = MAX_RENDER_LENGTH) -> str:
    """Truncate ``text`` to ``limit`` characters with a trailing ``...``."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def render_value(value: object, limit: int = MAX_RENDER_LENGTH) -> str:
    """Render ``value`` for a message: ``reprlib`` (bounded, cycle-safe, shared-safe).

    ``reprlib`` replaces deep or long parts with ``...`` and never expands an alias
    structure, so the result is short even for a YAML alias bomb; the final ``limit``
    keeps every message small (CFG-13).
    """
    return truncate(_RENDERER.repr(value), limit)


def read_failure_reason(error: BaseException) -> str:
    """A short, stable reason for a failed ``Path.read_text(encoding="utf-8")``.

    Callers wrap it in their own code (``E110`` for the config file, ``E404`` for
    override files, ``E302`` for a dbt manifest) so the reason wording is identical
    everywhere (CFG-09).
    """
    if isinstance(error, FileNotFoundError):
        return "file not found"
    if isinstance(error, IsADirectoryError):
        return "is a directory"
    if isinstance(error, PermissionError):
        return "permission denied"
    if isinstance(error, UnicodeDecodeError):
        return "not valid UTF-8"
    return truncate(str(error))


@dataclass(frozen=True, slots=True)
class Issue:
    """A coded message. ``message`` already includes the location prefix."""

    code: str  # "E202", "W002"
    message: str
    location: str | None = None


def format_issue(issue: Issue) -> str:
    """Render one issue the way the CLI prints it: ``CODE location: message`` (§4.6).

    The location is printed exactly once: several §4.6 message templates interpolate
    their own ``{loc}`` prefix, so a message that already carries ``location: `` keeps
    its text and the prefix is not added a second time (the CLI-03 contract for
    ``validate``; ``docs/configuration.md``).
    """
    location = issue.location
    message = issue.message
    if location and message.startswith(f"{location}: "):
        message = message[len(location) + 2 :]
    if location:
        return f"{issue.code} {location}: {message}"
    return f"{issue.code} {message}"


class FreshCalError(Exception):
    """Base class for errors that carry one or more :class:`Issue` objects.

    ``issue`` is the first (most relevant) issue and stays the single-issue accessor
    existing callers use; ``issues`` carries every issue the failure produced, so a
    command can report all of them instead of only the first (CFG-04).
    """

    issue: Issue
    issues: tuple[Issue, ...]

    def __init__(self, issue: Issue | Iterable[Issue]) -> None:
        issues = (issue,) if isinstance(issue, Issue) else tuple(issue)
        if not issues:
            raise ValueError("a FreshCalError needs at least one issue")
        super().__init__("; ".join(f"{item.code} {item.message}" for item in issues))
        self.issues = issues
        self.issue = issues[0]


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
        "E216": "No sources to evaluate",
        "E217": "Cannot write output file",
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
