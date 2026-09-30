"""I-PG-08: a DSN secret never reaches any output (CFG-01, T-7.6).

These checks need no PostgreSQL server: a malformed DSN fails while it is parsed, and the
two remaining cases fail before a connection is established (a closed port and an invalid
``sslmode``), so the module carries no ``postgres`` marker (BLUEPRINT.md §7.2 as amended
by A-12). The config file only ever holds the *name* of the environment variable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freshcal import cli
from freshcal.adapters.postgres_reader import PostgresReader
from freshcal.core.errors import QueryError

SECRET = "50%offS3cret"
ENCODED_SECRET = "50%25offS3cret"
ENV_VAR = "FRESHCAL_LEAK_DSN"

# Every case carries the secret; only the malformed-DSN cases are parse errors.
DSN_CASES = (
    pytest.param(
        f"postgresql://app:{SECRET}@127.0.0.1:1/postgres",
        id="percent-password",
    ),
    pytest.param(
        f"postgresql://app:{ENCODED_SECRET}@[127.0.0.1/postgres",
        id="unclosed-bracket",
    ),
    pytest.param(SECRET, id="bare-password"),
    pytest.param(
        f"postgresql://app:{ENCODED_SECRET}@127.0.0.1:1/postgres?sslmode=bogus",
        id="bad-sslmode",
    ),
)

CONFIG = """\
version: 1
connection: {type: postgres, dsn_env: FRESHCAL_LEAK_DSN}
defaults: {grace: 1h, timezone: UTC}
sources:
  - {name: a.b, relation: t, loaded_at_field: x, schedule: {kind: business_days, time: "16:00"}}
"""
NOW = "--now=2026-09-28T00:00:00Z"


@pytest.fixture
def leak_config(tmp_path: Path) -> str:
    path = tmp_path / "leak.yml"
    path.write_text(CONFIG)
    return str(path)


@pytest.mark.parametrize("dsn", DSN_CASES)
def test_i_pg_08_dsn_secret_never_leaks(
    dsn: str,
    leak_config: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CFG-01: the reader, the table, the JSON report and ``explain`` all stay clean."""
    monkeypatch.setenv(ENV_VAR, dsn)

    with pytest.raises(QueryError) as excinfo:
        PostgresReader(dsn_env=ENV_VAR)
    issue = excinfo.value.issue
    assert issue.code == "E501", issue.message
    for forbidden in (SECRET, ENCODED_SECRET, dsn):
        assert forbidden not in issue.message, issue.message

    code, out, err = _run(["check", "-c", leak_config, NOW], capsys)
    assert "E501" in out
    assert code == 3
    _assert_clean(out + err, dsn)

    code, out, err = _run(["check", "-c", leak_config, NOW, "--format", "json"], capsys)
    report = json.loads(out)
    assert report["results"][0]["status"] == "QUERY_ERROR"
    assert report["results"][0]["error"]["code"] == "E501"
    assert code == 3
    _assert_clean(out + err, dsn)

    code, out, err = _run(["explain", "a.b", "-c", leak_config, NOW], capsys)
    assert code == 3
    assert "E501" in err
    _assert_clean(out + err, dsn)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _assert_clean(text: str, dsn: str) -> None:
    for forbidden in (SECRET, ENCODED_SECRET, dsn):
        assert forbidden not in text, text
