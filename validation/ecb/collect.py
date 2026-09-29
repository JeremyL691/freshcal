#!/usr/bin/env python3
"""Record the ECB publication-time proxy for the daily euro reference rates.

FreshCal's arrival-time replay (T-6.7) needs to know when the ECB's daily file
became observable, which no independent record provides. This collector stores two
HTTP headers of ``eurofxref-daily.xml`` with their RFC 9110 meanings:

- ``Last-Modified``: when the origin server believes the representation was last
  modified. A proxy for publication time, not proof that the file was public then.
- ``Date`` of the first response that showed a given ``(rate_date, Last-Modified)``
  pair: an upper bound for when the file was observably available, with a precision
  limited by how often this script runs and by the site's ``max-age=300`` caching.

One row is appended per new ``(rate_date, last_modified_utc)`` pair, so a same-day
revision appears as a second row for the same ``rate_date``. Standard library only;
the local clock is never read, so the recorded times depend only on the server.

Usage::

    uv run python validation/ecb/collect.py

Exit codes: 0 on success or when nothing is new, 1 on a network or parse failure
(the data file is never modified in that case).
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
USER_AGENT = "freshcal-validation/0.1 (+https://github.com/freshcal/freshcal)"
TIMEOUT_SECONDS = 30
DATA_PATH = Path(__file__).with_name("publications.csv")
HEADER = ("rate_date", "last_modified_utc", "first_seen_utc")


@dataclass(frozen=True, slots=True)
class Publication:
    """One ``(rate_date, Last-Modified)`` pair as first observed."""

    rate_date: str  # ISO date of the dated Cube, e.g. "2026-09-28"
    last_modified_utc: str  # ISO 8601 with Z
    first_seen_utc: str  # ISO 8601 with Z, from the Date response header


def format_utc(value: datetime) -> str:
    """Render an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_publication(xml_text: str, last_modified_header: str, date_header: str) -> Publication:
    """Extract the dated Cube's date and both header times.

    Pure function: no I/O, no clock. Raises ``ValueError`` (from the XML parser or
    the header parsers) when the input is not the expected shape.
    """
    root = ElementTree.fromstring(xml_text)
    rate_date: str | None = None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Cube":
            time_attribute = element.get("time")
            if time_attribute is not None:
                rate_date = time_attribute
                break
    if rate_date is None:
        raise ValueError("no dated Cube (with a 'time' attribute) in the document")
    if len(rate_date) != 10 or rate_date[4] != "-" or rate_date[7] != "-":
        raise ValueError(f"unexpected Cube time attribute: {rate_date!r}")

    last_modified = parsedate_to_datetime(last_modified_header)
    first_seen = parsedate_to_datetime(date_header)
    if last_modified.tzinfo is None or first_seen.tzinfo is None:
        raise ValueError("response headers carry no timezone")
    return Publication(
        rate_date=rate_date,
        last_modified_utc=format_utc(last_modified),
        first_seen_utc=format_utc(first_seen),
    )


def fetch() -> tuple[str, str, str]:
    """Return ``(xml_text, Last-Modified, Date)`` from the ECB endpoint."""
    request = Request(URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        xml_text = response.read().decode("utf-8")
        return xml_text, response.headers["Last-Modified"], response.headers["Date"]


def read_pairs(path: Path) -> set[tuple[str, str]]:
    """Return the ``(rate_date, last_modified_utc)`` pairs already recorded."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {(row["rate_date"], row["last_modified_utc"]) for row in csv.DictReader(handle)}


def record(path: Path, publication: Publication) -> bool:
    """Append ``publication`` unless its pair is present; return whether it was added."""
    if (publication.rate_date, publication.last_modified_utc) in read_pairs(path):
        return False
    is_new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new_file:
            writer.writerow(HEADER)
        writer.writerow(
            (publication.rate_date, publication.last_modified_utc, publication.first_seen_utc)
        )
    return True


def main(argv: list[str] | None = None) -> int:
    """Collect once. ``argv`` is accepted so tests can call this without arguments."""
    del argv  # no flags in v0.1
    try:
        xml_text, last_modified_header, date_header = fetch()
        publication = parse_publication(xml_text, last_modified_header, date_header)
        result = record(DATA_PATH, publication)
    except Exception as exc:
        print(f"collect.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if result:
        print(
            f"recorded {publication.rate_date} "
            f"last_modified={publication.last_modified_utc} "
            f"first_seen={publication.first_seen_utc}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
