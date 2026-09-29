"""Tests for the terminal table reporter (BLUEPRINT.md §8.3, §6.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.unit.adapters.report_examples import (
    EVALUATED_AT,
    example_check_report,
    example_next_report,
    make_release,
)

from freshcal import __version__
from freshcal.adapters.report_table import TableReporter
from freshcal.core.errors import Issue
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    NextEntry,
    NextReport,
    Origin,
    Status,
)

BLUEPRINT = Path("BLUEPRINT.md")


def blueprint_block(heading: str) -> str:
    """The first ```-fenced block after a heading, without its fences."""
    text = BLUEPRINT.read_text(encoding="utf-8")
    start = text.index(heading)
    block = text.index("```", start)
    end = text.index("```", block + 3)
    body = text[block + 3 : end]
    return body[body.index("\n") + 1 :]


def test_u_table_01_check_report_matches_the_blueprint_example() -> None:
    rendered = TableReporter().render(example_check_report())
    expected = blueprint_block("### 8.3 Terminal table").replace(
        "FreshCal 0.1.0", f"FreshCal {__version__}"
    )
    assert rendered == expected


def test_u_table_02_missing_values_are_dashed_and_config_errors_render() -> None:
    config_error = EvaluationResult(
        source_id="broken.source",
        origin=Origin.CONFIG,
        status=Status.CONFIG_ERROR,
        evaluated_at=EVALUATED_AT,
        schedule_timezone=None,
        release=None,
        deadline=None,
        observation=None,
        next_expected_arrival=None,
        explanation=(
            "Configuration error: E201 sources[0].schedule.timezone: unknown time zone "
            "'Mars/Olympus'"
        ),
        error=Issue(
            "E201", "sources[0].schedule.timezone: unknown time zone 'Mars/Olympus'", "sources[0]"
        ),
    )
    report = CheckReport(evaluated_at=EVALUATED_AT, results=(config_error,), exit_code=2)
    rendered = TableReporter().render(report)

    lines = rendered.splitlines()
    assert lines[0] == (f"FreshCal {__version__} | evaluated at 2026-09-28T05:30:00Z | 1 source")
    assert lines[2] == "SOURCE         STATUS        RELEASE  DEADLINE  OBSERVED  NEXT EXPECTED"
    assert lines[3] == "broken.source  CONFIG_ERROR  -        -         -         -"
    assert "broken.source: Configuration error: E201" in rendered
    assert "  E201 sources[0].schedule.timezone: unknown time zone 'Mars/Olympus'" in rendered
    assert rendered.rstrip().endswith("Summary: 1 CONFIG_ERROR | exit code 2")


def test_u_table_02_no_color_and_no_box_drawing() -> None:
    rendered = TableReporter().render(example_check_report())
    assert "\x1b[" not in rendered
    assert all(character not in rendered for character in "│─┌┐└┘├┤┬┴┼")


def test_u_table_03_next_table_matches_the_blueprint_example() -> None:
    rendered = TableReporter().render_next(example_next_report())
    expected = blueprint_block("**`freshcal next` example:**").replace("$ ", "").split("\n", 1)[1]
    assert rendered == expected


def test_u_table_03_next_lists_warnings_and_errors_under_the_table() -> None:
    report = NextReport(
        evaluated_at=EVALUATED_AT,
        sources=(
            NextEntry(
                source_id="ecb.fx_rates",
                schedule_timezone="Europe/Berlin",
                releases=example_next_report().sources[0].releases[:1],
                warnings=(
                    Issue(
                        "W005",
                        "calendar target was consulted for 2027-01-04, after its "
                        "valid_until 2026-12-31; the next expected arrival may be wrong",
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
    lines = TableReporter().render_next(report).splitlines()
    assert lines[-2] == (
        "  W005 ecb.fx_rates: calendar target was consulted for 2027-01-04, after its "
        "valid_until 2026-12-31; the next expected arrival may be wrong"
    )
    assert lines[-1] == "  E303 broken.source: dbt:source.proj.broken.source: no loaded_at_field"
    # An entry with no releases still gets a row, with `-` in every time column.
    row = next(line for line in lines if line.startswith("broken.source"))
    assert row.split() == ["broken.source", "-", "-", "-"]


def test_u_table_03_next_without_sources() -> None:
    assert TableReporter().render_next(NextReport(evaluated_at=EVALUATED_AT, sources=())) == (
        "no sources\n"
    )


def test_columns_are_padded_to_the_widest_cell() -> None:
    short = EvaluationResult(
        source_id="a",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="UTC",
        release=make_release("2026-09-25T14:00:00Z", "2026-09-25T14:00:00+00:00"),
        deadline=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
        observation=None,
        next_expected_arrival=None,
        explanation="On time: no release has occurred yet; no next release within 1830 days.",
    )
    report = CheckReport(evaluated_at=EVALUATED_AT, results=(short,), exit_code=0)
    lines = TableReporter().render(report).splitlines()
    header, row = lines[2], lines[3]
    assert header.startswith("SOURCE  STATUS")
    # Every column starts at the same offset in the header and in the row.
    for label, cell in (("STATUS", "ON_TIME"), ("RELEASE", "Fri 2026-09-25 14:00 UTC")):
        assert header.index(label) == row.index(cell)
    # An entry with no observation and no next arrival shows dashes.
    assert row.split()[-1] == "-"
    assert lines[-1] == "Summary: 1 ON_TIME | exit code 0"


def test_summary_lists_only_non_zero_counts_in_status_order() -> None:
    report = example_check_report()
    assert (
        TableReporter()
        .render(report)
        .rstrip()
        .endswith("Summary: 2 ON_TIME, 1 OVERDUE | exit code 1")
    )


def test_explanation_lines_come_after_the_table_with_warnings_indented() -> None:
    rendered = TableReporter().render(example_check_report())
    lines = rendered.splitlines()
    table_start = lines.index(
        "SOURCE                STATUS   RELEASE                    DEADLINE                   "
        "OBSERVED                   NEXT EXPECTED"
    )
    explanations = [line for line in lines if line.startswith("ecb.fx_rates: ")]
    assert len(explanations) == 1
    warning = "  W002 observed_timezone 'UTC' ignored because the value is timezone-aware"
    assert warning in lines
    assert lines.index(warning) > table_start
    # One blank line separates the table block from the explanations, and another one
    # separates the explanations from the summary.
    assert lines[table_start - 1] == ""
    assert lines[lines.index(warning) + 1] == ""
