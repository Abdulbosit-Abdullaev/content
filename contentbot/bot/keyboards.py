"""Inline keyboards and callback data for the review group."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from contentbot import texts
from contentbot.db import VideoRow

NOOP = "noop"


class VideoCb(CallbackData, prefix="v"):
    action: str
    vid: int


class MusicCb(CallbackData, prefix="m"):
    action: str
    msg: int


def _button(text: str, action: str, video_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=VideoCb(action=action, vid=video_id).pack())


def _mark(label: str, active: bool) -> str:
    return label + texts.CHECK if active else label


def source_button(v: VideoRow) -> InlineKeyboardButton:
    score = f"{v.ai_score}/10" if v.ai_score is not None else texts.NO_SCORE
    category = texts.CATEGORY_NAMES.get(v.ai_category or "other", texts.CATEGORY_NAMES["other"])
    platform = texts.PLATFORM_NAMES.get(v.platform, v.platform)
    return InlineKeyboardButton(text=texts.SOURCE.format(platform=platform, score=score, category=category), url=v.url)


def review_keyboard(v: VideoRow) -> InlineKeyboardMarkup:
    mode = v.audio_mode
    second_row = [_button(texts.BTN_EDIT, "edit", v.id)]
    if mode == "music":
        second_row.append(_button(texts.BTN_TRACK, "track", v.id))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(_mark(texts.BTN_ORIGINAL, mode == "original"), "orig", v.id),
                _button(_mark(texts.BTN_MUTE, mode == "mute"), "mute", v.id),
                _button(_mark(texts.BTN_MUSIC, mode == "music"), "music", v.id),
            ],
            second_row,
            [_button(texts.BTN_APPROVE, "ok", v.id), _button(texts.BTN_REJECT, "no", v.id)],
            [source_button(v)],
        ]
    )


def approved_keyboard(v: VideoRow, when: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(texts.WILL_POST.format(when=when), NOOP, v.id), _button(texts.BTN_CANCEL, "undo", v.id)],
            [source_button(v)],
        ]
    )


def posted_keyboard(v: VideoRow, when: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.POSTED.format(when=when), url=link)], [source_button(v)]]
    )


def status_keyboard(v: VideoRow, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(label, NOOP, v.id)], [source_button(v)]])


def music_ask_keyboard(audio_message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_YES, callback_data=MusicCb(action="add", msg=audio_message_id).pack()),
                InlineKeyboardButton(text=texts.BTN_NO, callback_data=MusicCb(action="skip", msg=audio_message_id).pack()),
            ]
        ]
    )


def post_link(channel_id: str, message_id: int) -> str:
    channel_id = str(channel_id)
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"
    return f"https://t.me/c/{channel_id.removeprefix('-100')}/{message_id}"
