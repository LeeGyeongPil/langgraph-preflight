"""LP002: a Send target writes a reducer-less key. It hides until 2+ Sends run.

    START --Send x N--> worker --> END      every worker returns {"result": ...}

Run it:   python samples/send_fanout.py 1     -> fine (one Send)
          python samples/send_fanout.py 3     -> InvalidUpdateError (three Sends)
Check it: langgraph-preflight samples/send_fanout.py:graph
"""

import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class State(TypedDict):
    items: list
    item: str
    result: str  # 리듀서 없음: worker 가 둘 이상이면 충돌


def fan_out(state: State) -> list:
    # 항목마다 worker 를 하나씩 띄운다(map-reduce 의 map).
    return [Send("worker", {"item": item}) for item in state["items"]]


def worker(state: State) -> dict:
    return {"result": f"summary of {state['item']}"}


builder = StateGraph(State)
builder.add_node("worker", worker)
builder.add_conditional_edges(START, fan_out, ["worker"])
builder.add_edge("worker", END)
graph = builder.compile()


def example_input(count: int) -> dict:
    return {"items": [f"doc{i}" for i in range(1, count + 1)], "item": "", "result": ""}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(graph.invoke(example_input(n)))
