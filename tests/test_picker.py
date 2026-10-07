from contentbot.models import Score
from contentbot.pipeline.picker import CategoryQuota, Ranked, rank
from tests.factories import make_candidate


def item(video_id, score=None, category="foam", views=1000):
    s = Score(score, category, False, "r") if score is not None else None
    return Ranked(video_id, make_candidate(platform_id=str(video_id), views=views), s)


def test_rank_drops_low_scores_and_sorts_by_score_then_views():
    items = [item(1, 7, views=10), item(2, 9), item(3, 5), item(4, 7, views=99), item(5, None)]
    assert [r.video_id for r in rank(items, min_score=6, ai_available=True)] == [2, 4, 1]


def test_rank_without_ai_uses_views_and_keeps_unknown_last():
    items = [item(1, views=10), item(2, views=None), item(3, views=500)]
    assert [r.video_id for r in rank(items, min_score=6, ai_available=False)] == [3, 1, 2]


def test_quota_caps_each_category_and_total():
    quota = CategoryQuota(total=3, per_category=2)
    assert quota.wants("foam")
    quota.take("foam")
    quota.take("foam")
    assert not quota.wants("foam")
    assert quota.wants("legs")
    quota.take("legs")
    assert quota.full
    assert not quota.wants("fabric")


def test_quota_never_caps_unknown_category():
    quota = CategoryQuota(total=3, per_category=1)
    quota.take(None)
    quota.take(None)
    assert quota.wants(None)
