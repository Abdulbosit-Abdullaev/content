from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.publisher import Publisher
from contentbot.db import Database
from contentbot.models import Status
from tests.factories import make_candidate, make_secrets, make_settings
from tests.fakes import FakeBot

NOW = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 10:00 in Tashkent


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    sleeps = []
    during_pause = []  # callables run while the publisher waits between attempts

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        for action in during_pause:
            action()

    publisher = Publisher(bot, db, settings, make_secrets(), clock=lambda: NOW, retry_delays=(1, 2, 4), sleep=fake_sleep)
    yield SimpleNamespace(settings=settings, db=db, bot=bot, publisher=publisher, sleeps=sleeps, during_pause=during_pause)
    db.close()


def approved(env, platform_id, slot):
    videos = env.settings.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    video_id = env.db.insert_video(make_candidate(platform_id=platform_id), Status.APPROVED, NOW)
    src = videos / f"{video_id}_src.mp4"
    src.write_bytes(b"x")
    rendered = videos / f"{video_id}_music.mp4"
    rendered.write_bytes(b"x")
    env.db.update_video(
        video_id,
        slot_at=slot,
        caption_body=f"#test {platform_id}",
        original_path=str(src),
        rendered_path=str(rendered),
        review_message_id=77,
    )
    return video_id


async def test_due_video_is_posted_to_channel(env):
    video_id = approved(env, "a", NOW)
    assert await env.publisher.publish_due() == 1
    call = env.bot.named("send_video")[0]
    assert call["chat_id"] == "@test_channel"
    assert call["caption"] == "#test a\n\n" + env.settings.footer
    v = env.db.get_video(video_id)
    assert v.status is Status.POSTED and v.posted_at == NOW
    assert v.channel_message_id == env.bot.last_id("send_video")
    edit = env.bot.named("edit_message_reply_markup")[0]
    assert edit["message_id"] == 77
    button = edit["reply_markup"].inline_keyboard[0][0]
    assert button.url == f"https://t.me/test_channel/{v.channel_message_id}"
    assert button.text == "📢 10:00 da joylandi"
    assert not Path(v.rendered_path).exists() and not Path(v.original_path).exists()


async def test_future_video_waits(env):
    approved(env, "a", NOW + timedelta(hours=3))
    assert await env.publisher.publish_due() == 0
    assert env.bot.named("send_video") == []


async def test_retry_then_success(env):
    approved(env, "a", NOW)
    env.bot.failures["send_video"] = 1
    assert await env.publisher.publish_due() == 1
    assert len(env.bot.named("send_video")) == 2
    assert env.sleeps == [1]


async def test_all_attempts_fail_moves_to_next_slot(env):
    video_id = approved(env, "a", NOW)
    env.bot.failures["send_video"] = 4
    assert await env.publisher.publish_due() == 0
    assert env.sleeps == [1, 2, 4]
    v = env.db.get_video(video_id)
    assert v.status is Status.APPROVED
    assert v.slot_at == datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 13:00 in Tashkent
    sent_texts = [kw["text"] for kw in env.bot.named("send_message")]
    assert texts.POST_FAILED.format(when="13:00") in sent_texts


async def test_replan_missed_slots_after_downtime(env):
    first = approved(env, "a", datetime(2026, 10, 6, 11, 0, tzinfo=UTC))  # yesterday 16:00
    second = approved(env, "b", datetime(2026, 10, 6, 14, 0, tzinfo=UTC))  # yesterday 19:00
    morning = NOW - timedelta(hours=2)  # 08:00 in Tashkent
    assert await env.publisher.replan_missed(morning) == 2
    assert env.db.get_video(first).slot_at == datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 10:00
    assert env.db.get_video(second).slot_at == datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 13:00


async def test_cancel_during_retry_pause_is_not_posted(env):
    video_id = approved(env, "a", NOW)
    env.bot.failures["send_video"] = 1
    env.during_pause.append(lambda: env.db.update_video(video_id, status=Status.IN_REVIEW, slot_at=None))
    assert await env.publisher.publish_due() == 0
    assert len(env.bot.named("send_video")) == 1
    assert env.db.get_video(video_id).status is Status.IN_REVIEW


async def test_dropped_connection_is_not_retried_and_goes_back_to_review(env):
    video_id = approved(env, "a", NOW)
    env.bot.failure_kind = "network"
    env.bot.failures["send_video"] = 1
    assert await env.publisher.publish_due() == 0
    assert len(env.bot.named("send_video")) == 1
    assert env.sleeps == []
    v = env.db.get_video(video_id)
    assert v.status is Status.IN_REVIEW and v.slot_at is None and v.review_sent_at == NOW
    assert texts.POST_UNCERTAIN in [kw["text"] for kw in env.bot.named("send_message")]
    buttons = env.bot.named("edit_message_reply_markup")[-1]["reply_markup"].inline_keyboard
    assert buttons[2][0].text == texts.BTN_APPROVE


async def test_channel_upload_gets_a_long_timeout(env):
    approved(env, "a", NOW)
    await env.publisher.publish_due()
    assert env.bot.named("send_video")[0]["request_timeout"] == 300
