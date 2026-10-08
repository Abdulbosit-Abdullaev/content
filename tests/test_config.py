from datetime import time

import pytest

from contentbot.config import ConfigError, load_secrets, load_settings, parse_hhmm
from tests.factories import ROOT

SECRET_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "REVIEW_CHAT_ID",
    "CHANNEL_ID",
    "ANTHROPIC_API_KEY",
    "YOUTUBE_API_KEY",
    "APIFY_TOKEN",
)


def test_real_settings_file_loads():
    s = load_settings(ROOT / "settings.yaml")
    assert s.timezone.key == "Asia/Tashkent"
    assert s.search_time == time(7, 0)
    assert s.post_times == (time(10), time(13), time(16), time(19))
    assert (s.min_duration_s, s.max_duration_s) == (5, 120)
    assert s.candidates_per_day == 8
    assert s.max_per_category == 2
    assert s.ai_model == "claude-opus-5-5"
    assert set(s.apify_actors) == {"tiktok", "instagram", "pinterest"}
    assert s.apify_actors["tiktok"].actor_id == "clockworks/tiktok-scraper"
    assert set(s.seed_keywords) == {"en", "ru", "tr", "zh"}  # no Uzbek: it finds local shop ads
    assert "electric recliner sofa" in s.seed_keywords["en"]
    assert len(s.caption_examples) == 6
    assert s.data_dir == (ROOT / "data").resolve()


def test_footer_has_only_the_two_confirmed_phones():
    footer = load_settings(ROOT / "settings.yaml").footer
    assert "+998557770007" in footer
    assert "+998991330007" in footer
    assert "+998-95" not in footer
    assert "+998991550007" not in footer
    assert "@evim_uzb" in footer


def test_parse_hhmm_accepts_quoted_time():
    assert parse_hhmm("07:30") == time(7, 30)


def test_parse_hhmm_rejects_unquoted_yaml_time():
    # YAML reads an unquoted 07:00 as the number 420.
    with pytest.raises(ConfigError):
        parse_hhmm(420)


def test_missing_key_gives_clear_error(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("timezone: Asia/Tashkent\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="missing"):
        load_settings(path)


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")


def test_load_secrets_reports_every_missing_variable(monkeypatch, tmp_path):
    for var in SECRET_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN.*APIFY_TOKEN"):
        load_secrets(tmp_path / "missing.env")


def test_load_secrets_reads_environment(monkeypatch):
    values = dict(zip(SECRET_VARS, ("1:abc", "-100123", "@test", "k", "y", "a"), strict=True))
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    secrets = load_secrets(None)
    assert secrets.review_chat_id == -100123
    assert secrets.channel_id == "@test"


def test_review_chat_id_must_be_a_number(monkeypatch):
    values = dict(zip(SECRET_VARS, ("1:abc", "my-group", "@test", "k", "y", "a"), strict=True))
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError, match="REVIEW_CHAT_ID"):
        load_secrets(None)


def test_env_file_wins_over_inherited_environment(monkeypatch, tmp_path):
    for var in SECRET_VARS:
        monkeypatch.setenv(var, "-1" if var == "REVIEW_CHAT_ID" else "from-environment")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=from-file\n", encoding="utf-8")
    assert load_secrets(env_file).telegram_bot_token == "from-file"


def test_anthropic_key_is_optional(monkeypatch):
    values = dict(zip(SECRET_VARS, ("1:abc", "-100123", "@test", "", "y", "a"), strict=True))
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert load_secrets(None).anthropic_api_key is None


def test_tiktok_is_off_because_it_is_blocked_in_uzbekistan():
    assert load_settings(ROOT / "settings.yaml").enabled_sources == ("youtube", "instagram", "pinterest")


def test_unknown_source_is_a_config_error(tmp_path):
    text = (ROOT / "settings.yaml").read_text(encoding="utf-8").replace(
        "enabled_sources: [youtube, instagram, pinterest]", "enabled_sources: [youtube, facebook]"
    )
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="facebook"):
        load_settings(path)


def test_pinterest_gets_double_turns_and_more_results():
    s = load_settings(ROOT / "settings.yaml")
    assert s.source_weights == {"pinterest": 2}
    assert s.apify_actors["pinterest"].max_results == 40
