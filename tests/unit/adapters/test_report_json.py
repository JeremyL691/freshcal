"""Tests for the JSON reporter and the report schema."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator

from freshcal import __version__
from freshcal.adapters.report_json import SCHEMA_VERSION, JsonReporter
from freshcal.core.errors import Issue
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    NextEntry,
    NextRelease,
    NextReport,
    Observation,
    Origin,
    Release,
    Status,
)

#: Frozen copies of the specification's normative blocks (tests/fixtures/spec/README.md).
SPEC = Path("tests/fixtures/spec")
SCHEMA_PATH = Path("src/freshcal/schemas/report.v1.schema.json")
BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
EVALUATED_AT = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)


def release(
    instant: str,
    local: str,
    *,
    adjusted_from: date | None = None,
    dst: str = "normal",
    clamped: bool = False,
) -> Release:
    return Release(
        instant=datetime.fromisoformat(instant),
        local=datetime.fromisoformat(local),
        adjusted_from=adjusted_from,
        dst=dst,  # type: ignore[arg-type]
        clamped=clamped,
    )


def spec_example() -> dict[str, object]:
    """The specification's example report, as JSON."""
    loaded = json.loads((SPEC / "example_check_report.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def example_report() -> CheckReport:
    """The §8.2 example rebuilt from domain objects."""
    ecb = EvaluationResult(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="Europe/Berlin",
        release=release("2026-09-25T14:00:00Z", "2026-09-25T16:00:00+02:00"),
        deadline=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 25, 14, 7, tzinfo=UTC),
            raw=datetime(2026, 9, 25, 14, 7),
            was_naive=True,
            interpreted_timezone="UTC",
        ),
        next_expected_arrival=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        explanation=(
            "On time: latest release Fri 2026-09-25 16:00 CEST arrived (observed Fri "
            "2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST."
        ),
    )
    monthly = EvaluationResult(
        source_id="stats.monthly_report",
        origin=Origin.DBT_MANIFEST,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="Europe/Berlin",
        release=release("2026-09-03T07:00:00Z", "2026-09-03T09:00:00+02:00"),
        deadline=datetime(2026, 9, 3, 11, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 3, 7, 10, tzinfo=UTC),
            raw=datetime(2026, 9, 3, 7, 10, tzinfo=UTC),
            was_naive=False,
            interpreted_timezone=None,
        ),
        next_expected_arrival=datetime(2026, 10, 5, 7, 0, tzinfo=UTC),
        explanation=(
            "On time: latest release Thu 2026-09-03 09:00 CEST arrived (observed Thu "
            "2026-09-03 09:10 CEST); next release Mon 2026-10-05 09:00 CEST."
        ),
    )
    vendor = EvaluationResult(
        source_id="vendor.daily_prices",
        origin=Origin.CONFIG,
        status=Status.OVERDUE,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="UTC",
        release=release("2026-09-27T06:00:00Z", "2026-09-27T06:00:00+00:00"),
        deadline=datetime(2026, 9, 27, 7, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 26, 6, 3, tzinfo=UTC),
            raw=datetime(2026, 9, 26, 6, 3, tzinfo=UTC),
            was_naive=False,
            interpreted_timezone=None,
        ),
        next_expected_arrival=datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
        missed_count=1,
        explanation=(
            "Overdue: release Sun 2026-09-27 06:00 UTC missed its deadline Sun 2026-09-27 "
            "07:00 UTC; 1 release missed; latest data observed Sat 2026-09-26 06:03 UTC."
        ),
        warnings=(
            Issue(
                "W002",
                "observed_timezone 'UTC' ignored because the value is timezone-aware",
                "vendor.daily_prices",
            ),
        ),
    )
    return CheckReport(evaluated_at=EVALUATED_AT, results=(ecb, monthly, vendor), exit_code=1)


def schema_document() -> dict[str, object]:
    loaded = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def validate(document: object) -> None:
    Draft202012Validator(schema_document()).validate(document)


def test_u_json_01_example_renders_exactly() -> None:
    rendered = JsonReporter().render(example_report())
    expected = spec_example()
    # The blueprint's example was written for the released version string.
    expected["freshcal_version"] = __version__

    assert rendered.endswith("\n")
    assert rendered == json.dumps(expected, indent=2, ensure_ascii=False) + "\n"

    # ... and the normalised objects are equal too, so the comparison does not hinge on
    # our own dumps settings.
    assert json.loads(rendered) == json.loads(json.dumps(expected, indent=2) + "\n")


