"""The static verdicts match what real LangGraph does when the samples run (AC-2).

Nodes in the samples are plain functions, so these runs need no keys or network.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from conftest import ROOT, load_sample, rule_set
from langgraph.errors import GraphRecursionError, InvalidUpdateError

from langgraph_preflight import check


@pytest.mark.parametrize("name", ["fanout_conflict", "unbalanced_conflict", "command_fanout_conflict"])
def test_lp001_samples_raise_invalid_update(name: str) -> None:
    module = load_sample(name)
    assert ("LP001", "error") in rule_set(check(module.graph))
    with pytest.raises(InvalidUpdateError):
        module.graph.invoke(module.EXAMPLE_INPUT)


def test_send_fanout_depends_on_input_count() -> None:
    module = load_sample("send_fanout")
    assert ("LP002", "warning") in rule_set(check(module.graph))
    # Send 1개는 통과, 3개는 실패: 입력 수에 따라 숨는 충돌
    assert module.graph.invoke(module.example_input(1))["result"] == "summary of doc1"
    with pytest.raises(InvalidUpdateError):
        module.graph.invoke(module.example_input(3))


def test_closed_cycle_hits_recursion_limit() -> None:
    module = load_sample("closed_cycle")
    assert ("LP003", "error") in rule_set(check(module.graph))
    with pytest.raises(GraphRecursionError):
        module.graph.invoke(module.EXAMPLE_INPUT, {"recursion_limit": 50})


def test_opaque_route_ends_silently() -> None:
    module = load_sample("opaque_route")
    assert ("LP005", "warning") in rule_set(check(module.graph))
    seen: list[str] = []
    for chunk in module.graph.stream(module.EXAMPLE_INPUT, stream_mode="updates"):
        seen.extend(chunk)
    # 오류 없이 끝나지만 draft 다음 노드는 하나도 실행되지 않는다(ghost 도 review 도).
    assert seen == ["draft"]
    assert "reviewed" not in module.graph.invoke(module.EXAMPLE_INPUT)


def test_clean_agent_completes() -> None:
    module = load_sample("clean_agent")
    assert check(module.graph).findings == []
    result = module.graph.invoke(module.EXAMPLE_INPUT)
    assert result["rounds"] == 2
    assert len(result["notes"]) == 4


def test_running_the_sample_file_shows_the_error() -> None:
    # README 장면 2: python samples/fanout_conflict.py 를 직접 실행하면 실제 오류가 난다.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "samples" / "fanout_conflict.py")],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )
    assert proc.returncode != 0
    assert "InvalidUpdateError" in proc.stderr
