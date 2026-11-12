r"""LP001 with branches of different length: they still meet in the same step.

    a --> b --> d --------> END        d runs in step 3
      \--> c --> e --> f --> END       e runs in step 3 too

d and e do not share a parent, yet both write "x" in step 3.
Run it:   python samples/unbalanced_conflict.py    -> InvalidUpdateError
Check it: langgraph-preflight samples/unbalanced_conflict.py:graph
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int
    draft: str


def a(state: State) -> dict:
    return {"draft": "plan"}


def b(state: State) -> dict:
    return {"draft": state["draft"] + " -> searched"}


def c(state: State) -> dict:
    return {}


def d(state: State) -> dict:
    return {"x": 1}  # 3단계


def e(state: State) -> dict:
    return {"x": 2}  # 이것도 3단계 -> d 와 충돌


def f(state: State) -> dict:
    return {}


builder = StateGraph(State)
for name, fn in [("a", a), ("b", b), ("c", c), ("d", d), ("e", e), ("f", f)]:
    builder.add_node(name, fn)
builder.add_edge(START, "a")
builder.add_edge("a", "b")
builder.add_edge("a", "c")
builder.add_edge("b", "d")
builder.add_edge("c", "e")
builder.add_edge("e", "f")
builder.add_edge("d", END)
builder.add_edge("f", END)
graph = builder.compile()

EXAMPLE_INPUT = {"x": 0, "draft": ""}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
