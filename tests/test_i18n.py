"""Two display languages; JSON/SARIF and exit codes never change with the language (AC-6)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest
from conftest import ROOT

from langgraph_preflight.cli import main
from langgraph_preflight.i18n import catalog, format_message, resolve_lang

FAN = "samples/fanout_conflict.py:graph"


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_catalog_keys_match() -> None:
    en, ko = catalog("en"), catalog("ko")
    assert set(en) == set(ko)
    # 같은 키는 같은 자리표시를 쓴다(조사 표시 {이/가} 는 제외).
    placeholder = re.compile(r"\{(\w+)\}")
    for key in en:
        assert set(placeholder.findall(en[key])) == set(placeholder.findall(ko[key])), key


def test_every_message_id_used_in_code_exists() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "langgraph_preflight").glob("*.py"))
    used = set(re.findall(r'"((?:lp00\d|err|help|table|out)\.[a-z_.]+)"', source))
    en = catalog("en")
    for message_id in used:
        if message_id.endswith("."):
            continue
        assert message_id in en, message_id
    for reason in ("no_source", "not_function", "subgraph", "returns_state", "dynamic_return"):
        assert f"lp006.unknown_writes.{reason}" in en


@pytest.mark.parametrize(
    ("cli", "env", "want"),
    [
        ("ko", {"LANGGRAPH_PREFLIGHT_LANG": "en", "LANG": "en_US.UTF-8"}, "ko"),
        ("en", {"LANGGRAPH_PREFLIGHT_LANG": "ko", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"LANGGRAPH_PREFLIGHT_LANG": "ko", "LANG": "en_US.UTF-8"}, "ko"),
        (None, {"LANGGRAPH_PREFLIGHT_LANG": "en", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"LANG": "ko_KR.UTF-8"}, "ko"),
        (None, {"LC_ALL": "ko_KR.UTF-8", "LANG": "en_US.UTF-8"}, "ko"),
        (None, {"LC_MESSAGES": "ko_KR.UTF-8"}, "ko"),
        (None, {"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"LANG": "fr_FR.UTF-8"}, "en"),
        (None, {}, "en"),
    ],
)
def test_language_precedence(cli: str | None, env: dict[str, str], want: str) -> None:
    assert resolve_lang(cli, env) == want


def test_help_in_both_languages(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--help", "--lang", "en")
    assert code == 0
    assert out.startswith("usage: langgraph-preflight")
    assert "options:" in out and "exit codes" in out

    code, out, _ = run(capsys, "--lang", "ko", "--help")
    assert code == 0
    assert out.startswith("사용법: langgraph-preflight")
    assert "옵션:" in out and "종료 코드" in out
    assert "options:" not in out and "positional arguments" not in out


def test_table_in_both_languages(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    code_en, out_en, _ = run(capsys, FAN, "--lang", "en")
    assert "SEVERITY" in out_en and "fix: " in out_en
    assert "Key 'x' has no reducer" in out_en

    code_ko, out_ko, _ = run(capsys, FAN, "--lang", "ko")
    assert "심각도" in out_ko and "고치는 법: " in out_ko
    # 자리표시 뒤 조사도 받침에 맞춘다: "b, c" 의 c(씨) -> "가"
    assert "리듀서가 없는 키 'x'에 b, c가 같은 단계 2에서 함께 씁니다" in out_ko
    assert code_en == code_ko == 1

    # 환경 변수만으로도 바뀐다(스크린샷 촬영용).
    monkeypatch.setenv("LANGGRAPH_PREFLIGHT_LANG", "ko")
    _, out_env, _ = run(capsys, FAN)
    assert "고치는 법: " in out_env

    # 사용법 오류도 번역된다.
    code, _, err = run(capsys, FAN, "--format", "xml")
    assert code == 2
    assert err.startswith("사용법:") and "오류:" in err


def test_json_and_sarif_do_not_depend_on_language(capsys: pytest.CaptureFixture[str]) -> None:
    for fmt in ("json", "sarif"):
        for target in (FAN, "samples/opaque_route.py:graph", "samples/closed_cycle.py:graph"):
            code_en, out_en, _ = run(capsys, target, "--format", fmt, "--lang", "en")
            code_ko, out_ko, _ = run(capsys, target, "--format", fmt, "--lang", "ko")
            assert out_en.encode("utf-8") == out_ko.encode("utf-8"), (fmt, target)
            assert code_en == code_ko
            json.loads(out_en)


def test_json_bytes_identical_in_subprocess() -> None:
    outputs = []
    for lang in ("en", "ko"):
        env = dict(os.environ, LANG="ko_KR.UTF-8")
        proc = subprocess.run(
            [sys.executable, "-m", "langgraph_preflight", FAN, "--format", "json", "--lang", lang],
            capture_output=True, cwd=ROOT, env=env, timeout=120,
        )
        outputs.append((proc.returncode, proc.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0][0] == 1


def test_render_scene_uses_the_same_wording(capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_scene", ROOT / "scripts" / "render_scene.py")
    assert spec is not None and spec.loader is not None
    render_scene = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_scene)

    report = tmp_path / "fan.json"
    assert main([FAN, "--format", "json", "--output", str(report)]) == 1
    page = tmp_path / "scene.ko.html"
    assert render_scene.main([str(report), "--lang", "ko", "--output", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert "b, c가 같은 단계 2에서 함께 씁니다" in text
    assert "<th>심각도</th>" in text
    assert render_scene.main([str(report), "--lang", "en", "--output", str(page)]) == 0  # 자기 파일은 바꾼다
    other = tmp_path / "index.html"
    other.write_text("<p>mine</p>", encoding="utf-8")
    assert render_scene.main([str(report), "--lang", "ko", "--output", str(other)]) == 2
    assert other.read_text(encoding="utf-8") == "<p>mine</p>"
    capsys.readouterr()


@pytest.mark.parametrize(
    ("template", "value", "want"),
    [
        ("{v}{이/가}", "검색", "검색이"),
        ("{v}{이/가}", "노드", "노드가"),
        ("{v}{을/를}", "check", "check를"),
        ("'{v}'{은/는}", "x", "'x'는"),
        ("'{v}'{은/는}", "l", "'l'은"),
        ("{v}{은/는}", "p -> q -> p", "p -> q -> p는"),
        ("{v}{으로/로}", "파일", "파일로"),
        ("{v}{으로/로}", "책", "책으로"),
        ("{v}{을/를}", "3", "3을"),
        ("{v}{을/를}", "2", "2를"),
        ("{v}{과/와}", "web_search, doc_search", "web_search, doc_search와"),
    ],
)
def test_korean_particles_follow_the_value(template: str, value: str, want: str) -> None:
    assert format_message(template, {"v": value}) == want
