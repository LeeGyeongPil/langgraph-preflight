"""Explore supersteps without running any node.

A *state* is "which nodes run in this step" plus "how far each join edge
(``add_edge([a, b], c)``) has progressed". From one state we build every next
state: plain edges always fire, every conditional branch and every ``Command``
picks one of its known destinations (each pick is a separate path). States are
remembered, so loops end, and a cap (``max_states``) keeps big graphs bounded.

The result is the list of reachable steps, each with the shortest path that
leads there and the branch picks along that path (the evidence we print).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import product

from .model import END, START, GraphModel
from .writes import SourceFacts

# 한 단계 상태: (이번 단계에 실행되는 노드, 대기 엣지별로 이미 끝난 출발 노드)
State = tuple[frozenset[str], frozenset[tuple[int, frozenset[str]]]]


@dataclass
class Step:
    number: int  # START 다음 단계가 1
    nodes: frozenset[str]
    path: list[str]  # ["START", "a", "{b, c}"]
    choices: list[str]  # ["route@d -> a"]


@dataclass
class Exploration:
    steps: list[Step]
    truncated: bool


def _fmt_set(nodes: frozenset[str]) -> str:
    names = sorted(nodes)
    return names[0] if len(names) == 1 else "{" + ", ".join(names) + "}"


# 선택지: (함께 활성화되는 목적지들, 증거 라벨). 목적지가 없으면 이동 없이 끝나는 경우.
Option = tuple[tuple[str, ...], str | None]


def _options(model: GraphModel, facts: dict[str, SourceFacts], node: str) -> list[list[Option]]:
    """Choice groups for one node: each group lists (destinations, label) and exactly one is taken."""
    groups: list[list[Option]] = []
    for branch in model.branches:
        if branch.source != node:
            continue
        router = facts.get(f"router:{branch.source}:{branch.name}")
        sends = sorted(router.send_targets) if router else []
        dests: list[str] = []
        if branch.ends is not None:
            dests = sorted(set(branch.ends.values()) | set(sends))
        elif sends:
            dests = sends  # path_map 은 없지만 Send 대상은 소스에서 보인다
        if not dests:
            continue  # 목적지를 모르는 분기는 전개하지 않는다(LP005 가 따로 알린다)
        groups.append([((() if d == END else (d,)), f"{branch.name}@{node} -> {'END' if d == END else d}") for d in dests])
    if node in model.nodes:
        spec = model.nodes[node]
        node_facts = facts.get(node)
        dests = set(spec.ends)
        if node_facts:
            dests |= node_facts.send_targets
        multi = node_facts.command_goto_groups if node_facts else []
        if dests or multi:
            group: list[Option] = [
                ((() if d == END else (d,)), f"Command@{node} -> {'END' if d == END else d}") for d in sorted(dests)
            ]
            # Command(goto=["p", "q"]): 목적지 전부가 같은 단계에 함께 실행되는 선택지 하나
            for names in multi:
                group.append((tuple(d for d in names if d != END), f"Command@{node} -> {_fmt_set(frozenset(names))}"))
            if not (node_facts and node_facts.always_command):
                group.append(((), None))  # Command 없이 끝나는 경우
            groups.append(group)
    return groups


def explore(model: GraphModel, facts: dict[str, SourceFacts], max_states: int = 20000) -> Exploration:
    static: dict[str, list[str]] = {}
    for src, dst in sorted(model.edges):
        static.setdefault(src, []).append(dst)

    def successors(active: frozenset[str], joins: dict[int, frozenset[str]]):
        fixed: set[str] = set()
        for node in active:
            fixed.update(d for d in static.get(node, []) if d != END)
        # 대기 엣지: 출발 노드가 모두 끝난 단계 다음에 도착 노드가 실행된다.
        new_joins: dict[int, frozenset[str]] = {}
        for idx, (srcs, dst) in enumerate(model.waiting_edges):
            seen = joins.get(idx, frozenset()) | (active & frozenset(srcs))
            if seen == frozenset(srcs):
                if dst != END:
                    fixed.add(dst)
            elif seen:
                new_joins[idx] = seen
        joins_key = frozenset(new_joins.items())
        groups: list[list[Option]] = []
        for node in sorted(active):
            groups.extend(_options(model, facts, node))
        for picks in product(*groups):
            nxt = set(fixed)
            labels: list[str] = []
            for dests, label in picks:
                nxt.update(dests)
                if label is not None:
                    labels.append(label)
            yield (frozenset(nxt), joins_key), labels

    steps: list[Step] = []
    seen: set[State] = set()
    queue: deque[tuple[State, int, list[str], list[str]]] = deque()

    for state, labels in successors(frozenset([START]), {}):
        queue.append((state, 1, ["START"], labels))

    # 상태 수와 따져 본 전이 수 모두 상한을 둔다(분기 조합 폭발 방지).
    max_transitions = max_states * 10
    transitions = 0
    truncated = False
    while queue and not truncated:
        (active, joins), number, path, choices = queue.popleft()
        if not active or (active, joins) in seen:
            continue  # 실행할 노드가 없으면 그 갈래는 끝난다
        if len(seen) >= max_states:
            truncated = True
            break
        seen.add((active, joins))
        here = path + [_fmt_set(active)]
        steps.append(Step(number, active, here, choices))
        for nxt, labels in successors(active, dict(joins)):
            transitions += 1
            if transitions > max_transitions:
                truncated = True
                break
            if nxt not in seen:
                queue.append((nxt, number + 1, here, choices + labels))
    return Exploration(steps, truncated)
