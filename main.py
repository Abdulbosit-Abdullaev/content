"""Evim content bot.

Run the bot:   python main.py
Test a search: python main.py --dry-run   (searches, AI-checks and drafts captions; sends nothing)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from contentbot import texts
from contentbot.ai.caption_writer import CaptionWriter
from contentbot.ai.claude import ClaudeJSON
from contentbot.ai.relevance import RelevanceChecker
from contentbot.bot.commands import CommandService, build_commands_router
from contentbot.bot.publisher import Publisher
from contentbot.bot.review import ReviewService, build_review_router
from contentbot.config import ConfigError, Secrets, Settings, load_secrets, load_settings
from contentbot.db import Database
from contentbot.instance_lock import AlreadyRunning, InstanceLock
from contentbot.models import utc_now
from contentbot.pipeline.downloader import USER_AGENT, Downloader
from contentbot.pipeline.media import Media
from contentbot.pipeline.run import Pipeline, PrintSink, ReviewSink
from contentbot.sources.apify import ApifyRunner
from contentbot.sources.instagram import InstagramSource
from contentbot.sources.pinterest import PinterestSource
from contentbot.sources.tiktok import TikTokSource
from contentbot.sources.youtube import YouTubeSource

log = logging.getLogger("contentbot")


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [file_handler, console]
    logging.getLogger("httpx").setLevel(logging.WARNING)


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def build_sources(http: httpx.AsyncClient, settings: Settings, secrets: Secrets) -> list:
    runner = ApifyRunner(http, secrets.apify_token)
    actors = settings.apify_actors
    return [
        YouTubeSource(http, secrets.youtube_api_key, settings.youtube_per_keyword),
        TikTokSource(runner, actors["tiktok"]),
        InstagramSource(runner, actors["instagram"]),
        PinterestSource(runner, actors["pinterest"]),
    ]


def build_pipeline(
    db: Database,
    settings: Settings,
    secrets: Secrets,
    http: httpx.AsyncClient,
    sink: ReviewSink,
    *,
    dry_run: bool = False,
) -> Pipeline:
    claude = ClaudeJSON(settings.ai_model, api_key=secrets.anthropic_api_key)
    return Pipeline(
        db=db,
        settings=settings,
        sources=build_sources(http, settings, secrets),
        checker=RelevanceChecker(claude, http),
        writer=CaptionWriter(claude, settings.caption_examples),
        downloader=Downloader(
            http,
            settings.data_dir / "videos",
            cookies_file=settings.ytdlp_cookies_file,
            ffmpeg_path=settings.ffmpeg_path,
        ),
        media=Media(settings.ffmpeg_path, settings.ffprobe_path),
        sink=sink,
        dry_run=dry_run,
    )


async def run_dry(settings: Settings, secrets: Secrets) -> None:
    """Search + AI check + captions on a copy of the database; prints instead of sending."""
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "dry-run.db"
        real_path = settings.data_dir / "bot.db"
        if real_path.exists():
            real = Database(real_path)
            real.backup_to(copy_path)
            real.close()
        db = Database(copy_path)
        try:
            db.seed_keywords(settings.seed_keywords)
            sink = PrintSink(db)
            async with make_http() as http:
                summary = await build_pipeline(db, settings, secrets, http, sink, dry_run=True).run("dry-run")
            print(sink.report())
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        finally:
            db.close()


async def run_bot(settings: Settings, secrets: Secrets) -> None:
    settings.music_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "bot.db")
    db.seed_keywords(settings.seed_keywords)
    log.info("Music tracks added from folder: %s", db.sync_music_dir(settings.music_dir, utc_now()))
    bot = Bot(token=secrets.telegram_bot_token)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    try:
        async with make_http() as http:
            review = ReviewService(bot, db, settings, secrets, Media(settings.ffmpeg_path, settings.ffprobe_path))
            pipeline = build_pipeline(db, settings, secrets, http, review)
            publisher = Publisher(bot, db, settings, secrets)
            commands = CommandService(bot, db, settings, secrets, pipeline)

            dispatcher = Dispatcher()
            dispatcher.include_router(build_commands_router(commands, secrets.review_chat_id))
            dispatcher.include_router(build_review_router(review, secrets.review_chat_id))

            scheduler.add_job(
                pipeline.run,
                CronTrigger(
                    hour=settings.search_time.hour, minute=settings.search_time.minute, timezone=settings.timezone
                ),
                kwargs={"trigger": "schedule"},
                id="search",
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
            )
            scheduler.add_job(publisher.publish_due, "interval", seconds=60, id="publish", coalesce=True, max_instances=1)
            scheduler.add_job(review.expire_old, "interval", hours=1, id="expire", coalesce=True, max_instances=1)

            await publisher.replan_missed()
            await bot.set_my_commands(
                [BotCommand(command=name, description=description) for name, description in texts.COMMANDS.items()],
                scope=BotCommandScopeChat(chat_id=secrets.review_chat_id),
            )
            scheduler.start()
            log.info("Bot started. Daily search at %s (%s)", settings.search_time.strftime("%H:%M"), settings.timezone.key)
            await dispatcher.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evim content bot")
    parser.add_argument("--dry-run", action="store_true", help="search, AI-check and draft captions; send nothing")
    parser.add_argument("--settings", default="settings.yaml")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
        secrets = load_secrets(args.env)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    lock = None
    if not args.dry_run:  # a dry run never posts, so it may run next to the real bot
        lock = InstanceLock(settings.data_dir / "bot.lock")
        try:
            lock.acquire()
        except AlreadyRunning as exc:
            print(f"Not started: {exc}", file=sys.stderr)
            return 3
    setup_logging(settings.data_dir / "logs")
    try:
        asyncio.run(run_dry(settings, secrets) if args.dry_run else run_bot(settings, secrets))
    except KeyboardInterrupt:
        pass
    finally:
        if lock is not None:
            lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
