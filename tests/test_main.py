import logging

import httpx
from aiogram import Bot, Dispatcher

from contentbot.bot.commands import CommandService, build_commands_router
from contentbot.bot.review import ReviewService, build_review_router
from contentbot.db import Database
from contentbot.pipeline.run import PrintSink
from main import build_pipeline, main, setup_logging
from tests.factories import make_secrets, make_settings
from tests.fakes import FakePipeline


def test_config_error_exits_with_code_2(tmp_path, capsys):
    code = main(["--settings", str(tmp_path / "missing.yaml"), "--env", str(tmp_path / "none.env")])
    assert code == 2
    assert "Config error" in capsys.readouterr().err


async def test_build_pipeline_wires_all_sources(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    async with httpx.AsyncClient() as http:
        pipeline = build_pipeline(db, settings, make_secrets(), http, PrintSink(db))
    assert [s.name for s in pipeline.sources] == ["youtube", "tiktok", "instagram", "pinterest"]
    assert pipeline.dry_run is False


async def test_routers_attach_to_a_dispatcher(tmp_path):
    settings = make_settings(tmp_path)
    secrets = make_secrets()
    db = Database(":memory:")
    bot = Bot(token=secrets.telegram_bot_token)
    try:
        review = ReviewService(bot, db, settings, secrets, media=None)
        commands = CommandService(bot, db, settings, secrets, FakePipeline())
        dispatcher = Dispatcher()
        dispatcher.include_router(build_commands_router(commands, secrets.review_chat_id))
        dispatcher.include_router(build_review_router(review, secrets.review_chat_id))
        assert len(dispatcher.sub_routers) == 2
    finally:
        await bot.session.close()


def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        setup_logging(tmp_path / "logs")
        logging.getLogger("contentbot").info("hello")
        assert (tmp_path / "logs" / "bot.log").exists()
    finally:
        for handler in root.handlers[:]:
            handler.close()
        root.handlers[:] = saved


def test_second_copy_of_the_bot_exits(monkeypatch, capsys):
    from contentbot.config import SECRET_VARS, load_settings
    from contentbot.instance_lock import InstanceLock
    from tests.factories import ROOT

    for var in SECRET_VARS:
        monkeypatch.setenv(var, "-100123" if var == "REVIEW_CHAT_ID" else "123456:TESTTOKEN")
    lock = InstanceLock(load_settings(ROOT / "settings.yaml").data_dir / "bot.lock")
    lock.acquire()
    try:
        code = main(["--settings", str(ROOT / "settings.yaml"), "--env", "does-not-exist.env"])
    finally:
        lock.release()
    assert code == 3
    assert "already running" in capsys.readouterr().err
