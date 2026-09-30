#!/usr/bin/env python3
"""Rebuild the committed dbt manifest fixture.

Runs a real ``dbt parse`` with pinned versions through ``uvx`` and writes a trimmed
manifest next to the other fixtures. The trimming keeps only what FreshCal reads, so the
committed file stays small, deterministic, and free of machine-specific paths:

- ``metadata`` reduced to ``dbt_schema_version``, ``dbt_version``, ``project_name``,
  ``adapter_type`` (this drops ``invocation_id``, ``run_started_at``, ``generated_at``
  and the anonymous-usage ``user_id``);
- ``sources`` with each node's ``created_at`` removed;
- sorted keys, two-space indentation, one trailing newline.

Usage::

    uv run python scripts/regenerate_dbt_fixture.py

If the ``uvx`` build fails with ``CERTIFICATE_VERIFY_FAILED``, point OpenSSL at a CA
bundle first (observed on macOS with the python.org build):
``SSL_CERT_FILE=/opt/homebrew/etc/openssl@3/cert.pth uv run ...``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "dbt_project"
OUTPUT = REPOSITORY_ROOT / "tests" / "fixtures" / "dbt_manifest_v12.json"
DBT_CORE = "dbt-core==1.12.5"
DBT_DUCKDB = "dbt-duckdb==1.11.0"
METADATA_KEYS = ("dbt_schema_version", "dbt_version", "project_name", "adapter_type")


def run_dbt_parse(target_dir: Path) -> Path:
    """Run ``dbt parse`` and return the manifest it wrote."""
    command = [
        "uvx",
        "--from",
        DBT_CORE,
        "--with",
        DBT_DUCKDB,
        "dbt",
        "parse",
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(PROJECT_DIR),
        "--target-path",
        str(target_dir),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, cwd=REPOSITORY_ROOT
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout[-4000:])
        sys.stderr.write(completed.stderr[-4000:])
        raise SystemExit(f"dbt parse failed with exit code {completed.returncode}")
    manifest = target_dir / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"dbt parse did not write {manifest}")
    return manifest


def trim(manifest: dict[str, object]) -> dict[str, object]:
    """Keep only the keys FreshCal reads, with the noise removed."""
    metadata = manifest["metadata"]
    assert isinstance(metadata, dict)
    sources = manifest["sources"]
    assert isinstance(sources, dict)

    trimmed_metadata = {key: metadata[key] for key in METADATA_KEYS}
    trimmed_sources = {}
    for unique_id, node in sources.items():
        assert isinstance(node, dict)
        trimmed_node = {key: value for key, value in node.items() if key != "created_at"}
        trimmed_sources[unique_id] = trimmed_node
    return {"metadata": trimmed_metadata, "sources": trimmed_sources}


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        target_dir = Path(temporary) / "target"
        manifest_path = run_dbt_parse(target_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    trimmed = trim(manifest)
    OUTPUT.write_text(
        json.dumps(trimmed, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(REPOSITORY_ROOT)} with {len(trimmed['sources'])} sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