def test_u_json_01_key_order_follows_the_schema() -> None:
    document = json.loads(JsonReporter().render(example_report()))
    assert list(document) == [
        "schema_version",
        "kind",
        "freshcal_version",
        "evaluated_at",
        "exit_code",
        "summary",
        "results",
    ]
    assert list(document["results"][0]) == [
        "source_id",
        "origin",
        "status",
        "is_verdict",
        "schedule_timezone",
        "release",
        "deadline",
        "observed",
        "next_expected_arrival",
        "missed_count",
        "missed_truncated",
        "pending_count",
        "explanation",
        "warnings",
        "error",
    ]
    assert list(document["results"][0]["release"]) == [
        "instant",
        "local",
        "adjusted_from",
        "dst",
        "clamped",
    ]
    assert list(document["summary"]) == [
        "total",
        "ON_TIME",
        "NOT_DUE",
        "OVERDUE",
        "NO_DATA",
        "CONFIG_ERROR",
        "QUERY_ERROR",
    ]


def test_u_json_02_every_rendered_report_validates() -> None:
    validate(json.loads(JsonReporter().render(example_report())))

    # A configuration error, a no-data result, an expired calendar, and a missing
    # observation exercise the nullable branches of the schema.
    variants = [
        EvaluationResult(
            source_id="a.config_error",
            origin=Origin.CONFIG,
            status=Status.CONFIG_ERROR,
            evaluated_at=EVALUATED_AT,
            schedule_timezone=None,
            release=None,
            deadline=None,
            observation=None,
            next_expected_arrival=None,
            explanation="Configuration error: E201 unknown time zone 'Mars/Olympus'",
            error=Issue("E201", "sources[0].schedule.timezone: unknown time zone 'Mars/Olympus'"),
        ),
        EvaluationResult(
            source_id="b.no_data",
            origin=Origin.CONFIG,
            status=Status.NO_DATA,
            evaluated_at=EVALUATED_AT,
            schedule_timezone="Europe/Berlin",
            release=release("2026-09-25T14:00:00Z", "2026-09-25T16:00:00+02:00"),
            deadline=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
            observation=None,
            next_expected_arrival=None,
            explanation=(
                "No data: max(_loaded_at) returned NULL; latest release none; no next "
                "release within 1830 days."
            ),
        ),
        EvaluationResult(
            source_id="c.clamped",
            origin=Origin.DBT_MANIFEST,
            status=Status.NOT_DUE,
            evaluated_at=EVALUATED_AT,
            schedule_timezone="UTC",
            release=release(
                "2026-02-27T18:00:00Z",
                "2026-02-27T18:00:00+00:00",
                adjusted_from=date(2026, 2, 26),
                dst="gap",
                clamped=True,
            ),
            deadline=datetime(2026, 2, 27, 20, 0, tzinfo=UTC),
            observation=Observation(
                instant=datetime(2026, 1, 30, 18, 5, tzinfo=UTC),
                raw=datetime(2026, 1, 30, 18, 5),
                was_naive=True,
                interpreted_timezone="Europe/Berlin",
            ),
            next_expected_arrival=datetime(2026, 3, 31, 18, 0, tzinfo=UTC),
            pending_count=1,
            explanation=(
                "Not due: release Fri 2026-02-27 18:00 UTC has not arrived yet; grace "
                "window ends Fri 2026-02-27 20:00 UTC; next release Tue 2026-03-31 "
                "18:00 UTC."
            ),
        ),
    ]
    for variant in variants:
        report = CheckReport(evaluated_at=EVALUATED_AT, results=(variant,), exit_code=2)
        validate(json.loads(JsonReporter().render(report)))


def test_u_json_03_test_report_schema_matches_the_specification() -> None:
    spec_schema = json.loads((SPEC / "report.v1.schema.json").read_text(encoding="utf-8"))
    assert spec_schema == schema_document()


def test_u_json_04_next_report_validates() -> None:
    report = NextReport(
        evaluated_at=EVALUATED_AT,
        sources=(
            NextEntry(
                source_id="ecb.fx_rates",
                schedule_timezone="Europe/Berlin",
                releases=(
                    NextRelease(
                        instant=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
                        local=datetime(2026, 9, 28, 16, 0, tzinfo=BERLIN),
                        deadline=datetime(2026, 9, 28, 16, 0, tzinfo=UTC),
                    ),
                    NextRelease(
                        instant=datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
                        local=datetime(2026, 9, 29, 16, 0, tzinfo=BERLIN),
                        deadline=datetime(2026, 9, 29, 16, 0, tzinfo=UTC),
                    ),
                ),
                warnings=(
                    Issue(
                        "W005",
                        "calendar cn_workdays was consulted for 2027-01-04, after its valid_until "
                        "2026-12-31; the next expected arrival may be wrong",
                        "ecb.fx_rates",
                    ),
                ),
            ),
            NextEntry(
                source_id="broken.source",
                schedule_timezone=None,
                error=Issue("E303", "dbt:source.proj.broken.source: no loaded_at_field"),
            ),
        ),
    )
    rendered = JsonReporter().render_next(report)
    document = json.loads(rendered)
    validate(document)
    assert document["kind"] == "next"
    assert document["schema_version"] == SCHEMA_VERSION
    assert len(document["sources"]) == 2
    assert list(document["sources"][0]) == [
        "source_id",
        "schedule_timezone",
        "releases",
        "warnings",
        "error",
    ]
    assert list(document["sources"][0]["releases"][0]) == ["instant", "local", "deadline"]
    assert rendered.endswith("\n")


