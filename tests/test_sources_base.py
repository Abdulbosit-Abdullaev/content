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


def test_to_url_accepts_only_web_links():
    from contentbot.sources.base import to_url

    assert to_url(" https://cdn.example/v.mp4 ") == "https://cdn.example/v.mp4"
    assert to_url(["https://cdn.example/v.mp4"]) is None
    assert to_url({"url": "https://x"}) is None
    assert to_url("javascript:alert(1)") is None
    assert to_url(None) is None


def test_parse_all_skips_items_that_crash_the_parser():
    from contentbot.sources.base import parse_all

    def parser(item):
        if item == "bad":
            raise TypeError("odd field")
        return item

    assert parse_all(parser, ["a", "bad", None, "b"]) == ["a", "b"]
