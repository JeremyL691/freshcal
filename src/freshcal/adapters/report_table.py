"""Terminal table reporter (BLUEPRINT.md §8.3, §6.2).

Plain text on purpose: CI logs and pipes are the primary consumers, so there is no
colour and no Unicode box drawing. Times are rendered in each source's *schedule* time
zone — the zone the rule was written in and the one an operator reasons about — except
for the ``next`` table, which shows the UTC instant in its own column.

Layout, exactly as §8.3 describes: a header line, a blank line, the table, a blank line,
one explanation line per source followed by its warnings and error indented by two
spaces, a blank line, and the summary.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from freshcal import __version__
from freshcal.core.errors import Issue
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    NextReport,
    Status,
)
from freshcal.core.ports import Reporter
from freshcal.core.timeutil import format_local, format_utc

__all__ = ["TableReporter"]

_MISSING = "-"
_COLUMNS = ("SOURCE", "STATUS", "RELEASE", "DEADLINE", "OBSERVED", "NEXT EXPECTED")
_NEXT_COLUMNS = ("SOURCE", "RELEASE", "RELEASE (UTC)", "DEADLINE")
_STATUS_ORDER = (
    Status.ON_TIME,
    Status.NOT_DUE,
    Status.OVERDUE,
    Status.NO_DATA,
    Status.CONFIG_ERROR,
    Status.QUERY_ERROR,
)


def _table(columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Left-aligned columns padded to their widest cell, two spaces apart."""
    widths = [len(column) for column in columns]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)).rstrip()
    ]
    for row in rows:
        lines.append(
            "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        )
    return lines


def _local(value: datetime | None, timezone: str | None) -> str:
    if value is None or timezone is None:
        return _MISSING
    return format_local(value, ZoneInfo(timezone))


def _issue_line(issue: Issue) -> str:
    return f"  {issue.code} {issue.message}"


class TableReporter(Reporter):
    """Renders check and next reports as plain text tables."""

    def render(self, report: CheckReport) -> str:
        """Render a ``check`` report (§8.3)."""
        rows = [self._row(result) for result in report.results]
        lines = [self._header(report), ""]
        lines.extend(_table(_COLUMNS, rows) if rows else ["no sources"])
        lines.append("")
        for result in report.results:
            lines.append(f"{result.source_id}: {result.explanation}")
            lines.extend(_issue_line(issue) for issue in result.warnings)
            if result.error is not None:
                lines.append(_issue_line(result.error))
        lines.append("")
        lines.append(self._summary(report))
        return "\n".join(lines) + "\n"

    def render_next(self, report: NextReport) -> str:
        """Render a ``next`` report (§6.2): one row per upcoming release."""
        rows: list[tuple[str, ...]] = []
        for entry in report.sources:
            if not entry.releases:
                rows.append((entry.source_id, _MISSING, _MISSING, _MISSING))
                continue
            timezone = entry.schedule_timezone
            for release in entry.releases:
                local = format_local(release.local, ZoneInfo(timezone)) if timezone else _MISSING
                deadline = (
                    format_local(release.deadline, ZoneInfo(timezone)) if timezone else _MISSING
                )
                rows.append((entry.source_id, local, format_utc(release.instant), deadline))
        lines = _table(_NEXT_COLUMNS, rows) if rows else ["no sources"]
        notices = [
            f"  {issue.code} {entry.source_id}: {issue.message}"
            for entry in report.sources
            for issue in ((*entry.warnings, entry.error) if entry.error else entry.warnings)
            if issue is not None
        ]
        if notices:
            lines.append("")
            lines.extend(notices)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _row(result: EvaluationResult) -> tuple[str, ...]:
        timezone = result.schedule_timezone
        return (
            result.source_id,
            result.status.value,
            _local(result.release.instant if result.release is not None else None, timezone),
            _local(result.deadline, timezone),
            _local(
                result.observation.instant if result.observation is not None else None, timezone
            ),
            _local(result.next_expected_arrival, timezone),
        )

    @staticmethod
    def _header(report: CheckReport) -> str:
        count = len(report.results)
        noun = "source" if count == 1 else "sources"
        return (
            f"FreshCal {__version__} | evaluated at {format_utc(report.evaluated_at)} | "
            f"{count} {noun}"
        )

    @staticmethod
    def _summary(report: CheckReport) -> str:
        counts = dict.fromkeys(_STATUS_ORDER, 0)
        for result in report.results:
            counts[result.status] += 1
        parts = [f"{counts[status]} {status.value}" for status in _STATUS_ORDER if counts[status]]
        listed = ", ".join(parts) if parts else "no sources"
        return f"Summary: {listed} | exit code {report.exit_code}"
