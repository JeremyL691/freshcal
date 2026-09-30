"""End-to-end CLI tests: every command, exit code and output channel (§6)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from freshcal import cli
from freshcal.adapters.duckdb_reader import DuckDBReader
from freshcal.core.errors import Issue
from freshcal.core.model import CheckReport

FIXTURES = Path("tests/fixtures/configs/cli")
ON_TIME = FIXTURES / "on_time.yml"
NO_DATA = FIXTURES / "no_data.yml"
MIXED = FIXTURES / "mixed.yml"
MISSING_TABLE = FIXTURES / "missing_table.yml"
MISSING_DATABASE = FIXTURES / "missing_database.yml"
NAIVE_WITHOUT_ZONE = FIXTURES / "naive_without_zone.yml"
NEAR_VALID_UNTIL = FIXTURES / "near_valid_until.yml"
OVERRIDES_WITHOUT_VALID_UNTIL = FIXTURES / "overrides_without_valid_until.yml"
SYNTAX_ERROR = FIXTURES / "syntax_error.yml"

REPORT_SCHEMA = Path("src/freshcal/schemas/report.v1.schema.json")


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_c_01_on_time_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(
        ["check", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00+02:00"], capsys
    )
    assert code == 0
    assert err == ""
    assert "ON_TIME" in out
    assert "ecb.fx_rates: On time: latest release Fri 2026-09-25 16:00 CEST arrived" in out
    assert out.rstrip().endswith("Summary: 1 ON_TIME | exit code 0")


def test_c_02_overdue_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    # Monday's deadline is 18:00 CEST; 18:30 CEST is past it.
    code, out, err = run(
        ["check", "-c", str(ON_TIME), "--now", "2026-09-28T18:30:00+02:00"], capsys
    )
    assert err == ""
    assert code == 1
    assert "OVERDUE" in out
    assert out.rstrip().endswith("Summary: 1 OVERDUE | exit code 1")


def test_c_03_no_data_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["check", "-c", str(NO_DATA), "--now", "2026-09-28T07:30:00+02:00"], capsys)
    assert code == 1
    assert "NO_DATA" in out
    assert "No data: max(_loaded_at) returned NULL" in out


def test_c_04_yaml_syntax_error_exits_two_with_e100(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = run(["check", "-c", str(SYNTAX_ERROR)], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("E100 ")
    assert "YAML syntax error" in err
    assert "line 3" in err  # the parser reports the end of the unfinished flow sequence


def test_c_05_naive_now_exits_two_with_e211(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["check", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00"], capsys)
    assert code == 2
    assert err.startswith("E211 ")
    assert "--now must be an ISO 8601 date-time with a UTC offset" in err


def test_c_06_selection_without_match_exits_two_with_e210(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(
        ["check", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00Z", "--select", "nothing.*"],
        capsys,
    )
    assert code == 2
    assert err.startswith("E210 ")
    assert "--select matched no sources: nothing.*" in err


def test_c_06_selection_picks_a_subset(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        ["check", "-c", str(MIXED), "--now", "2026-09-28T07:30:00Z", "--select", "ecb.*"], capsys
    )
    assert code == 0
    assert "ecb.fx_rates" in out
    assert "broken.timezone" not in out


def test_c_07_config_error_and_overdue_together_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(["check", "-c", str(MIXED), "--now", "2026-09-28T17:00:00Z"], capsys)
    assert code == 2
    assert "CONFIG_ERROR" in out
    assert "OVERDUE" in out
    assert "Summary: 1 OVERDUE, 1 CONFIG_ERROR | exit code 2" in out


def test_c_08_missing_table_exits_three(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["check", "-c", str(MISSING_TABLE), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 3
    assert "QUERY_ERROR" in out
    assert "query failed:" in out
    assert "exit code 3" in out


def test_c_09_missing_database_file_exits_three_with_e501(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(
        ["check", "-c", str(MISSING_DATABASE), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 3
    assert "QUERY_ERROR" in out
    assert "E501" in out
    assert "cannot connect to duckdb" in out


def test_c_10_validate_does_not_open_a_connection(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(
        ["validate", "-c", str(MISSING_DATABASE), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 0
    assert err == ""
    assert out.rstrip().endswith("1 source valid, 0 with errors, 0 warnings")


def test_c_11_explain_with_observed_null_needs_no_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    no_connection = FIXTURES / "no_connection.yml"
    no_connection.write_text(
        "version: 1\nsources:\n"
        "  - name: ecb.fx_rates\n"
        "    relation: raw.ecb_fx_rates\n"
        "    loaded_at_field: _loaded_at\n"
        '    schedule: {kind: business_days, time: "16:00", timezone: Europe/Berlin}\n'
        "    grace: 2h\n"
        "    observed_timezone: UTC\n",
        encoding="utf-8",
    )
    try:
        code, out, _ = run(
            [
                "explain",
                "ecb.fx_rates",
                "-c",
                str(no_connection),
                "--now",
                "2026-09-28T07:30:00Z",
                "--observed",
                "null",
            ],
            capsys,
        )
    finally:
        no_connection.unlink()
    assert code == 1  # NO_DATA counts as a freshness failure
    assert "Observed  NULL" in out
    assert "=> NO_DATA" in out
    assert "Result    NO_DATA: No data: max(_loaded_at) returned NULL" in out


def test_c_11_explain_unknown_source_exits_two_with_e212(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(["explain", "nope", "-c", str(ON_TIME)], capsys)
    assert code == 2
    assert err.startswith("E212 ")
    assert "unknown source 'nope'" in err


def test_c_11_explain_reads_the_warehouse_when_observed_is_absent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(
        ["explain", "ecb.fx_rates", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 0
    assert "Query     SELECT max(_loaded_at) AS observed FROM" in out
    assert "=> ON_TIME" in out


def test_c_12_json_output_to_a_file_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "out.json"
    code, out, err = run(
        [
            "check",
            "-c",
            str(ON_TIME),
            "--now",
            "2026-09-28T07:30:00Z",
            "--format",
            "json",
            "--output",
            str(destination),
        ],
        capsys,
    )
    assert code == 0
    assert out == ""
    assert err == ""

    document = json.loads(destination.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))).validate(document)
    assert document["kind"] == "check"
    assert document["results"][0]["source_id"] == "ecb.fx_rates"


def test_c_13_next_count_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(
        [
            "next",
            "-c",
            str(ON_TIME),
            "--now",
            "2026-09-28T07:30:00Z",
            "--count",
            "2",
            "--format",
            "json",
        ],
        capsys,
    )
    assert code == 0
    assert err == ""
    document = json.loads(out)
    assert document["kind"] == "next"
    assert len(document["sources"][0]["releases"]) == 2
    assert document["sources"][0]["releases"][0]["instant"] == "2026-09-28T14:00:00Z"


def test_c_13_next_table(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        ["next", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00Z", "--count", "1"], capsys
    )
    assert code == 0
    assert out.startswith("SOURCE")
    assert (
        "ecb.fx_rates  Mon 2026-09-28 16:00 CEST  2026-09-28T14:00:00Z  Mon 2026-09-28 18:00 CEST"
        in out
    )


def test_c_13_next_rejects_an_out_of_range_count(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["next", "-c", str(ON_TIME), "--count", "101"], capsys)
    assert code == 2
    assert "expected an integer between 1 and 100" in err


def test_c_14_unexpected_exception_exits_three_with_e599(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*args: object, **kwargs: object) -> CheckReport:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_check", explode)
    code, _, err = run(["check", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 3
    assert err.startswith("E599 ")
    assert "internal error: RuntimeError: boom" in err


def test_c_15_naive_column_without_zone_is_e214(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        ["check", "-c", str(NAIVE_WITHOUT_ZONE), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 2
    assert "CONFIG_ERROR" in out
    assert "E214" in out
    assert "set observed_timezone on the source or under defaults" in out


def test_c_16_next_near_valid_until_reports_w005(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        [
            "next",
            "-c",
            str(NEAR_VALID_UNTIL),
            "--now",
            "2026-12-30T10:00:00+08:00",
            "--count",
            "3",
            "--format",
            "json",
        ],
        capsys,
    )
    assert code == 0
    document = json.loads(out)
    codes = [issue["code"] for issue in document["sources"][0]["warnings"]]
    assert "W005" in codes
    Draft202012Validator(json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))).validate(document)


def test_c_16_next_table_shows_w005_under_the_table(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        ["next", "-c", str(NEAR_VALID_UNTIL), "--now", "2026-12-30T10:00:00+08:00"], capsys
    )
    assert code == 0
    assert "  W005 cn.daily_sales: calendar cn_workdays was consulted for" in out


def test_c_17_validate_prints_w006_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(
        ["validate", "-c", str(OVERRIDES_WITHOUT_VALID_UNTIL), "--now", "2026-09-28T07:30:00Z"],
        capsys,
    )
    assert code == 0
    assert err == ""
    assert out.startswith("W006 ")
    assert "has overrides but no valid_until" in out
    assert out.rstrip().endswith("1 source valid, 0 with errors, 1 warnings")


def test_c_17_validate_reports_w005_near_valid_until(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI-04 (W005 part): ``validate`` reports the warning ``next`` and ``check`` report.

    On 2026-12-01 the 34-day notice window of the CN-like rule reaches 2027-01-04, past
    its ``valid_until: 2026-12-31``, so all three commands must say so.
    """
    code, out, err = run(
        ["validate", "-c", str(NEAR_VALID_UNTIL), "--now", "2026-12-01T00:00:00Z"], capsys
    )
    assert code == 0
    assert err == ""
    assert out.startswith("W005 ")
    assert "cn.daily_sales" in out.splitlines()[0]
    assert "calendar cn_workdays was consulted for 2027-01-04" in out
    assert "after its valid_until 2026-12-31" in out
    assert out.rstrip().endswith("1 source valid, 0 with errors, 1 warnings")

    code, out, _ = run(
        ["next", "-c", str(NEAR_VALID_UNTIL), "--now", "2026-12-01T00:00:00Z", "--count", "3"],
        capsys,
    )
    assert code == 0
    assert "  W005 cn.daily_sales: calendar cn_workdays was consulted for 2027-01-04" in out

    code, out, _ = run(
        ["check", "-c", str(NEAR_VALID_UNTIL), "--now", "2026-12-01T00:00:00Z"], capsys
    )
    assert code == 1  # the September data is long overdue, but it is still a verdict
    assert "  W005 calendar cn_workdays was consulted for 2027-01-04" in out


