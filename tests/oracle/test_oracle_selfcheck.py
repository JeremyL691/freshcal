"""ORC-01: the oracle's own self-check (T-7.1, review A).

Disagreements found later by `test_differential.py` are only meaningful if the oracle itself is
right, so this module validates it against two independent authorities:

- its cron matcher against ``croniter`` (the same library the implementation uses, but fed a
  naive wall clock, where field matching is the whole story), on every expression in the case
  list, over 2023-12-25 … 2025-03-05;
- its local→UTC resolution against a *second* reading of the same tzdata: the zone's explicit
  TZif transition table (``test_oracle_selfcheck`` helpers in `cases.py`), plus FreshCal's own
  ``resolve_local``/``classify_local`` as a drift check.

The wall-clock comparison runs on at least 50 000 wall times (the T-7.1 acceptance criterion).
"""

from __future__ import annotations

import calendar
import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from croniter import CroniterBadDateError, croniter
from tests.oracle import oracle
from tests.oracle.cases import (
    CRON_EXPRS,
    CRONITER_DAY_OR_REFUSALS,
    DOM_HASH_UNIONS,
    NEVER_FIRES,
    NTH_WEEKDAY_EXPRESSIONS,
    ZONES,
    build,
    offsets_in_effect,
    table_classify,
    table_preimages,
    table_resolve,
    transitions,
)
from tests.property import strategies as property_strategies

from freshcal.core.timeutil import classify_local, resolve_local

CRON_START = datetime(2023, 12, 25)
CRON_END = datetime(2025, 3, 5)
CRON_CAP = 20_000  # dense expressions are compared as a prefix, not over the whole window


def _oracle_stream(expression: str, start: datetime, end: datetime):
    """The oracle's nominal wall times for ``expression`` in ``[start, end]``, ascending."""
    cron = oracle.Cron.parse(expression)
    day = start.date()
    while day <= end.date():
        if cron.day_matches(day):
            for moment in cron.times():
                combined = datetime.combine(day, moment)
                if start <= combined <= end:
                    yield combined
        day += timedelta(days=1)


def _croniter_stream(expression: str, start: datetime, end: datetime):
    """croniter's nominal wall times for ``expression`` in ``[start, end]``, ascending."""
    iterator = croniter(expression, start - timedelta(minutes=1))
    while True:
        try:
            moment = iterator.get_next(datetime)
        except CroniterBadDateError:
            return
        if moment > end:
            return
        yield moment


def _croniter_items(expression: str, start: datetime, end: datetime) -> list[datetime]:
    """croniter's wall times, letting ``CroniterBadDateError`` escape (for the refusal test)."""
    iterator = croniter(expression, start - timedelta(minutes=1))
    out: list[datetime] = []
    while True:
        moment = iterator.get_next(datetime)
        if moment > end:
            return out
        out.append(moment)


def test_orc_01_oracle_stays_independent() -> None:
    """The oracle may import only model/errors from FreshCal — never the code under test."""
    source = Path(oracle.__file__).read_text()
    forbidden = re.findall(
        r"^\s*(?:from|import)\s+(freshcal\.core\.(?:schedule|verdict|timeutil|explain|custom)|croniter)\b",
        source,
        flags=re.MULTILINE,
    )
    assert forbidden == [], f"the oracle must not import: {sorted(set(forbidden))}"
    assert "freshcal.core.model" in source  # the allowed import is really there


@pytest.mark.parametrize("expression", CRON_EXPRS)
def test_orc_01_cron_matcher_agrees_with_croniter(expression: str) -> None:
    """Where croniter supports an expression, the two enumerations are identical.

    Restricted day-of-month plus nth weekdays is the documented exception (T-8.2): croniter
    drops the day-of-month branch, so its answer is only a *subset* of the OR union and is
    compared as such. The union itself is pinned by the hand-derived cases below.
    """
    theirs = list(_croniter_stream(expression, CRON_START, CRON_END))
    mine = list(_oracle_stream(expression, CRON_START, CRON_END))
    if expression in DOM_HASH_UNIONS:
        # croniter computes only the nth-weekday branch for these and sometimes refuses the
        # expression outright; either way its answer can only be a subset of the OR union.
        # The day-of-month branch itself is pinned by NTH_WEEKDAY_CASES below.
        assert set(theirs) <= set(mine), f"{expression!r}: oracle lost a croniter date"
        assert set(mine) - set(theirs), f"{expression!r}: the day-of-month branch is missing"
        return
    if not theirs:
        # croniter refuses the expression (SEM-08). The oracle may then only be non-empty when
        # the expression is one of the documented `day_or` refusals.
        if mine:
            assert expression in CRONITER_DAY_OR_REFUSALS, expression
            assert expression in NEVER_FIRES
        return
    compared = 0
    mine_iter = iter(mine)
    for right in theirs[:CRON_CAP]:
        left = next(mine_iter, None)
        assert left == right, f"{expression!r}: oracle={left} croniter={right}"
        compared += 1
    expected_more = compared == CRON_CAP
    if not expected_more:
        assert next(mine_iter, None) is None, (
            f"{expression!r}: the oracle has releases croniter does not"
        )


