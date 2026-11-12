"""Rules LP001-LP006: turn the model, the source facts and the exploration into findings."""

from __future__ import annotations

from .model import END, START, GraphModel, default_recursion_limit
from .report import Finding, Severity
from .simulate import explore
from .writes import DYNAMIC, SourceFacts, analyze_function


def collect_facts(model: GraphModel) -> dict[str, SourceFacts]:
    """Source facts for every node (key = node name) and router (key = 'router:<src>:<name>')."""
    facts: dict[str, SourceFacts] = {}
    for name, node in model.nodes.items():
        node_facts = analyze_function(node.func, node.runnable_kind)
        if node_facts.returns_list and node_facts.unknown is None:
            node_facts.unknown = DYNAMIC  # 노드가 리스트를 돌려주면 쓰기 키를 알 수 없다
        facts[name] = node_facts
    for branch in model.branches:
        facts[f"router:{branch.source}:{branch.name}"] = analyze_function(branch.router)
    return facts


def _send_targets(model: GraphModel, facts: dict[str, SourceFacts]) -> set[str]:
    targets: set[str] = set()
    for f in facts.values():
        targets |= f.send_targets
    return {t for t in targets if t in model.nodes}


# --- LP001 -------------------------------------------------------------------

def concurrent_writes(model: GraphModel, facts: dict[str, SourceFacts], max_states: int) -> tuple[list[Finding], bool]:
    exploration = explore(model, facts, max_states)
    single = {k for k, ch in model.channels.items() if ch.single_value}
    findings: list[Finding] = []
    reported: set[tuple[str, frozenset[str]]] = set()
    for step in exploration.steps:  # BFS 순서라 가장 짧은 경로가 먼저 나온다
        writers: dict[str, list[str]] = {}
        for node in sorted(step.nodes):
            f = facts.get(node)
            if f is None or f.unknown is not None:
                continue  # 쓰기 키를 모르는 노드는 판정에서 뺀다(LP006 이 알린다)
            for key in sorted(f.keys & single):
                writers.setdefault(key, []).append(node)
        for key, nodes in writers.items():
            if len(nodes) < 2 or (key, frozenset(nodes)) in reported:
                continue
            reported.add((key, frozenset(nodes)))
            path = " -> ".join(step.path)
            args: dict[str, object] = {"key": key, "nodes": ", ".join(nodes), "step": step.number, "path": path}
            message_id = "lp001.static"
            if step.choices:
                message_id = "lp001.branch"  # 분기 선택이 있어야 생기는 충돌
                args["choices"] = "; ".join(step.choices)
            findings.append(Finding(
                "LP001", Severity.ERROR, message_id, args, nodes=list(nodes), keys=[key],
                step=step.number, path=list(step.path), location=facts[nodes[0]].location,
            ))
    return findings, exploration.truncated


# --- LP002 -------------------------------------------------------------------

def send_fanout_writes(model: GraphModel, facts: dict[str, SourceFacts]) -> list[Finding]:
    findings: list[Finding] = []
    single = {k for k, ch in model.channels.items() if ch.single_value}
    for target in sorted(_send_targets(model, facts)):
        f = facts.get(target)
        if f is None or f.unknown is not None:
            continue
        keys = sorted(f.keys & single)
        if keys:
            findings.append(Finding(
                "LP002", Severity.WARNING, "lp002.send", {"node": target, "keys": ", ".join(keys)},
                nodes=[target], keys=keys, location=f.location,
            ))
    return findings


# --- LP003 -------------------------------------------------------------------

def _forced_successors(model: GraphModel, facts: dict[str, SourceFacts], node: str, inside: set[str]) -> tuple[bool, bool]:
    """(stays_inside, only_plain_edges): does every possible outcome of ``node`` trigger a node in ``inside``?"""
    for src, dst in model.edges:
        if src == node and dst in inside:
            return True, True  # 일반 엣지는 항상 실행된다
    for branch in model.branches:
        if branch.source != node or branch.ends is None:
            continue
        dests = set(branch.ends.values())
        if dests and END not in dests and dests <= inside:
            return True, False  # 어떤 목적지를 골라도 안에 머문다
    spec = model.nodes.get(node)
    node_facts = facts.get(node)
    if spec and spec.ends and node_facts and node_facts.always_command:
        dests = set(spec.ends)
        if END not in dests and dests <= inside:
            return True, False
    return False, False


