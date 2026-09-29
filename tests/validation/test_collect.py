"""Tests for the ECB publication-time collector. No network is used."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest

COLLECT_PATH = Path(__file__).resolve().parents[2] / "validation" / "ecb" / "collect.py"

LAST_MODIFIED = "Mon, 28 Sep 2026 13:56:44 GMT"
DATE_HEADER = "Mon, 28 Sep 2026 14:02:10 GMT"

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender>
    <gesmes:name>European Central Bank</gesmes:name>
  </gesmes:Sender>
  <Cube>
    <Cube time='2026-09-28'>
      <Cube currency='USD' rate='1.1657'/>
      <Cube currency='JPY' rate='173.42'/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


def load_collect() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ecb_collect", COLLECT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so register it first.
    sys.modules["ecb_collect"] = module
    spec.loader.exec_module(module)
    return module


collect = load_collect()


def test_parse_publication() -> None:
    publication = collect.parse_publication(SAMPLE_XML, LAST_MODIFIED, DATE_HEADER)
    assert publication.rate_date == "2026-09-28"
    assert publication.last_modified_utc == "2026-09-28T13:56:44Z"
    assert publication.first_seen_utc == "2026-09-28T14:02:10Z"


def test_parse_publication_rejects_document_without_dated_cube() -> None:
    with pytest.raises(ValueError, match="no dated Cube"):
        collect.parse_publication("<Envelope><Cube/></Envelope>", LAST_MODIFIED, DATE_HEADER)


def test_parse_publication_rejects_malformed_xml() -> None:
    with pytest.raises(ElementTree.ParseError):
        collect.parse_publication("<Envelope>", LAST_MODIFIED, DATE_HEADER)


def test_append_is_idempotent(tmp_path: Path) -> None:
    data_path = tmp_path / "publications.csv"
    publication = collect.parse_publication(SAMPLE_XML, LAST_MODIFIED, DATE_HEADER)

    assert collect.record(data_path, publication) is True
    assert collect.record(data_path, publication) is False

    rows = list(csv.DictReader(data_path.open(newline="", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0] == {
        "rate_date": "2026-09-28",
        "last_modified_utc": "2026-09-28T13:56:44Z",
        "first_seen_utc": "2026-09-28T14:02:10Z",
    }


def test_revision_appends_second_row(tmp_path: Path) -> None:
    data_path = tmp_path / "publications.csv"
    first = collect.parse_publication(SAMPLE_XML, LAST_MODIFIED, DATE_HEADER)
    revised = collect.parse_publication(
        SAMPLE_XML, "Mon, 28 Sep 2026 15:10:00 GMT", "Mon, 28 Sep 2026 15:15:00 GMT"
    )

    assert collect.record(data_path, first) is True
    assert collect.record(data_path, revised) is True

    rows = list(csv.DictReader(data_path.open(newline="", encoding="utf-8")))
    assert [row["rate_date"] for row in rows] == ["2026-09-28", "2026-09-28"]
    assert [row["last_modified_utc"] for row in rows] == [
        "2026-09-28T13:56:44Z",
        "2026-09-28T15:10:00Z",
    ]
