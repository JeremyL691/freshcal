"""Replay-logic tests on a small hand-written publication file (BLUEPRINT.md §9.10 B).

The committed ``validation/ecb/publications.csv`` has too few rows to exercise the logic,
so these tests build a file with exactly the four shapes that matter: a publication on
time, one early (before the configured release time), one late, one missing entirely, and
a same-day revision. Every count is asserted exactly.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

FIXTURES_ROOT = Path(__file__).resolve().parents[2]
REPLAY_PATH = FIXTURES_ROOT / "validation" / "replay_ecb.py"
BERLIN_OFFSET = timezone(timedelta(hours=2))  # CEST, which covers September 2026

# Four consecutive TARGET business days: 2026-09-23 (Wed) … 2026-09-28 (Mon).
ON_TIME = date(2026, 9, 23)
EARLY = date(2026, 9, 24)
LATE = date(2026, 9, 25)
MISSING = date(2026, 9, 28)


def load_replay() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay_ecb", REPLAY_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_ecb"] = module
    spec.loader.exec_module(module)
    return module


replay_module = load_replay()


def write_publications(tmp_path: Path) -> Path:
    """The four-day fixture: on time, early, late, and (for one day) nothing at all."""
    path = tmp_path / "publications.csv"
    first_seen = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    rows = [
        # On time: modified at 16:05 CEST, after the 16:00 release.
        (ON_TIME, datetime(2026, 9, 23, 14, 5, tzinfo=UTC)),
        # Early: modified at 15:50 CEST, before the release (the known proxy artefact).
        (EARLY, datetime(2026, 9, 24, 13, 50, tzinfo=UTC)),
        # Late: modified at 17:30 CEST, 1.5 h after the release.
        (LATE, datetime(2026, 9, 25, 15, 30, tzinfo=UTC)),
        # A revision of the late day: a second value for the same rate date.
        (LATE, datetime(2026, 9, 25, 16, 45, tzinfo=UTC)),
        # MISSING has no row at all.
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rate_date", "last_modified_utc", "first_seen_utc"])
        for rate_date, modified in rows:
            writer.writerow(
                [
                    rate_date.isoformat(),
                    modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    first_seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ]
            )
    return path


def instant(day: int, hour: int, minute: int, *, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=BERLIN_OFFSET)


def test_r_01_proxies_take_the_earliest_and_revisions_are_kept(tmp_path: Path) -> None:
    publications = replay_module.load_publications(write_publications(tmp_path))

    proxies = replay_module.proxies(publications)
    assert sorted(proxies) == [ON_TIME, EARLY, LATE]
    assert proxies[LATE] == datetime(2026, 9, 25, 15, 30, tzinfo=UTC)
    assert MISSING not in proxies, "a business day without a row stays missing, never guessed"

    revisions = replay_module.revisions(publications)
    assert revisions == {LATE: [datetime(2026, 9, 25, 16, 45, tzinfo=UTC)]}


def test_r_02_the_two_loader_models(tmp_path: Path) -> None:
    publications = replay_module.load_publications(write_publications(tmp_path))
    proxies = replay_module.proxies(publications)

    immediate = replay_module.loader_timestamps(proxies, "immediate")
    assert immediate[ON_TIME] == datetime(2026, 9, 23, 14, 5, tzinfo=UTC)
    assert immediate[EARLY] == datetime(2026, 9, 24, 13, 50, tzinfo=UTC)

    hourly = replay_module.loader_timestamps(proxies, "hourly_at_05")
    # 16:05 CEST is itself an HH:05, so the hourly loader is not delayed.
    assert hourly[ON_TIME].astimezone(replay_module.BERLIN).strftime("%H:%M") == "16:05"
    # 15:50 CEST waits for the next hour: 16:05 CEST.
    assert hourly[EARLY].astimezone(replay_module.BERLIN).strftime("%H:%M") == "16:05"
    assert hourly[EARLY] > immediate[EARLY]


def test_r_03_exact_agreement_counts_for_the_immediate_loader(tmp_path: Path) -> None:
    """The immediate loader sees exactly the two artefacts the proxy model predicts.

    Sampled every 15 minutes from 2026-09-23 00:00 CEST to 2026-09-28 23:45 CEST: 576
    instants, of which 65 are skipped because no load has happened yet (NO_DATA is not a
    verdict), leaving 511 comparisons.

    116 false alarms, in two groups:
    - 93 from 2026-09-24 18:15 CEST: the 24th's file was observably available at 15:50
      CEST, *before* the configured 16:00 release, so the immediate loader's timestamp
      predates the release and the day looks missing (BLUEPRINT.md §3.6);
    - 23 from 2026-09-28 18:15 CEST: the 28th has no collected row at all, so the proxy
      reference cannot speak about it and scores the alarm as a disagreement (the replay
      is restricted to days with collected data).
    """
    publications = replay_module.load_publications(write_publications(tmp_path))
    proxies = replay_module.proxies(publications)
    start = instant(23, 0, 0)
    end = instant(28, 23, 45)

    matrices = replay_module.replay(proxies, start=start, end=end, step=timedelta(minutes=15))
    immediate = matrices["immediate"]

    assert immediate.true_positive == 0
    assert immediate.false_positive == 116
    assert immediate.false_negative == 0
    assert immediate.true_negative == 395
    assert immediate.total == 511
    assert len(immediate.disagreements) == immediate.false_positive

    explanations = [why for _, _, why in immediate.disagreements]
    early = [why for why in explanations if "observably available before" in why]
    uncollected = [why for why in explanations if "no collected publication row" in why]
    assert len(early) == 93
    assert len(uncollected) == 23
    assert all("2026-09-24" in why for why in early)
    assert all("2026-09-28" in why for why in uncollected)


def test_r_04_the_hourly_loader_is_quieter_and_the_fixed_threshold_is_noisier(
    tmp_path: Path,
) -> None:
    """Two contrasts the replay exists to show.

    - The hourly loader waits until 16:05 CEST, so it never sees a pre-release timestamp:
      its only disagreements are the 23 instants of the uncollected day.
    - The fixed 26 h threshold ignores the calendar entirely, so it keeps alarming across
      the weekend: 209 false alarms, of which 186 are the weekend after the 25th's load
      and 23 the uncollected day. It also never skips an instant (576 comparisons, because
      it has no NO_DATA state).
    """
    publications = replay_module.load_publications(write_publications(tmp_path))
    proxies = replay_module.proxies(publications)
    matrices = replay_module.replay(
        proxies, start=instant(23, 0, 0), end=instant(28, 23, 45), step=timedelta(minutes=15)
    )

    hourly = matrices["hourly_at_05"]
    assert (hourly.true_positive, hourly.false_positive, hourly.false_negative) == (0, 23, 0)
    assert hourly.true_negative == 488
    assert hourly.total == 511
    assert {why for _, _, why in hourly.disagreements} == {
        "the rate date 2026-09-28 has no collected publication row, so the proxy reference "
        "cannot speak about that day and counts the alarm as a disagreement; the replay is "
        "restricted to days with collected data"
    }

    threshold = matrices["fixed_threshold_26h"]
    assert (threshold.true_positive, threshold.false_positive, threshold.false_negative) == (
        0,
        209,
        0,
    )
    assert threshold.true_negative == 367
    assert threshold.total == 576, "the fixed threshold has no non-verdict outcome"
    weekend = [stamp for _, stamp, why in threshold.disagreements if "2026-09-25" in why]
    assert len(weekend) == 186
    assert all("2026-09-2" in stamp for stamp in weekend)


def test_report_contains_the_required_sections() -> None:
    publications = replay_module.load_publications(
        Path(FIXTURES_ROOT / "validation" / "ecb" / "publications.csv")
    )
    report = replay_module.build_report(publications, run_date=date(2026, 9, 29))
    assert report.startswith("### Agreement with the `Last-Modified` publication proxy")
    assert "Collected business days" in report
    assert "Publication-proxy local times" in report
    assert "first_seen - last_modified` gap" in report
    assert "Same-day revisions" in report
    caveat = (
        "These results measure agreement with the Last-Modified publication proxy, not "
        "arrival accuracy."
    )
    assert caveat in report
    assert "accuracy" not in report.replace(caveat, "")
    if "Insufficient data" not in report:
        assert "| Model" in report
