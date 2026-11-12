r"""A correct parallel research loop: no findings.

    START --> plan --> web_search --\
                  \--> doc_search --+--(join)--> write --route--> plan | END

- web_search and doc_search run in the same step, but both write "notes",
  which has a reducer (operator.add), so their results are merged.
- The loop plan -> ... -> write -> plan has an exit: route() may return END.
Check it: langgraph-preflight samples/clean_agent.py:graph   (exit code 0)
"""

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    question: str
    rounds: int
    notes: Annotated[list, operator.add]  # 병렬 노드가 함께 써도 합쳐진다
    answer: str


def plan(state: State) -> dict:
    return {"rounds": state["rounds"] + 1}


def web_search(state: State) -> dict:
    return {"notes": [f"web result {state['rounds']}"]}


def doc_search(state: State) -> dict:
    return {"notes": [f"doc result {state['rounds']}"]}


def write(state: State) -> dict:
    return {"answer": " / ".join(state["notes"])}


def route(state: State) -> Literal["plan", "__end__"]:
    # 두 번 돌면 끝낸다: 순환에 END 출구가 있다.
    return "plan" if state["rounds"] < 2 else END


builder = StateGraph(State)
builder.add_node("plan", plan)
builder.add_node("web_search", web_search)
builder.add_node("doc_search", doc_search)
builder.add_node("write", write)
builder.add_edge(START, "plan")
builder.add_edge("plan", "web_search")
builder.add_edge("plan", "doc_search")
builder.add_edge(["web_search", "doc_search"], "write")  # 둘 다 끝나야 write
builder.add_conditional_edges("write", route)
graph = builder.compile()

EXAMPLE_INPUT = {"question": "what changed?", "rounds": 0, "notes": [], "answer": ""}

if __name__ == "__main__":
    print(graph.invoke(EXAMPLE_INPUT))
