"""Tests for source merging and selection (BLUEPRINT.md §4.8, §6.2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import FakeCalendarProvider

from freshcal.app import (
    exit_code_for,
    merge_entries,
    run_check,
    run_explain,
    run_next,
    run_validate,
    select_entries,
)
from freshcal.core.errors import ConfigError, Issue, QueryError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    Origin,
    RawObservation,
    SourceEntry,
    SourceRule,
    Status,
    mark_duplicate_source_ids,
)


def rule(source_id: str, *, origin: Origin = Origin.CONFIG) -> SourceRule:
    return SourceRule(
        source_id=source_id,
        origin=origin,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin")),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation=f"raw.{source_id}", loaded_at_field="_loaded_at"),
    )


def entry(
    source_id: str,
    *,
    origin: Origin = Origin.CONFIG,
    location: str = "sources[0]",
) -> SourceEntry:
    return SourceEntry(
        source_id=source_id, origin=origin, rule=rule(source_id, origin=origin), location=location
    )


def test_u_app_01_config_wins_over_manifest_with_w004() -> None:
    config_entry = entry("ecb.fx_rates", location="sources[0]")
    manifest_entry = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.proj.ecb.fx_rates"
    )
    other = entry(
        "vendor.daily", origin=Origin.DBT_MANIFEST, location="dbt:source.proj.vendor.daily"
    )

    merged, warnings = merge_entries(
        [config_entry], [manifest_entry, other], config_file="freshcal.yml"
    )

    assert [(item.source_id, item.origin) for item in merged] == [
        ("ecb.fx_rates", Origin.CONFIG),
        ("vendor.daily", Origin.DBT_MANIFEST),
    ]
    assert merged[0].rule is config_entry.rule, "the config definition is used entirely"
    assert warnings == [
        Issue(
            "W004",
            "'ecb.fx_rates' is defined in both freshcal.yml and the dbt manifest; using the "
            "config file definition",
            "ecb.fx_rates",
        )
    ]


def test_u_app_02_manifest_internal_duplicates_are_e206_on_each() -> None:
    first = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_a.ecb.fx_rates"
    )
    second = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_b.ecb.fx_rates"
    )
    unique = entry(
        "vendor.daily", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_a.vendor.daily"
    )

    merged, warnings = merge_entries([], [first, second, unique])
    by_id = {item.source_id: item for item in merged}

    assert warnings == []
    assert by_id["vendor.daily"].errors == ()
    assert by_id["vendor.daily"].rule is not None
    assert by_id["ecb.fx_rates"].rule is None
    assert [issue.code for issue in by_id["ecb.fx_rates"].errors] == ["E206"]

    # Every member of the duplicated group is marked, each message naming both locations.
    messages = [item.errors[0].message for item in merged if item.errors]
    assert messages == [
        "duplicate source id 'ecb.fx_rates' at dbt:source.pkg_a.ecb.fx_rates and "
        "dbt:source.pkg_b.ecb.fx_rates",
        "duplicate source id 'ecb.fx_rates' at dbt:source.pkg_a.ecb.fx_rates and "
        "dbt:source.pkg_b.ecb.fx_rates",
    ]


def test_u_app_02_config_duplicates_are_marked_before_merging() -> None:
    """The config loader marks its own duplicates; merging keeps them."""
    duplicate = SourceEntry(
        source_id="a.b",
        origin=Origin.CONFIG,
        rule=None,
        errors=(
            Issue("E206", "duplicate source id 'a.b' at sources[0] and sources[1]", "sources[1]"),
        ),
        location="sources[1]",
    )
    merged, warnings = merge_entries([entry("a.b", location="sources[0]"), duplicate], [])
    assert warnings == []
    assert [(item.source_id, [issue.code for issue in item.errors]) for item in merged] == [
        ("a.b", []),
        ("a.b", ["E206"]),
    ]


def test_u_app_03_selection_with_several_patterns() -> None:
    entries = [
        entry("ecb.fx_rates"),
        entry("ecb.rates_archive"),
        entry("vendor.daily"),
        entry("stats.monthly"),
    ]
    assert [item.source_id for item in select_entries(entries, ["ecb.*"])] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
    ]
    assert [item.source_id for item in select_entries(entries, ["*.daily", "stats.*"])] == [
        "vendor.daily",
        "stats.monthly",
    ]
    assert [item.source_id for item in select_entries(entries, None)] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
        "vendor.daily",
        "stats.monthly",
    ]
    assert [item.source_id for item in select_entries(entries, [])] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
        "vendor.daily",
        "stats.monthly",
    ]


def test_u_app_03_selection_is_case_sensitive_and_exact() -> None:
    entries = [entry("ecb.fx_rates"), entry("ecb.fx_rates_daily")]
    assert [item.source_id for item in select_entries(entries, ["ecb.fx_rates"])] == [
        "ecb.fx_rates"
    ]
    with pytest.raises(ConfigError) as excinfo:  # patterns are case-sensitive
        select_entries(entries, ["ECB.*"])
    assert excinfo.value.issue.code == "E210"


def test_u_app_04_no_match_is_e210() -> None:
    with pytest.raises(ConfigError) as excinfo:
        select_entries([entry("a.b")], ["nothing.*"])
    issue = excinfo.value.issue
    assert issue.code == "E210"
    assert issue.message == "--select matched no sources: nothing.*"


def test_u_app_04_no_match_lists_every_pattern() -> None:
    with pytest.raises(ConfigError) as excinfo:
        select_entries([entry("a.b")], ["x.*", "y.*"])
    assert excinfo.value.issue.message == "--select matched no sources: x.*, y.*"


def test_u_app_05_merged_output_is_sorted_by_source_id() -> None:
    config_entries = [entry("z.last"), entry("a.first")]
    manifest_entries = [
        entry("m.middle", origin=Origin.DBT_MANIFEST),
        entry("b.second", origin=Origin.DBT_MANIFEST),
    ]
    merged, _ = merge_entries(config_entries, manifest_entries)
    assert [item.source_id for item in merged] == ["a.first", "b.second", "m.middle", "z.last"]


def test_merge_keeps_erroring_entries() -> None:
    """An invalid source from either side stays in the list, so it can be reported."""
    broken = SourceEntry(
        source_id="broken.source",
        origin=Origin.DBT_MANIFEST,
        rule=None,
        errors=(Issue("E303", "dbt:source.proj.broken.source: no loaded_at_field", "x"),),
        location="dbt:source.proj.broken.source",
    )
    merged, _ = merge_entries([], [broken])
    assert len(merged) == 1
    assert merged[0].rule is None
    assert [issue.code for issue in merged[0].errors] == ["E303"]


# --------------------------------------------------------------------------
# Use cases and exit codes (T-6.1)
# --------------------------------------------------------------------------


class FakeReader:
    """A ``FreshnessReader`` that returns per-target values or raises."""

    def __init__(
        self, values: Mapping[str, RawObservation], failure: Exception | None = None
    ) -> None:
        self._values = values
        self._failure = failure
        self.closed = False

    def read_latest(self, target: FreshnessTarget) -> RawObservation:
        if self._failure is not None:
            raise self._failure
        return self._values.get(target.relation, RawObservation(None))

    def close(self) -> None:
        self.closed = True


def source_entry(
    source_id: str,
    *,
    relation: str | None = None,
    grace: timedelta = timedelta(hours=2),
    calendar: CalendarSpec | None = None,
    valid_until: date | None = None,
    active_from: date | None = None,
    origin: Origin = Origin.CONFIG,
) -> SourceEntry:
    spec = calendar or CalendarSpec()
    if valid_until is not None:
        spec = replace(spec, valid_until=valid_until)
    rule = SourceRule(
        source_id=source_id,
        origin=origin,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin")),
        calendar=spec,
        grace=grace,
        target=FreshnessTarget(
            relation=relation or f"raw.{source_id}", loaded_at_field="_loaded_at"
        ),
        observed_timezone=ZoneInfo("UTC"),
        active_from=active_from,
    )
    return SourceEntry(source_id=source_id, origin=origin, rule=rule, location="sources[0]")


ECB_NOW = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)
FRIDAY_DATA = RawObservation(datetime(2026, 9, 25, 14, 7))


def test_u_app_06_run_check_covers_every_status() -> None:
    entries = [
        source_entry("a.on_time", relation="raw.on_time"),
        source_entry("b.not_due", relation="raw.not_due"),
        source_entry("c.overdue", relation="raw.overdue"),
        source_entry("d.no_data", relation="raw.no_data"),
        SourceEntry(
            source_id="e.config_error",
            origin=Origin.CONFIG,
            rule=None,
            errors=(
                Issue("E201", "sources[4].schedule.timezone: unknown time zone 'Mars/Olympus'"),
            ),
            location="sources[4]",
        ),
        source_entry("f.active_from", relation="raw.active_from", active_from=date(2026, 10, 1)),
    ]
    reader = FakeReader(
        {
            # Monday's release arrived exactly at its instant.
            "raw.on_time": RawObservation(datetime(2026, 9, 28, 14, 0)),
            # Friday's data: Monday's release is unarrived but inside its grace window.
            "raw.not_due": FRIDAY_DATA,
            # Thursday's data: Friday's release is past its deadline.
            "raw.overdue": RawObservation(datetime(2026, 9, 24, 14, 0)),
            "raw.no_data": RawObservation(None),
        }
    )
    now = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)  # 16:30 CEST, inside the grace window
    report = run_check(entries, reader, FakeCalendarProvider(), now)

    assert [result.source_id for result in report.results] == sorted(
        entry.source_id for entry in entries
    )
    statuses = {result.source_id: result.status for result in report.results}
    assert statuses["a.on_time"] is Status.ON_TIME
    assert statuses["b.not_due"] is Status.NOT_DUE
    assert statuses["c.overdue"] is Status.OVERDUE
    assert statuses["d.no_data"] is Status.NO_DATA
    assert statuses["e.config_error"] is Status.CONFIG_ERROR
    assert statuses["f.active_from"] is Status.ON_TIME  # nothing is due before the floor
    assert report.exit_code == 2  # a CONFIG_ERROR result outranks everything
    assert report.evaluated_at == now
    for result in report.results:
        assert result.explanation


def test_u_app_07_reader_creation_failure_marks_every_valid_source() -> None:
    entries = [
        source_entry("a.b"),
        source_entry("c.d"),
        SourceEntry(
            source_id="e.f",
            origin=Origin.CONFIG,
            rule=None,
            errors=(Issue("E208", "sources[2]: 'grace' is required"),),
            location="sources[2]",
        ),
    ]
    issue = Issue("E501", "cannot connect to duckdb: file is not a database")
    report = run_check(entries, None, FakeCalendarProvider(), ECB_NOW, reader_error=issue)

    by_id = {result.source_id: result for result in report.results}
    for source_id in ("a.b", "c.d"):
        result = by_id[source_id]
        assert result.status is Status.QUERY_ERROR
        assert result.error == issue
        # The schedule context is still filled in for a valid rule.
        assert result.release is not None
        assert result.next_expected_arrival is not None
        assert result.deadline == result.release.instant + timedelta(hours=2)
    assert by_id["e.f"].status is Status.CONFIG_ERROR
    assert report.exit_code == 2  # a config error still outranks the runtime error


def test_u_app_07_query_error_during_a_read() -> None:
    entries = [source_entry("a.b")]
    reader = FakeReader({}, failure=QueryError(Issue("E502", "query failed: no such table")))
    report = run_check(entries, reader, FakeCalendarProvider(), ECB_NOW)
    result = report.results[0]
    assert result.status is Status.QUERY_ERROR
    assert result.error is not None
    assert result.error.code == "E502"
    assert result.release is not None
    assert result.next_expected_arrival is not None
    assert report.exit_code == 3


def test_u_app_07_query_error_with_a_rule_that_has_no_releases() -> None:
    """A rule that cannot be evaluated is a CONFIG_ERROR, even on the query path."""
    broken = source_entry("a.b")
    rule = broken.rule
    assert rule is not None
    impossible = replace(rule, schedule=CronSchedule("0 0 30 2 *", ZoneInfo("UTC")))
    entry = replace(broken, rule=impossible)
    report = run_check([entry], None, FakeCalendarProvider(), ECB_NOW)
    assert report.results[0].status is Status.CONFIG_ERROR
    assert report.results[0].error is not None
    assert report.results[0].error.code == "E209"


def test_run_check_contains_a_normalisation_overflow() -> None:
    """E2E-02/I-PG-12: an unrepresentable value is that source's E502, not an E599."""
    entry = source_entry("a.b")
    rule = entry.rule
    assert rule is not None
    entry = replace(entry, rule=replace(rule, observed_timezone=ZoneInfo("America/New_York")))
    reader = FakeReader({"raw.a.b": RawObservation(datetime(9999, 12, 31, 22, 0))})

    report = run_check([entry], reader, FakeCalendarProvider(), ECB_NOW)

    result = report.results[0]
    assert result.status is Status.QUERY_ERROR
    assert result.error is not None
    assert result.error.code == "E502"
    assert result.error.message.startswith("query failed: ")
    assert "9999-12-31 22:00:00" in result.error.message
    assert report.exit_code == 3


