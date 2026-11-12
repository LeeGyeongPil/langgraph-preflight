"""LP004: a node nobody calls. It compiles fine and silently never runs.

    START --> respond --> END
              cite   --> END      (no edge into "cite")

Run it:   python samples/orphan_node.py    -> finishes, "citations" never set
Check it: langgraph-preflight samples/orphan_node.py:graph
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    question: str
    answer: str
    citations: list


def respond(state: State) -> dict:
    return {"answer": f"answer to {state['question']}"}


def cite(state: State) -> dict:
    return {"citations": ["source-1"]}


builder = StateGraph(State)
builder.add_node("respond", respond)
builder.add_node("cite", cite)
builder.add_edge(START, "respond")
builder.add_edge("respond", END)
builder.add_edge("cite", END)  # 버그: respond -> cite 엣지를 빠뜨렸다
graph = builder.compile()

EXAMPLE_INPUT = {"question": "why"}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
