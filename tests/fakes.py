"""Fake collaborators for tests: no network, no Telegram, no ffmpeg."""
from __future__ import annotations

import json
from types import SimpleNamespace


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClaudeClient:
    """Stands in for anthropic.AsyncAnthropic (only .beta.messages.create is used)."""

    def __init__(self, responses):
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=self.messages)


def claude_reply(payload, stop_reason: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def connection_error():
    import anthropic

    try:
        import httpx2 as http_lib  # anthropic 1.x is built on httpx2
    except ImportError:
        import httpx as http_lib
    return anthropic.APIConnectionError(request=http_lib.Request("POST", "https://api.anthropic.com/v1/messages"))


class FakeClaudeJSON:
    """Stands in for ClaudeJSON: returns queued dicts (or None) and records calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def ask_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)
