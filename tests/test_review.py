import asyncio
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.review import Outcome, ReviewService
from contentbot.db import Database
from contentbot.models import AudioMode, Status
from tests.factories import make_candidate, make_secrets, make_settings
from tests.fakes import FakeBot, FakeMedia

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent
TEN_AM = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
ONE_PM = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    media = FakeMedia()
    service = ReviewService(bot, db, settings, make_secrets(), media, clock=lambda: NOW, rng=random.Random(1))
    yield SimpleNamespace(settings=settings, db=db, bot=bot, media=media, service=service)
    db.close()


async def sent_video(env, platform_id="v1", body="#mexanizm Zo'r mexanizm!"):
    """A downloaded, captioned video that has been sent to the review group."""
    videos = env.settings.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    video_id = env.db.insert_video(make_candidate(platform_id=platform_id), Status.SELECTED, NOW)
    src = videos / f"{video_id}_src.mp4"
    src.write_bytes(b"src")
    rendered = videos / f"{video_id}_original.mp4"
    rendered.write_bytes(b"orig")
    env.db.update_video(
        video_id, original_path=str(src), rendered_path=str(rendered), caption_body=body, ai_score=8, ai_category="mechanism"
    )
    await env.service.send_candidate(video_id)
    return video_id


def add_tracks(env, count):
    env.settings.music_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        path = env.settings.music_dir / f"t{i}.mp3"
        path.write_bytes(b"mp3")
        env.db.add_track(str(path), f"t{i}", NOW)


async def test_send_candidate_posts_preview_and_marks_in_review(env):
    video_id = await sent_video(env)
    call = env.bot.named("send_video")[0]
    assert call["chat_id"] == -100111
    assert call["caption"].startswith("#mexanizm Zo'r mexanizm!")
    assert call["caption"].endswith(env.settings.footer)
    v = env.db.get_video(video_id)
    assert v.status is Status.IN_REVIEW
    assert v.review_message_id == env.bot.last_id("send_video")
    assert v.review_sent_at == NOW


async def test_empty_caption_preview_asks_a_human(env):
    await sent_video(env, body="")
    assert env.bot.named("send_video")[0]["caption"].startswith(texts.AI_FAILED_BODY)


async def test_mute_replaces_video_in_same_message(env):
    video_id = await sent_video(env)
    old_render = env.db.get_video(video_id).rendered_path
    assert await env.service.set_sound(video_id, "mute") is Outcome.OK
    assert env.media.renders[0][0] is AudioMode.MUTE
    v = env.db.get_video(video_id)
    edit = env.bot.named("edit_message_media")[0]
    assert edit["message_id"] == v.review_message_id
    assert edit["reply_markup"].inline_keyboard[0][1].text == texts.BTN_MUTE + texts.CHECK
    assert v.audio_mode == "mute"
    assert not Path(old_render).exists()
    assert Path(v.original_path).exists()


async def test_render_failure_keeps_previous_version(env):
    video_id = await sent_video(env)
    env.media.fail = True
    assert await env.service.set_sound(video_id, "mute") is Outcome.FAILED
    assert env.db.get_video(video_id).audio_mode == "original"
    assert env.bot.named("edit_message_media") == []


async def test_music_without_tracks_reports_empty(env):
    video_id = await sent_video(env)
    assert env.service.has_music() is False
    assert await env.service.set_sound(video_id, "music") is Outcome.MUSIC_EMPTY
    assert env.media.renders == []


async def test_another_track_picks_a_different_track(env):
    video_id = await sent_video(env)
    add_tracks(env, 3)
    assert await env.service.set_sound(video_id, "music") is Outcome.OK
    first = env.db.get_video(video_id).music_track_id
    assert await env.service.set_sound(video_id, "track") is Outcome.OK
    second = env.db.get_video(video_id).music_track_id
    assert first is not None and second is not None and second != first


