"""Findings, the report, and its three outputs (table, JSON, SARIF).

JSON and SARIF are built from the English catalog only, so they are identical
whatever the display language is. The table uses the chosen language.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from . import __version__
from .i18n import Messages, catalog

TOOL_NAME = "langgraph-preflight"
SCHEMA_VERSION = 1
INFO_URI = "https://github.com/LeeGyeongPil/langgraph-preflight"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _RANK[self.value]


_RANK = {"info": 0, "warning": 1, "error": 2}
_SARIF_LEVEL = {"info": "note", "warning": "warning", "error": "error"}

# 규칙 ID -> (이름, 기본 심각도)
RULES: dict[str, tuple[str, Severity]] = {
    "LP001": ("concurrent-write", Severity.ERROR),
    "LP002": ("send-fanout-write", Severity.WARNING),
    "LP003": ("closed-cycle", Severity.ERROR),
    "LP004": ("unreachable-node", Severity.WARNING),
    "LP005": ("opaque-route", Severity.WARNING),
    "LP006": ("analysis-gap", Severity.INFO),
}


@dataclass
class Finding:
    rule: str
    severity: Severity
    message_id: str
    args: dict[str, object] = field(default_factory=dict)
    nodes: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    step: int | None = None
    path: list[str] = field(default_factory=list)
    location: dict | None = None

    @property
    def name(self) -> str:
        return RULES[self.rule][0]

    def message(self, lang: str = "en") -> str:
        return Messages(lang)(self.message_id, **self.args)

    def fix(self, lang: str = "en") -> str:
        # 메시지별 처방이 있으면 그것을, 없으면 규칙 공통 처방을 쓴다.
        specific = "fix." + self.message_id
        key = specific if specific in catalog(lang) else "fix." + self.rule
        return Messages(lang)(key, **self.args)

    def sort_key(self) -> tuple:
        return (-self.severity.rank, self.rule, self.message_id, self.nodes, self.keys)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "name": self.name,
            "severity": self.severity.value,
            "message_id": self.message_id,
            "message": self.message("en"),
            "args": self.args,
            "nodes": self.nodes,
            "keys": self.keys,
            "step": self.step,
            "path": self.path,
            "location": self.location,
        }


def fail_threshold(fail_on: str) -> int | None:
    """'error' -> 2, 'warning' -> 1, 'never' -> None."""
    if fail_on == "never":
        return None
    if fail_on in ("error", "warning"):
        return _RANK[fail_on]
    raise ValueError(f"fail_on must be error, warning or never, not {fail_on!r}")


@dataclass
class Report:
    findings: list[Finding]
    target: str | None = None
    langgraph_version: str = "unknown"
    tool_version: str = __version__

    def __post_init__(self) -> None:
        self.findings = sorted(self.findings, key=Finding.sort_key)

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=lambda s: s.rank)

    def counts(self) -> dict[str, int]:
        out = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def fails(self, fail_on: str = "error") -> bool:
        threshold = fail_threshold(fail_on)
        top = self.max_severity
        return threshold is not None and top is not None and top.rank >= threshold

    # --- JSON ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "tool": TOOL_NAME,
            "schema_version": SCHEMA_VERSION,
            "tool_version": self.tool_version,
            "langgraph_version": self.langgraph_version,
            "target": self.target,
            "summary": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    # --- SARIF 2.1.0 --------------------------------------------------------
    def to_sarif(self) -> str:
        en = Messages("en")
        rules = []
        for rule_id, (name, severity) in RULES.items():
            rules.append({
                "id": rule_id,
                "name": name,
                "shortDescription": {"text": en("rule." + rule_id)},
                "helpUri": f"{INFO_URI}#rules",
                "defaultConfiguration": {"level": _SARIF_LEVEL[severity.value]},
            })
        results = []
        for f in self.findings:
            result: dict = {
                "ruleId": f.rule,
                "level": _SARIF_LEVEL[f.severity.value],
                "message": {"text": f.message("en") + " " + f.fix("en")},
                "properties": {"nodes": f.nodes, "keys": f.keys, "step": f.step, "path": f.path},
            }
            location = f.location or _target_location(self.target)
            if location:
                result["locations"] = [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": location["file"]},
                        "region": {"startLine": location["line"]},
                    }
                }]
            results.append(result)
        sarif = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {
                    "name": TOOL_NAME,
                    "version": self.tool_version,
                    "informationUri": INFO_URI,
                    "rules": rules,
                }},
                "results": results,
            }],
        }
        return json.dumps(sarif, indent=2, ensure_ascii=False) + "\n"

    # --- table --------------------------------------------------------------
    def render_table(self, lang: str = "en", fail_on: str = "error") -> str:
        t = Messages(lang)
        lines = [t("table.title", target=self.target or "-", langgraph=self.langgraph_version)]
        if not self.findings:
            lines.append("")
            lines.append(t("table.no_findings"))
        else:
            header = [t("table.col.rule"), t("table.col.severity"), t("table.col.nodes"),
                      t("table.col.keys"), t("table.col.step"), t("table.col.where")]
            rows = []
            for f in self.findings:
                where = f"{f.location['file']}:{f.location['line']}" if f.location else "-"
                rows.append([f.rule, t("sev." + f.severity.value), ", ".join(f.nodes) or "-",
                             ", ".join(f.keys) or "-", str(f.step) if f.step is not None else "-", where])
            lines.append("")
            lines.extend(_grid([header] + rows))
            lines.append("")
            for f in self.findings:
                lines.append(f"{f.rule}  {f.message(lang)}")
                lines.append(" " * 7 + t("table.fix_prefix") + f.fix(lang))
        lines.append("")
        counts = self.counts()
        verdict = t("table.verdict.fail" if self.fails(fail_on) else "table.verdict.pass", fail_on=fail_on)
        lines.append(t("table.summary", error=counts["error"], warning=counts["warning"],
                       info=counts["info"], verdict=verdict))
        return "\n".join(lines) + "\n"


def _target_location(target: str | None) -> dict | None:
    # 노드 위치가 없는 발견(탐색 잘림 등)은 대상 파일 첫 줄에 단다.
    if not target or ":" not in target:
        return None
    path = target.rsplit(":", 1)[0]
    if not path.endswith(".py"):
        return None
    return {"file": path.replace("\\", "/"), "line": 1}


def display_width(text: str) -> int:
    """Terminal cell width: East Asian wide/fullwidth characters take two cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _grid(rows: list[list[str]]) -> list[str]:
    widths = [max(display_width(row[i]) for row in rows) for i in range(len(rows[0]))]
    out = []
    for n, row in enumerate(rows):
        cells = [cell + " " * (widths[i] - display_width(cell)) for i, cell in enumerate(row)]
        out.append("  ".join(cells).rstrip())
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return out
