import json

import httpx
import pytest

from contentbot.config import ApifyActor
from contentbot.models import Keyword
from contentbot.sources.apify import APIFY_BASE, ApifyError, ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.instagram import InstagramSource, parse_instagram_item
from contentbot.sources.pinterest import PinterestSource, parse_pinterest_item
from contentbot.sources.tiktok import TikTokSource, parse_tiktok_item

TIKTOK_ACTOR = ApifyActor("clockworks/tiktok-scraper", 1.70, 20)
TIKTOK_URL = f"{APIFY_BASE}/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items"

TIKTOK_ITEM = {
    "id": "7350000000000000001",
    "text": "Divan mexanizmi #sofa",
    "webVideoUrl": "https://www.tiktok.com/@maker/video/7350000000000000001",
    "createTimeISO": "2026-09-30T08:00:00.000Z",
    "playCount": 15200,
    "authorMeta": {"name": "maker"},
    "videoMeta": {"duration": 21, "coverUrl": "https://p16.tiktokcdn.com/cover.jpg"},
}
INSTAGRAM_ITEM = {
    "id": "3400000000000000002",
    "type": "Video",
    "shortCode": "C9abc",
    "caption": "Перетяжка дивана #перетяжкамебели",
    "url": "https://www.instagram.com/p/C9abc/",
    "videoUrl": "https://scontent.cdninstagram.com/v.mp4?sig=1",
    "displayUrl": "https://scontent.cdninstagram.com/p.jpg",
    "videoDuration": 34.5,
    "videoPlayCount": 8800,
    "ownerUsername": "obivka_master",
    "timestamp": "2026-09-29T12:00:00.000Z",
    "productType": "clips",
}
PINTEREST_ITEM = {
    "id": "424605071130957254",
    "title": "Sofa upholstery",
    "description": "step by step",
    "url": "https://www.pinterest.com/pin/424605071130957254/",
    "imageUrl": "https://i.pinimg.com/originals/x.jpg",
    "videoUrl": "https://v1.pinimg.com/videos/mc/720p/x.mp4",
    "isVideo": True,
    "createdAt": "Mon, 29 Sep 2026 10:00:00 +0000",
    "pinner": {"username": "homeideas"},
}
KEYWORDS = [Keyword(1, "sofa mechanism", "en", "query"), Keyword(2, "#перетяжка мебели", "ru", "hashtag")]


def test_parse_tiktok_item():
    c = parse_tiktok_item(TIKTOK_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("tiktok", "7350000000000000001", 15200, 21.0)
    assert c.thumbnail_url == "https://p16.tiktokcdn.com/cover.jpg"
    assert c.author == "maker" and c.media_url is None


def test_parse_tiktok_item_tolerates_odd_types():
    c = parse_tiktok_item({**TIKTOK_ITEM, "playCount": "2300", "videoMeta": "broken", "authorMeta": None})
    assert c.views == 2300 and c.duration_s is None and c.thumbnail_url is None and c.author == ""


def test_parse_tiktok_item_without_id_is_skipped():
    assert parse_tiktok_item({**TIKTOK_ITEM, "id": None}) is None
    assert parse_tiktok_item({"text": "no url"}) is None


def test_parse_instagram_reel():
    c = parse_instagram_item(INSTAGRAM_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("instagram", "3400000000000000002", 8800, 34.5)
    assert c.media_url.startswith("https://scontent") and c.author == "obivka_master"


def test_instagram_photo_post_is_skipped():
    assert parse_instagram_item({**INSTAGRAM_ITEM, "type": "Image", "productType": "feed"}) is None


def test_instagram_uses_ig_play_count_when_needed():
    item = {k: v for k, v in INSTAGRAM_ITEM.items() if k != "videoPlayCount"}
    assert parse_instagram_item({**item, "igPlayCount": 4100}).views == 4100


def test_parse_pinterest_video_pin():
    c = parse_pinterest_item(PINTEREST_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("pinterest", "424605071130957254", None, None)
    assert c.media_url.endswith(".mp4") and c.author == "homeideas" and c.published_at is not None


def test_pinterest_image_pin_is_skipped():
    assert parse_pinterest_item({**PINTEREST_ITEM, "isVideo": False, "videoUrl": None}) is None


def test_pinterest_pinner_as_plain_string():
    assert parse_pinterest_item({**PINTEREST_ITEM, "pinner": "someone"}).author == "someone"


def test_tiktok_input_splits_queries_and_hashtags():
    data = TikTokSource(None, TIKTOK_ACTOR).build_input(KEYWORDS)
    assert data["searchQueries"] == ["sofa mechanism"]
    assert data["hashtags"] == ["перетяжкамебели"]
    assert data["resultsPerPage"] == 10
    assert data["shouldDownloadVideos"] is False


def test_instagram_input_turns_keywords_into_hashtags():
    data = InstagramSource(None, ApifyActor("apify/instagram-hashtag-scraper", 2.6, 20)).build_input(KEYWORDS)
    assert data == {"hashtags": ["sofamechanism", "перетяжкамебели"], "resultsType": "reels", "resultsLimit": 10}


def test_pinterest_input_uses_plain_queries():
    data = PinterestSource(None, ApifyActor("cirkit/pinterest-pins-scraper", 2.0, 30)).build_input(KEYWORDS)
    assert data == {"searchQueries": ["sofa mechanism", "перетяжка мебели"], "maxResults": 30, "enrichWithDetails": False}


def test_cost_helpers():
    assert charge_cap(ApifyActor("x", 2.0, 30)) == 0.14
    assert estimate_cost(2, TIKTOK_ACTOR) == 0.0034


async def test_runner_calls_run_sync_endpoint(respx_mock):
    route = respx_mock.post(TIKTOK_URL).respond(201, json=[TIKTOK_ITEM, "junk"])
    async with httpx.AsyncClient() as http:
        items = await ApifyRunner(http, "TOKEN").run(
            "clockworks/tiktok-scraper", {"a": 1}, max_items=20, max_charge_usd=0.1
        )
    assert items == [TIKTOK_ITEM]
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer TOKEN"
    assert request.url.params["maxItems"] == "20"
    assert request.url.params["maxTotalChargeUsd"] == "0.10"
    assert json.loads(request.content) == {"a": 1}


async def test_runner_raises_on_http_error(respx_mock):
    respx_mock.post(TIKTOK_URL).respond(402, text="Payment required")
    async with httpx.AsyncClient() as http:
        with pytest.raises(ApifyError, match="402"):
            await ApifyRunner(http, "TOKEN").run("clockworks/tiktok-scraper", {}, max_items=1, max_charge_usd=0.1)


async def test_source_search_estimates_cost(respx_mock):
    respx_mock.post(TIKTOK_URL).respond(201, json=[TIKTOK_ITEM, {**TIKTOK_ITEM, "id": None}])
    async with httpx.AsyncClient() as http:
        result = await TikTokSource(ApifyRunner(http, "T"), TIKTOK_ACTOR).search([Keyword(1, "sofa", "en", "query")])
    assert len(result.candidates) == 1
    assert result.cost_usd == 0.0034
