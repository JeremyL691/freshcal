#!/usr/bin/env python3
"""Arrival-time replay against the ECB publication-time proxy.

``Last-Modified`` is the origin server's belief about when a representation was last
modified; it is a **proxy** for publication time, not proof that the file was public then.
This script therefore measures *agreement with the proxy* — never arrival accuracy — and
lists every disagreement with its explanation.

What it does:

1. reads ``validation/ecb/publications.csv`` (written by ``collect.py``), takes the
   earliest ``last_modified_utc`` per ``rate_date`` as that day's publication proxy, and
   lists every additional row for a rate date as a revision;
2. simulates two loaders — *immediate* (``loaded_at`` = proxy) and *hourly at :05* (the
   first HH:05 after the proxy);
3. evaluates the ECB rule (business days 16:00 Europe/Berlin, TARGET holidays, 2 h grace)
   every 15 minutes across the collected span with FreshCal's own core, and compares each
   verdict with the proxy reference: OVERDUE at *t* iff some TARGET business day's rate has
   its deadline before *t* and its publication proxy after *t*;
4. does the same for a dbt-style fixed threshold (``error_after`` 26 h on the same
   timestamps), so the report can contrast the two approaches;
5. reports the distribution of local proxy times, of the ``first_seen - last_modified``
   gap (which bounds how far the proxy can be trusted), and the revisions.

The output is a Markdown section; ``docs/validation.md`` embeds it verbatim, and a run with
fewer than 20 collected business days says "insufficient data (N business days)".
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from freshcal.adapters.holidays_provider import HolidaysCalendarProvider
from freshcal.core.calendar import BusinessCalendar
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    FreshnessTarget,
    HolidayCalendarRef,
    Origin,
    RawObservation,
    SourceRule,
)
from freshcal.core.verdict import evaluate

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLICATIONS = REPOSITORY_ROOT / "validation" / "ecb" / "publications.csv"
BERLIN = ZoneInfo("Europe/Berlin")
RULE_TIME = time(16, 0)
GRACE = timedelta(hours=2)
STEP = timedelta(minutes=15)
MINIMUM_BUSINESS_DAYS = 20
FIXED_THRESHOLD = timedelta(hours=26)
LOADER_MODELS = ("immediate", "hourly_at_05")
Loader = Literal["immediate", "hourly_at_05"]

PROXY_CAVEAT = (
    "These results measure agreement with the Last-Modified publication proxy, not arrival "
    "accuracy."
)


@dataclass(frozen=True, slots=True)
class Publication:
    """One collected ``(rate_date, last_modified)`` pair, as first seen."""

    rate_date: date
    last_modified_utc: datetime
    first_seen_utc: datetime


@dataclass
class Agreement:
    """Confusion matrix against the proxy reference, plus the disagreements."""

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    disagreements: list[tuple[str, str, str]] = field(default_factory=list)  # kind, stamp, why

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.false_negative + self.true_negative

    def record(self, predicted: bool, reference: bool, stamp: str, explanation: str) -> None:
        if predicted and reference:
            self.true_positive += 1
        elif predicted and not reference:
            self.false_positive += 1
            self.disagreements.append(("false alarm", stamp, explanation))
        elif reference and not predicted:
            self.false_negative += 1
            self.disagreements.append(("missed catch-up", stamp, explanation))
        else:
            self.true_negative += 1


def load_publications(path: Path = PUBLICATIONS) -> list[Publication]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Publication(
                rate_date=date.fromisoformat(row["rate_date"]),
                last_modified_utc=datetime.fromisoformat(
                    row["last_modified_utc"].replace("Z", "+00:00")
                ),
                first_seen_utc=datetime.fromisoformat(row["first_seen_utc"].replace("Z", "+00:00")),
            )
            for row in csv.DictReader(handle)
        ]


def proxies(publications: list[Publication]) -> dict[date, datetime]:
    """The earliest ``Last-Modified`` per rate date: the publication proxy."""
    earliest: dict[date, datetime] = {}
    for publication in publications:
        current = earliest.get(publication.rate_date)
        if current is None or publication.last_modified_utc < current:
            earliest[publication.rate_date] = publication.last_modified_utc
    return earliest


def revisions(publications: list[Publication]) -> dict[date, list[datetime]]:
    """Every additional ``Last-Modified`` value seen for a rate date."""
    grouped: dict[date, set[datetime]] = {}
    for publication in publications:
        grouped.setdefault(publication.rate_date, set()).add(publication.last_modified_utc)
    return {
        rate_date: sorted(values)[1:]
        for rate_date, values in sorted(grouped.items())
        if len(values) > 1
    }


def ecb_rule() -> SourceRule:
    return SourceRule(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=RULE_TIME, timezone=BERLIN),
        calendar=CalendarSpec(holiday_calendars=(HolidayCalendarRef("financial", "XECB"),)),
        grace=GRACE,
        target=FreshnessTarget(relation="validation", loaded_at_field="_loaded_at"),
    )


def loader_timestamps(
    publication_proxies: dict[date, datetime], loader: Loader
) -> dict[date, datetime]:
    """The simulated ``loaded_at`` value per rate date for one loader model."""
    stamps: dict[date, datetime] = {}
    for rate_date, proxy in publication_proxies.items():
        if loader == "immediate":
            stamps[rate_date] = proxy
        else:
            local = proxy.astimezone(BERLIN)
            first_after = local.replace(minute=5, second=0, microsecond=0)
            if first_after < local:
                first_after += timedelta(hours=1)
            stamps[rate_date] = first_after.astimezone(proxy.tzinfo)
    return stamps


def reference_overdue(publication_proxies: dict[date, datetime], instant: datetime) -> bool:
    """The proxy reference: a due release whose proxy publication is still in the future."""
    for rate_date, proxy in publication_proxies.items():
        release = datetime.combine(rate_date, RULE_TIME, tzinfo=BERLIN)
        deadline = release + GRACE
        if deadline < instant and proxy > instant:
            return True
    return False


def freshcal_overdue(
    publication_proxies: dict[date, datetime], loader: Loader, instant: datetime
) -> bool | None:
    """FreshCal's verdict for a simulated loader; ``None`` when the outcome is not a verdict."""
    stamps = loader_timestamps(publication_proxies, loader)
    candidates = [value for value in stamps.values() if value <= instant]
    observed = max(candidates) if candidates else None
    # The simulated timestamp is aware UTC, so no observed_timezone is needed.
    result = evaluate(ecb_rule(), RawObservation(observed), instant, HolidaysCalendarProvider())
    if result.status.value == "OVERDUE":
        return True
    if result.status.value in ("ON_TIME", "NOT_DUE"):
        return False
    return None


