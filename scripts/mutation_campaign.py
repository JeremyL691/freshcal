"""Mutation spot-check campaign (BLUEPRINT §13 rule P9, task T-7.4).

A product-side adaptation of the owner's reviewer harness
(``review/v0.1.0/D-process-tests/mutate.py``, kept read-only): one mutant at a time is
applied to a scratch copy of the working tree under ``/tmp``, a targeted test subset is
run (and, if the mutant survives, the whole suite except ``tests/perf``), then the file
is restored. The repository itself is never mutated.

The table below is the reviewer's 46 hand-picked mutants. Five of them (S4, S9, S10, S11,
S14) targeted code that T-7.2 replaced; they keep their original IDs but are re-expressed
against the current search (A-9): the old ``padding``/``lookback`` terms now live in
``_scan_lookback()``, the old ``_candidate_release`` window check in ``_earliest_release``'s
``qualifies`` expression, the old ``_needs_exact_verification`` guard in the
``best.dst != "gap"`` fast path, and the old ``HOLD_BACK`` constant in ``_hold()``.
S12 is expected to remain equivalent: ``previous_release_at_or_before`` returns when its
window reaches ``limit``, so ``hi == limit`` is unreachable and ``>=`` vs ``>`` cannot
differ. Any other survivor is a finding, not a result.

Usage::

    uv run python scripts/mutation_campaign.py                  # all mutants
    uv run python scripts/mutation_campaign.py V1 S12           # only these IDs
    uv run python scripts/mutation_campaign.py --report /tmp/mutations.json

Exit code 0 only when every non-equivalent mutant was killed and no pattern was missing
or ambiguous. The scratch copy is created fresh under ``/tmp`` on every run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Scratch-only copy of the working tree; .git and .venv are never copied.
_EXCLUDED = (
    ".git",
    ".venv",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".import_linter_cache",
    "review",
    "dist",
    "build",
    "htmlcov",
    "__pycache__",
)

CORE_V = (
    "tests/unit/core/test_verdict.py",
    "tests/unit/core/test_explain.py",
    "tests/golden",
    "tests/property/test_verdict_properties.py",
)
CORE_S = (
    "tests/unit/core/test_schedule.py",
    "tests/property/test_schedule_properties.py",
    "tests/golden",
    "tests/unit/core/test_verdict.py",
)
CORE_C = (
    "tests/unit/core/test_calendar.py",
    "tests/unit/core/test_schedule.py",
    "tests/property/test_schedule_properties.py",
    "tests/golden",
)
CORE_O = (
    "tests/unit/core/test_observation.py",
    "tests/unit/core/test_verdict.py",
    "tests/golden",
)
APP = ("tests/unit/test_app.py", "tests/cli")
JSONR = ("tests/unit/adapters/test_report_json.py", "tests/cli")

V = "src/freshcal/core/verdict.py"
S = "src/freshcal/core/schedule.py"
C = "src/freshcal/core/calendar.py"
OBS = "src/freshcal/core/observation.py"
A = "src/freshcal/app.py"
MD = "src/freshcal/core/model.py"
J = "src/freshcal/adapters/report_json.py"


@dataclass(frozen=True, slots=True)
class Mutant:
    """One mutation: replace exactly one occurrence of ``old`` with ``new``."""

    id: str
    file: str
    old: str
    new: str
    targets: tuple[str, ...]
    equivalent_reason: str | None = None


MUTANTS: tuple[Mutant, ...] = (
    # --- verdict.py: decision table and deadline comparisons
    Mutant(
        "V1",
        V,
        "if recent is not None and now > recent.instant + rule.grace:",
        "if recent is not None and now >= recent.instant + rule.grace:",
        CORE_V,
    ),
    Mutant(
        "V2",
        V,
        "missed = [release for release in unarrived if now > release.instant + rule.grace]",
        "missed = [release for release in unarrived if now >= release.instant + rule.grace]",
        CORE_V,
    ),
    Mutant(
        "V3",
        V,
        "pending = [release for release in unarrived if now <= release.instant + rule.grace]",
        "pending = [release for release in unarrived if now < release.instant + rule.grace]",
        CORE_V,
    ),
    Mutant(
        "V4",
        V,
        "truncated = truncated or len(unarrived) == MAX_COUNTED_RELEASES",
        "truncated = truncated or len(unarrived) > MAX_COUNTED_RELEASES",
        CORE_V,
    ),
    Mutant(
        "V5", V, "if start >= now - SEARCH_HORIZON:", "if start > now - SEARCH_HORIZON:", CORE_V
    ),
    Mutant(
        "V6",
        V,
        "            start = observation.instant\n            inclusive = False",
        "            start = observation.instant\n            inclusive = True",
        CORE_V,
    ),
    Mutant(
        "V7",
        V,
        "            start = resolve_local(datetime.combine(rule.active_from, time.min), "
        "timezone)\n            inclusive = True",
        "            start = resolve_local(datetime.combine(rule.active_from, time.min), "
        "timezone)\n            inclusive = False",
        CORE_V,
    ),
    Mutant("V8", V, "        return recent, True", "        return recent, False", CORE_V),
    Mutant(
        "V9",
        V,
        "rule, start, calendar, inclusive=inclusive, until=start + SEARCH_HORIZON",
        "rule, start, calendar, inclusive=inclusive, until=now",
        CORE_V,
    ),
    Mutant(
        "V10",
        V,
        # T-7.5 (A-11) moved the W005 condition into `calendar_notice`/`note_consult_horizon`.
        # The guard in `calendar_notice` is redundant with "nothing was consulted"; the
        # non-equivalent mutation is the horizon guard, which is what makes a plain cron
        # (policy `none`) warn about `valid_until` it never consults.
        "    if consulted is None or valid_until is None:\n        return None",
        "    if valid_until is None:\n        return None",
        (*CORE_V, "tests/unit/test_app.py", "tests/cli"),
    ),
    Mutant(
        "V11",
        V,
        "                Status.ON_TIME,\n                calendar,\n                release=last,",
        "                Status.ON_TIME,\n                calendar,\n                release=None,",
        CORE_V,
    ),
    Mutant(
        "V12",
        V,
        "                missed_count=len(missed),",
        "                missed_count=len(unarrived),",
        CORE_V,
    ),
    # --- schedule.py: windows, floor, clamping, policies, streaming search
    Mutant(
        "S1",
        S,
        "if start <= instant <= end and instant not in yielded",
        "if start < instant <= end and instant not in yielded",
        CORE_S,
    ),
    Mutant(
        "S2",
        S,
        "if start <= instant <= end and instant not in yielded",
        "if start <= instant < end and instant not in yielded",
        CORE_S,
    ),
    Mutant(
        "S3",
        S,
        "        if floor is not None and instant < floor:\n            continue\n"
        "        if start <= instant",
        "        if floor is not None and instant <= floor:\n            continue\n"
        "        if start <= instant",
        CORE_S,
    ),
    # S4 was the generator padding ``DATE_PADDING_DAYS + MAX_ROLL_DAYS``; T-7.2 replaced
    # it with the ``_scan_lookback`` call (the lookback itself now lives there), so this
    # mutates the generator's own scan start.
    Mutant(
        "S4",
        S,
        "    scan_local = (\n"
        "        (start - _scan_lookback(rule, start, timezone)).astimezone(timezone)"
        ".replace(tzinfo=None)\n"
        "    )",
        "    scan_local = start.astimezone(timezone).replace(tzinfo=None)",
        CORE_S,
    ),
    Mutant(
        "S5",
        S,
        "index = min(schedule.business_day, len(business_days)) - 1",
        "index = min(schedule.business_day, len(business_days) - 1)",
        CORE_S,
    ),
    Mutant(
        "S6",
        S,
        "clamped = schedule.business_day > len(business_days)",
        "clamped = schedule.business_day >= len(business_days)",
        CORE_S,
    ),
    Mutant(
        "S7",
        S,
        "clamped = -schedule.business_day > len(business_days)",
        "clamped = -schedule.business_day >= len(business_days)",
        CORE_S,
    ),
    Mutant(
        "S8",
        S,
        "yield Nominal(local=datetime.combine(rolled, nominal.time()), "
        "adjusted_from=nominal.date())\n    elif policy is NonBusinessDayPolicy.PRECEDING:",
        "yield Nominal(local=datetime.combine(rolled, nominal.time()), "
        "adjusted_from=rolled)\n    elif policy is NonBusinessDayPolicy.PRECEDING:",
        CORE_S,
    ),
    # S9 was ``_needs_exact_verification`` returning True for a non-normal release; the
    # current equivalent is the gap fast path in ``_earliest_release``.
    Mutant(
        "S9",
        S,
        '                if best.dst != "gap":\n                    return best\n',
        "                return best\n",
        CORE_S,
    ),
    # S10 was ``HOLD_BACK = timedelta(hours=6)``; the constant is now the derived
    # ``_hold()`` value used by the streaming generator.
    Mutant(
        "S10",
        S,
        "    return HOLD_BACK + (ROLL_PAD if is_rolling(rule.schedule) else timedelta(0))",
        "    return timedelta(0)",
        CORE_S,
    ),
    # S11 was the ``_candidate_release`` lookback; the same lookback is now applied in
    # ``_earliest_release`` through ``_scan_lookback``.
    Mutant(
        "S11",
        S,
        "    scan_local = (lo - _scan_lookback(rule, lo, timezone)).astimezone(timezone)"
        ".replace(tzinfo=None)",
        "    scan_local = lo.astimezone(timezone).replace(tzinfo=None)",
        CORE_S,
    ),
    Mutant(
        "S12",
        S,
        "    while hi >= limit:",
        "    while hi > limit:",
        CORE_S,
        equivalent_reason=(
            "hi == limit is unreachable: previous_release_at_or_before clamps its "
            "window to limit and returns when lo == limit, so the two conditions "
            "cannot select different iterations"
        ),
    ),
    Mutant(
        "S13",
        S,
        "    if policy is NonBusinessDayPolicy.NONE or calendar.is_business_day(nominal.date()):",
        "    if policy is NonBusinessDayPolicy.NONE:",
        CORE_S,
    ),
    # S14 was the lower window bound in ``_candidate_release``; the same bound is the
    # ``qualifies`` expression in ``_earliest_release``.
    Mutant(
        "S14",
        S,
        "qualifies = lo <= instant <= hi and (instant > t or (inclusive and instant == t))",
        "qualifies = lo < instant <= hi and (instant > t or (inclusive and instant == t))",
        CORE_S,
    ),
    # --- calendar.py: predicate, roll, valid_until
    Mutant(
        "C1",
        C,
        "        if day in self._spec.extra_working_days:\n            return True\n"
        "        if day in self._spec.extra_non_working_days:\n            return False\n"
        "        if day.weekday() in self._spec.weekend:\n            return False\n"
        "        return self.holiday_name(day) is None",
        "        if day.weekday() in self._spec.weekend:\n            return False\n"
        "        if day in self._spec.extra_working_days:\n            return True\n"
        "        if day in self._spec.extra_non_working_days:\n            return False\n"
        "        return self.holiday_name(day) is None",
        CORE_C,
    ),
    Mutant(
        "C2",
        C,
        "for distance in range(1, MAX_ROLL_DAYS + 1):",
        "for distance in range(1, MAX_ROLL_DAYS):",
        CORE_C,
    ),
    Mutant(
        "C3",
        C,
        "if valid_until is not None and local_date > valid_until:",
        "if valid_until is not None and local_date >= valid_until:",
        CORE_C,
    ),
    Mutant(
        "C4",
        C,
        "            and self.max_date_looked_up > valid_until",
        "            and self.max_date_looked_up >= valid_until",
        (*CORE_C, "tests/unit/test_app.py", "tests/cli"),
    ),
    Mutant(
        "C5",
        C,
        "        return self.holiday_name(day) is None",
        "        return day not in self._holidays_for(day.year)",
        (*CORE_C, "tests/unit/test_app.py", "tests/cli"),
        equivalent_reason=(
            "T-7.5 (A-11) records the lookup with `_note_lookup(day)` at the top of "
            "`is_business_day`, before it branches on weekend/override/holiday, so "
            "replacing the holiday branch's `holiday_name` call with a direct dictionary "
            "test changes no observable state. The lookup contract is still pinned by "
            "test_c5_is_business_day_records_the_holiday_lookup"
        ),
    ),
    # --- observation.py: naive/aware normalisation and W003 skew
    Mutant(
        "O1",
        OBS,
        "if observation.instant > now + FUTURE_SKEW_TOLERANCE:",
        "if observation.instant >= now + FUTURE_SKEW_TOLERANCE:",
        CORE_O,
    ),
    Mutant(
        "O2",
        OBS,
        "FUTURE_SKEW_TOLERANCE = timedelta(minutes=5)",
        "FUTURE_SKEW_TOLERANCE = timedelta(minutes=6)",
        CORE_O,
    ),
    Mutant(
        "O3",
        OBS,
        "instant = value.replace(tzinfo=observed_timezone, fold=0).astimezone(UTC)",
        "instant = value.replace(tzinfo=observed_timezone, fold=1).astimezone(UTC)",
        CORE_O,
    ),
    Mutant(
        "O4",
        OBS,
        # T-7.7 re-indented this branch; the mutation is unchanged (drop the W002 warning).
        "            if observed_timezone is not None:\n                warnings.append(",
        "            if False:\n                warnings.append(",
        CORE_O,
    ),
    # --- app.py: exit-code precedence, merge, warnings
    Mutant(
        "A1",
        A,
        "    if fatal_config_error or any(status is Status.CONFIG_ERROR for status in "
        "statuses):\n        return 2\n    if internal_error or any(status is Status.QUERY_ERROR "
        "for status in statuses):\n        return 3",
        "    if internal_error or any(status is Status.QUERY_ERROR for status in "
        "statuses):\n        return 3\n    if fatal_config_error or any(status is "
        "Status.CONFIG_ERROR for status in statuses):\n        return 2",
        APP,
    ),
    Mutant(
        "A2",
        A,
        "if any(status in (Status.OVERDUE, Status.NO_DATA) for status in statuses):",
        "if any(status in (Status.OVERDUE,) for status in statuses):",
        APP,
    ),
    Mutant(
        "A3",
        A,
        "            )\n            continue\n        manifest_survivors.append(entry)",
        "            )\n        manifest_survivors.append(entry)",
        APP,
    ),
    Mutant(
        "A4",
        A,
        "matching = tuple(issue for issue in warnings if issue.location in "
        "(None, result.source_id))",
        "matching = tuple(issue for issue in warnings if issue.location == result.source_id)",
        APP,
    ),
    Mutant(
        "A5",
        MD,
        "        if len(group) < 2:",
        "        if len(group) < 3:",
        (*APP, "tests/unit/config/test_loader.py"),
    ),
    Mutant(
        "A6",
        A,
        "        deadline=release.instant + rule.grace if release is not None else None,\n"
        "        observation=None,",
        "        deadline=None,\n        observation=None,",
        APP,
    ),
    Mutant(
        "A7",
        A,
        "if any(fnmatchcase(entry.source_id, pattern) for pattern in patterns)",
        "if all(fnmatchcase(entry.source_id, pattern) for pattern in patterns)",
        APP,
    ),
    # --- report_json.py: null handling
    Mutant(
        "J1",
        J,
        '        "adjusted_from": None\n        if release.adjusted_from is None\n'
        "        else release.adjusted_from.isoformat(),",
        '        "adjusted_from": str(release.adjusted_from),',
        JSONR,
    ),
    Mutant(
        "J2",
        J,
        '    return {"code": issue.code, "message": issue.message, "location": issue.location}',
        '    return {"code": issue.code, "message": issue.message, '
        '"location": issue.location or ""}',
        JSONR,
    ),
    Mutant(
        "J3",
        J,
        "def _observation(observation: Observation | None) -> dict[str, object] | None:\n"
        "    if observation is None:\n        return None",
        "def _observation(observation: Observation | None) -> dict[str, object] | None:\n"
        "    if observation is None:\n        return {}",
        JSONR,
    ),
    Mutant(
        "J4",
        J,
        '        "interpreted_timezone": observation.interpreted_timezone,',
        '        "interpreted_timezone": observation.interpreted_timezone or "UTC",',
        JSONR,
    ),
)


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _EXCLUDED or name.endswith(".egg-info")}


def make_scratch(root: Path) -> Path:
    """Copy the working tree into a fresh directory under ``root``."""
    scratch = Path(tempfile.mkdtemp(prefix="freshcal-mutation-", dir=root))
    shutil.copytree(REPO, scratch, ignore=_ignore, dirs_exist_ok=True)
    return scratch


def _run(
    scratch: Path, targets: Sequence[str], hypothesis_dir: Path, timeout: int
) -> tuple[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{scratch}/src:{scratch}",
        HYPOTHESIS_STORAGE_DIRECTORY=str(hypothesis_dir),
        PYTHONDONTWRITEBYTECODE="1",
    )
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-x",
        "--no-header",
        *targets,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=scratch, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"no result within {timeout} s"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    failed = [line for line in lines if line.startswith("FAILED")]
    return ("KILLED" if proc.returncode != 0 else "SURVIVED"), (
        failed[0] if failed else " | ".join(lines[-3:])
    )


def run_mutant(mutant: Mutant, scratch: Path, root: Path, timeout: int) -> dict[str, object]:
    """Apply one mutant, run targeted (then full) tests, restore the file."""
    path = scratch / mutant.file
    original = path.read_text(encoding="utf-8")
    count = original.count(mutant.old)
    if count != 1:
        return {
            "id": mutant.id,
            "file": mutant.file,
            "status": "BAD_PATTERN",
            "detail": f"pattern count {count}",
            "stage": "none",
        }
    path.write_text(original.replace(mutant.old, mutant.new), encoding="utf-8")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="hypothesis-", dir=root) as hyp:
            status, detail = _run(scratch, mutant.targets, Path(hyp), timeout)
            stage = "targeted"
            if status == "SURVIVED":
                status, detail = _run(scratch, ("tests", "--ignore=tests/perf"), Path(hyp), timeout)
                stage = "full(no perf)"
    finally:
        path.write_text(original, encoding="utf-8")
    row: dict[str, object] = {
        "id": mutant.id,
        "file": mutant.file,
        "status": status,
        "stage": stage,
        "detail": detail,
        "seconds": round(time.monotonic() - started, 1),
    }
    if mutant.equivalent_reason is not None:
        row["equivalent_reason"] = mutant.equivalent_reason
    return row


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FreshCal mutation spot-check campaign (P9)")
    parser.add_argument("only", nargs="*", help="mutant IDs to run (default: all)")
    parser.add_argument("--timeout", type=int, default=1800, help="per-run pytest timeout")
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=Path("/tmp"),
        help="directory that holds the scratch copy (default /tmp)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the JSON result table here (keep it outside the repo)",
    )
    args = parser.parse_args(argv)

    selected = [m for m in MUTANTS if not args.only or m.id in set(args.only)]
    unknown = set(args.only) - {m.id for m in MUTANTS}
    if unknown:
        print(f"unknown mutant IDs: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    scratch = make_scratch(args.scratch_root)
    report_path = Path("/tmp/freshcal-mutation-report.json") if args.report is None else args.report
    print(f"scratch: {scratch} (report: {report_path})", flush=True)
    results: list[dict[str, object]] = []
    try:
        for mutant in selected:
            row = run_mutant(mutant, scratch, args.scratch_root, args.timeout)
            results.append(row)
            print(json.dumps(row), flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    non_equivalent = [row for row in results if "equivalent_reason" not in row]
    killed = [row for row in non_equivalent if row["status"] == "KILLED"]
    equivalents = [row for row in results if "equivalent_reason" in row]
    survived = [row for row in non_equivalent if row["status"] != "KILLED"]
    score = 100.0 * len(killed) / len(non_equivalent) if non_equivalent else 100.0
    summary = {
        "total": len(results),
        "killed": len(killed),
        "non_equivalent": len(non_equivalent),
        "survivors": [row["id"] for row in survived],
        "equivalents": [row["id"] for row in equivalents],
        "score_percent": round(score, 1),
        "results": results,
    }
    print(
        f"score: {len(killed)}/{len(non_equivalent)} non-equivalent killed "
        f"({score:.1f}%); equivalents: "
        f"{', '.join(str(row['id']) for row in equivalents) or 'none'}; "
        f"survivors: {', '.join(str(row['id']) for row in survived) or 'none'}",
        flush=True,
    )
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if not survived and all(row["status"] != "BAD_PATTERN" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
