"""Guess which state keys a node writes, by reading its source with ``ast``.

The user's code is only parsed, never executed here. When the source does not
say clearly which keys come back (a helper's result, the whole state, ``**``
spreads), the node is marked *unknown* and later excluded from conflict checks.
"""

from __future__ import annotations

import ast
import inspect
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# "알 수 없음" 사유 코드(메시지 ID 의 끝부분이 된다)
NO_SOURCE = "no_source"  # 소스를 읽을 수 없음(내장·lambda·partial 등)
NOT_FUNCTION = "not_function"  # 일반 함수가 아닌 Runnable
SUBGRAPH = "subgraph"  # 컴파일된 그래프를 노드로 씀(M2 에서 다룸)
RETURNS_STATE = "returns_state"  # 입력 상태를 그대로 돌려줌
DYNAMIC = "dynamic_return"  # 다른 함수 결과·** 펼치기 등


@dataclass
class SourceFacts:
    keys: set[str] = field(default_factory=set)  # 확실히 쓰는 키
    unknown: str | None = None  # 판정 불가 사유(None 이면 keys 가 전부)
    send_targets: set[str] = field(default_factory=set)  # Send("<이름>", ...) 리터럴
    command_goto: bool = False  # Command(goto=...) 를 쓴다
    command_goto_groups: list[tuple[str, ...]] = field(default_factory=list)  # Command(goto=["p", "q"]) 묶음
    command_goto_unmodelled: bool = False  # goto 목록에 문자열 상수가 아닌 원소가 섞임
    always_command: bool = False  # 모든 return 이 Command(goto=...)
    returns_list: bool = False  # 라우터가 목적지 리스트를 돌려준다(Send 리스트 제외)
    location: dict | None = None  # {"file": ..., "line": ...}


def display_path(path: str | os.PathLike[str]) -> str:
    """Path relative to the current directory with forward slashes (stable across OSes)."""
    p = Path(path)
    try:
        rel = os.path.relpath(p.resolve(), Path.cwd().resolve())
    except ValueError:  # 윈도에서 드라이브가 다를 때
        return p.resolve().as_posix()
    if rel.startswith(".."):
        return p.resolve().as_posix()
    return Path(rel).as_posix()


def source_location(func: object) -> dict | None:
    try:
        filename = inspect.getsourcefile(func)  # type: ignore[arg-type]
        _, line = inspect.getsourcelines(func)  # type: ignore[arg-type]
    except (OSError, TypeError):
        return None
    if not filename:
        return None
    return {"file": display_path(filename), "line": int(line)}


def _parse_function(func: object) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        source = inspect.getsource(func)  # type: ignore[arg-type]
    except (OSError, TypeError):
        return None
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _own_nodes(fn: ast.AST):
    """Walk a function body without entering nested defs, lambdas or classes."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _call_name(node: ast.AST) -> str | None:
    """'Command' for Command(...) or types.Command(...)."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _send_target(call: ast.Call) -> str | None:
    target = call.args[0] if call.args else _keyword(call, "node")
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    return None


