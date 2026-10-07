"""Post approved videos to the channel when their slot time arrives."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from contentbot import texts
from contentbot.bot.keyboards import approved_keyboard, post_link, posted_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import Database, VideoRow
from contentbot.models import Status, utc_now
from contentbot.pipeline.captions import build_caption
from contentbot.scheduling import next_free_slot, when_label

log = logging.getLogger(__name__)


class Publisher:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        *,
        clock: Callable[[], datetime] = utc_now,
        retry_delays: Sequence[float] = (60, 120, 240),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.channel_id = secrets.channel_id
        self.review_chat_id = secrets.review_chat_id
        self.clock = clock
        self.retry_delays = tuple(retry_delays)
        self.sleep = sleep

    async def publish_due(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        posted = 0
        for video in self.db.due_for_posting(now):
            if await self._post(video):
                posted += 1
        return posted

    async def replan_missed(self, now: datetime | None = None) -> int:
        """After downtime, move videos whose slot already passed to the next free slots."""
        now = now or self.clock()
        moved = 0
        for video in self.db.due_for_posting(now - timedelta(minutes=5)):
            slot = self._next_slot(now, exclude=video.slot_at)
            self.db.update_video(video.id, slot_at=slot)
            await self._show_slot(video.id, slot, now)
            moved += 1
        return moved

    async def _post(self, video: VideoRow) -> bool:
        caption = build_caption(video.caption_body, self.settings.footer)
        attempts = len(self.retry_delays) + 1
        last_error: Exception | None = None
        message = None
        for attempt in range(attempts):
            try:
                message = await self.bot.send_video(
                    chat_id=self.channel_id,
                    video=FSInputFile(video.rendered_path),
                    caption=caption,
                    supports_streaming=True,
                )
                break
            except (TelegramAPIError, OSError) as exc:
                last_error = exc
                log.warning("Posting video %s failed (attempt %s/%s): %s", video.id, attempt + 1, attempts, exc)
                if attempt < len(self.retry_delays):
                    await self.sleep(self.retry_delays[attempt])
        if message is None:
            now = self.clock()
            slot = self._next_slot(now, exclude=video.slot_at)
            self.db.update_video(video.id, slot_at=slot, error=str(last_error)[:300])
            when = when_label(slot, now, self.settings.timezone)
            await self._safe(self.bot.send_message(chat_id=self.review_chat_id, text=texts.POST_FAILED.format(when=when)))
            await self._show_slot(video.id, slot, now)
            return False
        posted_at = self.clock()
        self.db.update_video(video.id, status=Status.POSTED, posted_at=posted_at, channel_message_id=message.message_id)
        updated = self.db.get_video(video.id)
        link = post_link(self.channel_id, message.message_id)
        when = when_label(posted_at, posted_at, self.settings.timezone)
        if video.review_message_id is not None:
            await self._safe(
                self.bot.edit_message_reply_markup(
                    chat_id=self.review_chat_id,
                    message_id=video.review_message_id,
                    reply_markup=posted_keyboard(updated, when, link),
                )
            )
        for path in {video.original_path, video.rendered_path}:
            if path:
                Path(path).unlink(missing_ok=True)
        return True

    def _next_slot(self, now: datetime, exclude: datetime | None) -> datetime:
        taken = [slot for slot in self.db.approved_slots() if slot != exclude]
        return next_free_slot(now, self.settings.post_times, taken, self.settings.timezone)

    async def _show_slot(self, video_id: int, slot: datetime, now: datetime) -> None:
        video = self.db.get_video(video_id)
        if video is None or video.review_message_id is None:
            return
        when = when_label(slot, now, self.settings.timezone)
        await self._safe(
            self.bot.edit_message_reply_markup(
                chat_id=self.review_chat_id, message_id=video.review_message_id, reply_markup=approved_keyboard(video, when)
            )
        )

    async def _safe(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except TelegramAPIError as exc:
            log.warning("Telegram call failed: %s", exc)
