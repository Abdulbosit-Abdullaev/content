from datetime import UTC, datetime

from contentbot.sources.base import as_dict, as_hashtag, as_query, parse_datetime, to_float, to_int, unique


def test_query_and_hashtag_forms():
    assert as_query("#upholstery") == "upholstery"
    assert as_hashtag("#перетяжка мебели") == "перетяжкамебели"
    assert as_hashtag("koltuk döşeme") == "koltukdöşeme"
    assert unique(["a", "", "b", "a"]) == ["a", "b"]
    assert as_dict("broken") == {}


def test_numbers_are_parsed_leniently():
    assert to_int("2300") == 2300
    assert to_int("12.7") == 12
    assert to_int(None) is None
    assert to_int("abc") is None
    assert to_int(True) is None
    assert to_int("inf") is None
    assert to_float("21.5") == 21.5
    assert to_float({}) is None


def test_datetime_formats():
    assert parse_datetime("2026-09-30T08:00:00.000Z") == datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    assert parse_datetime("Mon, 29 Sep 2026 10:00:00 +0000") == datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
    assert parse_datetime(1790000000) == datetime.fromtimestamp(1790000000, tz=UTC)
    assert parse_datetime("2026-09-30T08:00:00").tzinfo is UTC
    assert parse_datetime("garbage") is None
    assert parse_datetime(None) is None
