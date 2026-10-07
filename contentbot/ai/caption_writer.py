"""Ask Claude for a short Uzbek (Latin) caption in the channel's style."""
from __future__ import annotations

from collections.abc import Sequence

from contentbot.ai.claude import ClaudeJSON
from contentbot.models import Candidate, Score
from contentbot.pipeline.captions import trim_body

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": False,
}

CATEGORY_HASHTAGS = {
    "mechanism": "#mexanizm",
    "foam": "#porolon",
    "fabric": "#material",
    "leather": "#charm",
    "legs": "#mebel_oyoqlari",
    "tools": "#asbob",
    "fittings": "#furnitura",
    "upholstery_work": "#obivka",
    "finished_furniture": "#yumshoq_mebel",
    "other": "#evim",
}


def build_system_prompt(examples: Sequence[str]) -> str:
    joined = "\n\n---\n\n".join(examples)
    return f"""You write short captions for Evim's Telegram channel (@evim_uzb). Evim is a shop in Tashkent that sells materials and parts for soft-furniture makers. The captions go under short videos that were found on the internet, so the video was NOT filmed at Evim.

Rules:
- Write in Uzbek, Latin script, in the same style as the examples. Russian product words are fine where the examples use them.
- Start with the category hashtag you are given.
- 1-3 short sentences and 1-3 emojis.
- End with a call to action saying such products can be found at Evim, for example: "Bunday mahsulotlarni Evimda topishingiz mumkin!"
- Never write prices, sizes, model names or brand names, and never name another company or shop.
- Never claim the video was filmed at Evim or shows Evim's own workshop.
- At most 400 characters. Do not add phone numbers or addresses; they are added automatically.

Example captions from the channel:

{joined}"""


class CaptionWriter:
    def __init__(self, claude: ClaudeJSON, examples: Sequence[str]) -> None:
        self.claude = claude
        self.system = build_system_prompt(examples)

    async def draft(self, candidate: Candidate, score: Score | None) -> str:
        category = score.category if score else "other"
        hashtag = CATEGORY_HASHTAGS.get(category, "#evim")
        about = score.reason if score else ""
        description = " ".join(candidate.description.split())[:600]
        prompt = (
            f"Category hashtag: {hashtag}\n"
            f"What the video shows: {about}\n"
            f"Original title: {candidate.title[:200]}\n"
            f"Original description: {description}\n\n"
            "Write the caption."
        )
        data = await self.claude.ask_json(
            system=self.system,
            content=[{"type": "text", "text": prompt}],
            schema=CAPTION_SCHEMA,
            effort="medium",
        )
        if not data:
            return ""
        caption = str(data.get("caption", "")).strip()
        return trim_body(caption) if caption else ""
