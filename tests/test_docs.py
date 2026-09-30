"""Documentation guards: links resolve, and no unbacked marketing claims appear.

Four checks, all from BLUEPRINT.md §11.1 and its rules P6/P7: every relative Markdown link
in the README and in ``docs/*.md`` points to a file that exists; the README avoids the
phrases that imply claims this project cannot back with tests or produced validation
results; the README's platform sentence is exactly the one PROGRESS.md "Environment"
records as what ran (rule P6, finding CLI-02); and "Tested on Linux" stays banned while
the CI workflow has not run remotely.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
README = REPOSITORY_ROOT / "README.md"
PROGRESS = REPOSITORY_ROOT / "PROGRESS.md"
DOCS = sorted((REPOSITORY_ROOT / "docs").glob("*.md"))

#: The platform sentence (README Limitations, PROGRESS "Environment"): what actually ran.
#: Kept as one constant so README, PROGRESS and this guard cannot drift apart; widening it
#: (for example after running the suite on another Python) means updating all three
#: together (rule P6).
PLATFORM_SENTENCE = (
    "Tested on macOS with Python 3.11 (full suite); the CI workflow targets Linux "
    "and Python 3.11-3.14 but has not run yet."
)

BANNED_PHRASES = (
    "production-ready",
    "production ready",
    "battle-tested",
    "battle tested",
    "blazing",
    "enterprise-grade",
    "enterprise grade",
    "tested on linux",
)
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def normalised_text(path: Path) -> str:
    """The document's text with hard wrapping collapsed, so phrases can be searched."""
    return " ".join(path.read_text(encoding="utf-8").split())


def relative_links(path: Path) -> list[str]:
    """Link targets that point inside the repository (not URLs, anchors, or mailto)."""
    targets: list[str] = []
    for target in LINK_PATTERN.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        targets.append(target.split("#", 1)[0])
    return targets


def test_d_01_every_relative_link_points_to_an_existing_file() -> None:
    documents = [README, *DOCS]
    assert len(DOCS) >= 3, DOCS
    missing: list[str] = []
    for document in documents:
        for target in relative_links(document):
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert missing == []


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_d_02_readme_has_no_banned_phrases(phrase: str) -> None:
    text = README.read_text(encoding="utf-8").lower()
    assert phrase in BANNED_PHRASES
    assert phrase not in text, phrase


def test_d_02_docs_have_no_banned_phrases() -> None:
    for document in DOCS:
        lowered = document.read_text(encoding="utf-8").lower()
        for phrase in BANNED_PHRASES:
            assert phrase not in lowered, (document.name, phrase)


def test_d_03_readme_platform_sentence_matches_the_progress_environment_row() -> None:
    """D-03 (CLI-02): README states exactly the platform combination that ran.

    The sentence is the ``PLATFORM_SENTENCE`` constant PROGRESS.md's Environment table
    names; all three must agree, so a widening after a real run is a deliberate edit.
    """
    progress = normalised_text(PROGRESS)
    assert "PLATFORM_SENTENCE" in progress
    assert PLATFORM_SENTENCE in progress
    assert PLATFORM_SENTENCE in normalised_text(README)


def test_d_04_linux_stays_banned_while_ci_has_not_run() -> None:
    """D-04 (CLI-02): without a CI run, no "Tested on Linux" claim may appear.

    Pairs the banned phrase with PROGRESS.md's own CI status: once a remote exists and CI
    has run, both this guard and the PROGRESS row are updated together (rule P6).
    """
    progress = normalised_text(PROGRESS)
    if "CI not yet run remotely" in progress:
        assert "tested on linux" in BANNED_PHRASES
        assert "tested on linux" not in normalised_text(README).lower()


def test_readme_documents_every_cli_command_and_exit_code() -> None:
    text = README.read_text(encoding="utf-8")
    for command in ("freshcal check", "freshcal next", "freshcal explain", "freshcal validate"):
        assert command in text, command
    for code in ("**0**", "**1**", "**2**", "**3**"):
        assert code in text, code


def test_readme_states_the_current_state_promise() -> None:
    """The promise from BLUEPRINT §3.7.3 must appear verbatim in spirit."""
    text = normalised_text(README)
    assert "current-state verdict, not a punctuality record" in text
    assert "reload of old rows can hide a missing release" in text


def test_external_tool_statements_carry_their_check_date() -> None:
    text = README.read_text(encoding="utf-8")
    section = text.split("## How FreshCal relates to other tools", 1)[1]
    assert "Checked 2026-09-29" in section
    assert "checked 2026-09-29" in section
    assert "dbt issue\n  #10963" in section or "#10963" in section
    assert "data_freshness_sla" in section
