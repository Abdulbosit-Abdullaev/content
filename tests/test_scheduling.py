from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from contentbot.scheduling import next_free_slot, when_label

TZ = ZoneInfo("Asia/Tashkent")
TIMES = (time(10), time(13), time(16), time(19))


def local(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def test_first_free_slot_today():
    assert next_free_slot(local(7, 9), TIMES, [], TZ) == local(7, 10)


def test_taken_slot_is_skipped():
    assert next_free_slot(local(7, 9), TIMES, [local(7, 10)], TZ) == local(7, 13)


def test_after_last_slot_rolls_to_tomorrow():
    assert next_free_slot(local(7, 19, 30), TIMES, [], TZ) == local(8, 10)


def test_server_clock_in_utc_late_evening():
    utc_now = datetime(2026, 10, 7, 18, 30, tzinfo=UTC)  # 23:30 in Tashkent
    slot = next_free_slot(utc_now, TIMES, [], TZ)
    assert slot == local(8, 10)
    assert slot.tzinfo is UTC


def test_full_day_rolls_over():
    taken = [local(7, h) for h in (10, 13, 16, 19)]
    assert next_free_slot(local(7, 8), TIMES, taken, TZ) == local(8, 10)


def test_slot_exactly_now_is_not_used():
    assert next_free_slot(local(7, 10), TIMES, [], TZ) == local(7, 13)


def test_when_label():
    now = local(7, 8)
    assert when_label(local(7, 13), now, TZ) == "13:00"
    assert when_label(local(8, 10), now, TZ) == "08.10 10:00"
    assert when_label(datetime(2026, 10, 7, 8, 0, tzinfo=UTC), now, TZ) == "13:00"
