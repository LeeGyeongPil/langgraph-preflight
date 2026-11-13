# Changelog

All notable changes to this project are listed here. Format follows Keep a Changelog; versions follow SemVer.

## [0.1.2] - 2026-11-14

### Fixed
- `Command(goto=["p", "q"])` with string-literal destinations is now expanded as one step where all listed nodes run together, so a same-step write to a key without a reducer is reported as LP001 (it previously passed with "No findings" while the real run raised `InvalidUpdateError`). A goto list with non-literal items is reported as LP006. Router lists (`add_conditional_edges`) behave as before.

### Known issues
- **A goto list mixing `END` constants with node-name literals is reported as LP006 instead of being expanded; the LP001 message wording "when the branches pick" is also slightly off for `Command` lists.**

## [0.1.1] - 2026-11-13

### Fixed
- CI: pin line endings to LF (`.gitattributes`) so Windows checkouts read samples and fixtures byte-for-byte like Linux and macOS, and run the console-script steps under UTF-8 explicitly. No change to rules, output, exit codes or API.

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
