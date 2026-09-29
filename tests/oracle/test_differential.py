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
import random
from pathlib import Path

import pytest
from tests.oracle import oracle
from tests.oracle.cases import build, compare_case, describe

DEFAULT_SEEDS = (11, 12, 13)
DEFAULT_PER_SEED = 12
ORACLE_SEEDS = tuple(range(100, 120))
ORACLE_PER_SEED = 500
BUSINESS_KINDS = ("bd", "monthly")
REGRESSIONS = Path(__file__).with_name("regressions.json")


def _run_campaign(seeds: tuple[int, ...], per_seed: int, focuses: tuple[str, ...]) -> list[dict]:
    disagreements: list[dict] = []
    for seed in seeds:
        rng = random.Random(seed)
        for index in range(per_seed):
            focus = focuses[index % len(focuses)]
            rule, provider, instant = build(rng, focus)
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
    return disagreements


def _report(disagreements: list[dict]) -> str:
    return "\n".join(json.dumps(item, default=str) for item in disagreements[:10])


def test_orc_02_business_day_kinds_match_the_oracle() -> None:
    """`business_days` and `monthly_business_day` over seeded random rules and calendars."""
    disagreements = _run_campaign(DEFAULT_SEEDS, DEFAULT_PER_SEED, BUSINESS_KINDS)
    assert disagreements == [], _report(disagreements)


@pytest.mark.oracle
def test_orc_02_business_day_kinds_full_campaign() -> None:
    """The full seeded campaign (T-7.1 acceptance: report the seed count and case count)."""
    cases = len(ORACLE_SEEDS) * ORACLE_PER_SEED
    disagreements = _run_campaign(ORACLE_SEEDS, ORACLE_PER_SEED, BUSINESS_KINDS)
    print(f"oracle campaign: {len(ORACLE_SEEDS)} seeds x {ORACLE_PER_SEED} cases = {cases} cases")
    assert disagreements == [], _report(disagreements)
