"""LP005: a conditional edge without path_map or Literal hint returns a typo.

    START --> draft --route?--> "ghost"   (meant "review"; no such node)

LangGraph cannot know the destinations, so compile() passes and the run just
stops after "draft": no error, "review" never runs.
Run it:   python samples/opaque_route.py    -> finishes, "reviewed" never set
Check it: langgraph-preflight samples/opaque_route.py:graph
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    text: str
    reviewed: bool


def draft(state: State) -> dict:
    return {"text": "first draft"}


def review(state: State) -> dict:
    return {"reviewed": True}


def route(state: State):  # 반환 힌트도 path_map 도 없다
    return "ghost"  # 버그: "review" 를 의도했다


builder = StateGraph(State)
builder.add_node("draft", draft)
builder.add_node("review", review)
builder.add_edge(START, "draft")
builder.add_conditional_edges("draft", route)
builder.add_edge("review", END)
graph = builder.compile()

EXAMPLE_INPUT = {"text": ""}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
