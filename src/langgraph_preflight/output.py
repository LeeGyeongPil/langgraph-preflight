"""Write a report file without ever clobbering someone else's file.

Rules: a new path is created; an existing path is replaced only when it is a
previous langgraph-preflight report (JSON ``"tool"`` field or SARIF driver
name). Anything else is left untouched and the run ends with exit code 2.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .errors import LoadError
from .report import TOOL_NAME

# 이보다 큰 기존 파일은 우리 보고서로 보지 않는다(읽기 비용 상한).
MAX_EXISTING_BYTES = 50 * 1024 * 1024


def is_own_report(path: Path) -> bool:
    """True only if ``path`` holds a JSON or SARIF report written by this tool."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_EXISTING_BYTES:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("tool") == TOOL_NAME:
        return True
    runs = data.get("runs")
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        tool = runs[0].get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        return isinstance(driver, dict) and driver.get("name") == TOOL_NAME
    return False


def write_report(path_text: str, text: str) -> Path:
    """Create or replace ``path_text`` with ``text`` (UTF-8), guarded as described above."""
    path = Path(path_text)
    if path.exists() or path.is_symlink():
        if path.is_dir() or not is_own_report(path):
            raise LoadError("err.output_refused", path=path_text)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise LoadError("err.output_dir_missing", path=path_text)
    # 같은 폴더에 임시 파일로 다 쓴 뒤 바꿔 끼운다(중간에 끊겨도 반쪽 파일이 남지 않는다).
    fd, tmp = tempfile.mkstemp(prefix=".preflight-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
