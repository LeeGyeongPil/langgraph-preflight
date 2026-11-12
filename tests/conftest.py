"""Shared test setup: English screen, repository root as working directory, sample loader."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@pytest.fixture(autouse=True)
def english_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests always run with the English screen unless a test sets the language itself."""
    monkeypatch.setenv("LANGGRAPH_PREFLIGHT_LANG", "en")
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(ROOT)


def load_sample(name: str):
    """Import samples/<name>.py as a module (its __main__ block does not run)."""
    path = SAMPLES / f"{name}.py"
    module_name = f"_sample_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # samples/ 에 __pycache__ 를 남기지 않는다
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def rule_set(report) -> set[tuple[str, str]]:
    return {(f.rule, f.severity.value) for f in report.findings}
