from contentbot.ai.caption_writer import CaptionWriter, build_system_prompt
from contentbot.models import Score
from contentbot.pipeline.captions import BODY_LIMIT, utf16_len
from tests.factories import make_candidate
from tests.fakes import FakeClaudeJSON


async def test_draft_returns_caption_and_passes_hashtag_and_reason():
    fake = FakeClaudeJSON([{"caption": "#mexanizm Zo'r mexanizm! Bunday mahsulotlarni Evimda topishingiz mumkin!"}])
    writer = CaptionWriter(fake, ["Misol 1", "Misol 2"])
    out = await writer.draft(make_candidate(title="Sofa bed mechanism"), Score(9, "mechanism", False, "sofa bed opening"))
    assert out.startswith("#mexanizm")
    call = fake.calls[0]
    prompt = call["content"][0]["text"]
    assert "#mexanizm" in prompt and "sofa bed opening" in prompt and "Sofa bed mechanism" in prompt
    assert "Misol 2" in call["system"]
    assert call["effort"] == "medium"


async def test_draft_failure_returns_empty_string():
    writer = CaptionWriter(FakeClaudeJSON([None]), ["x"])
    assert await writer.draft(make_candidate(), None) == ""


async def test_long_caption_is_trimmed():
    writer = CaptionWriter(FakeClaudeJSON([{"caption": "Gap. " * 200}]), ["x"])
    out = await writer.draft(make_candidate(), Score(8, "foam", False, "foam"))
    assert 0 < utf16_len(out) <= BODY_LIMIT


def test_system_prompt_rules():
    prompt = build_system_prompt(["Novella ✨"])
    assert "Latin script" in prompt
    assert "NOT filmed at Evim" in prompt
    assert "Never write prices" in prompt
    assert "Novella ✨" in prompt
