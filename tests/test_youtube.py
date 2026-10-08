import httpx
import pytest

from contentbot.models import Keyword
from contentbot.sources.youtube import SEARCH_URL, VIDEOS_URL, YouTubeSource, parse_iso_duration, parse_video_item


def test_parse_iso_duration():
    assert parse_iso_duration("PT1M5S") == 65.0
    assert parse_iso_duration("PT45S") == 45.0
    assert parse_iso_duration("PT2M") == 120.0
    assert parse_iso_duration("PT1H0M1S") == 3601.0
    assert parse_iso_duration("P0D") == 0.0
    assert parse_iso_duration("PT") is None
    assert parse_iso_duration("garbage") is None
    assert parse_iso_duration("") is None


def test_parse_video_item_skips_missing_id():
    assert parse_video_item({"snippet": {"title": "x"}}) is None


async def test_search_collects_unique_ids_and_details(respx_mock):
    search = respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": {"videoId": "a1"}}, {"id": {"videoId": "b2"}}]}),
            httpx.Response(200, json={"items": [{"id": {"videoId": "b2"}}, {"id": {"kind": "youtube#channel"}}]}),
        ]
    )
    videos = respx_mock.get(VIDEOS_URL).respond(
        200,
        json={
            "items": [
                {
                    "id": "a1",
                    "snippet": {
                        "title": "Sofa mechanism",
                        "description": "demo",
                        "channelTitle": "Maker",
                        "publishedAt": "2026-09-01T10:00:00Z",
                        "tags": ["sofa bed"],
                        "thumbnails": {"high": {"url": "https://i.ytimg.com/a1.jpg"}},
                    },
                    "contentDetails": {"duration": "PT1M5S"},
                    "statistics": {"viewCount": "12345"},
                },
                {"id": "b2", "snippet": {"title": "Foam"}, "contentDetails": {"duration": "PT30S"}, "statistics": {}},
            ]
        },
    )
    keywords = [Keyword(1, "sofa mechanism", "en", "query"), Keyword(2, "#upholstery", "en", "hashtag")]
    async with httpx.AsyncClient() as http:
        result = await YouTubeSource(http, "KEY", per_keyword=5).search(keywords)
    assert [c.platform_id for c in result.candidates] == ["a1", "b2"]
    a1 = result.candidates[0]
    assert a1.duration_s == 65.0 and a1.views == 12345
    assert a1.url == "https://www.youtube.com/shorts/a1"
    assert a1.thumbnail_url == "https://i.ytimg.com/a1.jpg"
    assert "#sofabed" in a1.description and a1.author == "Maker"
    assert result.candidates[1].views is None
    assert result.cost_usd == 0.0
    first, second = search.calls[0].request.url.params, search.calls[1].request.url.params
    assert first["videoDuration"] == "short" and first["type"] == "video" and first["maxResults"] == "5"
    assert second["q"] == "upholstery"
    assert videos.calls[0].request.url.params["id"] == "a1,b2"


async def test_quota_error_raises(respx_mock):
    respx_mock.get(SEARCH_URL).respond(403, json={"error": {"message": "quotaExceeded"}})
    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.HTTPStatusError):
            await YouTubeSource(http, "KEY").search([Keyword(1, "sofa", "en", "query")])


def test_parse_video_item_tolerates_odd_thumbnails():
    item = {"id": "z9", "snippet": {"thumbnails": {"high": "not-an-object", "medium": {"url": ["x"]}}}}
    assert parse_video_item(item).thumbnail_url is None