def test_run_check_one_overflowing_value_does_not_abort_the_other_sources() -> None:
    """E2E-02: the healthy source is still evaluated when another one overflows."""
    entries = [source_entry("a.ok", relation="raw.ok"), source_entry("b.huge", relation="raw.huge")]
    reader = FakeReader(
        {
            "raw.ok": FRIDAY_DATA,
            # 23:30 UTC is a valid ``datetime`` but past Berlin's midnight: formatting the
            # explanation overflows, and the overflow must stay on this one source.
            "raw.huge": RawObservation(datetime(9999, 12, 31, 23, 30)),
        }
    )

    report = run_check(entries, reader, FakeCalendarProvider(), ECB_NOW)

    by_id = {result.source_id: result for result in report.results}
    assert by_id["b.huge"].status is Status.QUERY_ERROR
    assert by_id["b.huge"].error is not None
    assert by_id["b.huge"].error.code == "E502"
    assert "9999-12-31 23:30:00" in by_id["b.huge"].error.message
    assert by_id["a.ok"].status is Status.ON_TIME
    assert report.exit_code == 3


@pytest.mark.parametrize(
    ("statuses", "fatal", "internal", "expected"),
    [
        ([], False, False, 0),
        ([Status.ON_TIME, Status.NOT_DUE], False, False, 0),
        ([Status.ON_TIME, Status.OVERDUE], False, False, 1),
        ([Status.NO_DATA], False, False, 1),
        ([Status.OVERDUE, Status.NO_DATA], False, False, 1),
        ([Status.OVERDUE, Status.QUERY_ERROR], False, False, 3),
        ([Status.QUERY_ERROR], False, False, 3),
        ([Status.OVERDUE, Status.CONFIG_ERROR], False, False, 2),
        ([Status.CONFIG_ERROR], False, False, 2),
        ([], True, False, 2),
        ([], False, True, 3),
        ([Status.CONFIG_ERROR, Status.QUERY_ERROR, Status.OVERDUE], False, False, 2),
        ([Status.QUERY_ERROR, Status.OVERDUE], False, True, 3),  # an internal error outranks 1
        ([], True, True, 2),
    ],
)
def test_u_app_08_exit_code_precedence(
    statuses: list[Status], fatal: bool, internal: bool, expected: int
) -> None:
    assert exit_code_for(statuses, fatal, internal) == expected


