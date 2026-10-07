from datetime import UTC

from contentbot.models import CATEGORIES, AudioMode, Status, utc_now
from tests.factories import make_candidate


def test_status_values_are_plain_strings():
    assert Status.IN_REVIEW == "in_review"
    assert Status("approved") is Status.APPROVED


def test_audio_modes():
    assert [m.value for m in AudioMode] == ["original", "mute", "music"]


def test_candidate_defaults():
    c = make_candidate(media_url=None, views=None)
    assert c.media_url is None
    assert c.views is None
    assert c.platform == "youtube"


def test_categories_end_with_other():
    assert len(CATEGORIES) == 10
    assert CATEGORIES[-1] == "other"


def test_utc_now_is_aware():
    assert utc_now().tzinfo is UTC
