"""langgraph-preflight: check a LangGraph graph before it runs.

    from langgraph_preflight import check, assert_graph_ok

    report = check(graph)          # Report with findings LP001-LP006
    assert_graph_ok(graph)         # AssertionError if any error-level finding
"""

from __future__ import annotations

__version__ = "0.1.2"

from .errors import LoadError  # noqa: E402
from .report import Finding, Report, Severity, fail_threshold  # noqa: E402

__all__ = ["check", "assert_graph_ok", "Report", "Finding", "Severity", "LoadError", "__version__"]


def check(graph: object, *, max_states: int = 20000, target: str | None = None) -> Report:
    """Analyse a compiled graph (``builder.compile()``) or a ``StateGraph`` without running it."""
    from .loader import builder_of, ensure_compilable
    from .model import build_model
    from .rules import check_model

    builder = builder_of(graph)
    if builder is None:
        raise LoadError("err.not_graph", attr=target or "graph", kind=type(graph).__name__)
    if max_states < 1:
        raise ValueError("max_states must be at least 1")
    ensure_compilable(builder)
    model = build_model(builder)
    return Report(check_model(model, max_states), target=target, langgraph_version=model.langgraph_version)


def assert_graph_ok(graph: object, *, fail_on: str = "error", max_states: int = 20000) -> None:
    """Raise ``AssertionError`` listing rule, keys and nodes when findings reach ``fail_on``."""
    threshold = fail_threshold(fail_on)
    report = check(graph, max_states=max_states)
    if threshold is None:
        return
    bad = [f for f in report.findings if f.severity.rank >= threshold]
    if bad:
        lines = [f"langgraph-preflight found {len(bad)} problem(s) at or above '{fail_on}':"]
        for f in bad:
            lines.append(f"  {f.rule} {f.severity.value}: {f.message('en')}")
        raise AssertionError("\n".join(lines))