def test_u_app_09_run_next_count_and_order() -> None:
    entries = [source_entry("b.second"), source_entry("a.first")]
    provider = FakeCalendarProvider()
    report = run_next(entries, provider, ECB_NOW, count=3)

    assert [entry.source_id for entry in report.sources] == ["a.first", "b.second"]
    first = report.sources[0]
    assert [release.local.strftime("%Y-%m-%d %H:%M") for release in first.releases] == [
        "2026-09-28 16:00",
        "2026-09-29 16:00",
        "2026-09-30 16:00",
    ]
    assert all(
        release.deadline == release.instant + timedelta(hours=2) for release in first.releases
    )
    assert first.schedule_timezone == "Europe/Berlin"
    assert first.error is None

    # count=1 yields a single release, and count=0 yields none.
    assert len(run_next(entries, provider, ECB_NOW, count=1).sources[0].releases) == 1
    assert run_next(entries, provider, ECB_NOW, count=0).sources[0].releases == ()


def test_u_app_09_run_next_reports_a_broken_rule_as_an_error() -> None:
    entries = [
        SourceEntry(
            source_id="a.b",
            origin=Origin.CONFIG,
            rule=None,
            errors=(Issue("E303", "dbt:source.proj.a.b: no loaded_at_field"),),
            location="dbt:source.proj.a.b",
        )
    ]
    report = run_next(entries, FakeCalendarProvider(), ECB_NOW)
    entry = report.sources[0]
    assert entry.releases == ()
    assert entry.error is not None
    assert entry.error.code == "E303"
    assert entry.schedule_timezone is None