def fixed_threshold_overdue(
    publication_proxies: dict[date, datetime], loader: Loader, instant: datetime
) -> bool:
    """A dbt-style ``error_after: 26h`` on the same simulated timestamps."""
    stamps = loader_timestamps(publication_proxies, loader)
    candidates = [value for value in stamps.values() if value <= instant]
    if not candidates:
        return False
    return instant - max(candidates) > FIXED_THRESHOLD


def explain_disagreement(publication_proxies: dict[date, datetime], instant: datetime) -> str:
    """Why the model and the proxy reference disagree at ``instant``.

    Two shapes exist, and they are opposites: the proxy can be *later* than the configured
    release time (the rule expects a file the server says has not been modified yet), or
    *earlier* than it (the file was observably available before the rule expected it, so an
    immediate loader's timestamp predates the release and the release looks missing).
    """
    # The most recent TARGET business day whose deadline has passed, collected or not.
    missing_days: list[date] = []
    candidate = instant.astimezone(BERLIN).date()
    while len(missing_days) < 10:
        if (
            BusinessCalendar(ecb_rule().calendar, HolidaysCalendarProvider()).is_business_day(
                candidate
            )
            and datetime.combine(candidate, RULE_TIME, tzinfo=BERLIN) + GRACE < instant
        ):
            if candidate not in publication_proxies:
                return (
                    f"the rate date {candidate.isoformat()} has no collected publication row, "
                    "so the proxy reference cannot speak about that day and counts the alarm as "
                    "a disagreement; the replay is restricted to days with collected data"
                )
            proxies_for_day = [(candidate, publication_proxies[candidate])]
            break
        candidate -= timedelta(days=1)
    else:
        return "the schedule and the proxy disagree about which release is due"
    day, proxy = proxies_for_day[0]
    release_local = datetime.combine(day, RULE_TIME, tzinfo=BERLIN)
    if proxy < release_local:
        return (
            f"the file for {day.isoformat()} was observably available before the configured "
            f"release time (proxy {proxy.astimezone(BERLIN):%H:%M} CEST vs release "
            f"{RULE_TIME:%H:%M} CEST), so an immediate loader's timestamp predates the release "
            "and the release looks missing"
        )
    return (
        f"the proxy for {day.isoformat()} is later than the configured release time (proxy "
        f"{proxy.astimezone(BERLIN):%H:%M} CEST vs release {RULE_TIME:%H:%M} CEST), so the "
        "rule expected the file before the server says it was modified"
    )


