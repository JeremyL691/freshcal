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
import os
import sys
from collections.abc import Sequence
from contextlib import closing, suppress
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
from freshcal.core.errors import (
    ConfigError,
    Issue,
    QueryError,
    format_issue,
    read_failure_reason,
)
from freshcal.core.model import EvaluationResult, Origin, RawObservation, SourceEntry
from freshcal.core.ports import CalendarProvider, FreshnessReader
from freshcal.core.timeutil import parse_instant

__all__ = ["build_parser", "main"]

DEFAULT_CONFIG = Path("freshcal.yml")
DEFAULT_NEXT_COUNT = 3

_STATUS_HELP = """\
Statuses:
  ON_TIME   judged by load timestamps, no release whose deadline has passed is
            currently missing. It does not mean past releases were punctual, and a
            reload of old rows can hide a missing release.
  NOT_DUE   at least one release has occurred and is missing, but all missing
            releases are still inside their grace windows.
  OVERDUE   at least one release is missing after its deadline.

Exit codes (precedence 2 > 3 > 1 > 0):
  0  OK                 every evaluated source is ON_TIME or NOT_DUE; validate found no errors.
  1  FRESHNESS_FAILURE  at least one OVERDUE or NO_DATA.
  2  CONFIG_ERROR       invalid config or manifest, CLI usage error, or a CONFIG_ERROR result.
  3  RUNTIME_ERROR      at least one QUERY_ERROR or an unexpected internal error.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freshcal",
        description=(
            "Business-calendar-aware data freshness checks: declare when data should "
            "arrive and how late it may be, then ask whether a due release is missing."
        ),
        epilog=_STATUS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"freshcal {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = subparsers.add_parser(
        "check",
        help="evaluate sources and print a report",
        description=(
            "Evaluate the selected sources and print a freshness report: the status, the "
            "release it depends on, its deadline, the observed timestamp and the next "
            "expected arrival."
        ),
    )
    _add_common(check, with_select=True)
    _add_format(check, with_output=True)

    next_parser = subparsers.add_parser(
        "next",
        help="list upcoming expected releases",
        description=(
            "List the upcoming expected releases of each source; no warehouse is touched, "
            "but the sources and their schedules are still loaded and validated."
        ),
    )
    _add_common(next_parser, with_select=True)
    _add_format(next_parser, with_output=True)
    next_parser.add_argument(
        "--count",
        type=_count,
        default=DEFAULT_NEXT_COUNT,
        metavar="N",
        help=f"upcoming releases per source, 1-100 (default {DEFAULT_NEXT_COUNT})",
    )

    explain = subparsers.add_parser(
        "explain",
        help="step-by-step reasoning for one source",
        description=(
            "Trace how one source's status is decided, step by step: the schedule, the "
            "calendar, the releases around now, and every rule that led to the result."
        ),
    )
    explain.add_argument(
        "source_id",
        metavar="SOURCE_ID",
        help="the source ID to explain, for example ecb.fx_rates",
    )
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

    validate = subparsers.add_parser(
        "validate",
        help="validate config and schedules",
        description=(
            "Validate the config and manifest and check every schedule without a "
            "warehouse: one line per issue as 'CODE location: message', errors first, "
            "then warnings."
        ),
    )
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
        catalog = DbtManifestCatalog(
            config.dbt_manifest,
            config.defaults,
            config.named_calendars,
            config_dir=config.directory,
        )
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


def _e217(path: Path, error: OSError) -> Issue:
    """``E217`` for an output file that cannot be written (CLI-08)."""
    return Issue("E217", f"cannot write output file {path}: {read_failure_reason(error)}")


def _prepare_output(output: Path | None) -> None:
    """Check the ``--output`` path before any query runs (CLI-08).

    Opening the file in append mode proves the whole path is writable without
    truncating an existing report, and turns every failure into ``E217`` (exit 2)
    instead of a query run whose report is then lost.
    """
    if output is None:
        return
    try:
        with output.open("a", encoding="utf-8"):
            pass
    except OSError as error:
        raise ConfigError(_e217(output, error)) from error


def _write(text: str, output: Path | None) -> None:
    if output is None:
        # Flush inside the guarded call: CPython also flushes buffered stdout at interpreter
        # shutdown, and that later flush is what turned a caught broken pipe into an ignored
        # exception and exit code 120 (AUD-04). Flushing here keeps the failure inside the
        # caller's handler, which then points stdout at the null device.
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as error:
        raise ConfigError(_e217(output, error)) from error


def _silence_stdout() -> None:
    """Point stdout at the null device after a broken pipe (CPython's documented recipe).

    The reader is gone, so nothing can be written any more; redirecting the descriptor means
    the interpreter's shutdown flush has nothing left to fail on. Failures here are ignored
    on purpose — the report's exit code is already decided — and no unrelated ``OSError`` is
    swallowed anywhere else.
    """
    # A stream without a descriptor (a test double, an embedded caller) has nothing to
    # redirect, and a closed descriptor cannot be replaced; both are fine.
    with suppress(AttributeError, OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


def _write_report(text: str, output: Path | None, report_code: int) -> int:
    """Write the report and return the exit code it implies (CLI-08).

    A broken pipe on stdout is not an error: the reader went away, so the command exits
    quietly with the report's own code — 0, 1, 2 or 3, never 120 (AUD-04). A failed file
    write still reports the report's code when that is higher than the configuration-error
    code 2, and says with ``E217`` which file could not be written.
    """
    try:
        _write(text, output)
    except BrokenPipeError:
        _silence_stdout()
        return report_code
    except ConfigError as error:
        for issue in error.issues:
            sys.stderr.write(format_issue(issue) + "\n")
        return max(report_code, 2)
    return report_code


def _fatal(error: ConfigError) -> int:
    # Every issue the loader found is reported, one line each (CFG-04).
    for issue in error.issues:
        sys.stderr.write(f"{issue.code} {issue.message}\n")
    return 2


def _internal(error: BaseException) -> int:
    issue = Issue("E599", f"internal error: {type(error).__name__}: {error}")
    sys.stderr.write(f"{issue.code} {issue.message}\n")
    return 3


def _reconfigure_stdout() -> None:
    """Make stdout escape what its encoding cannot represent (CLI-19).

    A terminal whose encoding is not UTF-8 (``PYTHONIOENCODING=ascii``, a legacy
    locale) must not turn a trace with an accented path or a localized holiday name
    into ``E599``; ``backslashreplace`` keeps the report readable in ASCII.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    # A stream that cannot be changed (a StringIO stand-in, a closed pipe) is fine.
    with suppress(AttributeError, OSError, ValueError):
        reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    _reconfigure_stdout()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --version, --help, and usage errors
        return exc.code if isinstance(exc.code, int) else 0

    if args.command is None:
        # A bare invocation is a usage error, exactly like argparse's own (§6.3).
        parser.print_usage(sys.stderr)
        return 2

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


def _load(
    args: argparse.Namespace, *, require_sources: bool = False
) -> tuple[AppConfig, list[SourceEntry], list[Issue]]:
    config = load_config(args.config)
    entries, warnings = _catalogs(config)
    if require_sources and not entries:
        raise ConfigError(Issue("E216", "no sources to evaluate"))
    selected = select_entries(entries, getattr(args, "select", None))
    return config, selected, warnings


def _require_connection(config: AppConfig) -> None:
    """``E213`` (exit 2) for a command that cannot run without a connection (CLI-01)."""
    if config.connection is None:
        raise ConfigError(Issue("E213", "'connection' is required for this command"))


def _run_check(args: argparse.Namespace) -> int:
    config, entries, warnings = _load(args, require_sources=True)
    _require_connection(config)
    _prepare_output(args.output)
    reader, reader_error = _make_reader(config)
    now = _clock(args).now()
    if reader is None:
        report = run_check(
            entries,
            None,
            _provider(),
            now,
            reader_error=reader_error,
            extra_warnings=warnings,
        )
    else:
        # The CLI owns the reader, so it closes it as soon as the evaluation is done (CFG-22).
        with closing(reader):
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
    return _write_report(text, args.output, report.exit_code)


def _run_next_command(args: argparse.Namespace) -> int:
    config, entries, warnings = _load(args, require_sources=True)
    _require_connection(config)
    _prepare_output(args.output)
    report = run_next(
        entries, _provider(), _clock(args).now(), count=args.count, extra_warnings=warnings
    )
    text = (
        JsonReporter().render_next(report)
        if args.format == "json"
        else TableReporter().render_next(report)
    )
    code = 2 if any(source.error is not None for source in report.sources) else 0
    return _write_report(text, args.output, code)


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
        if entry.rule is None:
            # CLI-05: a source whose rule failed shows its own errors, never E213.
            issues = entry.errors or (
                Issue("E209", "schedule produces no release within 1830 days"),
            )
            for issue in issues:
                sys.stderr.write(format_issue(issue) + "\n")
            return 2
        reader, reader_error = _make_reader(config)
        if reader is None:
            assert reader_error is not None
            if reader_error.code == "E213":
                # A missing connection is a fatal usage error (CLI-01/§6.2): there is no
                # trace to carry it, so it goes to stderr and exits 2.
                sys.stderr.write(format_issue(reader_error) + "\n")
                return 2
            # CLI-19: every other reader failure is the same QUERY_ERROR as a failed read,
            # written to the stream the rest of the trace uses (E501/E505, §6.3).
            result = query_error_result(entry, _clock(args).now(), _provider(), reader_error)
            lines = [f"Result    {result.status.value}: {result.explanation}"]
            return _write_report("\n".join(lines) + "\n", None, 3)
        try:
            with closing(reader):
                raw = reader.read_latest(entry.rule.target)
        except QueryError as error:
            result = query_error_result(entry, _clock(args).now(), _provider(), error.issue)
            lines = [f"Result    {result.status.value}: {result.explanation}"]
            return _write_report("\n".join(lines) + "\n", None, 3)
        query_text = _query_text(entry.rule.target)

    result, lines = run_explain(
        entry,
        raw,
        _clock(args).now(),
        _provider(),
        origin_label=_origin_label(args, config, entry),
        query_text=query_text,
    )
    text = "\n".join(lines) + "\n"
    for warning in result.warnings:
        text += f"{warning.code} {warning.message}\n"
    if result.error is not None:
        text += f"{result.error.code} {result.error.message}\n"
    return _write_report(text.lstrip("\n"), None, _status_exit_code(result))


def _origin_label(args: argparse.Namespace, config: AppConfig, entry: SourceEntry) -> str:
    """Where the trace's ``Source`` line says the entry came from (CFG-21).

    A dbt-manifest source is labelled with the manifest it was read from, not with the
    config file, which for a manifest-only source would name the wrong document.
    """
    if entry.origin is Origin.DBT_MANIFEST:
        return f"manifest {config.dbt_manifest}, {entry.location}"
    return f"config {args.config}, {entry.location}"


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
    _, entries, warnings = _load(args, require_sources=True)
    report = run_validate(entries, _provider(), _clock(args).now(), extra_warnings=warnings)
    lines = [format_issue(issue) for issue in report.issues]
    noun = "source" if report.valid_count == 1 else "sources"
    lines.append(
        f"{report.valid_count} {noun} valid, {report.error_count} with errors, "
        f"{report.warning_count} warnings"
    )
    code = 2 if report.error_count else 0
    return _write_report("\n".join(lines) + "\n", None, code)


if __name__ == "__main__":
    sys.exit(main())
