"""Render a langgraph-preflight JSON report as an HTML table in English or Korean.

Used to take README screenshots: terminals draw Hangul two cells wide and the
table lines drift, so the Korean scene is captured from a browser instead.

    uv run langgraph-preflight samples/fanout_conflict.py:graph --format json --output runs/fan.json
    uv run python scripts/render_scene.py runs/fan.json --lang ko --output runs/scene-1.ko.html

The HTML is rebuilt from ``message_id`` + ``args`` with the same catalogs the
CLI uses, so the wording is identical to ``--lang ko``. An existing output file
is replaced only if this script wrote it (marker comment on the first line).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from langgraph_preflight.i18n import Messages

MARKER = "<!-- langgraph-preflight scene -->"


def render(report: dict, lang: str) -> str:
    t = Messages(lang)
    rows = []
    for f in report["findings"]:
        loc = f.get("location")
        where = f"{loc['file']}:{loc['line']}" if loc else "-"
        cells = [
            f["rule"],
            t("sev." + f["severity"]),
            ", ".join(f["nodes"]) or "-",
            ", ".join(f["keys"]) or "-",
            str(f["step"]) if f["step"] is not None else "-",
            where,
        ]
        cells_html = [html.escape(c) for c in cells]
        # 마지막 칸: 메시지 + 고치는 법(작은 글씨)
        message = t(f["message_id"], **f["args"])
        fix = t("table.fix_prefix") + t("fix." + f["rule"], **f["args"])
        cells_html.append(html.escape(message) + "<br><small>" + html.escape(fix) + "</small>")
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells_html) + "</tr>")
    header = [t("table.col.rule"), t("table.col.severity"), t("table.col.nodes"), t("table.col.keys"),
              t("table.col.step"), t("table.col.where"), t("table.col.message")]
    counts = report["summary"]
    title = t("table.title", target=report.get("target") or "-", langgraph=report.get("langgraph_version", "?"))
    summary = t("table.summary", error=counts["error"], warning=counts["warning"], info=counts["info"],
                verdict=t("table.verdict.fail" if counts["error"] else "table.verdict.pass", fail_on="error"))
    body = (
        f"<p class='title'>{html.escape(title)}</p>"
        + ("<table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in header) + "</tr></thead><tbody>"
           + "".join(rows) + "</tbody></table>" if rows else f"<p>{html.escape(t('table.no_findings'))}</p>")
        + f"<p class='summary'>{html.escape(summary)}</p>"
    )
    return (
        f"{MARKER}\n<!doctype html>\n<html lang='{lang}'><head><meta charset='utf-8'>"
        "<title>langgraph-preflight</title><style>"
        "body{background:#0d1117;color:#e6edf3;font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:16px}"
        "table{border-collapse:collapse}th,td{border:1px solid #30363d;padding:4px 8px;text-align:left;vertical-align:top}"
        "th{color:#8b949e}small{color:#8b949e}.title,.summary{color:#7ee787}"
        "</style></head><body>" + body + "</body></html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a JSON report as an HTML table (en/ko).")
    parser.add_argument("report", help="JSON report from --format json")
    parser.add_argument("--lang", choices=["en", "ko"], default="en")
    parser.add_argument("--output", required=True, help="HTML file to write")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if data.get("tool") != "langgraph-preflight":
        print(f"{args.report} is not a langgraph-preflight JSON report", file=sys.stderr)
        return 2
    out = Path(args.output)
    if out.exists():
        first = out.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if first != [MARKER]:
            print(f"refusing to overwrite {out}: not written by render_scene.py", file=sys.stderr)
            return 2
    out.write_text(render(data, args.lang), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
