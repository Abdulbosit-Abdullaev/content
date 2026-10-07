import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from contentbot.db import Database, from_db, to_db
from contentbot.models import Status
from tests.factories import make_candidate, make_video

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def test_datetime_round_trip_is_utc():
    tashkent = datetime(2026, 10, 7, 8, 0, tzinfo=ZoneInfo("Asia/Tashkent"))
    assert to_db(tashkent) == "2026-10-07T03:00:00+00:00"
    assert from_db(to_db(tashkent)) == tashkent
    with pytest.raises(ValueError):
        to_db(datetime(2026, 10, 7, 8, 0))


def test_insert_get_and_exists(db):
    video_id = db.insert_video(make_candidate(platform_id="abc", views=123), Status.FOUND, NOW)
    v = db.get_video(video_id)
    assert v.platform_id == "abc" and v.views == 123
    assert v.status is Status.FOUND
    assert v.found_at == NOW
    assert db.video_exists("youtube", "abc")
    assert not db.video_exists("tiktok", "abc")
    assert v.to_candidate().platform_id == "abc"


def test_same_platform_id_cannot_be_inserted_twice(db):
    db.insert_video(make_candidate(), Status.FOUND, NOW)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_video(make_candidate(), Status.FOUND, NOW)


def test_update_video_converts_types_and_rejects_unknown_columns(db):
    v = make_video(db)
    slot = NOW + timedelta(hours=2)
    db.update_video(v.id, status=Status.APPROVED, slot_at=slot, caption_body="Salom")
    v = db.get_video(v.id)
    assert v.status is Status.APPROVED and v.slot_at == slot and v.caption_body == "Salom"
    with pytest.raises(ValueError):
        db.update_video(v.id, platform="tiktok")


def test_due_for_posting_and_slots(db):
    due = make_video(db, platform_id="a", status=Status.APPROVED, slot_at=NOW - timedelta(minutes=1))
    make_video(db, platform_id="b", status=Status.APPROVED, slot_at=NOW + timedelta(hours=3))
    make_video(db, platform_id="c", status=Status.IN_REVIEW)
    assert [v.id for v in db.due_for_posting(NOW)] == [due.id]
    assert sorted(db.approved_slots()) == [NOW - timedelta(minutes=1), NOW + timedelta(hours=3)]
    assert [v.platform_id for v in db.approved_in_order()] == ["a", "b"]
    assert db.count_with_status(Status.APPROVED) == 2


def test_review_sent_before(db):
    old = make_video(db, platform_id="old", review_sent_at=NOW - timedelta(hours=50))
    make_video(db, platform_id="new", review_sent_at=NOW - timedelta(hours=1))
    assert [v.id for v in db.review_sent_before(NOW - timedelta(hours=48))] == [old.id]


def test_seed_keywords_only_once_and_detects_hashtags(db):
    assert db.seed_keywords({"en": ["sofa", "#upholstery"]}) == 2
    assert db.seed_keywords({"en": ["other"]}) == 0
    kinds = {k.text: k.kind for k in db.list_keywords()}
    assert kinds == {"sofa": "query", "#upholstery": "hashtag"}


def test_pick_keywords_rotates_and_mixes_languages(db):
    db.seed_keywords({"en": ["a", "b", "c"], "ru": ["x", "y"]})
    first = db.pick_keywords(3)
    assert [k.text for k in first] == ["a", "x", "b"]
    db.mark_keywords_used([k.id for k in first], NOW)
    assert [k.text for k in db.pick_keywords(3)] == ["c", "y", "a"]


def test_add_and_deactivate_keyword(db):
    assert db.add_keyword("divan", "uz") is True
    assert db.add_keyword("divan", "uz") is False
    assert db.deactivate_keyword("divan") is True
    assert db.deactivate_keyword("divan") is False
    assert db.add_keyword("divan", "uz") is True  # reactivated
    assert [k.text for k in db.list_keywords()] == ["divan"]


def test_music_tracks_and_sync(db, tmp_path):
    (tmp_path / "calm.mp3").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not music")
    assert db.sync_music_dir(tmp_path, NOW) == 1
    assert db.sync_music_dir(tmp_path, NOW) == 0
    track = db.list_tracks()[0]
    assert track.title == "calm" and track.last_used_at is None
    assert db.add_track(track.file_path, "again", NOW) == track.id
    db.mark_track_used(track.id, NOW)
    assert db.get_track(track.id).last_used_at == NOW
    assert db.sync_music_dir(tmp_path / "missing", NOW) == 0


def test_runs_and_apify_spend(db):
    old = db.start_run("schedule", NOW - timedelta(days=40))
    db.finish_run(old, {"apify_cost_usd": 9.0}, NOW - timedelta(days=40))
    first = db.start_run("schedule", NOW - timedelta(days=1))
    db.finish_run(first, {"apify_cost_usd": 0.5, "found": {"youtube": 3}}, NOW)
    second = db.start_run("manual", NOW)
    db.finish_run(second, {"apify_cost_usd": 1.0}, NOW)
    assert db.apify_spend_since(NOW - timedelta(days=5)) == 1.5
    last = db.last_run()
    assert last.trigger == "manual" and last.summary == {"apify_cost_usd": 1.0}


def test_caption_edits_and_meta(db):
    db.add_caption_edit(555, 7)
    assert db.caption_edit_video(555) == 7
    db.remove_caption_edit(555)
    assert db.caption_edit_video(555) is None
    assert db.get_meta("k") is None
    db.set_meta("k", "v1")
    db.set_meta("k", "v2")
    assert db.get_meta("k") == "v2"


def test_backup_to_copies_data(db, tmp_path):
    make_video(db, platform_id="kept")
    target = tmp_path / "copy.db"
    db.backup_to(target)
    copy = Database(target)
    assert copy.video_exists("youtube", "kept")
    copy.close()
