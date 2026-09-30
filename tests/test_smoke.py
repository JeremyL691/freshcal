"""Smoke tests: the package installs, is versioned, and answers ``--version``."""

from __future__ import annotations

import pytest

from freshcal import __version__
from freshcal.cli import main

EXPECTED_VERSION = "0.1.2"


def test_version_matches_metadata() -> None:
    assert __version__ == EXPECTED_VERSION


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"freshcal {EXPECTED_VERSION}\n"
    assert captured.err == ""
