"""Pinterest video pins through the Apify actor cirkit/pinterest-pins-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import SourceResult, as_query, parse_all, parse_datetime, to_url, unique


def parse_pinterest_item(item: dict) -> Candidate | None:
    # In real actor output isVideo and videoUrl often disagree, so either one marks a video pin.
    # Without a direct link, the downloader falls back to yt-dlp on the pin page.
    video_url = to_url(item.get("videoUrl"))
    if not video_url and item.get("isVideo") is not True:
        return None
    pin_id = item.get("id")
    page_url = to_url(item.get("url"))
    if not pin_id or not page_url:
        return None
    pinner = item.get("pinner")
    if isinstance(pinner, dict):
        author = str(pinner.get("username") or pinner.get("fullName") or "")
    else:
        author = str(pinner or "")
    return Candidate(
        platform="pinterest",
        platform_id=str(pin_id),
        url=page_url,
        media_url=video_url,
        thumbnail_url=to_url(item.get("imageUrl")),
        title=str(item.get("title") or ""),
        description=str(item.get("description") or ""),
        author=author,
        duration_s=None,
        views=None,
        published_at=parse_datetime(item.get("createdAt")),
    )


class PinterestSource:
    name = "pinterest"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        return {
            "searchQueries": unique(as_query(k.text) for k in keywords),
            "maxResults": self.actor.max_results,
            "enrichWithDetails": False,
        }

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = parse_all(parse_pinterest_item, items)
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
