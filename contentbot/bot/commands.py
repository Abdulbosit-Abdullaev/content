"""Review-group commands (/queue /search /status /keywords /addkw /delkw) and music uploads."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from contentbot import texts
from contentbot.bot.keyboards import MusicCb, music_ask_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import MUSIC_SUFFIXES, Database
from contentbot.models import Status, utc_now
from contentbot.scheduling import when_label

log = logging.getLogger(__name__)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
MIME_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def detect_language(text: str) -> str:
    """Rough label for a new keyword; only used for display and rotation."""
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru"
    if re.search(r"[ğışĞİŞ]", text):
        return "tr"
    if re.search(r"[oOgG][ʻ'’`]", text):
        return "uz"
    return "en"


def audio_suffix(file_name: str | None, mime_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix.lower()
        if suffix in MUSIC_SUFFIXES:
            return suffix
    return MIME_SUFFIXES.get(mime_type or "", ".mp3")


class CommandService:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        pipeline: Any,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.pipeline = pipeline
        self.clock = clock
        self._pending_audio: dict[int, tuple[str, str, str]] = {}
        self._tasks: set[asyncio.Task] = set()

    def queue_text(self, now: datetime | None = None) -> str:
        now = now or self.clock()
        videos = self.db.approved_in_order()
        if not videos:
            return texts.QUEUE_EMPTY
        lines = [texts.QUEUE_TITLE]
        for number, v in enumerate(videos, 1):
            when = when_label(v.slot_at, now, self.settings.timezone)
            category = texts.CATEGORY_NAMES.get(v.ai_category or "other", texts.CATEGORY_NAMES["other"])
            preview = " ".join(v.caption_body.split())[:40]
            lines.append(f"{number}. {when} · {category} · {preview}")
        return "\n".join(lines)

    def status_text(self, now: datetime | None = None) -> str:
        now = now or self.clock()
        tz = self.settings.timezone
        run = self.db.last_run()
        if run is None:
            last_run, sources = texts.NEVER, "-"
        else:
            last_run = run.started_at.astimezone(tz).strftime("%d.%m %H:%M")
            found = run.summary.get("found", {})
            errors = run.summary.get("errors", {})
            parts = [f"{texts.PLATFORM_NAMES.get(n, n)}: {count}" for n, count in found.items()]
            parts += [f"{texts.PLATFORM_NAMES.get(n, n)}: {texts.ERROR_WORD}" for n in errors]
            sources = " · ".join(parts) or "-"
        month_start = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return texts.STATUS.format(
            last_run=last_run,
            sources=sources,
            in_review=self.db.count_with_status(Status.IN_REVIEW),
            queued=self.db.count_with_status(Status.APPROVED),
            tracks=len(self.db.list_tracks()),
            spent=self.db.apify_spend_since(month_start),
            budget=self.settings.apify_monthly_budget_usd,
        )

    def keywords_text(self) -> str:
        by_language: dict[str, list[str]] = {}
        for keyword in self.db.list_keywords():
            by_language.setdefault(keyword.language, []).append(keyword.text)
        lines = [texts.KW_TITLE] + [f"{lang}: {', '.join(words)}" for lang, words in by_language.items()]
        return "\n".join(lines) if by_language else texts.KW_TITLE + "\n-"

    def add_keyword(self, raw: str | None) -> str:
        text = (raw or "").strip()
        if not text:
            return texts.KW_USAGE_ADD
        if self.db.add_keyword(text, detect_language(text)):
            return texts.KW_ADDED.format(text=text)
        return texts.KW_EXISTS

    def remove_keyword(self, raw: str | None) -> str:
        text = (raw or "").strip()
        if not text:
            return texts.KW_USAGE_DEL
        if self.db.deactivate_keyword(text):
            return texts.KW_REMOVED.format(text=text)
        return texts.KW_NOT_FOUND

    def start_search(self) -> str:
        if self.pipeline.lock.locked():
            return texts.SEARCH_RUNNING
        task = asyncio.create_task(self.pipeline.run("manual"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return texts.SEARCH_STARTED

    def remember_audio(self, message_id: int, file_id: str, title: str, suffix: str) -> None:
        self._pending_audio[message_id] = (file_id, title, suffix)

    def forget_audio(self, message_id: int) -> None:
        self._pending_audio.pop(message_id, None)

    async def save_audio(self, message_id: int) -> str | None:
        pending = self._pending_audio.pop(message_id, None)
        if pending is None:
            return None
        file_id, title, suffix = pending
        self.settings.music_dir.mkdir(parents=True, exist_ok=True)
        dest = self.settings.music_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
        await self.bot.download(file_id, destination=dest)
        self.db.add_track(str(dest.resolve()), title, self.clock())
        return texts.MUSIC_ADDED.format(title=title)


def build_commands_router(service: CommandService, chat_id: int) -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.id == chat_id)
    router.callback_query.filter(F.message.chat.id == chat_id)

    @router.message(Command("queue"))
    async def on_queue(message: Message) -> None:
        await message.answer(service.queue_text())

    @router.message(Command("search"))
    async def on_search(message: Message) -> None:
        await message.answer(service.start_search())

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(service.status_text())

    @router.message(Command("keywords"))
    async def on_keywords(message: Message) -> None:
        await message.answer(service.keywords_text())

    @router.message(Command("addkw"))
    async def on_add_keyword(message: Message, command: CommandObject) -> None:
        await message.answer(service.add_keyword(command.args))

    @router.message(Command("delkw"))
    async def on_remove_keyword(message: Message, command: CommandObject) -> None:
        await message.answer(service.remove_keyword(command.args))

    @router.message(F.audio)
    async def on_audio(message: Message) -> None:
        audio = message.audio
        if audio.file_size and audio.file_size > TELEGRAM_DOWNLOAD_LIMIT:
            await message.reply(texts.MUSIC_TOO_BIG)
            return
        title = audio.title or audio.file_name or "track"
        service.remember_audio(message.message_id, audio.file_id, title, audio_suffix(audio.file_name, audio.mime_type))
        await message.reply(texts.MUSIC_ASK, reply_markup=music_ask_keyboard(message.message_id))

    @router.callback_query(MusicCb.filter())
    async def on_music_choice(query: CallbackQuery, callback_data: MusicCb) -> None:
        if callback_data.action == "add":
            result = await service.save_audio(callback_data.msg)
            await query.message.edit_text(result or texts.ALREADY)
        else:
            service.forget_audio(callback_data.msg)
            await query.message.delete()
        await query.answer()

    return router
