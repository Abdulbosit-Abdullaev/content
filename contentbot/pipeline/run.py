"""One full run: search → free filter → AI check → pick → download → caption → review group."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from contentbot import texts
from contentbot.config import Settings
from contentbot.db import Database
from contentbot.models import AudioMode, Status, utc_now
from contentbot.pipeline.discover import discover
from contentbot.pipeline.picker import CategoryQuota, Ranked, rank
from contentbot.pipeline.prefilter import FilterRules, dedupe, order_for_ai, split
from contentbot.sources.base import Source

log = logging.getLogger(__name__)


class ReviewSink(Protocol):
    async def send_candidate(self, video_id: int) -> None: ...

    async def notify(self, text: str) -> None: ...


class PrintSink:
    """Collects what a dry run would have sent to Telegram."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.sent: list[int] = []
        self.notes: list[str] = []

    async def send_candidate(self, video_id: int) -> None:
        self.sent.append(video_id)

    async def notify(self, text: str) -> None:
        self.notes.append(text)

    def report(self) -> str:
        lines: list[str] = []
        for video_id in self.sent:
            v = self.db.get_video(video_id)
            score = v.ai_score if v.ai_score is not None else "-"
            lines.append(f"[{v.platform}] score={score} {v.ai_category or '-'} {v.url}")
            lines.append("    " + v.caption_body.replace("\n", " "))
        lines += [f"NOTE: {note}" for note in self.notes]
        return "\n".join(lines) or "Nothing selected."


class Pipeline:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        sources: Sequence[Source],
        checker: Any,
        writer: Any,
        downloader: Any,
        media: Any,
        sink: ReviewSink,
        dry_run: bool = False,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db = db
        self.settings = settings
        self.sources = list(sources)
        self.checker = checker
        self.writer = writer
        self.downloader = downloader
        self.media = media
        self.sink = sink
        self.dry_run = dry_run
        self.clock = clock
        self.rules = FilterRules.from_settings(settings)
        self.videos_dir = settings.data_dir / "videos"
        self.lock = asyncio.Lock()

    async def run(self, trigger: str = "schedule") -> dict:
        if self.lock.locked():
            return {"skipped": "already running"}
        async with self.lock:
            return await self._run(trigger)

    async def _run(self, trigger: str) -> dict:
        started = self.clock()
        run_id = self.db.start_run(trigger, started)
        summary: dict = {"trigger": trigger, "found": {}, "errors": {}, "apify_cost_usd": 0.0}
        try:
            await self._steps(started, summary)
        except Exception as exc:
            log.exception("Pipeline run failed")
            summary["fatal"] = f"{type(exc).__name__}: {exc}"[:300]
            await self._notify(texts.RUN_FAILED)
        finally:
            self.db.finish_run(run_id, summary, self.clock())
        return summary

    async def _steps(self, now: datetime, summary: dict) -> None:
        s = self.settings
        keywords = self.db.pick_keywords(s.keywords_per_run)
        if not keywords:
            summary["errors"] = {"keywords": "no active keywords"}
            return
        self.db.mark_keywords_used([k.id for k in keywords], now)
        summary["keywords"] = [k.text for k in keywords]

        result = await discover(await self._sources_within_budget(now), keywords)
        summary.update(found=result.counts, errors=result.errors, apify_cost_usd=result.cost_usd)
        for name in result.errors:
            await self._notify(texts.SOURCE_FAILED.format(source=texts.PLATFORM_NAMES.get(name, name)))

        fresh = [c for c in dedupe(result.candidates) if not self.db.video_exists(c.platform, c.platform_id)]
        passed, dropped = split(fresh, self.rules)
        ordered = order_for_ai(passed)
        to_check, overflow = ordered[: s.ai_check_limit], ordered[s.ai_check_limit :]
        for c in dropped + overflow:
            self.db.insert_video(c, Status.FILTERED_OUT, now)
        ids = [self.db.insert_video(c, Status.FOUND, now) for c in to_check]
        summary.update(new=len(fresh), checked=len(to_check))

        ai_enabled = self.checker is not None
        scores = await self.checker.score(to_check) if ai_enabled else None
        ai_ok = scores is not None
        items: list[Ranked] = []
        for video_id, candidate, score in zip(ids, to_check, scores if ai_ok else [None] * len(to_check), strict=True):
            if score is not None:
                self.db.update_video(
                    video_id, status=Status.SCORED, ai_score=score.score, ai_category=score.category, ai_reason=score.reason
                )
            items.append(Ranked(video_id, candidate, score))

        quota = CategoryQuota(s.candidates_per_day, s.max_per_category)
        selected: list[Ranked] = []
        # Without AI, keep the platforms taking turns (each platform's most-viewed first).
        for item in rank(items, s.min_ai_score, ai_ok) if ai_enabled else items:
            if quota.full:
                break
            category = item.score.category if item.score else None
            if not quota.wants(category):
                continue
            if await self._prepare(item):
                quota.take(category)
                selected.append(item)
        summary["selected"] = len(selected)

        for item in selected:
            body = await self.writer.draft(item.candidate, item.score) if self.writer is not None else ""
            self.db.update_video(item.video_id, caption_body=body, status=Status.SELECTED)
            try:
                await self.sink.send_candidate(item.video_id)
            except Exception as exc:  # one Telegram failure must not lose the rest of the batch
                log.warning("Sending video %s to review failed: %s", item.video_id, exc)
                self.db.update_video(item.video_id, status=Status.FAILED, error=f"send failed: {exc}"[:300])

        if ai_enabled and not ai_ok and to_check:
            await self._notify(texts.AI_UNAVAILABLE)
        if len(selected) < s.candidates_per_day:
            await self._notify(texts.ONLY_N.format(n=len(selected)))

    async def _prepare(self, item: Ranked) -> bool:
        """Download, check the real duration and render a Telegram-ready original."""
        if self.dry_run:
            return True
        video_id = item.video_id
        try:
            source = await self.downloader.fetch(video_id, item.candidate)
            info = await self.media.probe(source)
            if not self.rules.min_duration_s <= info.duration_s <= self.rules.max_duration_s:
                source.unlink(missing_ok=True)
                self.db.update_video(
                    video_id, status=Status.FILTERED_OUT, duration_s=info.duration_s, error="duration out of range"
                )
                return False
            rendered = await self.media.render(source, AudioMode.ORIGINAL, self.videos_dir / f"{video_id}_original.mp4")
        except Exception as exc:  # any download or ffmpeg failure: take the next candidate
            log.warning("Preparing video %s failed: %s", video_id, exc)
            self.db.update_video(video_id, status=Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:300])
            return False
        self.db.update_video(
            video_id,
            original_path=str(source),
            rendered_path=str(rendered),
            audio_mode=AudioMode.ORIGINAL.value,
            duration_s=info.duration_s,
        )
        return True

    async def _sources_within_budget(self, now: datetime) -> list[Source]:
        local = now.astimezone(self.settings.timezone)
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self.db.apify_spend_since(month_start) < self.settings.apify_monthly_budget_usd:
            return list(self.sources)
        month = local.strftime("%Y-%m")
        if self.db.get_meta("apify_budget_warned") != month:
            self.db.set_meta("apify_budget_warned", month)
            await self._notify(texts.APIFY_BUDGET)
        return [s for s in self.sources if not s.is_paid]

    async def _notify(self, text: str) -> None:
        try:
            await self.sink.notify(text)
        except Exception:  # a failed warning message must not stop the run
            log.exception("Could not send notification")