def _strong_components(nodes: set[str], succ: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's algorithm (iterative) restricted to ``nodes``."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(succ.get(root, set()) & nodes)))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(succ.get(w, set()) & nodes))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


def _possible_successors(model: GraphModel, facts: dict[str, SourceFacts]) -> dict[str, set[str]]:
    succ: dict[str, set[str]] = {}
    for src, dst in model.edges:
        succ.setdefault(src, set()).add(dst)
    for srcs, dst in model.waiting_edges:
        for src in srcs:
            succ.setdefault(src, set()).add(dst)
    for branch in model.branches:
        if branch.ends is not None:
            succ.setdefault(branch.source, set()).update(branch.ends.values())
        router = facts.get(f"router:{branch.source}:{branch.name}")
        if router:
            succ.setdefault(branch.source, set()).update(router.send_targets)
    for name, spec in model.nodes.items():
        succ.setdefault(name, set()).update(spec.ends)
        if name in facts:
            succ[name].update(facts[name].send_targets)
    return succ


def closed_cycles(model: GraphModel, facts: dict[str, SourceFacts], reachable: set[str]) -> list[Finding]:
    # 가장 큰 "갇힌 집합": 안의 모든 노드가 어떤 선택을 해도 안의 노드를 다시 트리거한다.
    trap = set(model.nodes)
    plain_only: dict[str, bool] = {}
    changed = True
    while changed:
        changed = False
        for node in sorted(trap):
            stays, plain = _forced_successors(model, facts, node, trap)
            if not stays:
                trap.discard(node)
                changed = True
            else:
                plain_only[node] = plain
    trap &= reachable
    if not trap:
        return []
    succ = _possible_successors(model, facts)
    limit = default_recursion_limit()
    findings: list[Finding] = []
    for comp in _strong_components(trap, succ):
        is_cycle = len(comp) > 1 or comp[0] in succ.get(comp[0], set())
        if not is_cycle:
            continue  # 순환으로 들어가는 꼬리 노드는 보고하지 않는다
        cycle = _cycle_text(comp, succ)
        unconditional = all(plain_only.get(n, False) for n in comp)
        message_id = "lp003.unconditional" if unconditional else "lp003.branch"
        findings.append(Finding(
            "LP003", Severity.ERROR, message_id,
            {"cycle": cycle, "limit": str(limit) if limit is not None else "unknown"},
            nodes=comp, path=cycle.split(" -> "),
            location=facts[comp[0]].location if comp[0] in facts else None,
        ))
    return findings


def _cycle_text(comp: list[str], succ: dict[str, set[str]]) -> str:
    """One readable loop inside the component, e.g. 'check -> fix -> check'."""
    members = set(comp)
    start = comp[0]
    order = [start]
    current = start
    while True:
        nxt = sorted(succ.get(current, set()) & members)
        candidates = [n for n in nxt if n not in order] or nxt
        current = candidates[0]
        order.append(current)
        if current in order[:-1]:
            break
    return " -> ".join(order[order.index(current):])


# --- LP004 / LP005 / LP006 -----------------------------------------------------

def reachable_nodes(model: GraphModel) -> set[str]:
    """Structural reachability from START: plain edges, join edges, branch destinations, Command ends."""
    succ: dict[str, set[str]] = {}
    for src, dst in model.edges:
        succ.setdefault(src, set()).add(dst)
    for branch in model.branches:
        if branch.ends is not None:
            succ.setdefault(branch.source, set()).update(branch.ends.values())
    for name, spec in model.nodes.items():
        succ.setdefault(name, set()).update(spec.ends)
    seen = {START}
    changed = True
    while changed:
        changed = False
        frontier = list(seen)
        for node in frontier:
            for dst in succ.get(node, set()):
                if dst not in seen:
                    seen.add(dst)
                    changed = True
        for srcs, dst in model.waiting_edges:
            if dst not in seen and all(s in seen for s in srcs):
                seen.add(dst)  # 대기 엣지는 출발 노드가 모두 도달 가능해야 한다
                changed = True
    return seen - {START, END}


def opaque_routes(model: GraphModel, facts: dict[str, SourceFacts]) -> list[Finding]:
    findings: list[Finding] = []
    for branch in model.branches:
        if branch.ends is None:
            router = facts.get(f"router:{branch.source}:{branch.name}")
            source = "START" if branch.source == START else branch.source
            findings.append(Finding(
                "LP005", Severity.WARNING, "lp005.branch", {"source": source, "router": branch.name},
                nodes=[source], location=router.location if router else None,
            ))
    for name, spec in sorted(model.nodes.items()):
        f = facts.get(name)
        if f and f.command_goto and not spec.ends:
            findings.append(Finding(
                "LP005", Severity.WARNING, "lp005.command", {"node": name}, nodes=[name], location=f.location,
            ))
    return findings


def check_model(model: GraphModel, max_states: int = 20000) -> list[Finding]:
    """Run every rule on one graph model."""
    facts = collect_facts(model)
    findings: list[Finding] = []

    lp001, truncated = concurrent_writes(model, facts, max_states)
    findings += lp001
    findings += send_fanout_writes(model, facts)

    reachable = reachable_nodes(model)
    findings += closed_cycles(model, facts, reachable)

    lp005 = opaque_routes(model, facts)
    findings += lp005
    send_targets = _send_targets(model, facts)
    for name in sorted(set(model.nodes) - reachable):
        if name in send_targets:
            sev, mid = Severity.INFO, "lp004.send_target"  # Send 로만 가는 노드일 수 있다
        elif lp005:
            sev, mid = Severity.INFO, "lp004.open_route"  # 목적지를 모르는 분기가 이 노드로 갈 수 있다
        else:
            sev, mid = Severity.WARNING, "lp004.unreachable"
        findings.append(Finding("LP004", sev, mid, {"node": name}, nodes=[name],
                                location=facts[name].location if name in facts else None))

    for name in sorted(model.nodes):
        f = facts[name]
        if f.unknown is not None:
            findings.append(Finding("LP006", Severity.INFO, "lp006.unknown_writes." + f.unknown, {"node": name},
                                    nodes=[name], location=f.location))
    for branch in model.branches:
        router = facts.get(f"router:{branch.source}:{branch.name}")
        if router and router.returns_list:
            source = "START" if branch.source == START else branch.source
            findings.append(Finding("LP006", Severity.INFO, "lp006.multi_destination",
                                    {"source": source, "router": branch.name}, nodes=[source],
                                    location=router.location))
    if truncated:
        findings.append(Finding("LP006", Severity.INFO, "lp006.truncated", {"max_states": max_states}))
    return findings