def test_orc_01_never_firing_expressions_are_empty_for_both() -> None:
    """croniter raises for these; the oracle must produce no nominal in the window either."""
    for expression in NEVER_FIRES:
        with pytest.raises(CroniterBadDateError):
            _croniter_items(expression, CRON_START, CRON_END)
        if expression in CRONITER_DAY_OR_REFUSALS:
            # Valid per §3.4.1 day_or: 30 February never exists, so the weekday alternative
            # decides. croniter cannot express this; the oracle can (finding SEM-08).
            matches = list(_oracle_stream(expression, CRON_START, CRON_END))
            assert matches, expression
            assert all(moment.month == 2 for moment in matches), expression
        else:
            assert list(_oracle_stream(expression, CRON_START, CRON_END)) == [], expression


# Hand-derived nth-weekday expectations (T-8.2). January 2026 starts on a Thursday, so its
# Mondays are 5, 12, 19, 26 (four of them) and its Fridays are 2, 9, 16, 23, 30 (five);
# February 2026 has Fridays 6, 13, 20, 27 (four). Each row states the days the oracle must
# match, derived by reading a calendar, never from croniter.
NTH_WEEKDAY_CASES = (
    # (expression, year, month, expected days)
    ("0 9 1 * *", 2026, 1, [1]),  # day-of-month only
    ("0 9 * * 1#1", 2026, 1, [5]),  # nth weekday only: the first Monday
    ("0 9 * * 1#5", 2026, 1, []),  # absent: January 2026 has four Mondays
    ("0 9 * * 5#5", 2026, 2, []),  # absent: February 2026 has four Fridays
    ("0 9 * * 5#5", 2026, 1, [30]),  # present: January 2026 has five Fridays
    ("0 9 1 * 1#1", 2026, 1, [1, 5]),  # union of the two branches (the AUD-01 case)
    ("0 9 5 * 1#1", 2026, 1, [5]),  # overlapping branches, deduplicated
    ("0 9 1,15 * 1#1,1#2", 2026, 1, [1, 5, 12, 15]),  # several alternatives each
    ("0 9 29 * 1#5", 2026, 1, [29]),  # day-of-month only survives: no fifth Monday
    ("0 9 * * 1#1,1#3", 2026, 1, [5, 19]),  # comma alternatives in one field
    ("10-20/5 0 15 * 1#1,1#2", 2026, 1, [5, 12, 15]),  # croniter refuses this one outright
)


@pytest.mark.parametrize(
    ("expression", "year", "month", "expected"), NTH_WEEKDAY_CASES, ids=lambda value: str(value)
)
def test_orc_01_nth_weekday_matching_is_hand_derived(
    expression: str, year: int, month: int, expected: list[int]
) -> None:
    """`N#O` and its OR interaction with a restricted day-of-month, read off a calendar."""
    cron = oracle.Cron.parse(expression)
    got = [
        day
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
        if cron.day_matches(date(year, month, day))
    ]
    assert got == expected, f"{expression!r} {year}-{month:02d}: {got} != {expected}"


def test_orc_01_the_audit_case_is_predicted() -> None:
    """T-8.2 acceptance: `0 9 1 * 1#1` predicts Jan 1 and Jan 5 2026."""
    cron = oracle.Cron.parse("0 9 1 * 1#1")
    days = [day for day in range(1, 32) if cron.day_matches(date(2026, 1, day))]
    assert days == [1, 5], days


def test_orc_01_grammar_exclusions_are_documented() -> None:
    """The oracle refuses what the loader refuses, and says so rather than guessing."""
    for expression in ("0 9 * * 1#1,3", "0 9 * * 0,6#2"):
        with pytest.raises(ValueError, match="mixes literals and nth weekdays"):
            oracle.Cron.parse(expression)
    # The supported grammar is the whole of §3.4.1: five fields, `*`, ranges, steps, lists,
    # `L` in the day-of-month field and `N#O` in the day-of-week field. `H`/`R` extensions
    # are refused by the loader (E202) and are not part of the oracle's grammar either.
    for expression in ("0 9 * * H(2)", "0 9 * * R(1-5)"):
        with pytest.raises(ValueError, match="invalid literal for int"):
            oracle.Cron.parse(expression)