def replay(
    publication_proxies: dict[date, datetime],
    *,
    start: datetime,
    end: datetime,
    step: timedelta = STEP,
) -> dict[str, Agreement]:
    """Compare both FreshCal loader models and the fixed threshold with the reference."""
    matrices = {loader: Agreement() for loader in LOADER_MODELS}
    matrices["fixed_threshold_26h"] = Agreement()
    instant = start
    while instant <= end:
        reference = reference_overdue(publication_proxies, instant)
        stamp = instant.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M %Z")
        explanation = explain_disagreement(publication_proxies, instant)
        for loader in LOADER_MODELS:
            prediction = freshcal_overdue(publication_proxies, loader, instant)
            if prediction is None:
                continue
            matrices[loader].record(prediction, reference, stamp, explanation)
        matrices["fixed_threshold_26h"].record(
            fixed_threshold_overdue(publication_proxies, "immediate", instant),
            reference,
            stamp,
            explanation,
        )
        instant += step
    return matrices


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    widths = [len(cell) for cell in header]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(header)) + " |"]
    lines.append("|" + "|".join("-" * (width + 2) for width in widths) + "|")
    for row in rows:
        lines.append("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return lines


def build_report(publications: list[Publication], *, run_date: date) -> str:
    """The Markdown section for ``docs/validation.md``."""
    publication_proxies = proxies(publications)
    collected = sorted(publication_proxies)
    rule = ecb_rule()
    calendar = BusinessCalendar(rule.calendar, HolidaysCalendarProvider())
    business_days = [day for day in collected if calendar.is_business_day(day)]

    lines: list[str] = [
        "### Agreement with the `Last-Modified` publication proxy",
        "",
        f"Produced by `uv run python validation/replay_ecb.py` on {run_date.isoformat()}.",
        "",
        PROXY_CAVEAT,
        "",
        f"- Collected business days: **{len(business_days)}**"
        f" (rate dates in the file: {len(collected)}, span "
        f"{collected[0].isoformat() if collected else 'n/a'} … "
        f"{collected[-1].isoformat() if collected else 'n/a'}).",
    ]

    revisions_seen = revisions(publications)
    if revisions_seen:
        described = "; ".join(
            f"{day.isoformat()}: " + ", ".join(moment.isoformat() for moment in moments)
            for day, moments in revisions_seen.items()
        )
        lines.append(f"- Same-day revisions: {len(revisions_seen)} rate date(s) — {described}.")
    else:
        lines.append("- Same-day revisions: none collected.")

    if publication_proxies:
        local_times = Counter(
            proxy.astimezone(BERLIN).strftime("%H:%M") for proxy in publication_proxies.values()
        )
        distribution = ", ".join(
            f"{moment} (x{count})" for moment, count in sorted(local_times.items())
        )
        lines.append(f"- Publication-proxy local times (Europe/Berlin): {distribution}.")
        gaps = [
            (publication.first_seen_utc - publication.last_modified_utc).total_seconds() / 60
            for publication in publications
        ]
        lines.append(
            "- `first_seen - last_modified` gap: "
            f"min {min(gaps):.0f} min, max {max(gaps):.0f} min "
            f"({len(gaps)} observations)."
        )

    if len(business_days) < MINIMUM_BUSINESS_DAYS:
        lines.extend(
            [
                "",
                f"**Insufficient data ({len(business_days)} business days).** Fewer than "
                f"{MINIMUM_BUSINESS_DAYS} collected business days cannot support any statement "
                "about arrival-time agreement, so the README makes no timing claim. The "
                "matrices below are printed for completeness only.",
                "",
            ]
        )
    else:
        lines.append("")

    if not business_days:
        lines.append("No matrices: no collected business day overlaps the rule's calendar.")
        return "\n".join(lines) + "\n"

    start = datetime.combine(business_days[0], time(0, 0), tzinfo=BERLIN)
    end = datetime.combine(business_days[-1] + timedelta(days=1), time(0, 0), tzinfo=BERLIN)
    matrices = replay(publication_proxies, start=start, end=end)

    labels = {
        "immediate": "FreshCal, immediate loader",
        "hourly_at_05": "FreshCal, hourly loader (first HH:05)",
        "fixed_threshold_26h": "dbt-style fixed threshold (`error_after: 26h`)",
    }
    rows = [
        (
            labels[name],
            str(matrix.true_positive),
            str(matrix.false_positive),
            str(matrix.false_negative),
            str(matrix.true_negative),
        )
        for name, matrix in matrices.items()
    ]
    lines.extend(
        _table(
            ("Model", "Agree: overdue", "False alarms", "Missed catch-up", "Agree: nothing due"),
            rows,
        )
    )
    lines.extend(
        [
            "",
            "*Agree: overdue* = both the model and the proxy reference report a missing release; "
            "*false alarms* = the model alerts while the proxy says the data was published; "
            "*missed catch-up* = the proxy says the data is late while the model stays quiet.",
            "",
        ]
    )

    lines.append("**Every disagreement, with its explanation:**")
    lines.append("")
    any_disagreement = False
    for name, matrix in matrices.items():
        grouped: dict[tuple[str, str], list[str]] = {}
        for kind, stamp, explanation in matrix.disagreements:
            grouped.setdefault((kind, explanation), []).append(stamp)
        for (kind, explanation), stamps in grouped.items():
            any_disagreement = True
            lines.append(
                f"- {labels[name]}: {len(stamps)} {kind}(s) — {explanation}. "
                f"Sampled instants: {', '.join(stamps)}."
            )
    if not any_disagreement:
        lines.append(
            "- None: every model agreed with the proxy reference at every sampled instant."
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    publications = load_publications()
    sys.stdout.write(build_report(publications, run_date=date.today()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
