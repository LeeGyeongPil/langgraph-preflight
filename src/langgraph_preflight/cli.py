"""Command line: ``langgraph-preflight TARGET [options]``.

Exit codes (same in every language): 0 pass, 1 findings at or above
``--fail-on``, 2 usage error / load failure / refused output.
"""

from __future__ import annotations

import argparse
import io
import sys
import traceback
from collections.abc import Sequence

from . import __version__, check
from .errors import LoadError
from .i18n import Messages, resolve_lang

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

FORMATS = ("table", "json", "sarif")
FAIL_ON = ("error", "warning", "never")
LANGS = ("en", "ko")


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse with translated 'usage:' and errors that raise instead of exiting."""

    def __init__(self, t: Messages, **kwargs: object) -> None:
        self._t = t
        super().__init__(**kwargs)  # type: ignore[arg-type]

    def format_usage(self) -> str:
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups, self._t("help.usage_prefix"))
        return formatter.format_help()

    def format_help(self) -> str:
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups, self._t("help.usage_prefix"))
        formatter.add_text(self.description)
        for group in self._action_groups:
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(group._group_actions)
            formatter.end_section()
        formatter.add_text(self.epilog)
        return formatter.format_help()

    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _peek_lang(argv: Sequence[str]) -> str | None:
    """Find --lang before full parsing so --help is already translated."""
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


def build_parser(t: Messages) -> _Parser:
    parser = _Parser(
        t,
        prog="langgraph-preflight",
        description=t("help.description"),
        epilog=t("help.epilog"),
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 기본 그룹 제목("positional arguments" 등)은 번역되지 않으므로 직접 만든 그룹만 쓴다.
    target_group = parser.add_argument_group(t("help.group.target"))
    target_group.add_argument("target", nargs="?", metavar="TARGET", help=t("help.target"))
    opts = parser.add_argument_group(t("help.group.options"))
    opts.add_argument("--format", default="table", metavar="{table,json,sarif}", help=t("help.format"))
    opts.add_argument("--output", metavar="PATH", help=t("help.output"))
    opts.add_argument("--fail-on", default="error", metavar="{error,warning,never}", help=t("help.fail_on"))
    opts.add_argument("--lang", metavar="{en,ko}", help=t("help.lang"))
    opts.add_argument("--max-states", default="20000", metavar="N", help=t("help.max_states"))
    opts.add_argument("--debug", action="store_true", help=t("help.debug"))
    opts.add_argument("--version", action="store_true", help=t("help.version"))
    opts.add_argument("-h", "--help", action="store_true", help=t("help.help"))
    return parser


def _validate(args: argparse.Namespace, t: Messages) -> int:
    """Check option values ourselves so every error message is translated. Returns max_states."""
    if args.format not in FORMATS:
        raise _UsageError(t("err.bad_choice", option="--format", value=args.format, choices=", ".join(FORMATS)))
    if args.fail_on not in FAIL_ON:
        raise _UsageError(t("err.bad_choice", option="--fail-on", value=args.fail_on, choices=", ".join(FAIL_ON)))
    if args.lang is not None and args.lang not in LANGS:
        raise _UsageError(t("err.bad_choice", option="--lang", value=args.lang, choices=", ".join(LANGS)))
    try:
        max_states = int(args.max_states)
    except ValueError:
        max_states = 0
    if max_states < 1:
        raise _UsageError(t("err.bad_number", option="--max-states", value=args.max_states))
    if args.output and args.format == "table":
        raise _UsageError(t("err.output_needs_format"))
    if not args.target:
        raise _UsageError(t("err.missing_target"))
    return max_states


def _write_stdout(text: str, binary_utf8: bool) -> None:
    if binary_utf8 and hasattr(sys.stdout, "buffer"):
        # JSON·SARIF 는 OS 콘솔 인코딩과 상관없이 UTF-8 바이트로 내보낸다.
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = resolve_lang(_peek_lang(argv))
    t = Messages(lang)
    parser = build_parser(t)
    try:
        args = parser.parse_args(argv)
        if args.help:
            sys.stdout.write(parser.format_help())
            return EXIT_OK
        if args.version:
            sys.stdout.write(f"langgraph-preflight {__version__}\n")
            return EXIT_OK
        max_states = _validate(args, t)
    except _UsageError as exc:
        sys.stderr.write(parser.format_usage())
        sys.stderr.write(t("err.usage_line", message=str(exc)) + "\n")
        return EXIT_USAGE

    from .loader import load_target
    from .output import write_report

    try:
        builder = load_target(args.target)
        report = check(builder, max_states=max_states, target=args.target)
        if args.format == "json":
            text = report.to_json()
        elif args.format == "sarif":
            text = report.to_sarif()
        else:
            text = report.render_table(lang, args.fail_on)
        if args.output:
            write_report(args.output, text)
            sys.stdout.write(t("out.written", path=args.output, count=len(report.findings)) + "\n")
        else:
            _write_stdout(text, binary_utf8=args.format != "table")
    except LoadError as exc:
        if args.debug:
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.stderr.write(t("err.prefix") + t(exc.message_id, **exc.args_map) + "\n")
        return EXIT_USAGE
    except Exception as exc:  # 분석 중 예기치 못한 오류도 한 줄로 알리고 2 로 끝낸다
        if args.debug:
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.stderr.write(t("err.prefix") + t("err.internal", error=f"{type(exc).__name__}: {exc}") + "\n")
        return EXIT_USAGE
    return EXIT_FINDINGS if report.fails(args.fail_on) else EXIT_OK


def run() -> None:
    """Console-script entry point."""
    # 표 출력에서 콘솔이 못 그리는 글자가 있어도 죽지 않게 한다(윈도 cp1252 등).
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    sys.exit(main())