def test_u_app_10_run_validate_finds_schedule_calendar_and_warning_problems() -> None:
    expired = source_entry(
        "a.expired", calendar=CalendarSpec(name="cn_workdays"), valid_until=date(2026, 12, 31)
    )
    impossible = source_entry("b.impossible")
    rule = impossible.rule
    assert rule is not None
    impossible = replace(
        impossible, rule=replace(rule, schedule=CronSchedule("0 0 30 2 *", ZoneInfo("UTC")))
    )
    valid_second = source_entry("c.valid_second")
    good = source_entry("d.good")
    # The 34-day notice window reaches 2027-02-07 on 2027-01-04, so this calendar (valid
    # until 2027-02-01) earns W005 without being expired.
    inside_window = source_entry("f.window", valid_until=date(2027, 2, 1))
    unloaded = SourceEntry(
        source_id="e.unloaded",
        origin=Origin.CONFIG,
        rule=None,
        errors=(Issue("E201", "sources[5].schedule.timezone: unknown time zone 'Mars/Olympus'"),),
        warnings=(
            Issue("W006", "calendar of e.unloaded has overrides but no valid_until", "e.unloaded"),
        ),
        location="sources[5]",
    )

    eventually = datetime(2027, 1, 4, 2, 0, tzinfo=UTC)
    report = run_validate(
        [expired, impossible, valid_second, good, inside_window, unloaded],
        FakeCalendarProvider(start_year=1999),
        eventually,
    )

    codes = [issue.code for issue in report.issues]
    assert "E408" in codes  # a.expired: past valid_until
    assert "E209" in codes  # b.impossible: no release in the horizon
    assert "E201" in codes  # e.unloaded: carried from loading
    assert "W006" in codes
    assert "W005" in codes  # f.window: its notice window reaches past valid_until
    # The warning comes from the same function `evaluate` uses and names the horizon.
    w005 = next(issue for issue in report.issues if issue.code == "W005")
    assert w005.location == "f.window"
    assert "was consulted for 2027-02-07, after its valid_until 2027-02-01" in w005.message
    # errors come first, warnings after
    first_warning = next(index for index, code in enumerate(codes) if code.startswith("W"))
    assert all(code.startswith("E") for code in codes[:first_warning])
    assert report.error_count == 3  # expired, impossible, unloaded
    assert report.valid_count == 3
    assert report.warning_count == 2


