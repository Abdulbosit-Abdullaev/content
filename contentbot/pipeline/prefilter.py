"""Free checks done before any paid AI call."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from contentbot.config import Settings
from contentbot.models import Candidate


@dataclass(frozen=True)
class FilterRules:
    min_duration_s: float
    max_duration_s: float
    min_views: dict[str, int]

    @classmethod
    def from_settings(cls, settings: Settings) -> FilterRules:
        return cls(settings.min_duration_s, settings.max_duration_s, dict(settings.min_views))


def dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.platform, c.platform_id)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def passes(c: Candidate, rules: FilterRules) -> bool:
    if c.duration_s is not None and not rules.min_duration_s <= c.duration_s <= rules.max_duration_s:
        return False
    minimum = rules.min_views.get(c.platform)
    if minimum is not None and c.views is not None and c.views < minimum:
        return False
    return True


def split(candidates: Iterable[Candidate], rules: FilterRules) -> tuple[list[Candidate], list[Candidate]]:
    passed: list[Candidate] = []
    dropped: list[Candidate] = []
    for c in candidates:
        (passed if passes(c, rules) else dropped).append(c)
    return passed, dropped


def order_for_ai(candidates: list[Candidate], weights: dict[str, int] | None = None) -> list[Candidate]:
    """Platforms take turns (a platform with weight N takes N per turn); most-viewed first within a platform."""
    by_platform: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_platform.setdefault(c.platform, []).append(c)
    weights = weights or {}
    queues = [
        (max(1, weights.get(platform, 1)), sorted(group, key=lambda c: (c.views is None, -(c.views or 0))))
        for platform, group in by_platform.items()
    ]
    ordered: list[Candidate] = []
    while any(queue for _, queue in queues):
        for turns, queue in queues:
            for _ in range(turns):
                if queue:
                    ordered.append(queue.pop(0))
    return ordered