def test_validate_reports_every_top_level_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CFG-04: a fatal config error does not hide the other top-level errors."""
    config = tmp_path / "multi.yml"
    config.write_text(
        "version: 2\n"
        "unknown_section: {}\n"
        "calendars: {c: {weekend: [mon, tue, wed, thu, fri, sat, mon]}}\n",
        encoding="utf-8",
    )
    code, out, err = run(["validate", "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 2
    assert out == ""
    codes = sorted(line.split(maxsplit=1)[0] for line in err.splitlines())
    assert codes == ["E101", "E104", "E406"]


def test_version_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["--version"], capsys)
    assert code == 0
    assert out == f"freshcal {cli.__version__}\n"

    code, out, _ = run(["--help"], capsys)
    assert code == 0
    for command in ("check", "next", "explain", "validate"):
        assert command in out

    # CLI-13: a bare invocation is a usage error, not a help screen.
    code, out, err = run([], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("usage: freshcal")


def test_fatal_issue_is_reported_once_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(["check", "-c", "does-not-exist.yml"], capsys)
    assert code == 2
    assert out == ""
    assert err.count("\n") == 1
    assert err.startswith("E110 ")
    assert Issue("E110", "config file not found: does-not-exist.yml").code in err


@pytest.mark.parametrize(
    ("argv", "expected_code"),
    [
        (["check", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00+02:00"], 0),
        (["explain", "ecb.fx_rates", "-c", str(ON_TIME), "--now", "2026-09-28T07:30:00+02:00"], 0),
    ],
)
def test_the_reader_is_closed_after_the_command(
    argv: list[str],
    expected_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CFG-22: the CLI owns the reader, so it must close it (context manager)."""
    closed: list[bool] = []
    original = DuckDBReader.close

    def spy(self: DuckDBReader) -> None:
        closed.append(True)
        original(self)

    monkeypatch.setattr(DuckDBReader, "close", spy)
    code, _, _ = run(argv, capsys)
    assert code == expected_code
    assert closed == [True]


