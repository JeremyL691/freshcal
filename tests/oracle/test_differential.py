"""ORC-02 and ORC-03: differential tests, FreshCal's searches versus the brute-force oracle.

The default run (this module, no marker) covers the schedule kinds that T-7.1 can already
compare — `business_days` and `monthly_business_day` — with a few seeds, so a normal `pytest`
run catches a semantic drift in minutes. The full seeded campaign behind ``-m oracle``
(>= 20 seeds x 500 cases, rule P1's proof obligation) runs in CI and at every close-out; the
cron cases and the recorded regressions joined it in T-7.2.

Failure messages carry the case description (zone, kind, expression, calendar, grace,
`active_from`) so a disagreement can be replayed without the seed.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from tests.oracle import oracle
from tests.oracle.cases import (
    CRONITER_DAY_OR_REFUSALS,
    build,
    compare_case,
    describe,
)

from freshcal.core.model import CronSchedule

DEFAULT_SEEDS = (11, 12, 13)
DEFAULT_PER_SEED = 12
ORACLE_SEEDS = tuple(range(100, 120))
ORACLE_PER_SEED = 500
BUSINESS_KINDS = ("bd", "monthly")
REGRESSIONS = Path(__file__).with_name("regressions.json")


def _run_campaign(
    seeds: tuple[int, ...], per_seed: int, focuses: tuple[str, ...]
) -> tuple[list[dict], Counter[str]]:
    """Run one seeded campaign; return its disagreements and the syntax it actually exercised.

    The coverage counter is what makes the campaign's claim checkable (T-8.3/T-8.10): a run
    that never drew the nth-weekday or the OR-refusal expressions cannot prove them.
    """
    disagreements: list[dict] = []
    coverage: Counter[str] = Counter()
    for seed in seeds:
        rng = random.Random(seed)
        for index in range(per_seed):
            focus = focuses[index % len(focuses)]
            rule, provider, instant = build(rng, focus)
            expression = rule.schedule.expression if isinstance(rule.schedule, CronSchedule) else ""
            if "#" in expression:
                coverage["hash"] += 1
                if expression.split()[2] != "*":
                    coverage["hash_with_day_of_month"] += 1
            if expression in CRONITER_DAY_OR_REFUSALS:
                coverage["day_or_refusal"] += 1
            for check, args, got, want in compare_case(rule, provider, instant, rng, oracle):
                disagreements.append(
                    {
                        "seed": seed,
                        "case": index,
                        "rule": describe(rule),
                        "check": check,
                        "args": args,
                        "got": got,
                        "want": want,
                    }
                )
    return disagreements, coverage


def _coverage_line(seeds: tuple[int, ...], per_seed: int, coverage: Counter[str]) -> str:
    families = ", ".join(f"{name}={count}" for name, count in sorted(coverage.items()))
    cases = len(seeds) * per_seed
    return f"campaign: {len(seeds)} seeds x {per_seed} cases = {cases} cases; {families}"


def _report(disagreements: list[dict]) -> str:
    return "\n".join(json.dumps(item, default=str) for item in disagreements[:10])


def test_orc_02_business_day_kinds_match_the_oracle() -> None:
    """`business_days` and `monthly_business_day` over seeded random rules and calendars."""
    disagreements, _coverage = _run_campaign(DEFAULT_SEEDS, DEFAULT_PER_SEED, BUSINESS_KINDS)
    assert disagreements == [], _report(disagreements)


@pytest.mark.oracle
def test_orc_full_campaign() -> None:
    """The full seeded campaign behind `-m oracle` (rule P1: >= 20 seeds x 500 cases).

    One campaign over every schedule kind, so the quoted numbers describe a single run:
    `ORACLE_SEEDS` x `ORACLE_PER_SEED` = 10 000 cases with 0 disagreements.
    """
    disagreements, coverage = _run_campaign(ORACLE_SEEDS, ORACLE_PER_SEED, ALL_FOCUSES)
    print(_coverage_line(ORACLE_SEEDS, ORACLE_PER_SEED, coverage))
    assert disagreements == [], _report(disagreements)


CRON_FOCUSES = ("cron", "gap", "rolling")
ALL_FOCUSES = ("cron", "gap", "rolling", "bd", "monthly")


def _replay_regression(case: dict) -> list[object] | None:
    """Rebuild one recorded finding and return what the implementation answers now."""
    from datetime import date, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from tests.oracle.cases import REF, SeededProvider, release_tuple

    from freshcal.core.calendar import BusinessCalendar
    from freshcal.core.errors import ConfigError
    from freshcal.core.model import (
        BusinessDaysSchedule,
        CalendarSpec,
        CronSchedule,
        FreshnessTarget,
        MonthlyBusinessDaySchedule,
        NonBusinessDayPolicy,
        Origin,
        RawObservation,
        SourceRule,
        Weekday,
    )
    from freshcal.core.schedule import (
        next_release_after,
        previous_release_at_or_before,
        releases_between,
    )
    from freshcal.core.verdict import evaluate

    description = case["rule"]
    zone = ZoneInfo(description["tz"])
    if description["kind"] == "cron":
        schedule = CronSchedule(
            description["expr"], zone, NonBusinessDayPolicy(description["policy"])
        )
    elif description["kind"] == "business_days":
        schedule = BusinessDaysSchedule(time.fromisoformat(description["at"]), zone)
    else:
        schedule = MonthlyBusinessDaySchedule(
            description["n"], time.fromisoformat(description["at"]), zone
        )
    hours, minutes, seconds = (
        float(part) for part in description["grace"].split(", ")[-1].split(":")
    )
    days = int(description["grace"].split(" day")[0]) if "day" in description["grace"] else 0
    grace = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    spec = CalendarSpec(
        weekend=frozenset(Weekday(value) for value in description["weekend"]),
        holiday_calendars=(REF,),
        extra_working_days=frozenset(date.fromisoformat(x) for x in description["X+"]),
        extra_non_working_days=frozenset(date.fromisoformat(x) for x in description["X-"]),
    )
    active_from = (
        None
        if description["active_from"] == "None"
        else date.fromisoformat(description["active_from"])
    )
    rule = SourceRule(
        source_id="oracle.regression",
        origin=Origin.CONFIG,
        schedule=schedule,
        calendar=spec,
        grace=grace,
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=None,
        active_from=active_from,
    )
    provider = SeededProvider(case["provider_seed"], case["density"])
    calendar = BusinessCalendar(rule.calendar, provider)
    args = case["args"]
    try:
        if case["check"] == "evaluate":
            observed = None if args["O"] is None else datetime.fromisoformat(args["O"])
            result = evaluate(
                rule, RawObservation(observed), datetime.fromisoformat(args["now"]), provider
            )
            got: list[object] = [
                result.status.value,
                result.release and result.release.instant.isoformat(),
                result.deadline and result.deadline.isoformat(),
                result.next_expected_arrival and result.next_expected_arrival.isoformat(),
                result.missed_count,
                result.pending_count,
                result.missed_truncated,
                result.error and result.error.code,
            ]
            return got
        if case["check"] == "next_release_after":
            return release_tuple(
                next_release_after(rule, datetime.fromisoformat(args["t"]), calendar)
            )
        if case["check"] == "next_release_after_until":
            return release_tuple(
                next_release_after(
                    rule,
                    datetime.fromisoformat(args["t"]),
                    calendar,
                    inclusive=args["inclusive"],
                    until=datetime.fromisoformat(args["until"]),
                )
            )
        if case["check"] == "previous_release_at_or_before":
            return release_tuple(
                previous_release_at_or_before(rule, datetime.fromisoformat(args["t"]), calendar)
            )
        if case["check"] == "releases_between":
            return [
                release_tuple(release)
                for release in releases_between(
                    rule,
                    datetime.fromisoformat(args["A"]),
                    datetime.fromisoformat(args["B"]),
                    calendar,
                )
            ][:6]
    except ConfigError as failure:
        return ["CONFIG_ERROR", failure.issue.code]
    raise AssertionError(f"unknown check in the regression file: {case['check']}")


def test_orc_03_cron_schedules_match_the_oracle() -> None:
    """Cron schedules (every policy, gap and rolling focus) agree with the oracle."""
    disagreements, coverage = _run_campaign(DEFAULT_SEEDS, DEFAULT_PER_SEED, CRON_FOCUSES)
    print(_coverage_line(DEFAULT_SEEDS, DEFAULT_PER_SEED, coverage))
    assert disagreements == [], _report(disagreements)


def test_orc_04_recorded_regressions() -> None:
    """Every disagreement the audit recorded against v0.1.0 is gone (T-7.2 acceptance)."""
    cases = json.loads(REGRESSIONS.read_text())
    assert len(cases) >= 100
    failures = []
    for case in cases:
        got = _replay_regression(case)
        if got != case["expect"]:
            failures.append(
                {
                    "source": case["source"],
                    "rule": case["rule"],
                    "check": case["check"],
                    "args": case["args"],
                    "got": got,
                    "expect": case["expect"],
                    "oracle_limit": case["oracle_limit"],
                }
            )
    print(
        f"regressions replayed: {len(cases)} "
        f"({sum(1 for case in cases if case['oracle_limit'])} pinned to the v0.1.0 answer)"
    )
    assert failures == [], _report(failures)


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def _workflow_campaign_command() -> list[str]:
    """The pytest arguments of the workflow's full-campaign run step (AUD-05)."""
    lines = [
        line.strip().replace('"', "").replace("'", "")
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
    ]
    matches = [line for line in lines if "pytest" in line and "-m oracle" in line]
    assert len(matches) == 1, f"expected exactly one campaign step, got {matches}"
    words = matches[0].split()
    index = next(i for i, word in enumerate(words) if word.endswith("pytest"))
    args = [word for word in words[index + 1 :] if word != "-s"]
    assert "tests/oracle" in args, args
    return args


def _collect(args: list[str]) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        },
        check=False,
    )
    return completed.stdout + completed.stderr


def test_orc_15_the_ci_workflow_selects_the_full_campaign() -> None:
    """AUD-05: CI runs the campaign, and the exact workflow command selects it.

    Text claiming that CI runs the campaign is not evidence. The default `addopts` exclude
    the `oracle` marker, so the guard proves both halves: the same command without `-m
    oracle` does *not* collect the campaign, and the workflow's command does. The campaign
    size is asserted here too, so a smaller campaign cannot pass as the full one.
    """
    assert len(ORACLE_SEEDS) >= 20, ORACLE_SEEDS
    assert len(ORACLE_SEEDS) * ORACLE_PER_SEED >= 10_000, (ORACLE_SEEDS, ORACLE_PER_SEED)

    default = _collect(["tests/oracle"])
    assert "test_orc_full_campaign" not in default, default[-400:]

    campaign = _collect(_workflow_campaign_command())
    assert "test_orc_full_campaign" in campaign, campaign[-400:]
    # The recorded regressions are unmarked, so they run in the ordinary matrix job; the
    # campaign job selects the marked campaign only.
    assert "test_orc_04_recorded_regressions" not in campaign, campaign[-400:]
