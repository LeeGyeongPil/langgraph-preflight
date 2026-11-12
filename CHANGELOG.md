# Changelog

All notable changes to this project are listed here. Format follows Keep a Changelog; versions follow SemVer.

## [0.1.0] - 2026-11-13

### Added
- `langgraph-preflight` CLI and Python API (`check`, `assert_graph_ok`) that statically check a LangGraph `StateGraph` before it runs.
- Rules LP001 (same-step writes to a key without a reducer), LP002 (Send target writes), LP003 (cycle with no exit), LP004 (unreachable node), LP005 (route to nowhere), LP006 (analysis gap).
- Table, JSON and SARIF output; `--output` refuses to overwrite files that are not its own reports.
- English and Korean screens (`--lang`, `LANGGRAPH_PREFLIGHT_LANG`).
- A clear exit-2 error when the graph cannot be built (`compile()` would fail).

### Known issues
- Checking a `StateGraph` builder object calls `validate()` on it, which marks the builder as compiled; adding nodes afterwards logs a misleading warning. The CLI is unaffected.
- Two CLI messages have small grammar slips (`is a int`, `1 findings`).
