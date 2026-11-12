"""Python API: check() and assert_graph_ok() (AC-9), plus the pinned internals they rely on."""

from __future__ import annotations

import pytest
from conftest import load_sample

from langgraph_preflight import LoadError, Report, Severity, assert_graph_ok, check
from langgraph_preflight.model import build_model


def test_check_returns_report() -> None:
    report = check(load_sample("fanout_conflict").graph)
    assert isinstance(report, Report)
    assert report.max_severity is Severity.ERROR
    assert report.fails("error")
    assert check(load_sample("clean_agent").graph).max_severity is None


def test_assert_graph_ok() -> None:
    assert_graph_ok(load_sample("clean_agent").graph)
    with pytest.raises(AssertionError) as info:
        assert_graph_ok(load_sample("fanout_conflict").graph)
    text = str(info.value)
    assert "LP001" in text
    assert "'x'" in text
    assert "b, c" in text


def test_assert_graph_ok_threshold() -> None:
    send = load_sample("send_fanout").graph
    assert_graph_ok(send)  # 경고만 있으면 기본(error) 기준 통과
    with pytest.raises(AssertionError, match="LP002"):
        assert_graph_ok(send, fail_on="warning")
    assert_graph_ok(load_sample("fanout_conflict").graph, fail_on="never")
    with pytest.raises(ValueError):
        assert_graph_ok(send, fail_on="sometimes")


def test_check_rejects_non_graph() -> None:
    with pytest.raises(LoadError) as info:
        check({"not": "a graph"})
    assert info.value.message_id == "err.not_graph"


def test_model_reads_pinned_internals() -> None:
    # 내부 속성(1.2.x)에서 채널 리듀서·엣지·노드 함수를 읽는지 버전 고정으로 확인한다.
    module = load_sample("clean_agent")
    model = build_model(module.graph.builder)
    assert model.channels["notes"].reducer == "add"
    assert model.channels["notes"].single_value is False
    assert model.channels["answer"].single_value is True
    assert (("doc_search", "web_search"), "write") in [(tuple(sorted(s)), d) for s, d in model.waiting_edges]
    assert ("__start__", "plan") in model.edges
    assert model.branches[0].ends == {"plan": "plan", "__end__": "__end__"}
    assert model.nodes["plan"].func is module.plan


def test_unsupported_builder_is_reported() -> None:
    class Broken:
        nodes: dict = {}

    with pytest.raises(LoadError) as info:
        build_model(Broken())
    assert info.value.message_id == "err.unsupported"
