"""Ask Claude whether each candidate video fits the Evim channel."""
from __future__ import annotations

import base64
import io
import logging

import httpx
from PIL import Image

from contentbot.ai.claude import ClaudeJSON
from contentbot.models import CATEGORIES, Candidate, Score

log = logging.getLogger(__name__)

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "competitor_branding": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "category", "competitor_branding", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You review short videos for the Telegram channel of Evim, a shop in Tashkent that sells materials and parts for soft-furniture makers: sofa and chair mechanisms, foam, upholstery fabric, leather, furniture legs, tools, clips and small fittings.

For each numbered candidate you get the platform, title, description and hashtags, and usually a preview image right after its text. Score how well the video fits the channel, from 0 to 10:
- 8-10: clearly about soft furniture: the materials above, upholstery work, sofa or chair making in a workshop, mechanisms in action, finished sofas and chairs. Clear and good-looking.
- 5-7: related to soft furniture but weaker: unclear image, mostly a finished interior, little detail.
- 0-4: off-topic, mostly a person talking, memes, dancing, only hard furniture (kitchens, wardrobes), or an ad covered with text.

Set competitor_branding to true when you can see another shop's phone number, logo, website, price list or store name in the image or text. Evim must not advertise other shops.

Pick the closest category. Keep reason under 15 words, in English. Return one result for every candidate index."""


def describe(index: int, c: Candidate) -> str:
    description = " ".join(c.description.split())[:600]
    return f"[{index}] platform: {c.platform}\ntitle: {c.title[:200]}\ndescription: {description}"


def shrink_to_jpeg(data: bytes, max_side: int = 768) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    rgb.thumbnail((max_side, max_side))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return out.getvalue()


async def fetch_preview(http: httpx.AsyncClient, url: str | None, max_side: int = 768) -> bytes | None:
    if not url:
        return None
    try:
        response = await http.get(url, timeout=20)
        response.raise_for_status()
        return shrink_to_jpeg(response.content, max_side)
    except Exception as exc:  # a missing preview only means "judge by the text"
        log.info("Preview download failed for %s: %s", url, exc)
        return None


def parse_scores(data: dict, count: int) -> list[Score | None]:
    out: list[Score | None] = [None] * count
    for item in data.get("results", []):
        try:
            index = int(item["index"])
            if not 0 <= index < count:
                continue
            score = max(0, min(10, int(item["score"])))
            category = item["category"] if item["category"] in CATEGORIES else "other"
            branding = bool(item["competitor_branding"])
            if branding:
                score = min(score, 3)
            out[index] = Score(score, category, branding, str(item.get("reason", ""))[:200])
        except (KeyError, TypeError, ValueError):
            continue
    return out


class RelevanceChecker:
    def __init__(self, claude: ClaudeJSON, http: httpx.AsyncClient, batch_size: int = 10) -> None:
        self.claude = claude
        self.http = http
        self.batch_size = batch_size

    async def score(self, candidates: list[Candidate]) -> list[Score | None] | None:
        """One Score (or None) per candidate; None overall when every AI call failed."""
        if not candidates:
            return []
        results: list[Score | None] = []
        any_answer = False
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            data = await self.claude.ask_json(
                system=SYSTEM_PROMPT, content=await self._content(batch), schema=SCORE_SCHEMA, effort="low"
            )
            if data is None:
                results.extend([None] * len(batch))
                continue
            any_answer = True
            results.extend(parse_scores(data, len(batch)))
        return results if any_answer else None

    async def _content(self, batch: list[Candidate]) -> list[dict]:
        blocks: list[dict] = []
        for index, c in enumerate(batch):
            blocks.append({"type": "text", "text": describe(index, c)})
            preview = await fetch_preview(self.http, c.thumbnail_url)
            if preview:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(preview).decode("ascii"),
                        },
                    }
                )
        blocks.append({"type": "text", "text": f"Score all {len(batch)} candidates (indexes 0 to {len(batch) - 1})."})
        return blocks
