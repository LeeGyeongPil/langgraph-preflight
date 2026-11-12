"""Command line: formats, --fail-on, load errors, guarded --output (AC-5, AC-7, AC-8)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT, SAMPLES

from langgraph_preflight.cli import main

FAN = "samples/fanout_conflict.py:graph"
SEND = "samples/send_fanout.py:graph"
CLEAN = "samples/clean_agent.py:graph"


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


# --- AC-5 -----------------------------------------------------------------------

def test_formats_and_fail_on(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, FAN, "--format", "json")
    assert code == 1
    data = json.loads(out)
    assert data["tool"] == "langgraph-preflight"
    assert data["schema_version"] == 1
    assert data["summary"] == {"error": 1, "warning": 0, "info": 0}
    finding = data["findings"][0]
    for field in ("rule", "severity", "message_id", "args", "nodes", "keys", "step", "path", "location"):
        assert field in finding
    assert (finding["rule"], finding["severity"], finding["keys"], finding["step"]) == ("LP001", "error", ["x"], 2)
    assert finding["location"]["file"] == "samples/fanout_conflict.py"
    assert isinstance(finding["location"]["line"], int)

    code, out, _ = run(capsys, FAN, "--format", "sarif")
    assert code == 1
    sarif = json.loads(out)
    assert sarif["version"] == "2.1.0"
    driver = sarif["runs"][0]["tool"]["driver"]
    assert driver["name"] == "langgraph-preflight"
    assert {r["id"] for r in driver["rules"]} == {"LP001", "LP002", "LP003", "LP004", "LP005", "LP006"}
    result = sarif["runs"][0]["results"][0]
    assert result["ruleId"] == "LP001"
    assert result["level"] == "error"
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "samples/fanout_conflict.py"
    assert loc["region"]["startLine"] == finding["location"]["line"]

    # --fail-on 별 종료 코드: (대상, 기준) -> 기대 종료 코드
    expected = {
        (FAN, "error"): 1, (FAN, "warning"): 1, (FAN, "never"): 0,
        (SEND, "error"): 0, (SEND, "warning"): 1, (SEND, "never"): 0,
        (CLEAN, "error"): 0, (CLEAN, "warning"): 0, (CLEAN, "never"): 0,
    }
    for (target, fail_on), want in expected.items():
        code, _, _ = run(capsys, target, "--fail-on", fail_on, "--format", "json")
        assert code == want, (target, fail_on)


def test_table_output(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, FAN)
    assert code == 1
    assert "LP001" in out
    assert "Key 'x' has no reducer, but b, c write it in the same step 2 (START -> a -> {b, c})." in out
    assert "fix: " in out
    code, out, _ = run(capsys, CLEAN)
    assert code == 0
    assert "No findings." in out


def test_usage_errors(capsys: pytest.CaptureFixture[str]) -> None:
    for args in ([FAN, "--format", "xml"], [FAN, "--fail-on", "sometimes"], [FAN, "--max-states", "0"],
                 [FAN, "--lang", "fr"], [], [FAN, "--output", "x.txt"], [FAN, "--bogus"]):
        code, out, err = run(capsys, *args)
        assert code == 2, args
        assert out == ""
        assert "usage:" in err and "error:" in err


# --- AC-7 -----------------------------------------------------------------------

def _assert_one_line_error(err: str) -> None:
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, err
    assert lines[0].startswith("langgraph-preflight: error: ")
    assert "Traceback" not in err


def test_load_errors(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, _, err = run(capsys, "samples/does_not_exist.py:graph")
    assert code == 2
    _assert_one_line_error(err)
    assert "file not found" in err

    code, _, err = run(capsys, "samples/fanout_conflict.py:nope")
    assert code == 2
    _assert_one_line_error(err)
    assert "'nope'" in err

    broken = tmp_path / "broken_app.py"
    broken.write_text("raise RuntimeError('OPENAI_API_KEY is not set')\n", encoding="utf-8")
    code, _, err = run(capsys, f"{broken}:graph")
    assert code == 2
    _assert_one_line_error(err)
    assert "RuntimeError: OPENAI_API_KEY is not set" in err

    not_graph = tmp_path / "not_graph.py"
    not_graph.write_text("graph = {'nodes': []}\n", encoding="utf-8")
    code, _, err = run(capsys, f"{not_graph}:graph")
    assert code == 2
    _assert_one_line_error(err)
    assert "dict" in err

    code, _, err = run(capsys, "no_such_package.app:graph")
    assert code == 2
    _assert_one_line_error(err)
    assert "module not found" in err

    # --debug 면 트레이스백을 보여 준다.
    code, _, err = run(capsys, f"{broken}:graph", "--debug")
    assert code == 2
    assert "Traceback" in err


def test_factory_and_module_targets(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    factory = tmp_path / "factory_app.py"
    factory.write_text(
        "from typing import TypedDict\n"
        "from langgraph.graph import START, StateGraph\n\n"
        "class S(TypedDict):\n    x: int\n\n"
        "def a(s):\n    return {'x': 1}\n\n"
        "def b(s):\n    return {'x': 2}\n\n"
        "def build_graph():\n"
        "    g = StateGraph(S)\n"
        "    g.add_node('a', a)\n    g.add_node('b', b)\n"
        "    g.add_edge(START, 'a')\n    g.add_edge(START, 'b')\n"
        "    return g.compile()\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, f"{factory}:build_graph", "--format", "json")
    assert code == 1
    assert json.loads(out)["findings"][0]["nodes"] == ["a", "b"]

    # package.module:name 형식(현재 폴더 기준)
    code, out, _ = run(capsys, "samples.fanout_conflict:graph", "--format", "json")
    assert code == 1
    assert json.loads(out)["findings"][0]["rule"] == "LP001"


# --- AC-8 -----------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_output_guard(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    before = {p.name: _sha(p) for p in sorted(SAMPLES.glob("*.py"))}
    files_before = sorted(str(p) for p in SAMPLES.rglob("*"))

    # 새 경로는 만든다.
    report = tmp_path / "preflight.json"
    code, out, _ = run(capsys, FAN, "--format", "json", "--output", str(report))
    assert code == 1
    assert json.loads(report.read_text(encoding="utf-8"))["tool"] == "langgraph-preflight"
    assert "wrote" in out

    # 이전 보고서(JSON)는 덮어쓴다.
    code, _, _ = run(capsys, CLEAN, "--format", "json", "--output", str(report))
    assert code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["findings"] == []

    # 이전 보고서(SARIF)도 덮어쓴다.
    sarif = tmp_path / "preflight.sarif"
    run(capsys, FAN, "--format", "sarif", "--output", str(sarif))
    code, _, _ = run(capsys, CLEAN, "--format", "sarif", "--output", str(sarif))
    assert code == 0
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"] == []

    # 그 밖의 기존 파일은 건드리지 않고 종료 코드 2.
    others = {
        "notes.json": '{"tool": "something-else", "findings": []}\n',
        "results.sarif": '{"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "other"}}}]}\n',
        "app.py": "print('user code')\n",
        "empty.json": "",
    }
    for name, content in others.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        digest = _sha(path)
        code, out, err = run(capsys, FAN, "--format", "json", "--output", str(path))
        assert code == 2, name
        assert "refusing to overwrite" in err
        assert _sha(path) == digest, name

    # 폴더를 가리키면 거절한다.
    code, _, err = run(capsys, FAN, "--format", "json", "--output", str(tmp_path))
    assert code == 2
    assert not list(tmp_path.glob(".preflight-*"))  # 임시 파일이 남지 않는다

    # 검사 전후 샘플 소스가 바뀌지 않았고 __pycache__ 도 만들지 않았다.
    for target in ("fanout_conflict", "unbalanced_conflict", "send_fanout", "closed_cycle",
                   "orphan_node", "opaque_route", "clean_agent"):
        run(capsys, f"samples/{target}.py:graph", "--format", "json")
    after = {p.name: _sha(p) for p in sorted(SAMPLES.glob("*.py"))}
    assert before == after
    assert sorted(str(p) for p in SAMPLES.rglob("*")) == files_before


def test_console_script_entry_point(tmp_path: Path) -> None:
    # 설치된 명령과 python -m 이 같은 결과를 내는지(별도 프로세스).
    env = dict(os.environ, LANGGRAPH_PREFLIGHT_LANG="en", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "langgraph_preflight", FAN],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=120,
    )
    assert proc.returncode == 1
    assert "LP001" in proc.stdout


# --- QA round 1: LP006 fix text and uncompilable builders -----------------------

def test_truncated_fix_mentions_max_states(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "samples/unbalanced_conflict.py:graph", "--max-states", "1", "--lang", "en")
    assert "--max-states" in out.split("fix:")[-1]
    assert "dict literal" not in out.split("LP006")[-1]
    code, out, _ = run(capsys, "samples/unbalanced_conflict.py:graph", "--max-states", "1", "--lang", "ko")
    assert "고치는 법: --max-states" in out


def test_empty_state_graph_is_a_load_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "empty_graph.py"
    target.write_text(
        "from typing import TypedDict\nfrom langgraph.graph import StateGraph\n"
        "class S(TypedDict):\n    a: int\ng = StateGraph(S)\n",
        encoding="utf-8",
    )
    code, out, err = run(capsys, f"{target}:g", "--lang", "en")
    assert code == 2
    assert "No findings" not in out
    assert "compile() would fail" in err
    code, _, err = run(capsys, f"{target}:g", "--lang", "ko")
    assert code == 2 and "compile()" in err


def test_check_rejects_empty_builder() -> None:
    from langgraph.graph import StateGraph
    from typing_extensions import TypedDict

    from langgraph_preflight import LoadError, check

    class S(TypedDict):
        a: int

    with pytest.raises(LoadError):
        check(StateGraph(S))
