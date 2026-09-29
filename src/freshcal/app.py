"""Application use cases and exit-code policy.

This module depends only on :mod:`freshcal.core`; adapters are injected (``Clock``,
``FreshnessReader``, ``CalendarProvider``, ``SourceCatalog``). T-5.3 adds the two
functions that decide *which* sources a run evaluates; the ``run_*`` use cases and the
exit-code policy arrive in T-6.1.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import SourceEntry, mark_duplicate_source_ids

__all__ = ["merge_entries", "select_entries"]


def merge_entries(
    config_entries: Iterable[SourceEntry],
    dbt_entries: Iterable[SourceEntry],
    *,
    config_file: str = "",
) -> tuple[list[SourceEntry], list[Issue]]:
    """Combine config-file and manifest sources, sorted by source ID.

    Precedence (BLUEPRINT.md §4.8): a source defined in the config file wins **entirely**
    over the manifest definition — no field-level merge — and the run emits ``W004``
    naming both places. Duplicate IDs *inside* the manifest (the same
    ``source_name.name`` from two packages) are an error on every entry involved
    (``E206``), because nothing can say which one the operator meant.

    The returned warnings carry the affected source ID in their ``location``, so the
    application layer can attach each one to that source's result.
    """
    config_list = list(config_entries)
    config_ids = {entry.source_id for entry in config_list}
    warnings: list[Issue] = []
    merged: list[SourceEntry] = list(config_list)

    manifest_survivors: list[SourceEntry] = []
    for entry in dbt_entries:
        if entry.source_id in config_ids:
            warnings.append(
                Issue(
                    "W004",
                    f"'{entry.source_id}' is defined in both {config_file} and the dbt "
                    "manifest; using the config file definition",
                    entry.source_id,
                )
            )
            continue
        manifest_survivors.append(entry)

    merged.extend(mark_duplicate_source_ids(manifest_survivors))
    merged.sort(key=lambda entry: entry.source_id)
    return merged, warnings


def select_entries(
    entries: Sequence[SourceEntry], patterns: Sequence[str] | None
) -> list[SourceEntry]:
    """Apply ``--select`` ``fnmatch`` patterns; a pattern-free call selects everything.

    Raises ``ConfigError`` ``E210`` when patterns were given but matched nothing: a
    selection typo must fail loudly rather than silently check zero sources.
    """
    if not patterns:
        return list(entries)
    selected = [
        entry
        for entry in entries
        if any(fnmatchcase(entry.source_id, pattern) for pattern in patterns)
    ]
    if not selected:
        raise ConfigError(Issue("E210", f"--select matched no sources: {', '.join(patterns)}"))
    return selected