def test_u_app_10_run_validate_reports_the_horizon_and_calendar_range_errors() -> None:
    entry = source_entry(
        "a.b",
        calendar=CalendarSpec(holiday_calendars=(HolidayCalendarRef("financial", "XECB"),)),
    )
    rule = entry.rule
    assert rule is not None
    # A provider whose range excludes the evaluation year: E405 (the calendar must
    # reference the provider for it to be consulted at all).
    report = run_validate([entry], FakeCalendarProvider(start_year=2030, end_year=2100), ECB_NOW)
    assert [issue.code for issue in report.issues] == ["E405"]
    assert report.error_count == 1


def test_u_app_11_run_next_reports_w005_near_valid_until() -> None:
    """The CN calendar's valid_until is 2026-12-31; at 2026-12-30 the next release is 2027."""
    cn_calendar = CalendarSpec(
        name="cn_workdays",
        holiday_calendars=(HolidayCalendarRef("country", "CN"),),
        valid_until=date(2026, 12, 31),
    )
    entry = source_entry("cn.daily_sales", calendar=cn_calendar)
    rule = entry.rule
    assert rule is not None
    shanghai = ZoneInfo("Asia/Shanghai")
    entry = replace(entry, rule=replace(rule, schedule=BusinessDaysSchedule(time(15, 0), shanghai)))

    report = run_next(
        [entry], FakeCalendarProvider(), datetime(2026, 12, 30, 2, 0, tzinfo=UTC), count=3
    )
    entry_result = report.sources[0]
    assert entry_result.error is None
    # With the fake provider there are no CN holidays, so 2027-01-01 is a business day;
    # what matters here is that the search consulted dates past valid_until (W005).
    assert [release.local.strftime("%Y-%m-%d") for release in entry_result.releases] == [
        "2026-12-30",
        "2026-12-31",
        "2027-01-01",
    ]
    assert [issue.code for issue in entry_result.warnings] == ["W005"]
    assert "was consulted for 2027-" in entry_result.warnings[0].message
    assert entry_result.warnings[0].location == "cn.daily_sales"


