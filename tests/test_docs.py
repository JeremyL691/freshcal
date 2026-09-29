"""Documentation guards: links resolve, and no unbacked marketing claims appear.

Two checks, both from BLUEPRINT.md §11.1: every relative Markdown link in the README and
in ``docs/*.md`` points to a file that exists, and the README avoids the phrases that
imply claims this project cannot back with tests or produced validation results.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
README = REPOSITORY_ROOT / "README.md"
DOCS = sorted((REPOSITORY_ROOT / "docs").glob("*.md"))
BANNED_PHRASES = (
    "production-ready",
    "production ready",
    "battle-tested",
    "battle tested",
    "blazing",
    "enterprise-grade",
    "enterprise grade",
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