async def test_double_approve_gives_one_slot(env):
    video_id = await sent_video(env)
    outcomes = await asyncio.gather(env.service.approve(video_id), env.service.approve(video_id))
    assert sorted(outcomes) == [Outcome.ALREADY, Outcome.OK]
    assert env.db.approved_slots() == [TEN_AM]
    markup = env.bot.named("edit_message_reply_markup")[-1]["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "✅ 10:00 da joylanadi"


async def test_approve_needs_a_caption(env):
    video_id = await sent_video(env, body="")
    assert await env.service.approve(video_id) is Outcome.NEED_CAPTION
    assert env.db.get_video(video_id).status is Status.IN_REVIEW


async def test_cancel_frees_the_slot(env):
    first = await sent_video(env, "a")
    second = await sent_video(env, "b")
    await env.service.approve(first)
    await env.service.approve(second)
    assert env.db.get_video(second).slot_at == ONE_PM
    assert await env.service.undo(first) is Outcome.OK
    undone = env.db.get_video(first)
    assert undone.status is Status.IN_REVIEW and undone.slot_at is None
    third = await sent_video(env, "c")
    await env.service.approve(third)
    assert env.db.get_video(third).slot_at == TEN_AM


async def test_caption_edit_round_trip(env):
    video_id = await sent_video(env)
    assert await env.service.start_caption_edit(video_id) is Outcome.OK
    prompt = env.bot.named("send_message")[-1]
    assert prompt["parse_mode"] == "HTML"
    assert "<code>#mexanizm Zo&#x27;r mexanizm!</code>" in prompt["text"]
    prompt_id = env.bot.last_id("send_message")
    assert await env.service.apply_caption_reply(prompt_id, 555, "Yangi matn!") is Outcome.OK
    assert env.db.get_video(video_id).caption_body == "Yangi matn!"
    assert env.bot.named("edit_message_caption")[0]["caption"].startswith("Yangi matn!")
    assert {kw["message_id"] for kw in env.bot.named("delete_message")} == {prompt_id, 555}
    assert env.db.caption_edit_video(prompt_id) is None


async def test_too_long_caption_is_refused(env):
    video_id = await sent_video(env)
    await env.service.start_caption_edit(video_id)
    prompt_id = env.bot.last_id("send_message")
    assert await env.service.apply_caption_reply(prompt_id, 556, "🛋" * 520) is Outcome.FAILED
    assert env.bot.named("send_message")[-1]["text"].startswith("⚠️ Matn")
    assert env.db.get_video(video_id).caption_body == "#mexanizm Zo'r mexanizm!"
    assert env.db.caption_edit_video(prompt_id) == video_id  # the reviewer can try again


async def test_replies_to_other_messages_are_ignored(env):
    await sent_video(env)
    before = len(env.bot.calls)
    assert await env.service.apply_caption_reply(99999, 1, "salom") is None
    assert len(env.bot.calls) == before


async def test_reject_deletes_message_and_files(env):
    video_id = await sent_video(env)
    v = env.db.get_video(video_id)
    assert await env.service.reject(video_id) is Outcome.OK
    assert env.db.get_video(video_id).status is Status.REJECTED
    assert env.bot.named("delete_message")[0]["message_id"] == v.review_message_id
    assert not Path(v.original_path).exists() and not Path(v.rendered_path).exists()


async def test_expire_old_after_48_hours(env):
    video_id = await sent_video(env)
    assert await env.service.expire_old(NOW + timedelta(hours=47)) == 0
    assert await env.service.expire_old(NOW + timedelta(hours=49)) == 1
    v = env.db.get_video(video_id)
    assert v.status is Status.EXPIRED and not Path(v.rendered_path).exists()
    markup = env.bot.named("edit_message_reply_markup")[-1]["reply_markup"]
    assert markup.inline_keyboard[0][0].text == texts.EXPIRED


async def test_cancel_after_two_days_does_not_expire_the_video(env):
    video_id = await sent_video(env)
    await env.service.approve(video_id)
    env.service.clock = lambda: NOW + timedelta(hours=50)
    assert await env.service.undo(video_id) is Outcome.OK
    assert await env.service.expire_old(NOW + timedelta(hours=51)) == 0
    assert env.db.get_video(video_id).status is Status.IN_REVIEW


async def test_failed_message_update_rolls_back_sound_change(env):
    video_id = await sent_video(env)
    before = env.db.get_video(video_id)
    env.bot.failures["edit_message_media"] = 1
    assert await env.service.set_sound(video_id, "mute") is Outcome.FAILED
    after = env.db.get_video(video_id)
    assert (after.audio_mode, after.rendered_path) == ("original", before.rendered_path)
    assert Path(before.rendered_path).exists()
    assert list((env.settings.data_dir / "videos").glob("*_mute_*.mp4")) == []


async def test_video_uploads_get_a_long_timeout(env):
    video_id = await sent_video(env)
    await env.service.set_sound(video_id, "mute")
    assert env.bot.named("send_video")[0]["request_timeout"] == 300
    assert env.bot.named("edit_message_media")[0]["request_timeout"] == 300


async def test_without_ai_the_preview_asks_for_a_caption(env):
    env.service.ai_enabled = False
    await sent_video(env, body="")
    assert env.bot.named("send_video")[0]["caption"].startswith(texts.WRITE_CAPTION_BODY)
