"""Caption = body + footer, measured the way Telegram measures it (UTF-16 units)."""
from __future__ import annotations

CAPTION_LIMIT = 1024
BODY_LIMIT = 500


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def build_caption(body: str, footer: str) -> str:
    body = body.strip()
    footer = footer.strip()
    if not body:
        return footer
    if not footer:
        return body
    return f"{body}\n\n{footer}"


def caption_overflow(body: str, footer: str) -> int:
    return max(0, utf16_len(build_caption(body, footer)) - CAPTION_LIMIT)


def trim_body(text: str, limit: int = BODY_LIMIT) -> str:
    text = text.strip()
    if utf16_len(text) <= limit:
        return text
    cut = text
    while utf16_len(cut) > limit - 1:
        cut = cut[:-1]
    best = max(cut.rfind(mark) for mark in (".", "!", "?", "\n"))
    if best >= limit // 2:
        return cut[: best + 1].strip()
    return cut.rstrip() + "…"
