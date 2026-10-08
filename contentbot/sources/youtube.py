"""YouTube Shorts through the official YouTube Data API v3 (free daily quota)."""
from __future__ import annotations

import re

import httpx

from contentbot.models import Candidate, Keyword
from contentbot.sources.base import SourceResult, as_dict, as_query, parse_all, parse_datetime, to_int, to_url

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_iso_duration(value: str) -> float | None:
    if not value or value in ("P", "PT"):
        return None
    match = _DURATION.match(value)
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def parse_video_item(item: dict) -> Candidate | None:
    video_id = item.get("id")
    if not video_id or not isinstance(video_id, str):
        return None
    snippet = item.get("snippet") or {}
    thumbs = as_dict(snippet.get("thumbnails"))
    thumbnail = next(
        (url for k in ("high", "medium", "default") if (url := to_url(as_dict(thumbs.get(k)).get("url")))), None
    )
    tags = " ".join("#" + str(t).replace(" ", "") for t in (snippet.get("tags") or [])[:15])
    description = f"{snippet.get('description', '')} {tags}".strip()
    return Candidate(
        platform="youtube",
        platform_id=video_id,
        url=f"https://www.youtube.com/shorts/{video_id}",
        thumbnail_url=thumbnail,
        title=str(snippet.get("title", "")),
        description=description,
        author=str(snippet.get("channelTitle", "")),
        duration_s=parse_iso_duration((item.get("contentDetails") or {}).get("duration", "")),
        views=to_int((item.get("statistics") or {}).get("viewCount")),
        published_at=parse_datetime(snippet.get("publishedAt")),
    )


class YouTubeSource:
    name = "youtube"
    timeout_s = 90.0
    is_paid = False

    def __init__(self, http: httpx.AsyncClient, api_key: str, per_keyword: int = 5) -> None:
        self.http = http
        self.api_key = api_key
        self.per_keyword = per_keyword

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        ids: list[str] = []
        for keyword in keywords:
            response = await self.http.get(
                SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": as_query(keyword.text),
                    "type": "video",
                    "videoDuration": "short",
                    "maxResults": self.per_keyword,
                    "key": self.api_key,
                },
            )
            response.raise_for_status()
            for item in response.json().get("items", []):
                video_id = (item.get("id") or {}).get("videoId")
                if video_id and video_id not in ids:
                    ids.append(video_id)
        candidates: list[Candidate] = []
        for start in range(0, len(ids), 50):
            response = await self.http.get(
                VIDEOS_URL,
                params={"part": "snippet,contentDetails,statistics", "id": ",".join(ids[start : start + 50]), "key": self.api_key},
            )
            response.raise_for_status()
            candidates.extend(parse_all(parse_video_item, response.json().get("items", [])))
        return SourceResult(candidates)
