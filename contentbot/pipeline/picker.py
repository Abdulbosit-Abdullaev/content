"""Choose which scored candidates go to the review group."""
from __future__ import annotations

from dataclasses import dataclass

from contentbot.models import Candidate, Score


@dataclass
class Ranked:
    video_id: int
    candidate: Candidate
    score: Score | None


def rank(items: list[Ranked], min_score: int, ai_available: bool) -> list[Ranked]:
    if ai_available:
        kept = [i for i in items if i.score is not None and i.score.score >= min_score]
        return sorted(kept, key=lambda i: (-i.score.score, -(i.candidate.views or 0)))
    return sorted(items, key=lambda i: (i.candidate.views is None, -(i.candidate.views or 0)))


class CategoryQuota:
    """At most `total` picks, and at most `per_category` from one category."""

    def __init__(self, total: int, per_category: int) -> None:
        self.total = total
        self.per_category = per_category
        self.taken = 0
        self.counts: dict[str, int] = {}

    @property
    def full(self) -> bool:
        return self.taken >= self.total

    def wants(self, category: str | None) -> bool:
        if self.full:
            return False
        if category is None:
            return True
        return self.counts.get(category, 0) < self.per_category

    def take(self, category: str | None) -> None:
        self.taken += 1
        if category is not None:
            self.counts[category] = self.counts.get(category, 0) + 1
