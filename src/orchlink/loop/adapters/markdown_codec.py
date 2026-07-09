"""Markdown codec for the authoritative loop state block."""

from __future__ import annotations

import json
from dataclasses import dataclass

from orchlink.loop.domain.errors import IllegalTransition, StateCorrupt
from orchlink.loop.domain.item import LoopState

OPENING_FENCE = "```yaml orchloop.v1"
CLOSING_FENCE = "```"


@dataclass(frozen=True, slots=True)
class MarkdownStateDocument:
    prefix: str
    state: LoopState
    suffix: str


def default_document() -> MarkdownStateDocument:
    return MarkdownStateDocument(
        prefix="# Orchlink loop state\n\n",
        state=LoopState(),
        suffix="\n",
    )


def decode_markdown(text: str) -> MarkdownStateDocument:
    starts: list[int] = []
    search_from = 0
    token = OPENING_FENCE
    while True:
        index = text.find(token, search_from)
        if index == -1:
            break
        line_start = text.rfind("\n", 0, index) + 1
        if line_start == index:
            starts.append(index)
        search_from = index + len(token)

    if not starts:
        raise StateCorrupt("missing ```yaml orchloop.v1 state block")
    if len(starts) > 1:
        raise StateCorrupt("multiple orchloop.v1 state blocks")

    start = starts[0]
    open_line_end = text.find("\n", start)
    if open_line_end == -1:
        raise StateCorrupt("unterminated orchloop.v1 opening fence")
    content_start = open_line_end + 1
    close = text.find("\n```", content_start)
    if close == -1:
        if text[content_start:].strip() == CLOSING_FENCE:
            close = text.find(CLOSING_FENCE, content_start)
        else:
            raise StateCorrupt("unterminated orchloop.v1 state block")
    content = text[content_start:close]
    close_line_end = text.find("\n", close + 1)
    if close_line_end == -1:
        suffix_start = len(text)
    else:
        suffix_start = close_line_end + 1

    try:
        raw = json.loads(content) if content.strip() else {}
        if not isinstance(raw, dict):
            raise StateCorrupt("orchloop.v1 state block must decode to a mapping")
        state = LoopState.from_dict(raw)
    except StateCorrupt:
        raise
    except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError, IllegalTransition) as exc:
        raise StateCorrupt(f"malformed orchloop.v1 machine state: {exc}") from exc
    return MarkdownStateDocument(
        prefix=text[:start],
        state=state,
        suffix=text[suffix_start:],
    )


def encode_markdown(document: MarkdownStateDocument) -> str:
    body = json.dumps(document.state.to_dict(), indent=2, sort_keys=True)
    return f"{document.prefix}{OPENING_FENCE}\n{body}\n{CLOSING_FENCE}\n{document.suffix}"
