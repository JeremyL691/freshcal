"""JSON report renderer (BLUEPRINT.md §8.1, §8.2).

The report is a contract for automated callers, so it is rendered exactly as the schema
describes: keys in schema order, two-space indentation, UTC instants as
``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``, local times as ISO 8601 with an offset, results sorted
by source ID, and a trailing newline. Additive changes bump the minor version;
removing or renaming a field or changing status semantics bumps the major version and
needs an ADR (ADR 0009).
"""

from __future__ import annotations

import json
from datetime import datetime

from freshcal import __version__
from freshcal.core.errors import Issue
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    NextEntry,
    NextReport,
    Observation,
    Release,
    Status,
)
from freshcal.core.timeutil import format_utc

__all__ = ["SCHEMA_VERSION", "JsonReporter"]

SCHEMA_VERSION = "1.0"
_STATUS_ORDER = (
    Status.ON_TIME,
    Status.NOT_DUE,
    Status.OVERDUE,
    Status.NO_DATA,
    Status.CONFIG_ERROR,
    Status.QUERY_ERROR,
)


def _issue(issue: Issue | None) -> dict[str, object] | None:
    if issue is None:
        return None
    return {"code": issue.code, "message": issue.message, "location": issue.location}


def _instant(value: datetime | None) -> str | None:
    return None if value is None else format_utc(value)


def _release(release: Release | None) -> dict[str, object] | None:
    if release is None:
        return None
    return {
        "instant": format_utc(release.instant),
        "local": release.local.isoformat(),
        "adjusted_from": None
        if release.adjusted_from is None
        else release.adjusted_from.isoformat(),
        "dst": release.dst,
        "clamped": release.clamped,
    }


def _observation(observation: Observation | None) -> dict[str, object] | None:
    if observation is None:
        return None
    return {
        "instant": format_utc(observation.instant),
        "raw": observation.raw.isoformat(),
        "was_naive": observation.was_naive,
        "interpreted_timezone": observation.interpreted_timezone,
    }


def _summary(results: tuple[EvaluationResult, ...]) -> dict[str, object]:
    counts = dict.fromkeys(_STATUS_ORDER, 0)
    for result in results:
        counts[result.status] += 1
    summary: dict[str, object] = {"total": len(results)}
    for status in _STATUS_ORDER:
        summary[status.value] = counts[status]
    return summary


class JsonReporter:
    """Renders check and next reports as schema-valid JSON text."""

    schema_version = SCHEMA_VERSION

    def render(self, report: CheckReport) -> str:
        """Render a ``check`` report (§8.2)."""
        document: dict[str, object] = {
            "schema_version": self.schema_version,
            "kind": "check",
            "freshcal_version": __version__,
            "evaluated_at": format_utc(report.evaluated_at),
            "exit_code": report.exit_code,
            "summary": _summary(report.results),
            "results": [self._result(result) for result in report.results],
        }
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    def render_next(self, report: NextReport) -> str:
        """Render a ``next`` report (§8.1)."""
        document: dict[str, object] = {
            "schema_version": self.schema_version,
            "kind": "next",
            "freshcal_version": __version__,
            "evaluated_at": format_utc(report.evaluated_at),
            "sources": [self._next_entry(entry) for entry in report.sources],
        }
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    @staticmethod
    def _result(result: EvaluationResult) -> dict[str, object]:
        return {
            "source_id": result.source_id,
            "origin": result.origin.value,
            "status": result.status.value,
            "is_verdict": result.status.is_verdict,
            "schedule_timezone": result.schedule_timezone,
            "release": _release(result.release),
            "deadline": _instant(result.deadline),
            "observed": _observation(result.observation),
            "next_expected_arrival": _instant(result.next_expected_arrival),
            "missed_count": result.missed_count,
            "missed_truncated": result.missed_truncated,
            "pending_count": result.pending_count,
            "explanation": result.explanation,
            "warnings": [_issue(issue) for issue in result.warnings],
            "error": _issue(result.error),
        }

    @staticmethod
    def _next_entry(entry: NextEntry) -> dict[str, object]:
        return {
            "source_id": entry.source_id,
            "schedule_timezone": entry.schedule_timezone,
            "releases": [
                {
                    "instant": format_utc(release.instant),
                    "local": release.local.isoformat(),
                    "deadline": format_utc(release.deadline),
                }
                for release in entry.releases
            ],
            "warnings": [_issue(issue) for issue in entry.warnings],
            "error": _issue(entry.error),
        }
