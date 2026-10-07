"""Review group: send candidates, change sound, edit captions, approve, reject, expire."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ForceReply, FSInputFile, InputMediaVideo, Message, ReplyParameters

from contentbot import texts
from contentbot.bot.keyboards import NOOP, VideoCb, approved_keyboard, review_keyboard, status_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import Database, MusicTrack, VideoRow
from contentbot.models import AudioMode, Status, utc_now
from contentbot.pipeline.captions import build_caption, caption_overflow
from contentbot.pipeline.media import MediaError
from contentbot.scheduling import next_free_slot, when_label

log = logging.getLogger(__name__)

UPLOAD_TIMEOUT_S = 300  # seconds; a 49 MB upload can take longer than aiogram's 60 s default

SOUND_ACTIONS = {
    "orig": AudioMode.ORIGINAL,
    "mute": AudioMode.MUTE,
    "music": AudioMode.MUSIC,
    "track": AudioMode.MUSIC,
}


class Outcome(StrEnum):
    OK = "ok"
    ALREADY = "already"
    NEED_CAPTION = "need_caption"
    MUSIC_EMPTY = "music_empty"
    FAILED = "failed"


TOASTS = {
    Outcome.ALREADY: texts.ALREADY,
    Outcome.NEED_CAPTION: texts.NEED_CAPTION,
    Outcome.MUSIC_EMPTY: texts.MUSIC_EMPTY,
    Outcome.FAILED: texts.SOUND_FAILED,
}


class ReviewService:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        media: Any,
        *,
        clock: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.media = media
        self.chat_id = secrets.review_chat_id
        self.clock = clock
        self.rng = rng or random.Random()
        self.videos_dir = settings.data_dir / "videos"
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- used by the pipeline -------------------------------------------
    async def notify(self, text: str) -> None:
        await self.bot.send_message(chat_id=self.chat_id, text=text)

    async def send_candidate(self, video_id: int) -> None:
        v = self._get(video_id)
        message = await self.bot.send_video(
            chat_id=self.chat_id,
            video=FSInputFile(v.rendered_path),
            caption=self.preview_caption(v),
            reply_markup=review_keyboard(v),
            supports_streaming=True,
            request_timeout=UPLOAD_TIMEOUT_S,
        )
        self.db.update_video(
            video_id, status=Status.IN_REVIEW, review_message_id=message.message_id, review_sent_at=self.clock()
        )

    def preview_caption(self, v: VideoRow) -> str:
        return build_caption(v.caption_body or texts.AI_FAILED_BODY, self.settings.footer)

    def has_music(self) -> bool:
        return bool(self._existing_tracks())

    # ---- sound ------------------------------------------------------------
    async def set_sound(self, video_id: int, action: str) -> Outcome:
        mode = SOUND_ACTIONS[action]
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            if action != "track" and v.audio_mode == mode.value:
                return Outcome.OK
            track: MusicTrack | None = None
            if mode is AudioMode.MUSIC:
                track = self._pick_track(exclude_id=v.music_track_id)
                if track is None:
                    return Outcome.MUSIC_EMPTY
            dest = self.videos_dir / f"{v.id}_{mode.value}_{uuid.uuid4().hex[:8]}.mp4"
            try:
                await self.media.render(
                    Path(v.original_path), mode, dest, music=Path(track.file_path) if track else None
                )
            except MediaError as exc:
                log.warning("Sound change failed for video %s: %s", video_id, exc)
                return Outcome.FAILED
            previous = {"audio_mode": v.audio_mode, "music_track_id": v.music_track_id, "rendered_path": v.rendered_path}
            old_render = v.rendered_path
            self.db.update_video(
                v.id, audio_mode=mode.value, music_track_id=track.id if track else None, rendered_path=str(dest)
            )
            v = self._get(v.id)
            try:
                await self.bot.edit_message_media(
                    chat_id=self.chat_id,
                    message_id=v.review_message_id,
                    media=InputMediaVideo(media=FSInputFile(dest), caption=self.preview_caption(v), supports_streaming=True),
                    reply_markup=review_keyboard(v),
                    request_timeout=UPLOAD_TIMEOUT_S,
                )
            except TelegramAPIError as exc:
                # The reviewer still sees the old video, so keep the old version as the one that gets posted.
                log.warning("Could not show the new sound for video %s: %s", video_id, exc)
                self.db.update_video(v.id, **previous)
                dest.unlink(missing_ok=True)
                return Outcome.FAILED
            if track:
                self.db.mark_track_used(track.id, self.clock())
            if old_render and old_render not in (v.original_path, str(dest)):
                Path(old_render).unlink(missing_ok=True)
            return Outcome.OK

    def _existing_tracks(self) -> list[MusicTrack]:
        return [t for t in self.db.list_tracks() if Path(t.file_path).exists()]

    def _pick_track(self, exclude_id: int | None) -> MusicTrack | None:
        tracks = self._existing_tracks()
        if not tracks:
            return None
        used = [t for t in tracks if t.last_used_at is not None]
        last_id = max(used, key=lambda t: t.last_used_at).id if used else None
        pool = (
            [t for t in tracks if t.id not in (exclude_id, last_id)]
            or [t for t in tracks if t.id != exclude_id]
            or tracks
        )
        return self.rng.choice(pool)

    # ---- caption ------------------------------------------------------------
    async def start_caption_edit(self, video_id: int) -> Outcome:
        v = self.db.get_video(video_id)
        if v is None or v.status != Status.IN_REVIEW:
            return Outcome.ALREADY
        text = texts.EDIT_PROMPT
        if v.caption_body:
            text += f"\n\n{texts.EDIT_CURRENT}\n<code>{html.escape(v.caption_body)}</code>"
        prompt = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode="HTML",
            reply_parameters=ReplyParameters(message_id=v.review_message_id),
            reply_markup=ForceReply(),
        )
        self.db.add_caption_edit(prompt.message_id, v.id)
        return Outcome.OK

    async def apply_caption_reply(self, prompt_message_id: int, reply_message_id: int, text: str | None) -> Outcome | None:
        video_id = self.db.caption_edit_video(prompt_message_id)
        if video_id is None or not text or not text.strip():
            return None
        v = self.db.get_video(video_id)
        if v is None or v.status != Status.IN_REVIEW:
            self.db.remove_caption_edit(prompt_message_id)
            return Outcome.ALREADY
        overflow = caption_overflow(text, self.settings.footer)
        if overflow:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=texts.TOO_LONG.format(n=overflow),
                reply_parameters=ReplyParameters(message_id=reply_message_id),
            )
            return Outcome.FAILED
        self.db.update_video(v.id, caption_body=text.strip())
        v = self._get(v.id)
        await self.bot.edit_message_caption(
            chat_id=self.chat_id,
            message_id=v.review_message_id,
            caption=self.preview_caption(v),
            reply_markup=review_keyboard(v),
        )
        self.db.remove_caption_edit(prompt_message_id)
        await self._delete(prompt_message_id)
        await self._delete(reply_message_id)
        return Outcome.OK

    # ---- decisions ----------------------------------------------------------
    async def approve(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            if not v.caption_body.strip():
                return Outcome.NEED_CAPTION
            now = self.clock()
            slot = next_free_slot(now, self.settings.post_times, self.db.approved_slots(), self.settings.timezone)
            self.db.update_video(v.id, status=Status.APPROVED, slot_at=slot)
            v = self._get(v.id)
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id,
                message_id=v.review_message_id,
                reply_markup=approved_keyboard(v, when_label(slot, now, self.settings.timezone)),
            )
            return Outcome.OK

    async def undo(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.APPROVED:
                return Outcome.ALREADY
            # Restart the 48-hour expiry clock, so cancelling an old approval does not expire the video at once.
            self.db.update_video(v.id, status=Status.IN_REVIEW, slot_at=None, review_sent_at=self.clock())
            v = self._get(v.id)
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=review_keyboard(v)
            )
            return Outcome.OK

    async def reject(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            self.db.update_video(v.id, status=Status.REJECTED)
            self._delete_files(v)
            if not await self._delete(v.review_message_id):
                await self._safe(
                    self.bot.edit_message_reply_markup(
                        chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=status_keyboard(v, texts.REJECTED)
                    )
                )
            return Outcome.OK

    async def expire_old(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        cutoff = now - timedelta(hours=self.settings.review_expiry_hours)
        expired = 0
        for v in self.db.review_sent_before(cutoff):
            self.db.update_video(v.id, status=Status.EXPIRED)
            self._delete_files(v)
            await self._safe(
                self.bot.edit_message_reply_markup(
                    chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=status_keyboard(v, texts.EXPIRED)
                )
            )
            expired += 1
        return expired

    # ---- helpers ------------------------------------------------------------
    def _get(self, video_id: int) -> VideoRow:
        v = self.db.get_video(video_id)
        if v is None:
            raise LookupError(f"video {video_id} not found")
        return v

    @staticmethod
    def _delete_files(v: VideoRow) -> None:
        for path in {v.original_path, v.rendered_path}:
            if path:
                Path(path).unlink(missing_ok=True)

    async def _delete(self, message_id: int | None) -> bool:
        if message_id is None:
            return False
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            return True
        except TelegramAPIError as exc:
            log.info("Could not delete message %s: %s", message_id, exc)
            return False

    async def _safe(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except TelegramAPIError as exc:
            log.warning("Telegram call failed: %s", exc)


def build_review_router(service: ReviewService, chat_id: int) -> Router:
    router = Router(name="review")
    router.message.filter(F.chat.id == chat_id)
    router.callback_query.filter(F.message.chat.id == chat_id)

    @router.callback_query(VideoCb.filter())
    async def on_video_button(query: CallbackQuery, callback_data: VideoCb) -> None:
        action, video_id = callback_data.action, callback_data.vid
        if action == NOOP:
            await query.answer()
            return
        if action in SOUND_ACTIONS:
            if SOUND_ACTIONS[action] is AudioMode.MUSIC and not service.has_music():
                await query.answer(texts.MUSIC_EMPTY, show_alert=True)
                return
            await query.answer(texts.RENDERING)
            outcome = await service.set_sound(video_id, action)
            if outcome in (Outcome.FAILED, Outcome.MUSIC_EMPTY):
                await service.notify(TOASTS[outcome])
            return
        handlers = {
            "edit": service.start_caption_edit,
            "ok": service.approve,
            "undo": service.undo,
            "no": service.reject,
        }
        handler = handlers.get(action)
        if handler is None:
            await query.answer()
            return
        outcome = await handler(video_id)
        toast = texts.APPROVED_TOAST if action == "ok" and outcome is Outcome.OK else TOASTS.get(outcome)
        await query.answer(toast)

    @router.message(F.reply_to_message, F.text)
    async def on_reply(message: Message) -> None:
        await service.apply_caption_reply(message.reply_to_message.message_id, message.message_id, message.text)

    return router