class _KeyResolver:
    """Resolve the keys of an update expression (dict literal, dict(), Command, variable)."""

    def __init__(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> None:
        args = fn.args.posonlyargs + fn.args.args
        if is_method and args:
            args = args[1:]  # self 다음 인자가 상태다
        self.state_param = args[0].arg if args else None
        self.assigned: dict[str, list[ast.expr]] = {}
        self.item_keys: dict[str, set[str]] = {}
        self.dynamic_vars: set[str] = set()
        for node in _own_nodes(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._record_target(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._record_target(node.target, node.value)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                self.dynamic_vars.add(node.target.id)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.NamedExpr)):
                self._record_rebinding(node)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self._record_method_call(node)

    def _record_target(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.assigned.setdefault(target.id, []).append(value)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            var = target.value.id
            key = target.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.item_keys.setdefault(var, set()).add(key.value)
            else:
                self.dynamic_vars.add(var)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    self.dynamic_vars.add(elt.id)

    def _record_rebinding(self, node: ast.AST) -> None:
        # for x in ..., with ... as x, (x := ...) 로 다시 묶인 변수는 추적하지 않는다.
        names: list[ast.expr] = []
        if isinstance(node, (ast.For, ast.AsyncFor)):
            names.append(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            names.extend(item.optional_vars for item in node.items if item.optional_vars is not None)
        elif isinstance(node, ast.NamedExpr):
            names.append(node.target)
        for name in names:
            for sub in ast.walk(name):
                if isinstance(sub, ast.Name):
                    self.dynamic_vars.add(sub.id)

    def _record_method_call(self, call: ast.Call) -> None:
        owner = call.func.value  # type: ignore[union-attr]
        method = call.func.attr  # type: ignore[union-attr]
        if not isinstance(owner, ast.Name):
            return
        var = owner.id
        if method in ("update", "setdefault"):
            if method == "setdefault":
                key = call.args[0] if call.args else None
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.item_keys.setdefault(var, set()).add(key.value)
                else:
                    self.dynamic_vars.add(var)
                return
            for kw in call.keywords:
                if kw.arg is None:
                    self.dynamic_vars.add(var)
                else:
                    self.item_keys.setdefault(var, set()).add(kw.arg)
            for arg in call.args:
                if isinstance(arg, ast.Dict):
                    keys, unknown = self.keys_of(arg, set())
                    self.item_keys.setdefault(var, set()).update(keys)
                    if unknown:
                        self.dynamic_vars.add(var)
                else:
                    self.dynamic_vars.add(var)

    def keys_of(self, expr: ast.expr | None, seen: set[str]) -> tuple[set[str], str | None]:
        """Return (keys, unknown_reason) for an update expression."""
        if expr is None or (isinstance(expr, ast.Constant) and expr.value is None):
            return set(), None
        if isinstance(expr, ast.Dict):
            keys: set[str] = set()
            for key in expr.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
                else:
                    return keys, DYNAMIC  # **spread 또는 계산된 키
            return keys, None
        name = _call_name(expr)
        if name == "dict" and isinstance(expr, ast.Call) and not expr.args:
            keys = set()
            for kw in expr.keywords:
                if kw.arg is None:
                    return keys, DYNAMIC
                keys.add(kw.arg)
            return keys, None
        if name == "Command" and isinstance(expr, ast.Call):
            return self.keys_of(_keyword(expr, "update"), seen)
        if isinstance(expr, ast.IfExp):
            k1, u1 = self.keys_of(expr.body, seen)
            k2, u2 = self.keys_of(expr.orelse, seen)
            return k1 | k2, u1 or u2
        if isinstance(expr, ast.Name):
            return self._keys_of_var(expr.id, seen)
        return set(), DYNAMIC

    def _keys_of_var(self, var: str, seen: set[str]) -> tuple[set[str], str | None]:
        if var == self.state_param:
            return set(), RETURNS_STATE
        if var in seen or var in self.dynamic_vars or var not in self.assigned:
            return set(), DYNAMIC
        keys = set(self.item_keys.get(var, set()))
        for value in self.assigned[var]:
            more, unknown = self.keys_of(value, seen | {var})
            if unknown:
                return keys | more, unknown
            keys |= more
        return keys, None


def _is_send(node: ast.AST) -> bool:
    return _call_name(node) == "Send"


def analyze_function(func: object | None, runnable_kind: str = "function") -> SourceFacts:
    """Facts about one node or router function, from its source code only."""
    if func is None:
        return SourceFacts(unknown=SUBGRAPH if runnable_kind == "subgraph" else NOT_FUNCTION)
    facts = SourceFacts(location=source_location(func))
    fn = _parse_function(func)
    if fn is None:
        facts.unknown = NO_SOURCE
        return facts

    # Send("<이름>", ...) 은 중첩 함수·컴프리헨션 안에 있어도 찾는다.
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _is_send(node):
            target = _send_target(node)
            if target:
                facts.send_targets.add(target)
        if isinstance(node, ast.Call) and _call_name(node) == "Command" and _keyword(node, "goto") is not None:
            facts.command_goto = True
            _record_goto_list(facts, _keyword(node, "goto"))

    resolver = _KeyResolver(fn, inspect.ismethod(func))
    returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return)]
    keys: set[str] = set()
    unknown: str | None = None
    for ret in returns:
        value = ret.value
        if isinstance(value, (ast.List, ast.Tuple, ast.ListComp)):
            # 라우터의 목적지 리스트(또는 Send 리스트)는 상태 갱신이 아니다.
            elts = [value.elt] if isinstance(value, ast.ListComp) else list(value.elts)
            if not all(_is_send(e) for e in elts):
                facts.returns_list = True
            continue
        found, why = resolver.keys_of(value, set())
        keys |= found
        unknown = unknown or why
    facts.keys = keys
    facts.unknown = unknown
    # 모든 return 이 Command(goto=...) 이면 "이동 없이 끝나는" 경우가 없다(LP003 판정에 쓰인다).
    # 함수 끝이 return/raise 가 아니면 암묵적 None 반환이 있을 수 있으므로 보수적으로 False.
    ends_explicitly = bool(fn.body) and isinstance(fn.body[-1], (ast.Return, ast.Raise))
    facts.always_command = ends_explicitly and bool(returns) and all(_is_command_goto(r.value) for r in returns)
    return facts


def _record_goto_list(facts: SourceFacts, goto: ast.expr | None) -> None:
    """Remember a Command goto list: literal names run together in one step."""
    if isinstance(goto, ast.ListComp):
        if not _is_send(goto.elt):
            facts.command_goto_unmodelled = True
        return
    if not isinstance(goto, (ast.List, ast.Tuple)):
        return
    elts = list(goto.elts)
    if any(_is_send(e) for e in elts):
        return  # Send 가 섞이면 기존 send_targets/LP002 경로가 맡는다
    names = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if len(names) != len(elts):
        facts.command_goto_unmodelled = True
        return
    group = tuple(sorted(set(names)))
    if len(group) >= 2 and group not in facts.command_goto_groups:
        facts.command_goto_groups.append(group)


def _is_command_goto(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Call) and _call_name(value) == "Command" and _keyword(value, "goto") is not None
