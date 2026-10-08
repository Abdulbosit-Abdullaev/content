from contentbot.pipeline.prefilter import FilterRules, dedupe, order_for_ai, passes, split
from tests.factories import make_candidate, make_settings

RULES = FilterRules(min_duration_s=5, max_duration_s=120, min_views={"youtube": 1000, "tiktok": 2000})


def test_rules_from_settings(tmp_path):
    rules = FilterRules.from_settings(make_settings(tmp_path))
    assert rules.max_duration_s == 120 and rules.min_views["tiktok"] == 2000


def test_duration_limits():
    assert passes(make_candidate(duration_s=120), RULES)
    assert not passes(make_candidate(duration_s=121), RULES)
    assert not passes(make_candidate(duration_s=3), RULES)
    assert passes(make_candidate(duration_s=None), RULES)  # checked again after download


def test_view_minimum_per_platform():
    assert not passes(make_candidate(platform="tiktok", views=1999), RULES)
    assert passes(make_candidate(platform="tiktok", views=2000), RULES)
    assert passes(make_candidate(platform="pinterest", views=None), RULES)
    assert passes(make_candidate(platform="youtube", views=None), RULES)


def test_split_keeps_order():
    a = make_candidate(platform_id="a")
    b = make_candidate(platform_id="b", duration_s=500)
    c = make_candidate(platform_id="c")
    passed, dropped = split([a, b, c], RULES)
    assert passed == [a, c] and dropped == [b]


def test_dedupe_keeps_first_copy():
    first = make_candidate(platform_id="x", title="first")
    again = make_candidate(platform_id="x", title="again")
    other = make_candidate(platform="tiktok", platform_id="x")
    assert dedupe([first, again, other]) == [first, other]


def test_order_for_ai_interleaves_platforms():
    yt = [make_candidate(platform="youtube", platform_id=f"y{i}", views=v) for i, v in enumerate([10, 900, 50])]
    pin = [make_candidate(platform="pinterest", platform_id=f"p{i}", views=None) for i in range(2)]
    ordered = order_for_ai(yt + pin)
    assert [c.platform_id for c in ordered] == ["y1", "p0", "y2", "p1", "y0"]


def test_order_for_ai_gives_weighted_platforms_more_turns():
    yt = [make_candidate(platform="youtube", platform_id=f"y{i}", views=1000 - i) for i in range(3)]
    pin = [make_candidate(platform="pinterest", platform_id=f"p{i}", views=None) for i in range(4)]
    ordered = order_for_ai(yt + pin, {"pinterest": 2})
    assert [c.platform_id for c in ordered] == ["y0", "p0", "p1", "y1", "p2", "p3", "y2"]
