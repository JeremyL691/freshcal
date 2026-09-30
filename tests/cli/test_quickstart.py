"""The README quickstart is executable (BLUEPRINT.md §11.1, §6.2).

Every command and every line of expected output in the quickstart is taken from
``README.md`` between its markers, run through ``cli.main``, and compared byte for byte,
including the ``FreshCal 0.1.0`` version line: since T-6.8 the released version string *is*
the running one, so the comparison is verbatim (finding CLI-21: the old test normalised
the version, which would have hidden a mismatch).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from freshcal import cli

README = Path("README.md")
QUICKSTART_CASES = (
    (1, "ON_TIME", 0),
    (2, "NOT_DUE", 0),
    (3, "OVERDUE", 1),
)


def marked_block(marker: str) -> list[str]:
    """The lines inside the fenced block that follows ``marker``."""
    lines = README.read_text(encoding="utf-8").splitlines()
    index = lines.index(marker)
    fence = next(i for i in range(index, len(lines)) if lines[i].startswith("```"))
    end = next(i for i in range(fence + 1, len(lines)) if lines[i].startswith("```"))
    return lines[fence + 1 : end]


@pytest.mark.parametrize(("index", "status", "expected_code"), QUICKSTART_CASES)
def test_q_01_q_03_quickstart_command_matches_the_readme(
    index: int,
    status: str,
    expected_code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = " ".join(marked_block(f"<!-- quickstart:command:{index} -->"))
    expected = "\n".join(marked_block(f"<!-- quickstart:output:{index} -->")) + "\n"

    assert command.startswith("uv run freshcal "), command
    argv = command.split()[3:]  # drop "uv", "run", and the "freshcal" script name
    code = cli.main(argv)
    captured = capsys.readouterr()

    assert code == expected_code
    assert captured.err == ""
    assert captured.out == expected
    assert status in captured.out


def test_quickstart_covers_all_three_verdicts() -> None:
    statuses = {status for _, status, _ in QUICKSTART_CASES}
    assert statuses == {"ON_TIME", "NOT_DUE", "OVERDUE"}
    assert {code for _, _, code in QUICKSTART_CASES} == {0, 1}


def test_example_config_and_data_are_present_and_synthetic() -> None:
    config = Path("examples/ecb/freshcal.yml")
    data = Path("examples/ecb/fx_rates.csv")
    assert config.is_file()
    assert data.is_file()

    text = config.read_text(encoding="utf-8")
    for fragment in (
        'path: ":memory:"',
        "relation: \"read_csv('fx_rates.csv')\"",
        "observed_timezone: UTC",
        "financial: XECB",
        'time: "15:45"',
        "grace: 2h15m",
    ):
        assert fragment in text, fragment

    header, *rows = data.read_text(encoding="utf-8").splitlines()
    assert header == "rate_date,currency,rate,_loaded_at"
    assert rows[-1].endswith("2026-09-25 14:07:00")
    assert all(len(row.split(",")) == 4 for row in rows)

    readme = README.read_text(encoding="utf-8")
    assert "**synthetic**" in readme
