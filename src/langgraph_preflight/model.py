"""Turn a LangGraph builder into a plain ``GraphModel``.

This is the only module that touches LangGraph internals (``builder.edges``,
``waiting_edges``, ``branches``, ``NodeSpec.ends``, channel classes). They are
not public API, so every access is checked: if an attribute is missing we stop
with "unsupported langgraph version" instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata

from .errors import LoadError

START = "__start__"
END = "__end__"


@dataclass
class NodeInfo:
    name: str
    func: object | None  # 노드 함수(일반 함수가 아니면 None)
    runnable_kind: str  # "function" | "subgraph" | "other"
    ends: tuple[str, ...]  # Command[Literal[...]] 힌트 또는 destinations= 로 알려진 목적지


@dataclass
class BranchInfo:
    source: str
    name: str
    ends: dict[str, str] | None  # path_map 또는 Literal 힌트. None = 목적지를 모름
    router: object | None  # 라우터 함수


@dataclass
class ChannelInfo:
    key: str
    kind: str  # 채널 클래스 이름
    reducer: str | None  # 리듀서 이름(없으면 None)
    single_value: bool  # True = 한 단계에 값 하나만 받는다(동시 쓰기 시 InvalidUpdateError)


@dataclass
class GraphModel:
    nodes: dict[str, NodeInfo]
    edges: set[tuple[str, str]]
    waiting_edges: list[tuple[tuple[str, ...], str]]
    branches: list[BranchInfo]
    channels: dict[str, ChannelInfo]
    langgraph_version: str
    skipped_nodes: list[str] = field(default_factory=list)  # 자동 생성된 오류 처리 노드 등


def langgraph_version() -> str:
    try:
        return metadata.version("langgraph")
    except metadata.PackageNotFoundError:
        return "unknown"


def default_recursion_limit() -> int | None:
    """Read the installed langgraph default (10007 on 1.2.21); None if unknown."""
    try:
        from langgraph._internal._config import DEFAULT_RECURSION_LIMIT
    except ImportError:
        return None
    try:
        return int(DEFAULT_RECURSION_LIMIT)
    except (TypeError, ValueError):
        return None


def _unsupported(what: str) -> LoadError:
    return LoadError("err.unsupported", version=langgraph_version(), what=what)


def _callable_of(runnable: object) -> object | None:
    """Return the plain Python function wrapped by a RunnableCallable / RunnableLambda."""
    try:
        from langgraph._internal._runnable import RunnableCallable
    except ImportError as exc:  # pragma: no cover - only on unsupported versions
        raise _unsupported("langgraph._internal._runnable.RunnableCallable") from exc
    from langchain_core.runnables import RunnableLambda

    if type(runnable) is RunnableCallable:
        # ToolNode 같은 하위 클래스는 라이브러리 코드라 사용자 함수가 아니다(None -> "일반 함수 아님").
        return runnable.func or runnable.afunc
    if isinstance(runnable, RunnableLambda):
        return runnable.func or runnable.afunc
    return None


def _channel_info(key: str, channel: object) -> ChannelInfo:
    from langgraph.channels.binop import BinaryOperatorAggregate
    from langgraph.channels.ephemeral_value import EphemeralValue
    from langgraph.channels.last_value import LastValue, LastValueAfterFinish
    from langgraph.channels.untracked_value import UntrackedValue

    kind = type(channel).__name__
    if isinstance(channel, (LastValue, LastValueAfterFinish)):
        return ChannelInfo(key, kind, None, True)
    if isinstance(channel, (EphemeralValue, UntrackedValue)):
        # guard=True 면 한 단계에 값 하나만 허용한다.
        return ChannelInfo(key, kind, None, bool(channel.guard))
    if isinstance(channel, BinaryOperatorAggregate):
        op = channel.operator
        name = getattr(op, "__name__", None) or type(op).__name__
        return ChannelInfo(key, kind, name, False)
    # Topic·AnyValue 등은 여러 값을 받는다.
    return ChannelInfo(key, kind, kind, False)


def _as_names(ends: object) -> tuple[str, ...]:
    if not ends:
        return ()
    if isinstance(ends, dict):
        return tuple(str(k) for k in ends)  # destinations={"node": "label"}
    return tuple(str(e) for e in ends)


def build_model(builder: object) -> GraphModel:
    """Read a ``StateGraph`` (``compiled.builder``) into a ``GraphModel``."""
    from langgraph.pregel import Pregel

    # 내부 속성이 없으면 조용히 틀리지 않고 "지원하지 않는 버전"으로 멈춘다.
    try:
        raw_nodes = builder.nodes
        raw_edges = builder.edges
        raw_waiting = builder.waiting_edges
        raw_branches = builder.branches
        raw_channels = builder.channels
    except AttributeError as exc:
        raise _unsupported(f"StateGraph.{exc.name or 'nodes/edges/branches'}") from exc
    if not isinstance(raw_nodes, dict) or not isinstance(raw_branches, dict) or not isinstance(raw_channels, dict):
        raise _unsupported("StateGraph.nodes/branches/channels")

    nodes: dict[str, NodeInfo] = {}
    skipped: list[str] = []
    for name, spec in raw_nodes.items():
        if not hasattr(spec, "runnable") or not hasattr(spec, "ends"):
            raise _unsupported("StateNodeSpec.runnable/ends")
        if bool(getattr(spec, "is_error_handler", False)):
            skipped.append(name)  # add_node(error_handler=...) 가 만든 노드는 따로 다룬다
            continue
        runnable = spec.runnable
        func = _callable_of(runnable)
        if func is not None:
            kind = "function"
        elif isinstance(runnable, Pregel):
            kind = "subgraph"
        else:
            kind = "other"
        nodes[str(name)] = NodeInfo(str(name), func, kind, _as_names(spec.ends))

    edges = {(str(s), str(d)) for s, d in raw_edges}
    waiting = sorted(((tuple(str(s) for s in srcs), str(d)) for srcs, d in raw_waiting))

    branches: list[BranchInfo] = []
    for source in sorted(raw_branches):
        for bname, spec in sorted(raw_branches[source].items()):
            if not hasattr(spec, "ends") or not hasattr(spec, "path"):
                raise _unsupported("BranchSpec.ends/path")
            ends = None if spec.ends is None else {str(k): str(v) for k, v in spec.ends.items()}
            branches.append(BranchInfo(str(source), str(bname), ends, _callable_of(spec.path)))

    channels = {str(k): _channel_info(str(k), ch) for k, ch in raw_channels.items()}
    return GraphModel(nodes, edges, waiting, branches, channels, langgraph_version(), skipped)