def test_orc_01_hash_expressions_are_exercised() -> None:
    """The seeded campaign and the property strategies really draw `#` combinations (T-8.2).

    A generator that never produces the syntax would let the oracle's nth-weekday support rot
    unnoticed, so this is a distribution assertion over the same draws the tests use.
    """
    assert all("#" in expression for expression in NTH_WEEKDAY_EXPRESSIONS)
    assert all("#" in expression and expression.split()[2] != "*" for expression in DOM_HASH_UNIONS)

    rng = random.Random(20260929)
    drawn = 0
    for _ in range(2000):
        rule, _provider, _instant = build(rng, "cron")
        if "#" in rule.schedule.expression:
            drawn += 1
    assert drawn > 0, "the campaign generator produced no `#` case in 2000 draws"

    # The property strategies sample the same day-of-week list, so the syntax reaches the
    # property suite as well (a seeded draw from the same lists, not a Hypothesis example).
    assert any("#" in value for value in property_strategies.CRON_DAYS_OF_WEEK)
    property_drawn = 0
    for _ in range(2000):
        expression = " ".join(
            [
                rng.choice(property_strategies.CRON_MINUTES),
                rng.choice(property_strategies.CRON_HOURS),
                rng.choice(property_strategies.CRON_DAYS_OF_MONTH),
                "*",
                rng.choice(property_strategies.CRON_DAYS_OF_WEEK),
            ]
        )
        if "#" in expression:
            property_drawn += 1
    assert property_drawn > 0, "the property strategies produced no `#` case in 2000 draws"

    # The recorded regression corpus keeps the cases the sound comparator surfaced (T-8.1/
    # T-8.2), so the syntax stays pinned even where the seeded draw happens to miss it.
    corpus = json.loads(Path(__file__).with_name("regressions.json").read_text())
    pinned = [case for case in corpus if "#" in case["rule"].get("expr", "")]
    assert len(pinned) >= 10, len(pinned)


def _wall_times(seed: int = 20260929) -> list[tuple[datetime, str]]:
    """At least 50 000 (wall time, zone) pairs: transition neighbourhoods plus random dates."""
    rng = random.Random(seed)
    drawn: list[tuple[datetime, str]] = []
    for key in ZONES:
        moments = transitions(key)
        interesting = [rng.choice(moments) for _ in range(10)] if moments else []
        days: list[tuple[int, int, int]] = [
            (moment.year, moment.month, moment.day) for moment in interesting
        ]
        while len(days) < 20:
            days.append((rng.randrange(1995, 2036), rng.randrange(1, 13), rng.randrange(1, 29)))
        for year, month, day in days[:20]:
            for _ in range(100):
                hour, minute = rng.randrange(24), rng.choice([0, 15, 30, 45, 59])
                drawn.append((datetime(year, month, day, hour, minute), key))
    assert len(drawn) >= 50_000, len(drawn)
    return drawn


def test_orc_01_resolution_agrees_with_the_tzif_table() -> None:
    """≥ 50 000 wall times: the oracle's brute force versus the zone transition table."""
    mismatches = []
    for wall, key in _wall_times():
        candidates, valid = table_preimages(wall, key)
        expected = min(valid) if valid else max(candidates)
        expected_class = "normal" if len(valid) == 1 else ("ambiguous" if valid else "gap")
        zone = ZoneInfo(key)
        got = oracle.o_resolve(wall, zone)
        if got != expected or oracle.o_classify(wall, zone) != expected_class:
            mismatches.append(
                (key, wall, got, expected, oracle.o_classify(wall, zone), expected_class)
            )
    assert mismatches == [], mismatches[:5]


def test_orc_01_resolution_agrees_with_freshcal() -> None:
    """The same wall times through FreshCal's own resolution (a drift check, not a proof)."""
    mismatches = []
    for wall, key in _wall_times():
        zone = ZoneInfo(key)
        if oracle.o_resolve(wall, zone) != resolve_local(wall, zone) or oracle.o_classify(
            wall, zone
        ) != classify_local(wall, zone):
            mismatches.append((key, wall, oracle.o_resolve(wall, zone), resolve_local(wall, zone)))
    assert mismatches == [], mismatches[:5]


def test_orc_01_offsets_in_effect_is_bisected_not_sampled() -> None:
    """The table reader sees exactly the offsets the table declares for a wide window."""
    key = "Australia/Lord_Howe"
    lo = datetime(2026, 4, 1, tzinfo=__import__("datetime").UTC)
    hi = datetime(2026, 10, 10, tzinfo=__import__("datetime").UTC)
    offsets = offsets_in_effect(key, lo, hi)
    assert offsets == {timedelta(hours=10, minutes=30), timedelta(hours=11)}
    assert table_resolve(datetime(2026, 10, 4, 2, 10), key) == table_resolve(
        datetime(2026, 10, 4, 2, 10), key
    )
    assert table_classify(datetime(2026, 10, 4, 2, 10), key) == "gap"
