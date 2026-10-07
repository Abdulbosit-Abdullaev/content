import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.commands import CommandService, audio_suffix, detect_language
from contentbot.db import Database
from contentbot.models import Status
from tests.factories import make_secrets, make_settings, make_video
from tests.fakes import FakeBot, FakePipeline

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    pipeline = FakePipeline()
    service = CommandService(bot, db, settings, make_secrets(), pipeline, clock=lambda: NOW)
    yield SimpleNamespace(settings=settings, db=db, bot=bot, pipeline=pipeline, service=service)
    db.close()


def test_queue_text_lists_approved_videos_in_order(env):
    assert env.service.queue_text() == texts.QUEUE_EMPTY
    make_video(
        env.db, platform_id="b", status=Status.APPROVED, slot_at=datetime(2026, 10, 8, 5, 0, tzinfo=UTC),
        caption_body="Ertangi video", ai_category="foam",
    )
    make_video(
        env.db, platform_id="a", status=Status.APPROVED, slot_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
        caption_body="Bugungi video", ai_category="mechanism",
    )
    lines = env.service.queue_text().splitlines()
    assert lines == [texts.QUEUE_TITLE, "1. 13:00 · mexanizm · Bugungi video", "2. 08.10 10:00 · porolon · Ertangi video"]


def test_status_text(env):
    run = env.db.start_run("schedule", NOW - timedelta(hours=1))
    env.db.finish_run(
        run, {"found": {"youtube": 25}, "errors": {"instagram": "RuntimeError: x"}, "apify_cost_usd": 1.5}, NOW
    )
    make_video(env.db, platform_id="r", status=Status.IN_REVIEW)
    text = env.service.status_text()
    assert "07.10 07:00" in text
    assert "YouTube: 25" in text and "Instagram: xato" in text
    assert "Ko'rib chiqilmoqda: 1" in text
    assert "$1.50 / $5.00" in text


def test_status_before_first_run(env):
    assert texts.NEVER in env.service.status_text()


def test_keyword_commands(env):
    assert env.service.add_keyword("  ") == texts.KW_USAGE_ADD
    assert env.service.add_keyword(None) == texts.KW_USAGE_ADD
    assert env.service.add_keyword("divan mexanizmi") == texts.KW_ADDED.format(text="divan mexanizmi")
    assert env.service.add_keyword("divan mexanizmi") == texts.KW_EXISTS
    assert "en: divan mexanizmi" in env.service.keywords_text()
    assert env.service.remove_keyword("divan mexanizmi") == texts.KW_REMOVED.format(text="divan mexanizmi")
    assert env.service.remove_keyword("divan mexanizmi") == texts.KW_NOT_FOUND
    assert env.service.remove_keyword("") == texts.KW_USAGE_DEL


def test_detect_language():
    assert detect_language("沙发机构") == "zh"
    assert detect_language("перетяжка") == "ru"
    assert detect_language("koltuk döşeme") == "tr"
    assert detect_language("yog'och oyoq") == "uz"
    assert detect_language("sofa legs") == "en"


def test_audio_suffix():
    assert audio_suffix("calm.M4A", None) == ".m4a"
    assert audio_suffix(None, "audio/mpeg") == ".mp3"
    assert audio_suffix("track.xyz", "audio/ogg") == ".ogg"
    assert audio_suffix(None, None) == ".mp3"


async def test_start_search_runs_pipeline_in_background(env):
    assert env.service.start_search() == texts.SEARCH_STARTED
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert env.pipeline.runs == ["manual"]


async def test_start_search_while_running(env):
    async with env.pipeline.lock:
        assert env.service.start_search() == texts.SEARCH_RUNNING
    assert env.pipeline.runs == []


async def test_saving_uploaded_music(env):
    env.service.remember_audio(42, "file-id-1", "Calm piano", ".mp3")
    assert await env.service.save_audio(42) == texts.MUSIC_ADDED.format(title="Calm piano")
    track = env.db.list_tracks()[0]
    assert track.title == "Calm piano" and track.file_path.endswith(".mp3")
    assert env.bot.named("download")[0]["file"] == "file-id-1"
    assert await env.service.save_audio(42) is None
