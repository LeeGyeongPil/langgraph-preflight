"""Display language (en/ko) and message lookup.

Only the human-facing screen (help, table, error lines) is translated.
JSON and SARIF always use the English catalog so CI output never depends
on the user's locale.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources

SUPPORTED = ("en", "ko")
ENV_VAR = "LANGGRAPH_PREFLIGHT_LANG"
# 로캘 변수는 이 순서로 본다(POSIX 우선순위와 같다).
LOCALE_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")


def _normalize(value: str | None) -> str | None:
    """'ko', 'ko_KR.UTF-8', 'KO' -> 'ko'; 'en_US' -> 'en'; anything else -> None."""
    if not value:
        return None
    low = value.strip().lower()
    if low.startswith("ko"):
        return "ko"
    if low.startswith("en"):
        return "en"
    return None


def resolve_lang(cli_value: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """--lang > LANGGRAPH_PREFLIGHT_LANG > LC_ALL / LC_MESSAGES / LANG > English."""
    env = os.environ if environ is None else environ
    picked = _normalize(cli_value)
    if picked:
        return picked
    picked = _normalize(env.get(ENV_VAR))
    if picked:
        return picked
    for name in LOCALE_VARS:
        value = env.get(name)
        if value:
            # 처음으로 값이 있는 로캘 변수가 결정한다. 한국어가 아니면 영어.
            return "ko" if _normalize(value) == "ko" else "en"
    return "en"


@lru_cache(maxsize=4)
def catalog(lang: str) -> dict[str, str]:
    """Load locales/<lang>.json (UTF-8)."""
    if lang not in SUPPORTED:
        lang = "en"
    text = resources.files("langgraph_preflight").joinpath("locales", f"{lang}.json").read_text(encoding="utf-8")
    return json.loads(text)


# --- Korean particles (조사) -------------------------------------------------
# 템플릿의 {이/가} 같은 표시는 바로 앞 글자의 받침에 맞춰 한쪽을 고른다.
# 자리표시({nodes}) 뒤에 붙어도 치환된 값의 마지막 글자로 판단한다.

_TOKEN = re.compile(r"\{(\w+)\}|\{([가-힣]+)/([가-힣]+)\}")
_SKIP_TRAILING = "'\"`)]}>»’”」』 .,"
# 숫자를 한국어로 읽었을 때 받침: 0 영, 1 일(ㄹ), 3 삼, 6 육, 7 칠(ㄹ), 8 팔(ㄹ)
_DIGIT_FINAL = {"0": "other", "1": "rieul", "2": None, "3": "other", "4": None,
                "5": None, "6": "other", "7": "rieul", "8": "rieul", "9": None}
# 영문 한 글자는 알파벳 이름으로 읽는다: l 엘, r 알, m 엠, n 엔
_LETTER_NAME_FINAL = {"l": "rieul", "r": "rieul", "m": "other", "n": "other"}


def _final_consonant(text: str) -> str | None:
    """Return None (no final consonant), 'rieul' (ㄹ) or 'other' for the last readable char."""
    stripped = text.rstrip(_SKIP_TRAILING)
    if not stripped:
        return None
    ch = stripped[-1]
    code = ord(ch) - 0xAC00
    if 0 <= code < 11172:
        jong = code % 28
        if jong == 0:
            return None
        return "rieul" if jong == 8 else "other"
    if ch.isdigit():
        return _DIGIT_FINAL.get(ch)
    if ch.isalpha() and ch.isascii():
        low = ch.lower()
        # 앞 글자도 영문이면 단어로 보고, 아니면(예: 'x', 'b') 알파벳 이름으로 읽는다.
        word_end = len(stripped) >= 2 and stripped[-2].isascii() and stripped[-2].isalpha()
        if not word_end:
            return _LETTER_NAME_FINAL.get(low)
        if low == "l":
            return "rieul"
        if low in ("m", "n") or stripped.lower().endswith("ng"):
            return "other"
        return None
    return None


def _pick_particle(previous: str, with_final: str, without_final: str) -> str:
    final = _final_consonant(previous)
    if with_final == "으로":
        return "으로" if final == "other" else "로"
    return with_final if final else without_final


def format_message(template: str, args: Mapping[str, object]) -> str:
    """Fill {name} placeholders and resolve Korean particle markers like {이/가}."""
    out: list[str] = []
    pos = 0
    for match in _TOKEN.finditer(template):
        out.append(template[pos:match.start()])
        if match.group(1) is not None:
            out.append(str(args.get(match.group(1), "{" + match.group(1) + "}")))
        else:
            out.append(_pick_particle("".join(out), match.group(2), match.group(3)))
        pos = match.end()
    out.append(template[pos:])
    return "".join(out)


class Messages:
    """Message lookup bound to one language."""

    def __init__(self, lang: str) -> None:
        self.lang = lang if lang in SUPPORTED else "en"
        self._table = catalog(self.lang)

    def __call__(self, message_id: str, /, **args: object) -> str:
        template = self._table.get(message_id)
        if template is None:
            # 키가 빠졌으면 영어로 대신한다(테스트가 키 집합 일치를 보장한다).
            template = catalog("en").get(message_id, message_id)
        return format_message(template, args)
