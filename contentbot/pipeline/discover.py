"""Run every source at the same time; one broken source never stops the others."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from contentbot.models import Candidate, Keyword
from contentbot.sources.base import Source

log = logging.getLogger(__name__)


@dataclass
class DiscoverResult:
    candidates: list[Candidate] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0


async def discover(sources: Sequence[Source], keywords: list[Keyword]) -> DiscoverResult:
    async def one(source: Source):
        return await asyncio.wait_for(source.search(keywords), timeout=source.timeout_s)

    outcomes = await asyncio.gather(*(one(s) for s in sources), return_exceptions=True)
    result = DiscoverResult()
    for source, outcome in zip(sources, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("Source %s failed: %r", source.name, outcome)
            result.errors[source.name] = f"{type(outcome).__name__}: {outcome}"[:200]
            continue
        result.candidates.extend(outcome.candidates)
        result.counts[source.name] = len(outcome.candidates)
        result.cost_usd += outcome.cost_usd
    result.cost_usd = round(result.cost_usd, 4)
    return result
