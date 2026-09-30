"""ORC-05…ORC-12: the differential comparator's exception contract (T-8.1, AUD-06 part 1).

`compare_case` used to compare its four schedule operations inside one `try` block whose
`except` returned early, so a failure raised by *one* side was read as "both sides refused"
and the case was silently skipped. These tests inject failures into the implementation, the
oracle, or both, and assert the comparator reports exactly the incompatible answers and
still accepts compatible ones. The case (a `business_days` rule with matching calendars) is
deliberately one the two sides agree on before any injection.

The tests are deterministic: the rule, provider and instants come from a fixed seed, and the
injections use `unittest.mock.patch`, never a real failure in production code. An injected
oracle failure also reaches the verdict comparison, because `o_evaluate` is built from the
same oracle operations; those collateral differences are asserted explicitly instead of
being filtered away.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from unittest.mock import patch

from tests.oracle import oracle
from tests.oracle.cases import BoundedProvider, _refusals_agree, build, compare_case

from freshcal.core.errors import ConfigError, Issue

NEXT_CHECKS = {"next_release_after", "next_release_after_until"}


def _case():
    """A matching business-day case: the baseline comparison is empty."""
    rule, provider, instant = build(random.Random(11), "bd")
    return rule, provider, instant


def _run(rule, provider, instant):
    return compare_case(rule, provider, instant, random.Random(55), oracle)


def test_orc_05_a_matching_case_reports_nothing() -> None:
    """The control: without any injection the comparator is silent."""
    rule, provider, instant = _case()
    assert _run(rule, provider, instant) == []


def test_orc_06_a_product_only_refusal_is_a_difference() -> None:
    """The audit's blind spot: E209 raised by the implementation alone must be reported."""
    rule, provider, instant = _case()
    with patch(
        "freshcal.core.schedule.next_release_after",
        side_effect=ConfigError(Issue("E209", "injected product-only failure")),
    ):
        differences = _run(rule, provider, instant)
    assert {name for name, _, _, _ in differences} == NEXT_CHECKS, differences
    for name, _, got, want in differences:
        assert got == "E209", (name, got)
        assert want is None or isinstance(want, list), (name, want)  # the oracle still answered


def test_orc_07_an_oracle_only_refusal_is_a_difference() -> None:
    """The same blindness in the other direction: the oracle fails, the implementation does not."""
    rule, provider, instant = _case()
    with patch.object(oracle, "o_first_after", side_effect=oracle.OracleError("E209")):
        differences = _run(rule, provider, instant)
    names = {name for name, _, _, _ in differences}
    assert names >= NEXT_CHECKS, differences
    assert names <= NEXT_CHECKS | {"evaluate"}, differences
    for name, _, got, want in differences:
        if name in NEXT_CHECKS:
            assert got is None or isinstance(got, list), (name, got)  # the product answered
            assert want == "E209", (name, want)


def test_orc_08_matching_refusals_are_not_a_difference() -> None:
    """Two identical typed refusals for one operation are compatible, not a disagreement."""
    rule, provider, instant = _case()
    with (
        patch(
            "freshcal.core.schedule.previous_release_at_or_before",
            side_effect=ConfigError(Issue("E209", "injected")),
        ),
        patch.object(oracle, "o_last_before", side_effect=oracle.OracleError("E209")),
    ):
        differences = _run(rule, provider, instant)
    names = [name for name, _, _, _ in differences]
    assert "previous_release_at_or_before" not in names, differences
    assert set(names) <= {"evaluate"}, differences


def test_orc_09_differing_refusal_codes_are_a_difference() -> None:
    """The implementation's E209 and the oracle's E407 are not the same answer."""
    rule, provider, instant = _case()
    with (
        patch(
            "freshcal.core.schedule.next_release_after",
            side_effect=ConfigError(Issue("E209", "injected")),
        ),
        patch.object(oracle, "o_first_after", side_effect=oracle.OracleError("E407")),
    ):
        differences = _run(rule, provider, instant)
    named = {name: (got, want) for name, _, got, want in differences}
    assert set(named) >= NEXT_CHECKS, differences
    for name in NEXT_CHECKS:
        assert named[name] == ("E209", "E407"), (name, named[name])


def test_orc_10_a_one_sided_failure_after_matching_operations_is_still_reported() -> None:
    """The failure is reported even though every earlier operation compared equal."""
    rule, provider, instant = _case()
    with patch(
        "freshcal.core.schedule.previous_release_at_or_before",
        side_effect=ConfigError(Issue("E209", "injected later failure")),
    ):
        differences = _run(rule, provider, instant)
    assert [name for name, _, _, _ in differences] == ["previous_release_at_or_before"], differences
    assert differences[0][2] == "E209"
    assert differences[0][3] is None or isinstance(differences[0][3], (list, str))


def test_orc_11_a_real_refusal_shared_by_both_sides_is_not_a_difference() -> None:
    """A genuine failure both sides share (E405 beyond the provider's years) stays compatible."""
    rule, provider, _ = _case()
    supported = BoundedProvider(provider, 2026, 2026)
    beyond = datetime(2066, 6, 1, tzinfo=UTC)
    assert _run(rule, supported, beyond) == []


def test_orc_12_the_refusal_rules_are_explicit() -> None:
    """Pin the documented compatibility rule for typed refusals."""
    assert _refusals_agree(None, None)
    assert _refusals_agree("E209", "E209")
    assert _refusals_agree("E405", "E405")
    assert _refusals_agree("E209", None)
    assert _refusals_agree(None, "E209")
    assert not _refusals_agree("E209", "E407")
    assert not _refusals_agree("E407", None)
    assert not _refusals_agree(None, "E405")


def test_orc_13_many_seeded_business_day_cases_stay_silent() -> None:
    """A quick sweep over seeded cases, with the sound comparator in place."""
    for seed in (11, 12, 13, 14):
        rng = random.Random(seed)
        for _ in range(8):
            rule, provider, instant = build(rng, "bd")
            assert compare_case(rule, provider, instant, rng, oracle) == []
