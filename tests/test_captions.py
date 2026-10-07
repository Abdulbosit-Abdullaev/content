from contentbot.pipeline.captions import CAPTION_LIMIT, build_caption, caption_overflow, trim_body, utf16_len

FOOTER = "Aloqa uchun:\n+998557770007"  # 26 UTF-16 units


def test_build_caption_joins_body_and_footer():
    assert build_caption("  Salom!  ", FOOTER) == "Salom!\n\n" + FOOTER


def test_build_caption_without_body_is_just_footer():
    assert build_caption("   ", FOOTER) == FOOTER


def test_utf16_len_counts_emoji_as_two():
    assert utf16_len("🛋") == 2
    assert utf16_len("Диван") == 5
    assert utf16_len("abc") == 3


def test_overflow_counts_like_telegram():
    body = "🛋" * 500  # 500 Python characters, 1000 UTF-16 units
    assert len(build_caption(body, FOOTER)) < CAPTION_LIMIT
    assert caption_overflow(body, FOOTER) == 4  # 1000 + 2 + 26 - 1024


def test_body_that_fits_has_no_overflow():
    assert caption_overflow("Salom", FOOTER) == 0


def test_trim_body_keeps_short_text():
    assert trim_body("Qisqa matn.") == "Qisqa matn."


def test_trim_body_cuts_at_sentence_end():
    out = trim_body(("Birinchi gap. " * 30) + "Oxirgi", limit=100)
    assert utf16_len(out) <= 100
    assert out.endswith(".")


def test_trim_body_without_sentence_end_adds_ellipsis():
    out = trim_body("a" * 300, limit=100)
    assert out.endswith("…")
    assert utf16_len(out) == 100
