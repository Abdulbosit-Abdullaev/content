"""Instagram Reels by hashtag through the Apify actor apify/instagram-hashtag-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import SourceResult, as_hashtag, parse_datetime, to_float, to_int, unique


def parse_instagram_item(item: dict) -> Candidate | None:
    is_video = item.get("type") == "Video" or item.get("productType") == "clips"
    post_id = item.get("id") or item.get("shortCode")
    page_url = item.get("url")
    if not (is_video and post_id and page_url):
        return None
    views = to_int(item.get("videoPlayCount")) or to_int(item.get("igPlayCount")) or to_int(item.get("videoViewCount"))
    return Candidate(
        platform="instagram",
        platform_id=str(post_id),
        url=str(page_url),
        media_url=item.get("videoUrl") or None,
        thumbnail_url=item.get("displayUrl") or None,
        title="",
        description=str(item.get("caption") or ""),
        author=str(item.get("ownerUsername") or ""),
        duration_s=to_float(item.get("videoDuration")),
        views=views,
        published_at=parse_datetime(item.get("timestamp")),
    )


class InstagramSource:
    name = "instagram"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        hashtags = unique(as_hashtag(k.text) for k in keywords)
        per_tag = max(1, self.actor.max_results // max(1, len(hashtags)))
        return {"hashtags": hashtags, "resultsType": "reels", "resultsLimit": per_tag}

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = [c for c in (parse_instagram_item(i) for i in items) if c]
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
