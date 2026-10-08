"""TikTok videos through the Apify actor clockworks/tiktok-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import (
    SourceResult,
    as_dict,
    as_hashtag,
    as_query,
    parse_all,
    parse_datetime,
    to_float,
    to_int,
    to_url,
    unique,
)


def parse_tiktok_item(item: dict) -> Candidate | None:
    video_id = item.get("id")
    url = to_url(item.get("webVideoUrl"))
    if not video_id or not url:
        return None
    meta = as_dict(item.get("videoMeta"))
    return Candidate(
        platform="tiktok",
        platform_id=str(video_id),
        url=url,
        media_url=None,
        thumbnail_url=to_url(meta.get("coverUrl")),
        title="",
        description=str(item.get("text") or ""),
        author=str(as_dict(item.get("authorMeta")).get("name") or ""),
        duration_s=to_float(meta.get("duration")),
        views=to_int(item.get("playCount")),
        published_at=parse_datetime(item.get("createTimeISO")),
    )


class TikTokSource:
    name = "tiktok"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        queries = unique(as_query(k.text) for k in keywords if k.kind == "query")
        hashtags = unique(as_hashtag(k.text) for k in keywords if k.kind == "hashtag")
        per_term = max(1, self.actor.max_results // max(1, len(queries) + len(hashtags)))
        return {
            "searchQueries": queries,
            "hashtags": hashtags,
            "resultsPerPage": per_term,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = parse_all(parse_tiktok_item, items)
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