def test_utc_instants_keep_microseconds_only_when_present() -> None:
    result = EvaluationResult(
        source_id="a.b",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=datetime(2026, 9, 28, 5, 30, 0, 250000, tzinfo=UTC),
        schedule_timezone="UTC",
        release=release("2026-09-25T14:00:00Z", "2026-09-25T14:00:00+00:00"),
        deadline=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 25, 14, 7, tzinfo=UTC),
            raw=datetime(2026, 9, 25, 14, 7),
            was_naive=True,
            interpreted_timezone="UTC",
        ),
        next_expected_arrival=None,
        explanation=(
            "On time: latest release Fri 2026-09-25 14:00 UTC arrived (observed Fri "
            "2026-09-25 14:07 UTC); no next release within 1830 days."
        ),
    )
    document = json.loads(
        JsonReporter().render(
            CheckReport(evaluated_at=result.evaluated_at, results=(result,), exit_code=0)
        )
    )
    assert document["evaluated_at"] == "2026-09-28T05:30:00.250000Z"


def test_non_utc_local_times_carry_their_offset() -> None:
    result = EvaluationResult(
        source_id="a.b",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="America/New_York",
        release=release("2026-05-29T21:00:00Z", "2026-05-29T17:00:00-04:00"),
        deadline=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        observation=None,
        next_expected_arrival=None,
        explanation="On time: no release has occurred yet; no next release within 1830 days.",
    )
    document = json.loads(
        JsonReporter().render(
            CheckReport(evaluated_at=EVALUATED_AT, results=(result,), exit_code=0)
        )
    )
    assert document["results"][0]["release"]["local"] == "2026-05-29T17:00:00-04:00"


def test_summary_counts_and_verdict_flag() -> None:
    report = example_report()
    document = json.loads(JsonReporter().render(report))
    assert document["summary"] == {
        "total": 3,
        "ON_TIME": 2,
        "NOT_DUE": 0,
        "OVERDUE": 1,
        "NO_DATA": 0,
        "CONFIG_ERROR": 0,
        "QUERY_ERROR": 0,
    }
    assert document["results"][0]["is_verdict"] is True
    assert document["results"][2]["is_verdict"] is True


def test_aware_observation_uses_an_offset() -> None:
    result = EvaluationResult(
        source_id="a.b",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="UTC",
        release=None,
        deadline=None,
        observation=Observation(
            instant=datetime(2026, 9, 25, 14, 7, tzinfo=UTC),
            raw=datetime(2026, 9, 25, 23, 7, tzinfo=timezone(timedelta(hours=9))),
            was_naive=False,
            interpreted_timezone=None,
        ),
        next_expected_arrival=None,
        explanation="On time: no release has occurred yet; no next release within 1830 days.",
    )
    document = json.loads(
        JsonReporter().render(
            CheckReport(evaluated_at=EVALUATED_AT, results=(result,), exit_code=0)
        )
    )
    assert document["results"][0]["observed"]["raw"] == "2026-09-25T23:07:00+09:00"


def test_j2_issue_without_location_renders_as_json_null() -> None:
    """Kills mutant J2: an issue with no location is JSON ``null``, never ``""``.

    §8.1 models ``location`` as nullable, and the schema also accepts an empty string, so
    only the rendered value distinguishes them. The ``issue.location or ""`` mutant turns
    "no location" into "" and callers lose the ability to tell an absent location from an
    empty one.
    """
    result = EvaluationResult(
        source_id="a.config_error",
        origin=Origin.CONFIG,
        status=Status.CONFIG_ERROR,
        evaluated_at=EVALUATED_AT,
        schedule_timezone=None,
        release=None,
        deadline=None,
        observation=None,
        next_expected_arrival=None,
        explanation="Configuration error: E201 unknown time zone 'Mars/Olympus'",
        error=Issue("E201", "sources[0].schedule.timezone: unknown time zone 'Mars/Olympus'"),
    )
    rendered = JsonReporter().render(
        CheckReport(evaluated_at=EVALUATED_AT, results=(result,), exit_code=2)
    )
    assert '"location": null' in rendered
    document = json.loads(rendered)
    assert document["results"][0]["error"]["location"] is None


def test_every_schema_def_is_used() -> None:
    """Guard: the committed schema is the one the reporter's documents validate against."""
    schema = schema_document()
    assert set(schema["$defs"]) == {
        "utc",
        "local",
        "date",
        "status",
        "issue",
        "release",
        "observation",
        "result",
        "summary",
        "checkReport",
        "nextReport",
    }
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        validate({"schema_version": "2.0", "kind": "check"})
