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


from pathlib import Path  # noqa: E402

from contentbot.pipeline.media import MediaError  # noqa: E402


def telegram_error(method: str = "send_video", kind: str = "server"):
    """kind="server": Telegram answered with an error (safe to retry); kind="network": the reply never arrived."""
    from aiogram.exceptions import TelegramNetworkError, TelegramServerError
    from aiogram.methods import SendMessage

    error_class = TelegramNetworkError if kind == "network" else TelegramServerError
    return error_class(method=SendMessage(chat_id=1, text="x"), message=f"{method} failed")


class FakeBot:
    """Records Bot API calls and returns objects with a message_id, like aiogram does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int | None]] = []
        self.failures: dict[str, int] = {}
        self.failure_kind = "server"
        self._next_id = 1000

    async def _call(self, method: str, kwargs: dict):
        if self.failures.get(method, 0) > 0:
            self.failures[method] -= 1
            self.calls.append((method, kwargs, None))
            raise telegram_error(method, self.failure_kind)
        self._next_id += 1
        self.calls.append((method, kwargs, self._next_id))
        return SimpleNamespace(message_id=self._next_id)

    async def send_video(self, **kwargs):
        return await self._call("send_video", kwargs)

    async def send_message(self, **kwargs):
        return await self._call("send_message", kwargs)

    async def edit_message_media(self, **kwargs):
        return await self._call("edit_message_media", kwargs)

    async def edit_message_caption(self, **kwargs):
        return await self._call("edit_message_caption", kwargs)

    async def edit_message_reply_markup(self, **kwargs):
        return await self._call("edit_message_reply_markup", kwargs)

    async def delete_message(self, **kwargs):
        await self._call("delete_message", kwargs)
        return True

    async def download(self, file, destination=None, **kwargs):
        self.calls.append(("download", {"file": file, "destination": destination}, None))
        Path(destination).write_bytes(b"audio")

    def named(self, method: str) -> list[dict]:
        return [kwargs for name, kwargs, _ in self.calls if name == method]

    def last_id(self, method: str) -> int:
        return [message_id for name, _, message_id in self.calls if name == method][-1]


class FakeMedia:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.renders: list[tuple] = []

    async def render(self, src, mode, dest, music=None):
        self.renders.append((mode, music))
        if self.fail:
            raise MediaError("ffmpeg exploded")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"rendered")
        return dest


import asyncio  # noqa: E402


class FakePipeline:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.runs: list[str] = []
        self.sources: list = []

    async def run(self, trigger: str = "schedule") -> dict:
        self.runs.append(trigger)
        return {}
