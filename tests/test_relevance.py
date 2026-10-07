import io

import httpx
from PIL import Image

from contentbot.ai.relevance import RelevanceChecker, parse_scores, shrink_to_jpeg
from contentbot.models import Score
from tests.factories import make_candidate
from tests.fakes import FakeClaudeJSON


def png_bytes(size=(1200, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, "PNG")
    return buffer.getvalue()


def result(index, score, category, branding=False, reason="r"):
    return {"index": index, "score": score, "category": category, "competitor_branding": branding, "reason": reason}


def test_parse_scores_clamps_and_caps_competitors():
    data = {
        "results": [
            result(0, 14, "foam", reason="foam cutting"),
            result(1, 9, "mechanism", branding=True),
            result(2, 7, "spaceships"),
            result(7, 9, "foam"),
            {"index": 3},
        ]
    }
    out = parse_scores(data, 4)
    assert out[0] == Score(10, "foam", False, "foam cutting")
    assert out[1].score == 3 and out[1].competitor_branding
    assert out[2].category == "other"
    assert out[3] is None


def test_shrink_to_jpeg_limits_size():
    small = shrink_to_jpeg(png_bytes(), max_side=768)
    with Image.open(io.BytesIO(small)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 768


async def test_score_sends_image_and_text_blocks(respx_mock):
    respx_mock.get("https://img.example/1.jpg").respond(200, content=png_bytes())
    fake = FakeClaudeJSON([{"results": [result(0, 8, "fabric", reason="fabric roll")]}])
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http).score([make_candidate(thumbnail_url="https://img.example/1.jpg")])
    assert scores == [Score(8, "fabric", False, "fabric roll")]
    call = fake.calls[0]
    assert call["effort"] == "low"
    assert any(b["type"] == "image" and b["source"]["media_type"] == "image/jpeg" for b in call["content"])
    assert call["content"][0]["text"].startswith("[0] platform: youtube")


async def test_missing_preview_still_scores(respx_mock):
    respx_mock.get("https://img.example/1.jpg").respond(404)
    fake = FakeClaudeJSON([{"results": [result(0, 7, "legs")]}])
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http).score([make_candidate(thumbnail_url="https://img.example/1.jpg")])
    assert scores[0].category == "legs"
    assert all(b["type"] == "text" for b in fake.calls[0]["content"])


async def test_all_batches_failing_means_ai_unavailable():
    fake = FakeClaudeJSON([None, None])
    candidates = [make_candidate(thumbnail_url=None), make_candidate(platform_id="v2", thumbnail_url=None)]
    async with httpx.AsyncClient() as http:
        assert await RelevanceChecker(fake, http, batch_size=1).score(candidates) is None


async def test_one_failed_batch_gives_none_for_its_items():
    fake = FakeClaudeJSON([None, {"results": [result(0, 6, "legs")]}])
    candidates = [make_candidate(thumbnail_url=None), make_candidate(platform_id="v2", thumbnail_url=None)]
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http, batch_size=1).score(candidates)
    assert scores[0] is None and scores[1].score == 6
