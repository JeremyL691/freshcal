# Specification fixtures

Frozen copies of FreshCal's normative specification blocks, used by drift guards:

- `config.v1.schema.json` — the configuration JSON Schema; must equal
  `src/freshcal/schemas/config.v1.schema.json` (`tests/unit/config/test_schema.py`).
- `report.v1.schema.json` — the report JSON Schema; must equal
  `src/freshcal/schemas/report.v1.schema.json` (`tests/unit/adapters/test_report_json.py`).
- `example_check_report.json`, `example_check_table.txt`, `example_next_table.txt` — the
  example `check` report (JSON and table) and the `next` table that the reporters must
  reproduce byte-for-byte from domain objects (`tests/unit/adapters/`).

Changing one of these files is a change to the specified output format: it needs a
changelog entry and, for anything user-visible, a documented reason.
