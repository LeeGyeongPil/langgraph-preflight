"""Rule verdicts, severities and evidence (AC-1, AC-3, AC-4)."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from conftest import load_sample, rule_set
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from langgraph_preflight import Severity, check
from langgraph_preflight.model import default_recursion_limit, langgraph_version


class S(TypedDict, total=False):
    x: int
    y: int
    logs: Annotated[list, operator.add]


def writes_x(state: S) -> dict:
    return {"x": 1}


def writes_logs(state: S) -> dict:
    return {"logs": ["ok"]}


def noop(state: S) -> dict:
    return {}


def _helper() -> dict:
    return {"x": 2}


def writes_via_helper(state: S) -> dict:
    return _helper()  # 쓰기 키를 소스만으로는 알 수 없다


# --- AC-1 -----------------------------------------------------------------------

def test_concurrent_write_rules() -> None:
    fan = check(load_sample("fanout_conflict").graph)
    assert rule_set(fan) == {("LP001", "error")}
    (f,) = fan.findings
    assert f.keys == ["x"]
    assert f.nodes == ["b", "c"]
    assert f.step == 2
    assert f.path == ["START", "a", "{b, c}"]
    assert f.message_id == "lp001.static"
    assert f.location and f.location["file"] == "samples/fanout_conflict.py"

    unbalanced = check(load_sample("unbalanced_conflict").graph)
    assert rule_set(unbalanced) == {("LP001", "error")}
    (f,) = unbalanced.findings
    assert (f.keys, f.nodes, f.step) == (["x"], ["d", "e"], 3)
    assert f.path == ["START", "a", "{b, c}", "{d, e}"]

    assert check(load_sample("clean_agent").graph).findings == []

    send = check(load_sample("send_fanout").graph)
    assert rule_set(send) == {("LP002", "warning")}
    (f,) = send.findings
    assert (f.nodes, f.keys) == (["worker"], ["result"])


def test_different_steps_do_not_conflict() -> None:
    # b 는 2단계, e 는 3단계: 같은 키에 써도 단계가 달라 충돌하지 않는다(프로브 B).
    g = StateGraph(S)
    for name, fn in [("a", noop), ("b", writes_x), ("c", noop), ("e", writes_x)]:
        g.add_node(name, fn)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    g.add_edge("c", "e")
    g.add_edge("b", END)
    g.add_edge("e", END)
    assert check(g.compile()).findings == []


def test_reducer_key_is_not_a_conflict() -> None:
    g = StateGraph(S)
    g.add_node("a", noop)
    g.add_node("b", writes_logs)
    g.add_node("c", writes_logs)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    assert check(g).findings == []  # 컴파일 전 StateGraph 도 받는다


def test_join_edge_shifts_the_step() -> None:
    # a -> b, a -> c -> e, join([b, e]) -> d, e -> f. d 와 f 는 모두 4단계(단순 깊이로는 d 가 3).
    g = StateGraph(S)
    for name, fn in [("a", noop), ("b", noop), ("c", noop), ("e", noop), ("d", writes_x), ("f", writes_x)]:
        g.add_node(name, fn)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    g.add_edge("c", "e")
    g.add_edge(["b", "e"], "d")
    g.add_edge("e", "f")
    report = check(g.compile())
    assert rule_set(report) == {("LP001", "error")}
    (f,) = report.findings
    assert (f.nodes, f.step) == (["d", "f"], 4)


def route_p_or_q(state: S) -> Literal["p", "q"]:
    return "p"


def test_branch_choice_conflict_reports_the_choice() -> None:
    g = StateGraph(S)
    for name, fn in [("a", noop), ("side", writes_x), ("p", writes_x), ("q", noop)]:
        g.add_node(name, fn)
    g.add_edge(START, "a")
    g.add_edge("a", "side")
    g.add_conditional_edges("a", route_p_or_q)
    report = check(g.compile())
    assert rule_set(report) == {("LP001", "error")}
    (f,) = report.findings
    assert f.message_id == "lp001.branch"
    assert f.nodes == ["p", "side"]
    assert "route_p_or_q@a -> p" in str(f.args["choices"])


# --- AC-3 -----------------------------------------------------------------------

def stay_in_loop(state: S) -> Literal["loop", "other"]:
    return "loop"


def maybe_end(state: S) -> Literal["loop", "__end__"]:
    return END


def test_closed_cycle() -> None:
    report = check(load_sample("closed_cycle").graph)
    assert rule_set(report) == {("LP003", "error")}
    (f,) = report.findings
    assert f.message_id == "lp003.unconditional"
    assert f.nodes == ["check", "fix"]
    assert f.args["cycle"] == "check -> fix -> check"
    # 기본 recursion_limit 은 설치된 langgraph 에서 읽는다(1.2.21 은 10007).
    installed = default_recursion_limit()
    assert f.args["limit"] == (str(installed) if installed is not None else "unknown")
    if langgraph_version() == "1.2.21":
        assert f.args["limit"] == "10007"
        assert "10007" in f.message("en")

    # 가능한 목적지가 모두 순환 안: loop -> (loop | other), other -> loop
    g = StateGraph(S)
    g.add_node("loop", noop)
    g.add_node("other", noop)
    g.add_edge(START, "loop")
    g.add_conditional_edges("loop", stay_in_loop)
    g.add_edge("other", "loop")
    branchy = check(g.compile())
    assert rule_set(branchy) == {("LP003", "error")}
    assert branchy.findings[0].message_id == "lp003.branch"
    assert branchy.findings[0].nodes == ["loop", "other"]

    # path_map 에 END 출구가 있으면 보고하지 않는다.
    g = StateGraph(S)
    g.add_node("loop", noop)
    g.add_edge(START, "loop")
    g.add_conditional_edges("loop", maybe_end, {"loop": "loop", "__end__": END})
    assert check(g.compile()).findings == []


def always_goto_a(state: S) -> Command[Literal["a"]]:
    return Command(goto="a", update={"y": 1})


def test_command_only_cycle_is_closed() -> None:
    g = StateGraph(S)
    g.add_node("a", noop)
    g.add_node("b", always_goto_a)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    report = check(g.compile())
    assert rule_set(report) == {("LP003", "error")}
    assert report.findings[0].message_id == "lp003.branch"


# --- AC-4 -----------------------------------------------------------------------

def fan_out(state: S) -> list:
    return [Send("worker", {"x": i}) for i in range(3)]


def jump_without_hint(state: S):
    return Command(goto="b", update={"y": 1})


def jump_with_hint(state: S) -> Command[Literal["b"]]:
    return Command(goto="b", update={"y": 1})


def test_reachability_and_opaque() -> None:
    orphan = check(load_sample("orphan_node").graph)
    assert rule_set(orphan) == {("LP004", "warning")}
    assert orphan.findings[0].nodes == ["cite"]
    assert orphan.findings[0].message_id == "lp004.unreachable"

    # Send 로만 가는 노드: path_map 이 없어 정적 엣지가 없다 -> LP004 는 info 로 강등
    g = StateGraph(S)
    g.add_node("worker", writes_logs)
    g.add_conditional_edges(START, fan_out)
    g.add_edge("worker", END)
    sent = check(g.compile())
    lp004 = [f for f in sent.findings if f.rule == "LP004"]
    assert [(f.nodes, f.severity, f.message_id) for f in lp004] == [(["worker"], Severity.INFO, "lp004.send_target")]
    assert ("LP005", "warning") in rule_set(sent)

    opaque = check(load_sample("opaque_route").graph)
    assert rule_set(opaque) == {("LP005", "warning"), ("LP004", "info")}
    lp005 = [f for f in opaque.findings if f.rule == "LP005"]
    assert lp005[0].message_id == "lp005.branch"
    assert lp005[0].args == {"source": "draft", "router": "route"}

    # 힌트 없는 Command 반환 노드 -> LP005, 힌트가 있으면 없음
    g = StateGraph(S)
    g.add_node("a", jump_without_hint)
    g.add_node("b", noop)
    g.add_edge(START, "a")
    no_hint = check(g.compile())
    assert [(f.rule, f.message_id, f.nodes) for f in no_hint.findings if f.rule == "LP005"] == [
        ("LP005", "lp005.command", ["a"])
    ]
    g = StateGraph(S)
    g.add_node("a", jump_with_hint)
    g.add_node("b", noop)
    g.add_edge(START, "a")
    assert check(g.compile()).findings == []

    # 쓰기 키를 모르는 노드는 LP006 info 이고 LP001 판정에서 빠진다.
    g = StateGraph(S)
    g.add_node("a", noop)
    g.add_node("b", writes_x)
    g.add_node("c", writes_via_helper)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    unknown = check(g.compile())
    assert rule_set(unknown) == {("LP006", "info")}
    assert unknown.findings[0].message_id == "lp006.unknown_writes.dynamic_return"
    assert unknown.findings[0].nodes == ["c"]


def build_with_subscript(state: S) -> dict:
    out = {}
    out["x"] = 1
    out.update(y=2)
    out.update({"logs": ["z"]})
    return out


def command_update(state: S):
    if state.get("x"):
        return {"x": 1}
    return Command(update={"y": 2}, goto="b")


def returns_state(state: S) -> S:
    state["x"] = 3
    return state


def spreads(state: S) -> dict:
    return {**state, "x": 1}


def maybe_command(state: S) -> Command[Literal["a"]]:
    if state.get("x"):
        return Command(goto="a")


class Agent:
    def node(self, state: S) -> dict:
        return {"y": 1}


def test_written_keys_from_source() -> None:
    from langgraph_preflight.writes import DYNAMIC, NO_SOURCE, RETURNS_STATE, analyze_function

    facts = analyze_function(build_with_subscript)
    assert (facts.keys, facts.unknown) == ({"x", "y", "logs"}, None)

    facts = analyze_function(command_update)
    assert (facts.keys, facts.unknown, facts.command_goto, facts.always_command) == ({"x", "y"}, None, True, False)

    assert analyze_function(returns_state).unknown == RETURNS_STATE
    assert analyze_function(spreads).unknown == DYNAMIC
    assert analyze_function(writes_via_helper).unknown == DYNAMIC
    assert analyze_function(always_goto_a).always_command is True
    assert analyze_function(maybe_command).always_command is False  # 끝에서 None 을 돌려줄 수 있다
    assert analyze_function(Agent().node).keys == {"y"}
    assert analyze_function(lambda s: {"x": 1}).unknown == NO_SOURCE
    assert analyze_function(fan_out).send_targets == {"worker"}


def test_exploration_cap_is_reported() -> None:
    report = check(load_sample("clean_agent").graph, max_states=1)
    assert ("LP006", "info") in rule_set(report)
    assert any(f.message_id == "lp006.truncated" for f in report.findings)
