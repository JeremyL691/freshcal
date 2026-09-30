"""Tests for the safe YAML loader."""

from __future__ import annotations

import datetime
import os
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
    assert issue.message == f"cannot read config file {missing}: file not found"
    assert issue.location == str(missing)


def test_cfg_09_directory_is_e110_not_e599(tmp_path: Path) -> None:
    """CFG-09: a config path that is a directory is an E110 read failure, not E599."""
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_file(tmp_path)
    issue = excinfo.value.issue
    assert issue.code == "E110"
    assert issue.message == f"cannot read config file {tmp_path}: is a directory"


def test_cfg_09_non_utf8_file_is_e110(tmp_path: Path) -> None:
    """CFG-09: a config file that is not valid UTF-8 is an E110 read failure."""
    path = tmp_path / "latin1.yml"
    path.write_bytes(b"version: 1\n# caf\xe9\n")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_file(path)
    issue = excinfo.value.issue
    assert issue.code == "E110"
    assert issue.message == f"cannot read config file {path}: not valid UTF-8"


def test_cfg_09_unreadable_file_is_e110(tmp_path: Path) -> None:
    """CFG-09: a permission error is an E110 read failure, not E599."""
    if os.geteuid() == 0:
        pytest.skip("running as root, permissions are not enforced")
    path = tmp_path / "secret.yml"
    path.write_text("version: 1\n", encoding="utf-8")
    path.chmod(0)
    try:
        with pytest.raises(ConfigError) as excinfo:
            load_yaml_file(path)
    finally:
        path.chmod(0o600)
    issue = excinfo.value.issue
    assert issue.code == "E110"
    assert issue.message == f"cannot read config file {path}: permission denied"


def test_cfg_16_duplicate_mapping_key_is_e100_with_line_and_column() -> None:
    """CFG-16: a duplicate mapping key is a YAML error with the second key's position."""
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("version: 1\nsources: []\nversion: 2\n", source="conf.yml")
    issue = excinfo.value.issue
    assert issue.code == "E100"
    assert issue.message.startswith("conf.yml: YAML syntax error:")
    assert "duplicate key 'version'" in issue.message
    assert "line 3" in issue.message
    assert "column 1" in issue.message


def test_cfg_16_duplicate_nested_key_is_e100() -> None:
    """CFG-16: the check runs for every mapping, not only the document root."""
    text = "version: 1\nsources:\n  - name: a.b\n    grace: 1h\n    grace: 300d\n"
    with pytest.raises(ConfigError) as excinfo:
        load_yaml(text, source="conf.yml")
    assert excinfo.value.issue.code == "E100"
    assert "duplicate key 'grace'" in excinfo.value.issue.message


def test_cfg_16_merge_keys_are_not_duplicates() -> None:
    """CFG-16: ``<<`` is YAML's documented default mechanism, so an override is legal."""
    loaded = load_yaml(
        "defaults: &d {grace: 1h, timezone: UTC}\nsource:\n  <<: *d\n  grace: 2h\n",
        source="t",
    )
    assert loaded == {
        "defaults": {"grace": "1h", "timezone": "UTC"},
        "source": {"grace": "2h", "timezone": "UTC"},
    }


def test_u_yaml_07_python_object_tag_is_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("a: !!python/object:os.system {}\n", source="t")
    assert excinfo.value.issue.code == "E100"


def test_load_yaml_file_reads_documents(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("version: 1\nsources: []\n", encoding="utf-8")
    assert load_yaml_file(path) == {"version": 1, "sources": []}


def test_cfg_09_over_deep_yaml_is_e100_not_recursion_error() -> None:
    """CFG-09: a document the parser cannot descend is a decode error, not E599."""
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("[" * 100_000 + "]" * 100_000, source="deep.yml")
    issue = excinfo.value.issue
    assert issue.code == "E100"
    assert issue.message.startswith("deep.yml: YAML syntax error:")


def test_loader_does_not_mutate_safeloader() -> None:
    """The resolvers are replaced on our subclass only."""
    assert isinstance(yaml.SafeLoader("2026-01-04").get_single_data(), datetime.date), (
        "SafeLoader keeps the timestamp resolver"
    )
    assert load_yaml("d: 2026-01-04\n", source="t") == {"d": "2026-01-04"}


AUD07_BAD_TAGS = (
    ("version: !!int nope\n", "!!int"),
    ("version: !!float nope\n", "!!float"),
    ("version: !!int 0x\n", "!!int"),
    ("version: !!bool nope\n", "!!bool"),
    ("version: !!timestamp nope\n", "!!timestamp"),
)


@pytest.mark.parametrize(("text", "tag"), AUD07_BAD_TAGS, ids=[tag for _, tag in AUD07_BAD_TAGS])
def test_aud07_a_malformed_explicit_tag_is_e100_with_its_position(text: str, tag: str) -> None:
    """AUD-07: `!!int nope` is a document error, not an internal failure (was E599/exit 3).

    PyYAML lets the constructor's own exception escape — `ValueError` for int/float, a
    `KeyError` for bool, an `AttributeError` for timestamp — so the loader converts each one
    into the `E100` its docstring promises, naming the tag, the value and the position.
    """
    with pytest.raises(ConfigError) as excinfo:
        load_yaml(text, source="tag.yml")
    issue = excinfo.value.issue
    assert issue.code == "E100"
    assert f"invalid {tag} value" in issue.message
    assert "line 1, column 10" in issue.message
    assert "tag.yml" in issue.message


@pytest.mark.parametrize(
    "text",
    [
        "a: !!int 1\n",
        "a: !!int -7\n",
        "a: !!int 0x1f\n",
        "a: !!float 1.5\n",
        "a: !!float .inf\n",
        "a: !!bool true\n",
        "a: !!bool off\n",
        "a: !!timestamp 2026-01-04T12:00:00Z\n",
    ],
)
def test_aud07_valid_explicit_tags_still_construct(text: str) -> None:
    """AUD-07: the controls — a well-formed explicit tag keeps working."""
    loaded = load_yaml(text, source="tag.yml")
    assert set(loaded) == {"a"}
    assert loaded["a"] is not None


def test_aud07_the_implicit_resolvers_are_unchanged() -> None:
    """AUD-07: dates and base-60 numbers still stay strings, and ints still resolve."""
    loaded = load_yaml("date: 2026-01-04\ntime: 16:00\nint: 42\n", source="t")
    assert loaded == {"date": "2026-01-04", "time": "16:00", "int": 42}


def test_aud07_yaml_safeloader_is_isolated() -> None:
    """AUD-07: registering the constructors copies the dict — `yaml.SafeLoader` is untouched."""
    with pytest.raises(ValueError, match="invalid literal for int"):
        yaml.load("a: !!int nope\n", Loader=yaml.SafeLoader)
    assert yaml.load("a: 2026-01-04\n", Loader=yaml.SafeLoader) == {"a": datetime.date(2026, 1, 4)}


def test_aud07_duplicate_keys_are_still_rejected() -> None:
    """AUD-07: the duplicate-key check still runs (the constructors were only wrapped)."""
    with pytest.raises(ConfigError) as excinfo:
        load_yaml("a: 1\na: 2\n", source="dup.yml")
    assert excinfo.value.issue.code == "E100"
    assert "duplicate key" in excinfo.value.issue.message
