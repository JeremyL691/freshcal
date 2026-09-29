"""Tests for the safe YAML loader (BLUEPRINT.md §4.1)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

from freshcal.config.yaml_loader import load_yaml, load_yaml_file
from freshcal.core.errors import ConfigError


def test_u_yaml_01_base60_integers_stay_strings() -> None:
    loaded = load_yaml("a: 16:00\nb: 12:30:45\nc: 1:30\n", source="t")
    assert loaded == {"a": "16:00", "b": "12:30:45", "c": "1:30"}


def test_u_yaml_02_base60_floats_stay_strings() -> None:
    loaded = load_yaml("a: 1:30.5\n", source="t")
    assert loaded == {"a": "1:30.5"}


def test_u_yaml_03_dates_and_timestamps_stay_strings() -> None:
    loaded = load_yaml(
        "date: 2026-01-04\ntimestamp: 2026-01-04 12:00:00\niso: 2026-01-04T12:00:00Z\n"
        "explicit: 2001-12-14 21:59:43.10 -5\n",
        source="t",
    )
    assert loaded == {
        "date": "2026-01-04",
        "timestamp": "2026-01-04 12:00:00",
        "iso": "2026-01-04T12:00:00Z",
        "explicit": "2001-12-14 21:59:43.10 -5",
    }
    assert all(isinstance(value, str) for value in loaded.values())


def test_u_yaml_04_ordinary_scalars_unchanged() -> None:
    text = (
        "int: 42\nnegative: -7\nfloat: 1.5\nhex: 0x1f\nbinary: 0b1010\noctal: 017\n"
        "with_exp: 1.0e3\nsigned_exp: 1.0e+3\nflag: true\n'off': false\nnothing: null\n"
        "quoted_true: 'true'\ntext: hello\n"
    )
    loaded = load_yaml(text, source="t")
    assert loaded == yaml.safe_load(text), "values without colons or dates are untouched"
    assert loaded == {
        "int": 42,
        "negative": -7,
        "float": 1.5,
        "hex": 31,
        "binary": 10,
        "octal": 15,
        "with_exp": "1.0e3",
        "signed_exp": 1000.0,
        "flag": True,
        "off": False,
        "nothing": None,
        "quoted_true": "true",
        "text": "hello",
    }


def test_u_yaml_05_syntax_error_is_e100_with_position() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("a: b: c\n", source="conf.yml")
    issue = excinfo.value.issue
    assert issue.code == "E100"
    assert issue.message.startswith("conf.yml: YAML syntax error:")
    assert "line 1" in issue.message
    assert "column 5" in issue.message
    assert issue.location == "conf.yml"


def test_u_yaml_06_missing_file_is_e110(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yml"
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_file(missing)
    issue = excinfo.value.issue
    assert issue.code == "E110"
    assert issue.message == f"config file not found: {missing}"


def test_u_yaml_07_python_object_tag_is_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("a: !!python/object:os.system {}\n", source="t")
    assert excinfo.value.issue.code == "E100"


def test_load_yaml_file_reads_documents(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("version: 1\nsources: []\n", encoding="utf-8")
    assert load_yaml_file(path) == {"version": 1, "sources": []}


def test_loader_does_not_mutate_safeloader() -> None:
    """The resolvers are replaced on our subclass only."""
    assert isinstance(yaml.SafeLoader("2026-01-04").get_single_data(), datetime.date), (
        "SafeLoader keeps the timestamp resolver"
    )
    assert load_yaml("d: 2026-01-04\n", source="t") == {"d": "2026-01-04"}
