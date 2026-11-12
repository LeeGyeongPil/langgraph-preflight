"""LP003: a retry loop wired with plain edges only. Once entered it never ends.

    START --> check --> fix --> check --> fix --> ...   (no way to END)

Run it:   python samples/closed_cycle.py   -> GraphRecursionError after the default
          recursion_limit (10007 steps on langgraph 1.2.21; with LLM nodes that is
          thousands of paid calls)
Check it: langgraph-preflight samples/closed_cycle.py:graph
"""

from typing import TypedDict

from langgraph.graph import START, StateGraph


class State(TypedDict):
    attempts: int


def check(state: State) -> dict:
    return {"attempts": state["attempts"] + 1}


def fix(state: State) -> dict:
    return {}


builder = StateGraph(State)
builder.add_node("check", check)
builder.add_node("fix", fix)
builder.add_edge(START, "check")
builder.add_edge("check", "fix")
builder.add_edge("fix", "check")  # 버그: 끝내는 조건 분기가 없다
graph = builder.compile()

EXAMPLE_INPUT = {"attempts": 0}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
