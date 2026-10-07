from contentbot.ai.claude import FALLBACK_BETA, ClaudeJSON
from tests.fakes import FakeClaudeClient, claude_reply, connection_error

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


async def ask(client):
    claude = ClaudeJSON("claude-opus-5-5", client=client)
    return await claude.ask_json(system="sys", content=[{"type": "text", "text": "hi"}], schema=SCHEMA, effort="low")


async def test_returns_parsed_json_and_sends_expected_request():
    client = FakeClaudeClient([claude_reply({"ok": True})])
    assert await ask(client) == {"ok": True}
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == "sys"
    assert call["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["betas"] == [FALLBACK_BETA]
    assert call["extra_body"] == {"fallbacks": "default"}


async def test_refusal_returns_none():
    assert await ask(FakeClaudeClient([claude_reply({"ok": True}, stop_reason="refusal")])) is None


async def test_invalid_json_returns_none():
    assert await ask(FakeClaudeClient([claude_reply("not json")])) is None


async def test_api_error_returns_none():
    assert await ask(FakeClaudeClient([connection_error()])) is None
