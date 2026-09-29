"""Command-line interface: argparse parser, composition root, exit codes (§6).

``main`` returns the exit code instead of calling ``sys.exit``, so tests can call it
directly; the console script and ``python -m freshcal`` both wrap it. argparse usage
errors already exit 2, which is FreshCal's configuration-error code, so the two agree.

The composition happens here and only here: config file → catalogs → selection → clock →
reader → use case → reporter. Adapters are constructed from the ``connection`` section;
a failure to construct one is not fatal — it becomes ``QUERY_ERROR`` for every valid
source, because a warehouse that cannot be reached says nothing about the configuration.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from freshcal import __version__
from freshcal.adapters.clock import FixedClock, SystemClock
from freshcal.adapters.dbt_manifest import DbtManifestCatalog
from freshcal.adapters.duckdb_reader import DuckDBReader
from freshcal.adapters.holidays_provider import HolidaysCalendarProvider
from freshcal.adapters.postgres_reader import PostgresReader
from freshcal.adapters.report_json import JsonReporter
from freshcal.adapters.report_table import TableReporter
from freshcal.app import (
    exit_code_for,
    merge_entries,
    query_error_result,
    run_check,
    run_explain,
    run_next,
    run_validate,
    select_entries,
)
from freshcal.config.loader import (
    AppConfig,
    DuckDBConnection,
    PostgresConnection,
    load_config,
)
from freshcal.core.errors import ConfigError, Issue, QueryError
from freshcal.core.model import EvaluationResult, RawObservation, SourceEntry
from freshcal.core.ports import CalendarProvider, FreshnessReader
from freshcal.core.timeutil import parse_instant

__all__ = ["build_parser", "main"]

DEFAULT_CONFIG = Path("freshcal.yml")
DEFAULT_NEXT_COUNT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freshcal",
        description=(
            "Business-calendar-aware data freshness checks: declare when data should "
            "arrive and how late it may be, then ask whether a due release is missing."
        ),
    )
    parser.add_argument("--version", action="version", version=f"freshcal {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = subparsers.add_parser("check", help="evaluate sources and print a report")
    _add_common(check, with_select=True)
    _add_format(check, with_output=True)

    next_parser = subparsers.add_parser("next", help="list upcoming expected releases")
    _add_common(next_parser, with_select=True)
    _add_format(next_parser, with_output=True)
    next_parser.add_argument(
        "--count",
        type=_count,
        default=DEFAULT_NEXT_COUNT,
        metavar="N",
        help=f"upcoming releases per source, 1-100 (default {DEFAULT_NEXT_COUNT})",
    )

    explain = subparsers.add_parser("explain", help="step-by-step reasoning for one source")
    explain.add_argument("source_id", metavar="SOURCE_ID")
    _add_common(explain, with_select=False)
    explain.add_argument(
        "--observed",
        metavar="VALUE",
        default=None,
        help=(
            "use VALUE instead of querying: an ISO 8601 date-time with or without a UTC "
            "offset, or 'null'"
        ),
    )

    validate = subparsers.add_parser("validate", help="validate config and schedules")
    _add_common(validate, with_select=True)
    return parser


def _add_common(parser: argparse.ArgumentParser, *, with_select: bool) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        metavar="PATH",
        help=f"config file (default {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--now",
        metavar="INSTANT",
        default=None,
        help="evaluation instant, ISO 8601 with a UTC offset (default: the system clock)",
    )
    if with_select:
        parser.add_argument(
            "-s",
            "--select",
            action="append",
            default=None,
            metavar="PATTERN",
            help="fnmatch pattern on the source ID; repeatable",
        )


def _add_format(parser: argparse.ArgumentParser, *, with_output: bool) -> None:
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default table)",
    )
    if with_output:
        parser.add_argument(
            "-o",
            "--output",
            type=Path,
            default=None,
            metavar="PATH",
            help="write the report to PATH instead of stdout",
        )


def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected an integer between 1 and 100, got '{text}'"
        ) from None
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"expected an integer between 1 and 100, got {value}")
    return value


def _parse_observed(text: str) -> RawObservation:
    """``--observed`` accepts an aware instant, a naive one (E214 path), or ``null``."""
    if text == "null":
        return RawObservation(None)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ConfigError(
            Issue(
                "E211",
                f"--observed must be an ISO 8601 date-time (with or without a UTC offset) "
                f"or 'null'; got '{text}'",
            )
        ) from error
    return RawObservation(parsed)


def _catalogs(config: AppConfig) -> tuple[list[SourceEntry], list[Issue]]:
    """The config file's sources, merged with the dbt manifest's when one is configured."""
    manifest_entries: list[SourceEntry] = []
    if config.dbt_manifest is not None:
        catalog = DbtManifestCatalog(config.dbt_manifest, config.defaults, config.named_calendars)
        manifest_entries = catalog.entries()
    return merge_entries(config.entries, manifest_entries, config_file=str(config.path))


def _make_reader(config: AppConfig) -> tuple[FreshnessReader | None, Issue | None]:
    """Build the reader, or return the issue that explains why it could not be built."""
    connection = config.connection
    if connection is None:
        return None, Issue("E213", "'connection' is required for this command")
    try:
        if isinstance(connection, DuckDBConnection):
            return DuckDBReader(connection.path, config_dir=config.directory), None
        assert isinstance(connection, PostgresConnection)
        reader = PostgresReader(
            dsn_env=connection.dsn_env,
            statement_timeout_seconds=connection.statement_timeout_seconds,
        )
        return reader, None
    except QueryError as error:
        return None, error.issue


def _clock(args: argparse.Namespace) -> FixedClock | SystemClock:
    if args.now is not None:
        return FixedClock(parse_instant(args.now, flag="--now"))
    return SystemClock()


def _provider() -> CalendarProvider:
    return HolidaysCalendarProvider()


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def _fatal(error: ConfigError) -> int:
    issue = error.issue
    sys.stderr.write(f"{issue.code} {issue.message}\n")
    return 2


def _internal(error: BaseException) -> int:
    issue = Issue("E599", f"internal error: {type(error).__name__}: {error}")
    sys.stderr.write(f"{issue.code} {issue.message}\n")
    return 3


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --version, --help, and usage errors
        return exc.code if isinstance(exc.code, int) else 0

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "check":
            return _run_check(args)
        if args.command == "next":
            return _run_next_command(args)
        if args.command == "explain":
            return _run_explain_command(args)
        if args.command == "validate":
            return _run_validate_command(args)
        return _internal(ValueError(f"unknown command {args.command!r}"))
    except ConfigError as error:
        return _fatal(error)
    except QueryError as error:
        sys.stderr.write(f"{error.issue.code} {error.issue.message}\n")
        return 3
    except Exception as error:  # every unexpected failure becomes E599 (exit 3)
        return _internal(error)


def _load(args: argparse.Namespace) -> tuple[AppConfig, list[SourceEntry], list[Issue]]:
    config = load_config(args.config)
    entries, warnings = _catalogs(config)
    selected = select_entries(entries, getattr(args, "select", None))
    return config, selected, warnings


def _run_check(args: argparse.Namespace) -> int:
    config, entries, warnings = _load(args)
    reader, reader_error = _make_reader(config)
    now = _clock(args).now()
    report = run_check(
        entries,
        reader,
        _provider(),
        now,
        reader_error=reader_error,
        extra_warnings=warnings,
    )
    text = (
        JsonReporter().render(report) if args.format == "json" else TableReporter().render(report)
    )
    _write(text, args.output)
    return report.exit_code


def _run_next_command(args: argparse.Namespace) -> int:
    _, entries, warnings = _load(args)
    report = run_next(
        entries, _provider(), _clock(args).now(), count=args.count, extra_warnings=warnings
    )
    text = (
        JsonReporter().render_next(report)
        if args.format == "json"
        else TableReporter().render_next(report)
    )
    _write(text, args.output)
    return 2 if any(source.error is not None for source in report.sources) else 0


def _run_explain_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    entries, _ = _catalogs(config)
    lookup = {entry.source_id: entry for entry in entries}
    entry = lookup.get(args.source_id)
    if entry is None:
        raise ConfigError(Issue("E212", f"unknown source '{args.source_id}'", str(args.config)))

    if args.observed is not None:
        raw = _parse_observed(args.observed)
        query_text = None
    else:
        reader, reader_error = _make_reader(config)
        if reader is None or entry.rule is None:
            issue = reader_error or Issue("E213", "'connection' is required for this command")
            sys.stderr.write(f"{issue.code} {issue.message}\n")
            # E213 and per-source errors are configuration problems; anything else is runtime.
            return 2 if issue.code in ("E213",) or entry.rule is None else 3
        try:
            raw = reader.read_latest(entry.rule.target)
        except QueryError as error:
            result = query_error_result(entry, _clock(args).now(), _provider(), error.issue)
            sys.stdout.write(f"Result    {result.status.value}: {result.explanation}\n")
            return 3
        query_text = _query_text(entry.rule.target)

    result, lines = run_explain(
        entry,
        raw,
        _clock(args).now(),
        _provider(),
        origin_label=f"config {args.config}, {entry.location}",
        query_text=query_text,
    )
    text = "\n".join(lines) + "\n"
    for warning in result.warnings:
        text += f"{warning.code} {warning.message}\n"
    if result.error is not None:
        text += f"{result.error.code} {result.error.message}\n"
    _write(text.lstrip("\n"), None)
    return _status_exit_code(result)


def _query_text(target: object) -> str:
    relation = getattr(target, "relation", "?")
    field = getattr(target, "loaded_at_field", "?")
    filter_text = getattr(target, "filter", None)
    text = f"SELECT max({field}) AS observed FROM {relation}"
    if filter_text is not None:
        text += f" WHERE ({filter_text})"
    return text


def _status_exit_code(result: EvaluationResult) -> int:
    return exit_code_for([result.status])


def _run_validate_command(args: argparse.Namespace) -> int:
    _, entries, _ = _load(args)
    report = run_validate(entries, _provider(), _clock(args).now())
    for issue in report.issues:
        sys.stdout.write(f"{issue.code} {issue.message}\n")
    sys.stdout.write(
        f"{report.valid_count} sources valid, {report.error_count} with errors, "
        f"{report.warning_count} warnings\n"
    )
    return 2 if report.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
