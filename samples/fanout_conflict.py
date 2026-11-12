r"""LP001: two parallel nodes write the same reducer-less key.

    a --> b --\
      \--> c --+--> END       b and c both return {"x": ...} in step 2

Run it:   python samples/fanout_conflict.py        -> InvalidUpdateError
Check it: langgraph-preflight samples/fanout_conflict.py:graph
Nodes are plain functions standing in for LLM calls (no keys, no network).
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int  # 리듀서 없음: 한 단계에 값 하나만 받는다
    logs: Annotated[list, operator.add]  # 리듀서 있음: 병렬 쓰기가 합쳐진다


def a(state: State) -> dict:
    return {"x": 1, "logs": ["a"]}


def b(state: State) -> dict:
    return {"x": state["x"] + 1, "logs": ["b"]}


def c(state: State) -> dict:
    return {"x": state["x"] * 10, "logs": ["c"]}


builder = StateGraph(State)
builder.add_node("a", a)
builder.add_node("b", b)
builder.add_node("c", c)
builder.add_edge(START, "a")
builder.add_edge("a", "b")  # a 다음 단계에서 b 와 c 가 함께 실행된다
builder.add_edge("a", "c")
builder.add_edge("b", END)
builder.add_edge("c", END)
graph = builder.compile()

EXAMPLE_INPUT = {"x": 0, "logs": []}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