def test_u_app_12_run_validate_reports_e206_once_for_a_duplicate_group() -> None:
    """CFG-21: every member carries E206, but ``validate`` prints the group once."""
    first = SourceEntry(
        source_id="a.b", origin=Origin.CONFIG, rule=rule("a.b"), location="sources[0]"
    )
    second = replace(first, location="sources[1]")
    third = replace(first, location="sources[2]")
    entries = mark_duplicate_source_ids([first, second, third])

    report = run_validate(entries, FakeCalendarProvider(), ECB_NOW)

    e206 = [issue for issue in report.issues if issue.code == "E206"]
    assert len(e206) == 1
    assert e206[0].message == ("duplicate source id 'a.b' at sources[0], sources[1] and sources[2]")
    assert report.error_count == 3
    assert report.valid_count == 0


def test_u_app_12_run_validate_keeps_two_identical_e209_messages() -> None:
    """Only E206 is collapsed: two impossible schedules are two separate errors."""
    broken = replace(rule("a.b"), schedule=CronSchedule("0 0 30 2 *", ZoneInfo("UTC")))
    entries = [
        SourceEntry(source_id=source_id, origin=Origin.CONFIG, rule=broken, location="sources[0]")
        for source_id in ("a.b", "c.d")
    ]

    report = run_validate(entries, FakeCalendarProvider(), ECB_NOW)

    assert [issue.code for issue in report.issues] == ["E209", "E209"]
    assert report.error_count == 2


def test_run_explain_returns_the_result_and_the_trace() -> None:
    entry = source_entry("ecb.fx_rates")
    result, lines = run_explain(
        entry,
        FRIDAY_DATA,
        ECB_NOW,
        FakeCalendarProvider(),
        origin_label="config freshcal.yml, sources[0]",
        query_text="SELECT max(_loaded_at) AS observed FROM raw.ecb.fx_rates",
    )
    assert result.status is Status.ON_TIME
    text = "\n".join(lines)
    assert "Source    ecb.fx_rates (config freshcal.yml, sources[0])" in text
    assert text.rstrip().endswith(result.explanation)


def test_run_explain_for_a_rule_that_did_not_load() -> None:
    entry = SourceEntry(
        source_id="a.b",
        origin=Origin.CONFIG,
        rule=None,
        errors=(Issue("E208", "sources[0].grace: 'grace' is required"),),
        location="sources[0]",
    )
    result, lines = run_explain(entry, RawObservation(None), ECB_NOW, FakeCalendarProvider())
    assert result.status is Status.CONFIG_ERROR
    assert lines == [f"Result    CONFIG_ERROR: {result.explanation}"]


def test_run_check_attaches_merge_and_entry_warnings() -> None:
    entry = source_entry("a.b")
    w004 = Issue(
        "W004",
        "'a.b' is defined in both freshcal.yml and the dbt manifest; using the config file "
        "definition",
        "a.b",
    )
    w006 = Issue("W006", "calendar of a.b has overrides but no valid_until", "a.b")
    entry = replace(entry, warnings=(w006,))
    report = run_check(
        [entry],
        FakeReader({"raw.a.b": FRIDAY_DATA}),
        FakeCalendarProvider(),
        ECB_NOW,
        extra_warnings=[w004],
    )
    codes = [issue.code for issue in report.results[0].warnings]
    assert codes == ["W006", "W004"]


def test_a4_warning_without_location_reaches_every_result() -> None:
    """Kills mutant A4: ``_with_warnings`` keeps warnings whose location is ``None``.

    A warning that names no source belongs to every source (the run-level warnings the CLI
    passes in), so ``location in (None, source_id)`` must match; the ``== source_id``
    mutant silently drops it from every result and the operator never sees it.
    """
    entries = [source_entry("a.b"), source_entry("c.d")]
    global_warning = Issue("W004", "manifest v12 is deprecated; regenerate it", None)
    report = run_check(
        entries,
        FakeReader({"raw.a.b": FRIDAY_DATA, "raw.c.d": FRIDAY_DATA}),
        FakeCalendarProvider(),
        ECB_NOW,
        extra_warnings=[global_warning],
    )
    assert [result.source_id for result in report.results] == ["a.b", "c.d"]
    for result in report.results:
        assert [issue.code for issue in result.warnings] == ["W004"]
        assert result.warnings[0].location is None
