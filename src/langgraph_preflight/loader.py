"""Resolve ``path/to/file.py:attr`` or ``pkg.module:attr`` to a LangGraph builder.

Importing the user's module is the one place where user code runs (the same
as ``python -c "import app"``): module-level code executes, nodes do not.
Every failure becomes a ``LoadError`` with a one-line cause.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

from .errors import LoadError


def split_target(target: str) -> tuple[str, str]:
    """'app.py:graph' -> ('app.py', 'graph'). The last ':' separates (Windows drive letters keep theirs)."""
    if ":" not in target:
        raise LoadError("err.no_attr", target=target)
    where, attr = target.rsplit(":", 1)
    if not where or not attr.isidentifier():
        raise LoadError("err.no_attr", target=target)
    return where, attr


def _looks_like_file(where: str) -> bool:
    return where.endswith(".py") or "/" in where or "\\" in where


def _import_file(path_text: str) -> object:
    path = Path(path_text)
    if not path.is_file():
        raise LoadError("err.file_not_found", path=path_text)
    resolved = path.resolve()
    # 같은 이름 파일이 여러 곳에 있어도 섞이지 않게 경로 해시로 모듈 이름을 만든다.
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:10]
    module_name = f"_langgraph_preflight_target_{resolved.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise LoadError("err.file_not_found", path=path_text)
    module = importlib.util.module_from_spec(spec)
    # 대상 파일 옆의 모듈(from nodes import ...)을 찾을 수 있게 그 폴더를 경로 앞에 둔다.
    folder = str(resolved.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    sys.modules[module_name] = module
    # 사용자 폴더에 __pycache__ 를 만들지 않는다(검사는 읽기만 한다).
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as exc:
        sys.modules.pop(module_name, None)
        raise LoadError("err.import_failed", target=path_text, error=_one_line(exc)) from exc
    finally:
        sys.dont_write_bytecode = previous
    return module


def _import_module(name: str) -> object:
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (name == exc.name or name.startswith(exc.name + ".")):
            raise LoadError("err.module_not_found", module=name) from exc
        raise LoadError("err.import_failed", target=name, error=_one_line(exc)) from exc
    except (Exception, SystemExit) as exc:
        raise LoadError("err.import_failed", target=name, error=_one_line(exc)) from exc
    finally:
        sys.dont_write_bytecode = previous


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    first = text[0] if text else ""
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _is_builder(obj: object) -> bool:
    from langgraph.graph import StateGraph

    return isinstance(obj, StateGraph)


def _is_compiled(obj: object) -> bool:
    from langgraph.graph.state import CompiledStateGraph

    return isinstance(obj, CompiledStateGraph)


def builder_of(obj: object) -> object | None:
    """StateGraph for a CompiledStateGraph or a StateGraph; None for anything else."""
    if _is_compiled(obj):
        return obj.builder  # type: ignore[attr-defined]
    if _is_builder(obj):
        return obj
    return None


def ensure_compilable(builder: object) -> None:
    """Reject a StateGraph that the library would refuse to build (validate() runs no nodes)."""
    if _is_compiled(builder):
        return
    try:
        builder.validate()  # type: ignore[attr-defined]
    except Exception as exc:
        raise LoadError("err.not_compilable", error=_one_line(exc)) from exc


def _takes_no_arguments(func: object) -> bool:
    try:
        sig = inspect.signature(func)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.default is inspect.Parameter.empty and param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return False
    return True


def load_target(target: str) -> object:
    """Return the StateGraph behind ``target`` (compiled graph, builder, or zero-argument factory)."""
    where, attr = split_target(target)
    module = _import_file(where) if _looks_like_file(where) else _import_module(where)

    # 대상은 모듈 최상위 이름 하나다(app.py:graph). 모듈 이름공간에서 그 이름만 꺼낸다.
    namespace = module.__dict__
    if attr not in namespace:
        raise LoadError("err.attr_not_found", attr=attr, module=where)
    obj: object = namespace[attr]

    builder = builder_of(obj)
    if builder is not None:
        ensure_compilable(builder)
        return builder
    if callable(obj) and not isinstance(obj, type) and _takes_no_arguments(obj):
        try:
            produced = obj()  # 인자 없는 팩토리 함수: build_graph() 가 그래프를 돌려준다
        except Exception as exc:
            raise LoadError("err.factory_failed", attr=attr, error=_one_line(exc)) from exc
        builder = builder_of(produced)
        if builder is not None:
            ensure_compilable(builder)
            return builder
        raise LoadError("err.not_graph", attr=attr, kind=type(produced).__name__)
    raise LoadError("err.not_graph", attr=attr, kind=type(obj).__name__)
