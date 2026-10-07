import pytest

from contentbot import texts
from contentbot.bot.keyboards import (
    MusicCb,
    VideoCb,
    approved_keyboard,
    music_ask_keyboard,
    post_link,
    posted_keyboard,
    review_keyboard,
    status_keyboard,
)
from contentbot.db import Database
from contentbot.models import CATEGORIES, PLATFORMS
from tests.factories import make_video


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def texts_of(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_texts_cover_every_category_and_platform():
    assert set(texts.CATEGORY_NAMES) == set(CATEGORIES)
    assert set(texts.PLATFORM_NAMES) == set(PLATFORMS)


def test_review_keyboard_marks_active_sound(db):
    v = make_video(db, ai_score=8, ai_category="mechanism")
    markup = review_keyboard(v)
    rows = texts_of(markup)
    assert rows[0] == [texts.BTN_ORIGINAL + texts.CHECK, texts.BTN_MUTE, texts.BTN_MUSIC]
    assert rows[1] == [texts.BTN_EDIT]
    assert rows[2] == [texts.BTN_APPROVE, texts.BTN_REJECT]
    assert rows[3] == ["🔗 Manba: YouTube · 8/10 · mexanizm"]
    assert markup.inline_keyboard[3][0].url == v.url


def test_music_mode_shows_another_track_button(db):
    v = make_video(db, audio_mode="music")
    rows = texts_of(review_keyboard(v))
    assert rows[0][2] == texts.BTN_MUSIC + texts.CHECK
    assert rows[1] == [texts.BTN_EDIT, texts.BTN_TRACK]


def test_callback_data_round_trip(db):
    v = make_video(db)
    approve = review_keyboard(v).inline_keyboard[2][0]
    assert VideoCb.unpack(approve.callback_data) == VideoCb(action="ok", vid=v.id)


def test_source_without_ai_score(db):
    v = make_video(db, platform="pinterest")
    assert texts_of(review_keyboard(v))[3] == [f"🔗 Manba: Pinterest · {texts.NO_SCORE} · boshqa"]


def test_status_keyboards(db):
    v = make_video(db)
    assert texts_of(approved_keyboard(v, "13:00"))[0] == ["✅ 13:00 da joylanadi", texts.BTN_CANCEL]
    posted = posted_keyboard(v, "13:00", "https://t.me/evim_uzb/5")
    assert posted.inline_keyboard[0][0].url == "https://t.me/evim_uzb/5"
    assert posted.inline_keyboard[0][0].text == "📢 13:00 da joylandi"
    assert texts_of(status_keyboard(v, texts.EXPIRED))[0] == [texts.EXPIRED]
    ask = music_ask_keyboard(42).inline_keyboard[0]
    assert [b.text for b in ask] == [texts.BTN_YES, texts.BTN_NO]
    assert MusicCb.unpack(ask[0].callback_data) == MusicCb(action="add", msg=42)


def test_post_link():
    assert post_link("@evim_uzb", 55) == "https://t.me/evim_uzb/55"
    assert post_link("-1001234567890", 7) == "https://t.me/c/1234567890/7"
