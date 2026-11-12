"""Errors that end a run with exit code 2 (they carry a message id, not a sentence)."""

from __future__ import annotations


class LoadError(Exception):
    """The target could not be loaded or is not a supported LangGraph graph.

    ``message_id`` points into the locale catalogs (``err.*``) so the CLI can
    print the cause in the user's language; ``str(err)`` is always English.
    """

    def __init__(self, message_id: str, **args: object) -> None:
        self.message_id = message_id
        self.args_map = dict(args)
        from .i18n import Messages  # 순환 import 를 피하려고 여기서 불러온다.

        super().__init__(Messages("en")(message_id, **args))