# ---------------------------------------------------------------------------
# T-7.10 CLI contract fixes: C-18 … C-30 (findings CLI-01/03/04/05/08/13/16/17/19)
# ---------------------------------------------------------------------------

_SOURCE_ENTRY = (
    "  - {name: a.b, relation: t, loaded_at_field: x, grace: 1h, observed_timezone: UTC, "
    "schedule: {kind: business_days, time: '16:00', timezone: UTC}}\n"
)
_NO_CONNECTION_SOURCE = "version: 1\nsources:\n" + _SOURCE_ENTRY
_MANIFEST_HEAD = (
    '{"metadata":{"dbt_schema_version":"https://schemas.getdbt.com/dbt/manifest/v12.json"},'
    '"sources":{'
)
_MANIFEST_NODE = (
    '"source.p.a.b":{"source_name":"a","name":"b","relation_name":"t",'
    '"loaded_at_field":"x","meta":{"freshcal":{"grace":"1h",'
    '"schedule":{"kind":"business_days","time":"16:00","timezone":"UTC"}}}}}}'
)


@pytest.mark.parametrize("argv", [["check"], ["next"], ["explain", "a.b"]])
def test_c_18_a_command_without_connection_is_fatal_e213(
    argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-01: E213 is a usage error (stderr, exit 2, no report), never QUERY_ERROR exit 3."""
    config = tmp_path / "no_connection.yml"
    config.write_text(_NO_CONNECTION_SOURCE, encoding="utf-8")
    code, out, err = run([*argv, "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("E213 ")
    assert "'connection' is required for this command" in err


def test_c_19_explain_reports_the_sources_own_error_not_e213(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-05: a source whose rule failed shows its own E202, not the connection message."""
    config = tmp_path / "broken.yml"
    config.write_text(
        "version: 1\nconnection: {type: duckdb, path: ':memory:'}\nsources:\n"
        "  - {name: a.b, relation: t, loaded_at_field: x, grace: 1h, "
        "schedule: {kind: cron, cron: '0 25 * * *', timezone: UTC}}\n",
        encoding="utf-8",
    )
    code, out, err = run(
        ["explain", "a.b", "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 2
    assert "E202" in err
    assert "E213" not in out + err


def test_c_20_validate_prints_code_location_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-03: every validate line is `CODE location: message`, so E209 names its source."""
    config = tmp_path / "impossible.yml"
    config.write_text(
        "version: 1\nsources:\n"
        "  - {name: alpha.feb30, relation: t, loaded_at_field: x, grace: 1h, "
        "schedule: {kind: cron, cron: '0 0 30 2 *', timezone: UTC}}\n"
        "  - {name: beta.feb31, relation: t, loaded_at_field: x, grace: 1h, "
        "schedule: {kind: cron, cron: '0 0 31 2 *', timezone: UTC}}\n",
        encoding="utf-8",
    )
    code, out, _ = run(["validate", "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 2
    lines = [line for line in out.splitlines() if line.startswith("E209 ")]
    assert len(lines) == 2
    assert any(line.startswith("E209 alpha.feb30: ") for line in lines)
    assert any(line.startswith("E209 beta.feb31: ") for line in lines)


def test_c_20_validate_w006_names_the_source_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-03/CLI-17: the W006 line no longer repeats the source id as its location."""
    config = tmp_path / "w006.yml"
    config.write_text(
        "version: 1\nsources:\n"
        "  - {name: acme.timesheets, relation: t, loaded_at_field: x, grace: 1h, "
        "observed_timezone: UTC, schedule: {kind: business_days, time: '20:00', "
        "timezone: Europe/Berlin}, calendar: {overrides: "
        "[{non_working_days: ['2026-12-24']}]}}\n",
        encoding="utf-8",
    )
    code, out, _ = run(["validate", "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 0
    line = next(line for line in out.splitlines() if line.startswith("W006 "))
    assert line.count("acme.timesheets") == 1
    assert "has overrides but no valid_until" in line


def test_c_21_validate_reports_w004_for_a_config_manifest_overlap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-04 (W004 part): validate reports the warning the merge produced."""
    manifest = tmp_path / "target" / "m.json"
    manifest.parent.mkdir()
    manifest.write_text(_MANIFEST_HEAD + _MANIFEST_NODE, encoding="utf-8")
    config = tmp_path / "overlap.yml"
    config.write_text(
        "version: 1\ndbt: {manifest: target/m.json}\nsources:\n" + _SOURCE_ENTRY,
        encoding="utf-8",
    )
    code, out, err = run(["validate", "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 0
    assert err == ""
    assert any(line.startswith("W004 a.b: ") for line in out.splitlines())
    assert out.rstrip().endswith("1 source valid, 0 with errors, 1 warnings")


def test_c_22_validate_reports_w005_with_its_source(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI-04 (W005 part): the validate W005 line names the source before the message."""
    code, out, err = run(
        ["validate", "-c", str(NEAR_VALID_UNTIL), "--now", "2026-12-01T00:00:00Z"], capsys
    )
    assert code == 0
    assert err == ""
    line = next(line for line in out.splitlines() if line.startswith("W005 "))
    assert line.startswith("W005 cn.daily_sales: calendar cn_workdays was consulted for 2027-01-04")


@pytest.mark.parametrize("command", ["check", "next", "validate"])
def test_c_23_an_empty_catalog_is_e216(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-16: zero merged sources is an error (exit 2), not a silent green run."""
    config = tmp_path / "empty.yml"
    config.write_text(
        "version: 1\nconnection: {type: duckdb, path: ':memory:'}\n", encoding="utf-8"
    )
    code, out, err = run([command, "-c", str(config), "--now", "2026-09-28T07:30:00Z"], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("E216 ")
    assert "no sources to evaluate" in err


def test_c_24_unwritable_output_path_is_e217_before_any_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-08: a bad -o path is E217 (exit 2) before the reader is used; never E599/E501."""
    destination = tmp_path / "missing-dir" / "out.json"
    code, out, err = run(
        [
            "check",
            "-c",
            str(MISSING_DATABASE),
            "--now",
            "2026-09-28T07:30:00Z",
            "--format",
            "json",
            "-o",
            str(destination),
        ],
        capsys,
    )
    assert code == 2
    assert out == ""
    assert err.startswith("E217 cannot write output file ")
    assert "E599" not in err
    assert "E501" not in err
    assert not destination.exists()


def test_c_25_output_write_failure_returns_the_higher_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-08: a write failure exits with max(report code, 2) and names the file."""
    destination = tmp_path / "out.json"
    original = Path.write_text

    def failing(self: Path, *args: object, **kwargs: object) -> int:
        if self == destination:
            raise OSError(28, "No space left on device")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", failing)
    # OVERDUE is exit code 1, so the write failure raises the result to 2.
    code, _, err = run(
        ["check", "-c", str(ON_TIME), "--now", "2026-09-28T18:30:00+02:00", "-o", str(destination)],
        capsys,
    )
    assert code == 2
    assert "E217 cannot write output file" in err
    assert "No space left on device" in err

    # A QUERY_ERROR report (exit code 3) keeps 3: the higher code wins.
    code, _, err = run(
        [
            "check",
            "-c",
            str(MISSING_TABLE),
            "--now",
            "2026-09-28T07:30:00Z",
            "-o",
            str(destination),
        ],
        capsys,
    )
    assert code == 3
    assert "E217" in err


def test_c_26_broken_pipe_exits_quietly_with_the_report_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-08: BrokenPipeError keeps the report's exit code and prints no E599."""

    class BrokenStdout:
        def write(self, text: str) -> int:
            raise BrokenPipeError()

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", BrokenStdout())
    code = cli.main(["check", "-c", str(ON_TIME), "--now", "2026-09-28T18:30:00+02:00"])
    assert code == 1
    assert capsys.readouterr().err == ""


def test_c_27_help_shows_statuses_exit_codes_and_subcommand_descriptions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI-13: --help carries the §3.7.3 status wording and the exit codes."""
    code, out, _ = run(["--help"], capsys)
    assert code == 0
    assert "judged by load timestamps" in out
    assert "still inside their grace windows" in out
    assert "missing after its deadline" in out
    assert "Exit codes" in out
    for status in ("ON_TIME", "NOT_DUE", "OVERDUE", "CONFIG_ERROR", "RUNTIME_ERROR"):
        assert status in out
    for command, phrase in (
        ("check", "Evaluate"),
        ("next", "upcoming expected releases"),
        ("explain", "Trace"),
        ("validate", "Validate"),
    ):
        code, sub_out, _ = run([command, "--help"], capsys)
        assert code == 0
        assert phrase in sub_out
    code, out, _ = run(["explain", "--help"], capsys)
    assert code == 0
    assert "SOURCE_ID" in out


def test_c_28_bare_freshcal_prints_usage_on_stderr_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI-13: a bare `freshcal` is a usage error, not a successful help screen."""
    code, out, err = run([], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("usage: freshcal")
    assert "COMMAND" in err


def test_c_29_non_utf8_stdout_does_not_crash_explain(tmp_path: Path) -> None:
    """CLI-19: stdout is written with backslashreplace, so a non-UTF-8 terminal survives."""
    config = tmp_path / "café.yml"
    config.write_text(_NO_CONNECTION_SOURCE, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "freshcal",
            "explain",
            "a.b",
            "-c",
            str(config),
            "--now=2026-09-28T05:30:00Z",
            "--observed",
            "null",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )
    assert result.returncode == 1  # NO_DATA is still a verdict on a non-UTF-8 terminal
    assert "E599" not in result.stderr
    assert "Traceback" not in result.stderr
    assert "caf" in result.stdout


def test_c_30_validate_says_one_source_valid(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI-17: the summary uses the singular for exactly one valid source."""
    code, out, err = run(
        ["validate", "-c", str(MISSING_DATABASE), "--now", "2026-09-28T07:30:00Z"], capsys
    )
    assert code == 0
    assert err == ""
    assert out.rstrip().endswith("1 source valid, 0 with errors, 0 warnings")
    assert "1 sources valid" not in out


def test_c_31_explain_labels_a_dbt_source_with_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CFG-21: the trace says `manifest <path>`, not `config <path>`, for a dbt source."""
    manifest = tmp_path / "target" / "m.json"
    manifest.parent.mkdir()
    manifest.write_text(_MANIFEST_HEAD + _MANIFEST_NODE, encoding="utf-8")
    config = tmp_path / "f.yml"
    config.write_text("version: 1\ndbt: {manifest: target/m.json}\n", encoding="utf-8")
    code, out, err = run(
        [
            "explain",
            "a.b",
            "-c",
            str(config),
            "--now",
            "2026-09-28T05:30:00Z",
            "--observed",
            "null",
        ],
        capsys,
    )
    assert code == 1  # NO_DATA
    assert err == ""
    assert f"Source    a.b (manifest {manifest}" in out
    assert "config " not in out.splitlines()[0]


def test_c_32_explain_reader_failure_goes_to_the_trace_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-19: a reader that cannot be built is the same query error as a failed read.

    `check` reports it inside the report on stdout; `explain` must write its
    `Result    QUERY_ERROR: …` line to the same stream as the rest of the trace instead
    of sending the error to stderr and leaving stdout empty.
    """
    config = tmp_path / "f.yml"
    config.write_text(
        "version: 1\nconnection: {type: duckdb, path: '/nonexistent-dir-freshcal/x.duckdb'}\n"
        "sources:\n" + _SOURCE_ENTRY,
        encoding="utf-8",
    )
    code, out, err = run(
        ["explain", "a.b", "-c", str(config), "--now", "2026-09-28T05:30:00Z"], capsys
    )
    assert code == 3
    assert out.startswith("Result    QUERY_ERROR: Query error: E501 ")
    assert err == ""
