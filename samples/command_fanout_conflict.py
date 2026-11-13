r"""LP001 via Command: a node sends control to two nodes at once.

    r --Command(goto=["p", "q"])--> p, q      p and q both return {"x": ...} in step 2

Run it:   python samples/command_fanout_conflict.py   -> InvalidUpdateError
Check it: langgraph-preflight samples/command_fanout_conflict.py:graph
Nodes are plain functions standing in for LLM calls (no keys, no network).
"""

from typing import Literal, TypedDict

from langgraph.graph import START, StateGraph
from langgraph.types import Command


class State(TypedDict):
    x: str  # 리듀서 없음: 한 단계에 값 하나만 받는다


def r(state: State) -> Command[Literal["p", "q"]]:
    return Command(goto=["p", "q"])  # 두 노드가 같은 단계에 함께 실행된다


def p(state: State) -> dict:
    return {"x": "p"}


def q(state: State) -> dict:
    return {"x": "q"}


builder = StateGraph(State)
builder.add_node("r", r)
builder.add_node("p", p)
builder.add_node("q", q)
builder.add_edge(START, "r")
graph = builder.compile()

EXAMPLE_INPUT = {"x": ""}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
