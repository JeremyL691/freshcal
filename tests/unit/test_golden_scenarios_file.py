"""Structure checks for the golden scenario file (§3.10, §9.4)."""

from __future__ import annotations

from tests.golden.scenarios_loader import SCENARIO_IDS, SCENARIOS

EXPECTED_FIELDS = (
    "status",
    "release",
    "deadline",
    "next_expected_arrival",
    "missed_count",
    "missed_truncated",
    "pending_count",
    "warning_codes",
    "error_code",
    "explanation",
)


def test_scenario_file_holds_every_specified_row() -> None:
    assert len(SCENARIOS) == 44
    assert SCENARIO_IDS == [
        "G01",
        "G02",
        "G03",
        "G04",
        "G05",
        "G06",
        "G07",
        "G08",
        "G09",
        "G10",
        "G11",
        "G12",
        "G13",
        "G14",
        "G15",
        "G16",
        "G17",
        "G17b",
        "G18",
        "G19",
        "G20a",
        "G20b",
        "G21",
        "G22",
        "G23a",
        "G23b",
        "G24",
        "G25",
        "G26",
        "G27",
        "G28",
        "G29",
        "G30",
        "G31",
        "G32",
        "G33",
        "G34",
        "G35",
        "G36",
        "G37",
        "G41",
        "G42",
        "G43",
        "G44",
    ]
    for entry in SCENARIOS:
        for field in EXPECTED_FIELDS:
            assert field in entry["expect"], (entry["id"], field)


def test_scenario_titles_are_unique_and_present() -> None:
    titles = [str(entry["title"]) for entry in SCENARIOS]
    assert len(set(titles)) == len(titles)
    assert all(title.strip() for title in titles)
