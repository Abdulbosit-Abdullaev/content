"""One structured-output call to Claude that returns parsed JSON, or None on any failure."""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeJSON:
    def __init__(self, model: str, api_key: str | None = None, client: Any = None) -> None:
        self.model = model
        self.client = client if client is not None else anthropic.AsyncAnthropic(api_key=api_key)

    async def ask_json(
        self, *, system: str, content: list[dict], schema: dict, effort: str, max_tokens: int = 8000
    ) -> dict | None:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                betas=[FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
            )
        except anthropic.APIError as exc:
            log.warning("Claude request failed: %s", exc)
            return None
        if response.stop_reason != "end_turn":
            log.warning("Claude stopped with %s", response.stop_reason)
            return None
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            log.warning("Claude returned no text block")
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Claude returned invalid JSON")
            return None
        return data if isinstance(data, dict) else None
