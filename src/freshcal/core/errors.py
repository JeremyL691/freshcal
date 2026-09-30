"""Issue catalog and error types.

Every diagnostic FreshCal emits is an :class:`Issue` with a code from
``ISSUE_CODES``. Errors raise one of the :class:`FreshCalError` subclasses; the
application layer turns them into statuses and exit codes.

The two rendering helpers keep a message's size independent of its input: a YAML alias
bomb is 380 bytes and expands to hundreds of megabytes, so a value that reaches a
message is always rendered through :func:`render_value` (``reprlib``, bounded) and any
other interpolated text through :func:`truncate`.
"""

from __future__ import annotations

import reprlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
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
    "path_failure_reason",
    "read_failure_reason",
    "render_path",
    "render_value",
    "truncate",
]

#: Longest rendering of one value in a message.
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
    keeps every message small.
    """
    return truncate(_RENDERER.repr(value), limit)


def read_failure_reason(error: BaseException) -> str:
    """A short, stable reason for a failed ``Path.read_text(encoding="utf-8")``.

    Callers wrap it in their own code (``E110`` for the config file, ``E404`` for
    override files, ``E302`` for a dbt manifest) so the reason wording is identical
    everywhere.
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


def path_failure_reason(path: str) -> str | None:
    """The reason ``path`` cannot be resolved, or ``None`` when it can.

    ``Path.resolve()`` raises ``ValueError`` for an embedded NUL byte — the operating
    system cannot express such a name at all — and ``OSError`` for filesystem-level
    failures such as a symbolic-link loop. Both are configuration mistakes with a coded
    diagnostic, so the responsible boundary asks this *before* it touches the filesystem
    and never lets the exception escape as an internal error. The NUL case is named here
    rather than taken from the exception, whose wording differs between Python versions
    ("embedded null byte" up to 3.12, "lstat: embedded null character in path" after).
    """
    if "\x00" in path:
        return "embedded null byte"
    try:
        Path(path).resolve()
    except ValueError as error:
        return truncate(str(error))
    except OSError as error:
        return read_failure_reason(error)
    return None


def render_path(path: object) -> str:
    """Render a path for a message, escaping control characters.

    A path is normally written as it is, so every existing diagnostic keeps its exact
    wording; one that carries a control character (a NUL from a YAML escape, say) is
    rendered with ``reprlib`` instead, because writing the character itself to a terminal
    would corrupt the report.
    """
    text = str(path)
    if not any(ord(character) < 32 for character in text):
        return text
    # Escape only when something has to be escaped, so a normal path keeps its exact
    # wording (callers already quote it) and a control character never reaches the output.
    return text.encode("unicode_escape").decode("ascii")


@dataclass(frozen=True, slots=True)
class Issue:
    """A coded message. ``message`` already includes the location prefix."""

    code: str  # "E202", "W002"
    message: str
    location: str | None = None


def format_issue(issue: Issue) -> str:
    """Render one issue the way the CLI prints it: ``CODE location: message``.

    The location is printed exactly once: several issue message templates interpolate
    their own ``{loc}`` prefix, so a message that already carries ``location: `` keeps
    its text and the prefix is not added a second time (the CLI's contract for
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
    command can report all of them instead of only the first.
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


#: Every active issue code mapped to its condition.
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
