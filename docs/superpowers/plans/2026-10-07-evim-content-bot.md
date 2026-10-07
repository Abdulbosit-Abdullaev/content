# Evim Content Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Telegram bot that every morning finds short soft-furniture videos (YouTube Shorts, TikTok, Instagram Reels, Pinterest), lets a review group change the sound and caption and approve them, and posts approved videos to the Evim channel at fixed times.

**Architecture:** One asyncio Python process. aiogram runs the bot (long polling), APScheduler runs the daily search, the posting check every minute and an hourly cleanup. A pipeline goes search → free filter → Claude relevance check → pick → download → ffmpeg → Claude caption → review group. All state is in one SQLite file. Every external service sits behind a small class with a narrow interface, so tests use fakes and never touch the network.

**Tech Stack:** Python ≥ 3.11, aiogram 3.31, APScheduler 3.11, anthropic 1.11 (Claude `claude-opus-5-5`), httpx 0.28, yt-dlp, ffmpeg/ffprobe, SQLite, Pillow, PyYAML, python-dotenv. Tests: pytest 9 + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-10-07-telegram-content-bot-design.md`

## Global Constraints

- Python ≥ 3.11 (uses `enum.StrEnum`, `datetime.UTC`). The dev machine has 3.13; the server needs 3.11+.
- Timezone for all schedules: `Asia/Tashkent`. Store every datetime in SQLite as UTC ISO-8601 (`to_db`/`from_db` in `contentbot/db.py`).
- Video length: `5 ≤ duration ≤ 120` seconds.
- Default counts: 8 candidates/day, at most 2 per category, posting slots `10:00, 13:00, 16:00, 19:00`, review expiry 48 h, AI check limit 30, minimum AI score 6.
- Captions: Uzbek **Latin** script; body + `"\n\n"` + footer; at most **1024 UTF-16 units** total; AI body at most **500** units; sent as **plain text** (no parse mode).
- Footer phones are exactly `+998557770007` and `+998991330007`.
- All bot texts are Uzbek Latin and live only in `contentbot/texts.py`.
- Claude: model from settings (default `claude-opus-5-5`), call `client.beta.messages.create` with `betas=["server-side-fallback-2026-07-01"]`, `extra_body={"fallbacks": "default"}`, `output_config={"effort": ..., "format": {"type": "json_schema", "schema": ...}}`. Check `stop_reason == "end_turn"` before reading the text.
- Telegram uploads: rendered videos must be ≤ 49 MB, H.264 + AAC, `+faststart`.
- Apify: call `POST https://api.apify.com/v2/acts/{owner~name}/run-sync-get-dataset-items` with `Authorization: Bearer <token>`, `maxItems`, `maxTotalChargeUsd`. Monthly spend is estimated as `items × price_per_1000 / 1000`.
- Never call real network services in tests (respx mocks HTTP; fakes replace Claude, Telegram and ffmpeg; real-ffmpeg tests are skipped when ffmpeg is missing).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Captions full of emojis or Cyrillic near the 1,024 limit.** Telegram counts UTF-16 units, so 520 emojis (1,040 units) must be refused by the bot with "Matn N belgiga uzun", not by Telegram. *Test: Task 5 `test_overflow_counts_like_telegram`.*
2. **Two reviewers tap ✅ on the same video at the same moment.** Expect exactly one approval and one slot, with the second tap getting "already handled". *Test: Task 13 `test_double_approve_gives_one_slot`.*
3. **Server clock in UTC while the schedule is Tashkent time.** An approval at 23:30 Tashkent (18:30 UTC) must go to tomorrow 10:00 Tashkent, not today. *Test: Task 11 `test_server_clock_in_utc_late_evening`.*
4. **Scraper items with missing or oddly typed fields** (no id, `playCount` as a string, `videoMeta` not an object). Expect that one item to be skipped or parsed leniently, never the whole source crashing. *Tests: Task 10 `test_parse_tiktok_item_tolerates_odd_types`, `test_parse_tiktok_item_without_id_is_skipped`; Task 9 `test_parse_video_item_skips_missing_id`.*
5. **An Instagram signed video URL that has expired (HTTP 403) or returns a login HTML page.** Expect a fallback to yt-dlp on the post's page URL. *Tests: Task 8 `test_expired_signed_url_falls_back_to_ytdlp`, `test_html_instead_of_video_falls_back`.*

## File Structure

```
contentBot/                        (repo root = C:\Users\Zuc\Desktop\contentBot)
  main.py                          entry point: run bot, or --dry-run
  settings.yaml                    owner-editable settings (times, counts, keywords, footer, model, actors)
  .env.example                     list of secrets to fill in
  requirements.txt / requirements-dev.txt / pyproject.toml (pytest config only)
  README.md                        setup guide for the owner
  deploy/contentbot.service        optional systemd unit
  deploy/update-ytdlp.sh           weekly yt-dlp update
  music/.gitkeep                   calm tracks go here (not committed)
  contentbot/
    models.py                      Status, AudioMode, CATEGORIES, Candidate, Score, Keyword, utc_now
    config.py                      Settings, Secrets, load_settings, load_secrets
    db.py                          Database (SQLite), VideoRow, MusicTrack, RunInfo
    texts.py                       every user-visible string (Uzbek Latin)
    scheduling.py                  next_free_slot, when_label
    ai/claude.py                   ClaudeJSON: one structured-output call → dict | None
    ai/relevance.py                RelevanceChecker: preview + text → Score per candidate
    ai/caption_writer.py           CaptionWriter: Uzbek Latin caption draft
    sources/base.py                SourceResult, Source protocol, parsing helpers
    sources/youtube.py             YouTubeSource (Data API v3)
    sources/apify.py               ApifyRunner, cost helpers
    sources/tiktok.py              TikTokSource
    sources/instagram.py           InstagramSource
    sources/pinterest.py           PinterestSource
    pipeline/prefilter.py          FilterRules, dedupe, split, order_for_ai
    pipeline/picker.py             Ranked, rank, CategoryQuota
    pipeline/captions.py           utf16_len, build_caption, caption_overflow, trim_body
    pipeline/media.py              Media (ffprobe/ffmpeg), build_render_args
    pipeline/downloader.py         Downloader (direct URL or yt-dlp)
    pipeline/discover.py           discover(): run sources concurrently, isolate failures
    pipeline/run.py                Pipeline (one daily run), PrintSink (dry run)
    bot/keyboards.py               callback data + inline keyboards
    bot/review.py                  ReviewService + review router
    bot/publisher.py               Publisher: post due videos, retries, missed slots
    bot/commands.py                CommandService + commands router (/queue /search /status /keywords /addkw /delkw, music upload)
  tests/
    factories.py                   make_candidate, make_settings, make_secrets, make_video
    fakes.py                       FakeClaudeClient, FakeClaudeJSON, FakeBot, FakeMedia, FakePipeline
    media_helpers.py               ffmpeg test-clip helpers
    test_*.py                      one test file per module
```

## Working Conventions

- **Working directory:** `C:\Users\Zuc\Desktop\contentBot` on the dev machine.
- **Virtual environment:** created in Task 1. Before running any command, activate it: Windows PowerShell `.venv\Scripts\Activate.ps1`, Linux `source .venv/bin/activate`. All commands below then use plain `python`.
- **Running tests:** `python -m pytest <path> -v`.

---

### Task 1: Project scaffold, dependencies, shared models

**Files:**
- Create: `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `music/.gitkeep`
- Create: `contentbot/__init__.py`, `contentbot/ai/__init__.py`, `contentbot/sources/__init__.py`, `contentbot/pipeline/__init__.py`, `contentbot/bot/__init__.py` (all empty)
- Create: `contentbot/models.py`
- Create: `tests/__init__.py` (empty), `tests/factories.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Status` (StrEnum): `FOUND, FILTERED_OUT, SCORED, SELECTED, IN_REVIEW, APPROVED, POSTED, REJECTED, EXPIRED, FAILED`, with values `"found"`, `"filtered_out"`, and so on.
  - `AudioMode` (StrEnum): `ORIGINAL="original"`, `MUTE="mute"`, `MUSIC="music"`.
  - `CATEGORIES: tuple[str, ...]` (10 items, last is `"other"`) and `PLATFORMS`.
  - `Candidate` dataclass: `platform, platform_id, url, media_url=None, thumbnail_url=None, title="", description="", author="", duration_s=None, views=None, published_at=None`.
  - `Score(score:int, category:str, competitor_branding:bool, reason:str)`, frozen.
  - `Keyword(id:int, text:str, language:str, kind:str)`, frozen.
  - `utc_now() -> datetime` (aware, UTC).
  - `tests.factories.make_candidate(**overrides) -> Candidate`.

- [ ] **Step 1: Initialise git and the virtual environment**

```bash
git init
python -m venv .venv
```

Activate the venv (Windows: `.venv\Scripts\Activate.ps1`), then create the files below.

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/
music/*
!music/.gitkeep
```

`requirements.txt`:
```text
aiogram==3.31.0
APScheduler==3.11.3
anthropic==1.11.0
httpx==0.28.1
yt-dlp==2026.8.19
PyYAML==6.0.3
python-dotenv==1.2.4
Pillow==12.3.0
tzdata>=2024.1
```

`requirements-dev.txt`:
```text
-r requirements.txt
pytest==9.1.1
pytest-asyncio==1.4.0
respx==0.23.1
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
pythonpath = ["."]
markers = ["ffmpeg: needs ffmpeg and ffprobe on PATH"]
```

Create the empty files `music/.gitkeep`, `contentbot/__init__.py`, `contentbot/ai/__init__.py`, `contentbot/sources/__init__.py`, `contentbot/pipeline/__init__.py`, `contentbot/bot/__init__.py` and `tests/__init__.py`.

Run: `python -m pip install -r requirements-dev.txt`
Expected: installs without errors.

Run: `python -c "import inspect, anthropic; print('output_config' in inspect.signature(anthropic.AsyncAnthropic(api_key='x').beta.messages.create).parameters)"`
Expected: `True`. If it prints `False`, stop and report it: Task 6 must then pass `output_config` through `extra_body` as well.

- [ ] **Step 2: Write the failing test**

`tests/factories.py`:
```python
"""Test data builders shared by many test files."""
from __future__ import annotations

from contentbot.models import Candidate


def make_candidate(**overrides) -> Candidate:
    values = dict(
        platform="youtube",
        platform_id="vid1",
        url="https://www.youtube.com/shorts/vid1",
        media_url=None,
        thumbnail_url="https://img.example/1.jpg",
        title="Sofa mechanism",
        description="#sofa #mechanism",
        author="maker",
        duration_s=30.0,
        views=5000,
        published_at=None,
    )
    values.update(overrides)
    return Candidate(**values)
```

`tests/test_models.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.models'`

- [ ] **Step 4: Write the implementation**

`contentbot/models.py`:
```python
"""Shared data types used across the bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Status(StrEnum):
    FOUND = "found"
    FILTERED_OUT = "filtered_out"
    SCORED = "scored"
    SELECTED = "selected"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class AudioMode(StrEnum):
    ORIGINAL = "original"
    MUTE = "mute"
    MUSIC = "music"


CATEGORIES: tuple[str, ...] = (
    "mechanism",
    "foam",
    "fabric",
    "leather",
    "legs",
    "tools",
    "fittings",
    "upholstery_work",
    "finished_furniture",
    "other",
)

PLATFORMS: tuple[str, ...] = ("youtube", "tiktok", "instagram", "pinterest")


@dataclass
class Candidate:
    """A video found by a source, before it is stored."""

    platform: str
    platform_id: str
    url: str
    media_url: str | None = None
    thumbnail_url: str | None = None
    title: str = ""
    description: str = ""
    author: str = ""
    duration_s: float | None = None
    views: int | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class Score:
    """Claude's verdict for one candidate."""

    score: int
    category: str
    competitor_branding: bool
    reason: str


@dataclass(frozen=True)
class Keyword:
    id: int
    text: str
    language: str
    kind: str  # "query" or "hashtag"


def utc_now() -> datetime:
    return datetime.now(UTC)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements.txt requirements-dev.txt pyproject.toml music/.gitkeep contentbot tests docs
git commit -m "chore: scaffold project, dependencies and shared models" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settings and secrets

**Files:**
- Create: `settings.yaml`, `contentbot/config.py`
- Modify: `tests/factories.py` (full new content below)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ConfigError(Exception)`.
  - `ApifyActor(actor_id:str, price_per_1000:float, max_results:int)`.
  - `Settings` (frozen dataclass) with these fields: `timezone: ZoneInfo, search_time: time, post_times: tuple[time,...], candidates_per_day: int, max_per_category: int, review_expiry_hours: int, min_duration_s: float, max_duration_s: float, min_views: dict[str,int], keywords_per_run: int, youtube_per_keyword: int, ai_check_limit: int, min_ai_score: int, ai_model: str, apify_monthly_budget_usd: float, apify_actors: dict[str, ApifyActor], ytdlp_cookies_file: str|None, ffmpeg_path: str, ffprobe_path: str, footer: str, caption_examples: tuple[str,...], seed_keywords: dict[str, list[str]], data_dir: Path, music_dir: Path`.
  - `Secrets(telegram_bot_token, review_chat_id:int, channel_id, anthropic_api_key, youtube_api_key, apify_token)`.
  - `parse_hhmm(value) -> time`, `load_settings(path) -> Settings`, `load_secrets(env_file=".env") -> Secrets`.
  - `tests.factories.make_settings(tmp_path, **overrides) -> Settings` and `make_secrets(**overrides) -> Secrets`.

- [ ] **Step 1: Write `settings.yaml`**

`settings.yaml`:
```yaml
# Evim content bot settings. Times are Tashkent time, always in quotes: "HH:MM".
timezone: Asia/Tashkent
search_time: "07:00"
post_times: ["10:00", "13:00", "16:00", "19:00"]

candidates_per_day: 8        # videos sent to the review group each day
max_per_category: 2          # at most this many from one category per day
review_expiry_hours: 48      # untouched candidates are removed after this

duration:
  min_s: 5
  max_s: 120

min_views:                   # Pinterest has no view counts
  youtube: 1000
  tiktok: 2000
  instagram: 1000

keywords_per_run: 6          # keywords used by each search (rotating)
youtube_per_keyword: 5       # YouTube results per keyword
ai_check_limit: 30           # candidates sent to the AI check per run
min_ai_score: 6              # 0-10; lower scores are never sent for review

ai:
  model: claude-opus-5-5     # or claude-sonnet-5-5 (about half the cost)

apify:
  monthly_budget_usd: 5      # Apify sources pause when this month's estimate reaches it
  actors:
    tiktok:
      actor_id: clockworks/tiktok-scraper
      price_per_1000: 1.70
      max_results: 20
    instagram:
      actor_id: apify/instagram-hashtag-scraper
      price_per_1000: 2.60
      max_results: 20
    pinterest:
      actor_id: cirkit/pinterest-pins-scraper
      price_per_1000: 2.00
      max_results: 30

ytdlp_cookies_file: null     # path to a cookies.txt if YouTube blocks the server
ffmpeg_path: ffmpeg
ffprobe_path: ffprobe
data_dir: data
music_dir: music

footer: |
  ☎️ Aloqa uchun:
  +998557770007
  +998991330007

  📍 Manzil: Toshkent shahar, Kichik Halqa Yo'li, 151A/1
  🕑 Ish vaqti: 08:00-19:00

  📢 Telegram: @evim_uzb
  📸 Instagram: instagram.com/evim_uzb

caption_examples:
  - |-
    #mexanizm Eng eksklyuziv mexanizmlar Evimda! Puma — 3 bosqichda ochiluvchi, divan hamda krovat vazifasini bajarishga yordam beradigan mexanizm
  - |-
    #lentali_klipsa Evimda lentali klipsa! Ushbu plastmast lentali klipsalar orqali ishlaringiz karrasiga tezlashadi!
  - |-
    Trenddagi yog’och ножкалар

    2026-yildagi eng trendda yurgan mebel oyoqlari aynan yog’ochlilar hisoblanadi.

    Evim sizning trenddan qolib ketmasligingizni ta’minlaydi!
  - |-
    2026-yilga kelib tabiiylik urfga kirdi!

    Mebellar uchun yog'och oyoqlar barcha mebel xarid qiluvchilarda katta qiziqish uyg'otdi. Bu qiziqishlar uchun Evim sizga keng tanlov imkoniyatlarini beradi!

    Shunchaki keling, ko'ring, yoqqanini xarid qiling!
  - |-
    Diary - "mishkovina", ya'ni qopdek to'qish orqali tikilgan qattiq mato!
  - |-
    Novella ✨

    Haqiqiy nafislik na'munasi!

seed_keywords:
  en:
    - sofa mechanism
    - sofa bed mechanism
    - recliner mechanism
    - upholstery
    - sofa upholstery
    - furniture foam
    - upholstery fabric
    - sofa legs
    - furniture legs
    - "#upholstery"
    - "#sofabed"
    - "#upholsterywork"
  ru:
    - перетяжка дивана
    - механизм дивана
    - механизм еврокнижка
    - поролон для мебели
    - мебельная ткань
    - ножки для дивана
    - обивка мебели
    - "#перетяжкамебели"
  tr:
    - koltuk döşeme
    - kanepe mekanizması
    - mobilya ayağı
    - döşemelik kumaş
    - mobilya süngeri
    - "#döşeme"
  uz:
    - mebel oyoqlari
    - yumshoq mebel
    - divan ishlab chiqarish
    - "#mebel"
  zh:
    - 沙发机构
    - 沙发面料
    - 沙发海绵
    - 沙发脚
    - 沙发制作
    - 软包
```

- [ ] **Step 2: Write the failing tests**

`tests/factories.py` (replace the whole file):
```python
"""Test data builders shared by many test files."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from contentbot.config import Secrets, Settings, load_settings
from contentbot.models import Candidate

ROOT = Path(__file__).resolve().parent.parent


def make_candidate(**overrides) -> Candidate:
    values = dict(
        platform="youtube",
        platform_id="vid1",
        url="https://www.youtube.com/shorts/vid1",
        media_url=None,
        thumbnail_url="https://img.example/1.jpg",
        title="Sofa mechanism",
        description="#sofa #mechanism",
        author="maker",
        duration_s=30.0,
        views=5000,
        published_at=None,
    )
    values.update(overrides)
    return Candidate(**values)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    settings = load_settings(ROOT / "settings.yaml")
    return dataclasses.replace(
        settings, data_dir=tmp_path / "data", music_dir=tmp_path / "music", **overrides
    )


def make_secrets(**overrides) -> Secrets:
    values = dict(
        telegram_bot_token="123456:TESTTOKEN",
        review_chat_id=-100111,
        channel_id="@test_channel",
        anthropic_api_key="test-key",
        youtube_api_key="yt-key",
        apify_token="apify-token",
    )
    values.update(overrides)
    return Secrets(**values)
```

`tests/test_config.py`:
```python
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
    assert set(s.seed_keywords) == {"en", "ru", "tr", "uz", "zh"}
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.config'`

- [ ] **Step 4: Write the implementation**

`contentbot/config.py`:
```python
"""Load settings.yaml (owner-editable) and .env (secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """settings.yaml or .env is missing something or has a bad value."""


@dataclass(frozen=True)
class ApifyActor:
    actor_id: str
    price_per_1000: float
    max_results: int


@dataclass(frozen=True)
class Settings:
    timezone: ZoneInfo
    search_time: time
    post_times: tuple[time, ...]
    candidates_per_day: int
    max_per_category: int
    review_expiry_hours: int
    min_duration_s: float
    max_duration_s: float
    min_views: dict[str, int]
    keywords_per_run: int
    youtube_per_keyword: int
    ai_check_limit: int
    min_ai_score: int
    ai_model: str
    apify_monthly_budget_usd: float
    apify_actors: dict[str, ApifyActor]
    ytdlp_cookies_file: str | None
    ffmpeg_path: str
    ffprobe_path: str
    footer: str
    caption_examples: tuple[str, ...]
    seed_keywords: dict[str, list[str]]
    data_dir: Path
    music_dir: Path


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str
    review_chat_id: int
    channel_id: str
    anthropic_api_key: str
    youtube_api_key: str
    apify_token: str


REQUIRED_KEYS = (
    "timezone",
    "search_time",
    "post_times",
    "candidates_per_day",
    "max_per_category",
    "review_expiry_hours",
    "duration",
    "min_views",
    "keywords_per_run",
    "youtube_per_keyword",
    "ai_check_limit",
    "min_ai_score",
    "ai",
    "apify",
    "footer",
    "caption_examples",
    "seed_keywords",
)

SECRET_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "REVIEW_CHAT_ID",
    "CHANNEL_ID",
    "ANTHROPIC_API_KEY",
    "YOUTUBE_API_KEY",
    "APIFY_TOKEN",
)


def parse_hhmm(value: Any) -> time:
    if not isinstance(value, str):
        raise ConfigError(f"Bad time {value!r}: write times in quotes, like \"07:00\"")
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"Bad time {value!r}: expected HH:MM") from exc


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"settings.yaml is missing: {', '.join(missing)}")
    base = path.resolve().parent
    try:
        actors = {
            str(name): ApifyActor(
                actor_id=str(actor["actor_id"]),
                price_per_1000=float(actor["price_per_1000"]),
                max_results=int(actor["max_results"]),
            )
            for name, actor in raw["apify"]["actors"].items()
        }
        return Settings(
            timezone=ZoneInfo(str(raw["timezone"])),
            search_time=parse_hhmm(raw["search_time"]),
            post_times=tuple(sorted(parse_hhmm(t) for t in raw["post_times"])),
            candidates_per_day=int(raw["candidates_per_day"]),
            max_per_category=int(raw["max_per_category"]),
            review_expiry_hours=int(raw["review_expiry_hours"]),
            min_duration_s=float(raw["duration"]["min_s"]),
            max_duration_s=float(raw["duration"]["max_s"]),
            min_views={str(k): int(v) for k, v in raw["min_views"].items()},
            keywords_per_run=int(raw["keywords_per_run"]),
            youtube_per_keyword=int(raw["youtube_per_keyword"]),
            ai_check_limit=int(raw["ai_check_limit"]),
            min_ai_score=int(raw["min_ai_score"]),
            ai_model=str(raw["ai"]["model"]),
            apify_monthly_budget_usd=float(raw["apify"]["monthly_budget_usd"]),
            apify_actors=actors,
            ytdlp_cookies_file=raw.get("ytdlp_cookies_file") or None,
            ffmpeg_path=str(raw.get("ffmpeg_path") or "ffmpeg"),
            ffprobe_path=str(raw.get("ffprobe_path") or "ffprobe"),
            footer=str(raw["footer"]).strip(),
            caption_examples=tuple(str(x).strip() for x in raw["caption_examples"]),
            seed_keywords={str(k): [str(x) for x in v] for k, v in raw["seed_keywords"].items()},
            data_dir=(base / str(raw.get("data_dir", "data"))).resolve(),
            music_dir=(base / str(raw.get("music_dir", "music"))).resolve(),
        )
    except KeyError as exc:
        raise ConfigError(f"settings.yaml is missing key: {exc}") from exc
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone {raw['timezone']!r}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"settings.yaml has a bad value: {exc}") from exc


def load_secrets(env_file: str | Path | None = ".env") -> Secrets:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    missing = [var for var in SECRET_VARS if not os.environ.get(var)]
    if missing:
        raise ConfigError("Missing in .env: " + ", ".join(missing))
    try:
        review_chat_id = int(os.environ["REVIEW_CHAT_ID"])
    except ValueError as exc:
        raise ConfigError("REVIEW_CHAT_ID must be a number like -1001234567890") from exc
    return Secrets(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        review_chat_id=review_chat_id,
        channel_id=os.environ["CHANNEL_ID"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        youtube_api_key=os.environ["YOUTUBE_API_KEY"],
        apify_token=os.environ["APIFY_TOKEN"],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py tests/test_models.py -v`
Expected: all passed (14 tests)

- [ ] **Step 6: Commit**

```bash
git add settings.yaml contentbot/config.py tests/factories.py tests/test_config.py
git commit -m "feat: load settings.yaml and .env secrets" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: SQLite database

**Files:**
- Create: `contentbot/db.py`
- Modify: `tests/factories.py` (add `make_video`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Candidate`, `Keyword`, `Status` from `contentbot.models`.
- Produces (all methods are synchronous):
  - `to_db(dt) -> str | None` and `from_db(s) -> datetime | None`. `to_db` raises `ValueError` for naive datetimes.
  - `MUSIC_SUFFIXES = {".mp3", ".m4a", ".aac", ".ogg", ".wav"}`.
  - `VideoRow` dataclass. Its fields are the `videos` columns (see the schema below); datetimes are aware and `status` is a `Status`. It has `to_candidate() -> Candidate`.
  - `MusicTrack(id, file_path, title, last_used_at)` and `RunInfo(started_at, finished_at, trigger, summary: dict)`.
  - `Database(path)`. Use `":memory:"` in tests. Methods:
    - Setup: `close()`, `backup_to(path)`.
    - Videos: `video_exists(platform, platform_id) -> bool`, `insert_video(c, status, now) -> int`, `get_video(id) -> VideoRow | None`, `update_video(id, **values)` (raises `ValueError` for unknown columns; accepts aware datetimes and enums), `videos_with_status(status) -> list[VideoRow]`, `count_with_status(status) -> int`.
    - Queue: `approved_slots() -> list[datetime]`, `approved_in_order() -> list[VideoRow]`, `due_for_posting(now) -> list[VideoRow]`, `review_sent_before(cutoff) -> list[VideoRow]`.
    - Keywords: `seed_keywords(by_language) -> int`, `add_keyword(text, language) -> bool`, `deactivate_keyword(text) -> bool`, `list_keywords() -> list[Keyword]`, `pick_keywords(count) -> list[Keyword]` (round-robin across languages, least recently used first), `mark_keywords_used(ids, now)`.
    - Music: `add_track(file_path, title, now) -> int`, `list_tracks() -> list[MusicTrack]`, `get_track(id)`, `mark_track_used(id, now)`, `sync_music_dir(music_dir, now) -> int`.
    - Runs: `start_run(trigger, now) -> int`, `finish_run(run_id, summary, now)`, `last_run() -> RunInfo | None`, `apify_spend_since(since) -> float`.
    - Caption edits: `add_caption_edit(prompt_message_id, video_id)`, `caption_edit_video(prompt_message_id) -> int | None`, `remove_caption_edit(prompt_message_id)`.
    - Meta: `get_meta(key) -> str | None`, `set_meta(key, value)`.
  - `tests.factories.make_video(db, *, now=None, status=Status.IN_REVIEW, **fields) -> VideoRow`. Candidate fields go to `make_candidate`; the other fields go to `update_video`.

- [ ] **Step 1: Write the failing tests**

`tests/factories.py` (replace the whole file):
```python
"""Test data builders shared by many test files."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from contentbot.config import Secrets, Settings, load_settings
from contentbot.db import Database, VideoRow
from contentbot.models import Candidate, Status

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent


def make_candidate(**overrides) -> Candidate:
    values = dict(
        platform="youtube",
        platform_id="vid1",
        url="https://www.youtube.com/shorts/vid1",
        media_url=None,
        thumbnail_url="https://img.example/1.jpg",
        title="Sofa mechanism",
        description="#sofa #mechanism",
        author="maker",
        duration_s=30.0,
        views=5000,
        published_at=None,
    )
    values.update(overrides)
    return Candidate(**values)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    settings = load_settings(ROOT / "settings.yaml")
    return dataclasses.replace(
        settings, data_dir=tmp_path / "data", music_dir=tmp_path / "music", **overrides
    )


def make_secrets(**overrides) -> Secrets:
    values = dict(
        telegram_bot_token="123456:TESTTOKEN",
        review_chat_id=-100111,
        channel_id="@test_channel",
        anthropic_api_key="test-key",
        youtube_api_key="yt-key",
        apify_token="apify-token",
    )
    values.update(overrides)
    return Secrets(**values)


def make_video(db: Database, *, now: datetime | None = None, status: Status = Status.IN_REVIEW, **fields) -> VideoRow:
    """Insert a video row. Candidate fields go to make_candidate, the rest to update_video."""
    candidate_fields = {k: fields.pop(k) for k in list(fields) if k in Candidate.__dataclass_fields__}
    video_id = db.insert_video(make_candidate(**candidate_fields), status, now or DEFAULT_NOW)
    if fields:
        db.update_video(video_id, **fields)
    return db.get_video(video_id)
```

`tests/test_db.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.db'`

- [ ] **Step 3: Write the implementation**

`contentbot/db.py`:
```python
"""SQLite storage for videos, keywords, music, runs and small key/value data.

All datetimes are stored as UTC ISO-8601 strings, so comparing the text in
SQL gives the same order as comparing the times.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from contentbot.models import Candidate, Keyword, Status

MUSIC_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".ogg", ".wav"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    url TEXT NOT NULL,
    media_url TEXT,
    thumbnail_url TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    duration_s REAL,
    views INTEGER,
    published_at TEXT,
    found_at TEXT NOT NULL,
    status TEXT NOT NULL,
    ai_score INTEGER,
    ai_category TEXT,
    ai_reason TEXT,
    caption_body TEXT NOT NULL DEFAULT '',
    audio_mode TEXT NOT NULL DEFAULT 'original',
    music_track_id INTEGER,
    original_path TEXT,
    rendered_path TEXT,
    review_message_id INTEGER,
    review_sent_at TEXT,
    slot_at TEXT,
    posted_at TEXT,
    channel_message_id INTEGER,
    error TEXT,
    UNIQUE (platform, platform_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS music_tracks (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    added_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS caption_edits (
    prompt_message_id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetimes stored in the database must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def from_db(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class VideoRow:
    id: int
    platform: str
    platform_id: str
    url: str
    media_url: str | None
    thumbnail_url: str | None
    title: str
    description: str
    author: str
    duration_s: float | None
    views: int | None
    published_at: datetime | None
    found_at: datetime
    status: Status
    ai_score: int | None
    ai_category: str | None
    ai_reason: str | None
    caption_body: str
    audio_mode: str
    music_track_id: int | None
    original_path: str | None
    rendered_path: str | None
    review_message_id: int | None
    review_sent_at: datetime | None
    slot_at: datetime | None
    posted_at: datetime | None
    channel_message_id: int | None
    error: str | None

    def to_candidate(self) -> Candidate:
        return Candidate(
            platform=self.platform,
            platform_id=self.platform_id,
            url=self.url,
            media_url=self.media_url,
            thumbnail_url=self.thumbnail_url,
            title=self.title,
            description=self.description,
            author=self.author,
            duration_s=self.duration_s,
            views=self.views,
            published_at=self.published_at,
        )


@dataclass(frozen=True)
class MusicTrack:
    id: int
    file_path: str
    title: str
    last_used_at: datetime | None


@dataclass(frozen=True)
class RunInfo:
    started_at: datetime
    finished_at: datetime | None
    trigger: str
    summary: dict


_DATETIME_COLUMNS = frozenset({"published_at", "found_at", "review_sent_at", "slot_at", "posted_at"})
_UPDATABLE = frozenset(
    {
        "status",
        "ai_score",
        "ai_category",
        "ai_reason",
        "caption_body",
        "audio_mode",
        "music_track_id",
        "original_path",
        "rendered_path",
        "review_message_id",
        "review_sent_at",
        "slot_at",
        "posted_at",
        "channel_message_id",
        "error",
        "duration_s",
    }
)


def _video(row: sqlite3.Row) -> VideoRow:
    data = dict(row)
    for key in _DATETIME_COLUMNS:
        data[key] = from_db(data[key])
    data["status"] = Status(data["status"])
    return VideoRow(**data)


def _keyword(row: sqlite3.Row) -> Keyword:
    return Keyword(id=row["id"], text=row["text"], language=row["language"], kind=row["kind"])


def _track(row: sqlite3.Row) -> MusicTrack:
    return MusicTrack(
        id=row["id"], file_path=row["file_path"], title=row["title"], last_used_at=from_db(row["last_used_at"])
    )


class Database:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def backup_to(self, path: str | Path) -> None:
        target = sqlite3.connect(str(path))
        try:
            self.conn.backup(target)
        finally:
            target.close()

    # ---- videos -------------------------------------------------------
    def video_exists(self, platform: str, platform_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM videos WHERE platform = ? AND platform_id = ?", (platform, platform_id)
        ).fetchone()
        return row is not None

    def insert_video(self, c: Candidate, status: Status, now: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO videos (platform, platform_id, url, media_url, thumbnail_url, title,
                   description, author, duration_s, views, published_at, found_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                c.platform,
                c.platform_id,
                c.url,
                c.media_url,
                c.thumbnail_url,
                c.title,
                c.description,
                c.author,
                c.duration_s,
                c.views,
                to_db(c.published_at),
                to_db(now),
                status.value,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_video(self, video_id: int) -> VideoRow | None:
        row = self.conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _video(row) if row else None

    def update_video(self, video_id: int, **values) -> None:
        unknown = set(values) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update columns: {sorted(unknown)}")
        if not values:
            return
        params = []
        for key, value in values.items():
            if key in _DATETIME_COLUMNS:
                value = to_db(value)
            elif isinstance(value, Enum):
                value = value.value
            params.append(value)
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.conn.execute(f"UPDATE videos SET {assignments} WHERE id = ?", (*params, video_id))
        self.conn.commit()

    def videos_with_status(self, status: Status) -> list[VideoRow]:
        rows = self.conn.execute("SELECT * FROM videos WHERE status = ? ORDER BY id", (status.value,)).fetchall()
        return [_video(r) for r in rows]

    def count_with_status(self, status: Status) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM videos WHERE status = ?", (status.value,)).fetchone()[0])

    def approved_slots(self) -> list[datetime]:
        rows = self.conn.execute(
            "SELECT slot_at FROM videos WHERE status = 'approved' AND slot_at IS NOT NULL"
        ).fetchall()
        return [from_db(r[0]) for r in rows]

    def approved_in_order(self) -> list[VideoRow]:
        rows = self.conn.execute("SELECT * FROM videos WHERE status = 'approved' ORDER BY slot_at, id").fetchall()
        return [_video(r) for r in rows]

    def due_for_posting(self, now: datetime) -> list[VideoRow]:
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE status = 'approved' AND slot_at <= ? ORDER BY slot_at, id", (to_db(now),)
        ).fetchall()
        return [_video(r) for r in rows]

    def review_sent_before(self, cutoff: datetime) -> list[VideoRow]:
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE status = 'in_review' AND review_sent_at <= ? ORDER BY id", (to_db(cutoff),)
        ).fetchall()
        return [_video(r) for r in rows]

    # ---- keywords -----------------------------------------------------
    def seed_keywords(self, by_language: dict[str, list[str]]) -> int:
        if self.conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]:
            return 0
        added = 0
        for language, words in by_language.items():
            for word in words:
                added += self.add_keyword(word, language)
        return added

    def add_keyword(self, text: str, language: str) -> bool:
        text = text.strip()
        if not text:
            return False
        row = self.conn.execute("SELECT active FROM keywords WHERE text = ?", (text,)).fetchone()
        if row is not None and row["active"]:
            return False
        if row is None:
            kind = "hashtag" if text.startswith("#") else "query"
            self.conn.execute("INSERT INTO keywords (text, language, kind) VALUES (?, ?, ?)", (text, language, kind))
        else:
            self.conn.execute("UPDATE keywords SET active = 1 WHERE text = ?", (text,))
        self.conn.commit()
        return True

    def deactivate_keyword(self, text: str) -> bool:
        cur = self.conn.execute("UPDATE keywords SET active = 0 WHERE text = ? AND active = 1", (text.strip(),))
        self.conn.commit()
        return cur.rowcount > 0

    def list_keywords(self) -> list[Keyword]:
        rows = self.conn.execute(
            "SELECT id, text, language, kind FROM keywords WHERE active = 1 ORDER BY language, id"
        ).fetchall()
        return [_keyword(r) for r in rows]

    def pick_keywords(self, count: int) -> list[Keyword]:
        rows = self.conn.execute(
            """SELECT id, text, language, kind FROM keywords WHERE active = 1
               ORDER BY last_used_at IS NOT NULL, last_used_at, id"""
        ).fetchall()
        queues: dict[str, list[Keyword]] = {}
        for row in rows:
            queues.setdefault(row["language"], []).append(_keyword(row))
        picked: list[Keyword] = []
        while len(picked) < count and any(queues.values()):
            for queue in queues.values():
                if queue and len(picked) < count:
                    picked.append(queue.pop(0))
        return picked

    def mark_keywords_used(self, ids: list[int], now: datetime) -> None:
        self.conn.executemany("UPDATE keywords SET last_used_at = ? WHERE id = ?", [(to_db(now), i) for i in ids])
        self.conn.commit()

    # ---- music --------------------------------------------------------
    def add_track(self, file_path: str, title: str, now: datetime) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO music_tracks (file_path, title, added_at) VALUES (?, ?, ?)",
            (file_path, title, to_db(now)),
        )
        self.conn.commit()
        return int(self.conn.execute("SELECT id FROM music_tracks WHERE file_path = ?", (file_path,)).fetchone()[0])

    def list_tracks(self) -> list[MusicTrack]:
        return [_track(r) for r in self.conn.execute("SELECT * FROM music_tracks ORDER BY id").fetchall()]

    def get_track(self, track_id: int) -> MusicTrack | None:
        row = self.conn.execute("SELECT * FROM music_tracks WHERE id = ?", (track_id,)).fetchone()
        return _track(row) if row else None

    def mark_track_used(self, track_id: int, now: datetime) -> None:
        self.conn.execute("UPDATE music_tracks SET last_used_at = ? WHERE id = ?", (to_db(now), track_id))
        self.conn.commit()

    def sync_music_dir(self, music_dir: Path, now: datetime) -> int:
        if not music_dir.exists():
            return 0
        known = {t.file_path for t in self.list_tracks()}
        added = 0
        for path in sorted(music_dir.iterdir()):
            resolved = str(path.resolve())
            if path.is_file() and path.suffix.lower() in MUSIC_SUFFIXES and resolved not in known:
                self.add_track(resolved, path.stem, now)
                added += 1
        return added

    # ---- runs ---------------------------------------------------------
    def start_run(self, trigger: str, now: datetime) -> int:
        cur = self.conn.execute("INSERT INTO runs (started_at, trigger) VALUES (?, ?)", (to_db(now), trigger))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, summary: dict, now: datetime) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
            (to_db(now), json.dumps(summary, ensure_ascii=False, default=str), run_id),
        )
        self.conn.commit()

    def last_run(self) -> RunInfo | None:
        row = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return RunInfo(
            started_at=from_db(row["started_at"]),
            finished_at=from_db(row["finished_at"]),
            trigger=row["trigger"],
            summary=json.loads(row["summary"] or "{}"),
        )

    def apify_spend_since(self, since: datetime) -> float:
        rows = self.conn.execute("SELECT summary FROM runs WHERE started_at >= ?", (to_db(since),)).fetchall()
        total = sum(float(json.loads(r[0] or "{}").get("apify_cost_usd", 0) or 0) for r in rows)
        return round(total, 4)

    # ---- caption edits ------------------------------------------------
    def add_caption_edit(self, prompt_message_id: int, video_id: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO caption_edits (prompt_message_id, video_id) VALUES (?, ?)",
            (prompt_message_id, video_id),
        )
        self.conn.commit()

    def caption_edit_video(self, prompt_message_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT video_id FROM caption_edits WHERE prompt_message_id = ?", (prompt_message_id,)
        ).fetchone()
        return int(row[0]) if row else None

    def remove_caption_edit(self, prompt_message_id: int) -> None:
        self.conn.execute("DELETE FROM caption_edits WHERE prompt_message_id = ?", (prompt_message_id,))
        self.conn.commit()

    # ---- meta ---------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/db.py tests/factories.py tests/test_db.py
git commit -m "feat: add SQLite storage for videos, keywords, music and runs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Free filter and picking rules

**Files:**
- Create: `contentbot/pipeline/prefilter.py`, `contentbot/pipeline/picker.py`
- Test: `tests/test_prefilter.py`, `tests/test_picker.py`

**Interfaces:**
- Consumes: `Candidate`, `Score` (models), `Settings` (config).
- Produces:
  - `FilterRules(min_duration_s, max_duration_s, min_views: dict[str,int])` with `FilterRules.from_settings(settings)`.
  - `dedupe(candidates) -> list[Candidate]`: first copy of each `(platform, platform_id)` wins.
  - `passes(candidate, rules) -> bool`: unknown duration or views pass.
  - `split(candidates, rules) -> tuple[passed, dropped]`.
  - `order_for_ai(candidates) -> list[Candidate]`: round-robin across platforms, each platform sorted by views descending, unknown views last.
  - `Ranked(video_id:int, candidate:Candidate, score:Score|None)`.
  - `rank(items, min_score, ai_available) -> list[Ranked]`.
  - `CategoryQuota(total, per_category)` with `.full`, `.wants(category|None) -> bool` and `.take(category|None)`. A `None` category is never capped.

- [ ] **Step 1: Write the failing tests**

`tests/test_prefilter.py`:
```python
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
```

`tests/test_picker.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prefilter.py tests/test_picker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.pipeline.prefilter'`

- [ ] **Step 3: Write the implementation**

`contentbot/pipeline/prefilter.py`:
```python
"""Free checks done before any paid AI call."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from contentbot.config import Settings
from contentbot.models import Candidate


@dataclass(frozen=True)
class FilterRules:
    min_duration_s: float
    max_duration_s: float
    min_views: dict[str, int]

    @classmethod
    def from_settings(cls, settings: Settings) -> FilterRules:
        return cls(settings.min_duration_s, settings.max_duration_s, dict(settings.min_views))


def dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.platform, c.platform_id)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def passes(c: Candidate, rules: FilterRules) -> bool:
    if c.duration_s is not None and not rules.min_duration_s <= c.duration_s <= rules.max_duration_s:
        return False
    minimum = rules.min_views.get(c.platform)
    if minimum is not None and c.views is not None and c.views < minimum:
        return False
    return True


def split(candidates: Iterable[Candidate], rules: FilterRules) -> tuple[list[Candidate], list[Candidate]]:
    passed: list[Candidate] = []
    dropped: list[Candidate] = []
    for c in candidates:
        (passed if passes(c, rules) else dropped).append(c)
    return passed, dropped


def order_for_ai(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate from each platform in turn; most-viewed first within a platform."""
    by_platform: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_platform.setdefault(c.platform, []).append(c)
    queues = [sorted(group, key=lambda c: (c.views is None, -(c.views or 0))) for group in by_platform.values()]
    ordered: list[Candidate] = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered
```

`contentbot/pipeline/picker.py`:
```python
"""Choose which scored candidates go to the review group."""
from __future__ import annotations

from dataclasses import dataclass

from contentbot.models import Candidate, Score


@dataclass
class Ranked:
    video_id: int
    candidate: Candidate
    score: Score | None


def rank(items: list[Ranked], min_score: int, ai_available: bool) -> list[Ranked]:
    if ai_available:
        kept = [i for i in items if i.score is not None and i.score.score >= min_score]
        return sorted(kept, key=lambda i: (-i.score.score, -(i.candidate.views or 0)))
    return sorted(items, key=lambda i: (i.candidate.views is None, -(i.candidate.views or 0)))


class CategoryQuota:
    """At most `total` picks, and at most `per_category` from one category."""

    def __init__(self, total: int, per_category: int) -> None:
        self.total = total
        self.per_category = per_category
        self.taken = 0
        self.counts: dict[str, int] = {}

    @property
    def full(self) -> bool:
        return self.taken >= self.total

    def wants(self, category: str | None) -> bool:
        if self.full:
            return False
        if category is None:
            return True
        return self.counts.get(category, 0) < self.per_category

    def take(self, category: str | None) -> None:
        self.taken += 1
        if category is not None:
            self.counts[category] = self.counts.get(category, 0) + 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prefilter.py tests/test_picker.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/pipeline/prefilter.py contentbot/pipeline/picker.py tests/test_prefilter.py tests/test_picker.py
git commit -m "feat: add free filter, platform interleaving and category quota" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Caption text rules

**Files:**
- Create: `contentbot/pipeline/captions.py`
- Test: `tests/test_captions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CAPTION_LIMIT = 1024`, `BODY_LIMIT = 500`, `utf16_len(text) -> int`, `build_caption(body, footer) -> str` (empty body gives the footer only), `caption_overflow(body, footer) -> int` (0 when it fits), `trim_body(text, limit=BODY_LIMIT) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_captions.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_captions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.pipeline.captions'`

- [ ] **Step 3: Write the implementation**

`contentbot/pipeline/captions.py`:
```python
"""Caption = body + footer, measured the way Telegram measures it (UTF-16 units)."""
from __future__ import annotations

CAPTION_LIMIT = 1024
BODY_LIMIT = 500


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def build_caption(body: str, footer: str) -> str:
    body = body.strip()
    footer = footer.strip()
    if not body:
        return footer
    if not footer:
        return body
    return f"{body}\n\n{footer}"


def caption_overflow(body: str, footer: str) -> int:
    return max(0, utf16_len(build_caption(body, footer)) - CAPTION_LIMIT)


def trim_body(text: str, limit: int = BODY_LIMIT) -> str:
    text = text.strip()
    if utf16_len(text) <= limit:
        return text
    cut = text
    while utf16_len(cut) > limit - 1:
        cut = cut[:-1]
    best = max(cut.rfind(mark) for mark in (".", "!", "?", "\n"))
    if best >= limit // 2:
        return cut[: best + 1].strip()
    return cut.rstrip() + "…"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_captions.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/pipeline/captions.py tests/test_captions.py
git commit -m "feat: add caption assembly with Telegram-style length counting" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Claude calls: JSON wrapper, relevance check, caption writer

**Files:**
- Create: `contentbot/ai/claude.py`, `contentbot/ai/relevance.py`, `contentbot/ai/caption_writer.py`
- Create: `tests/fakes.py`
- Test: `tests/test_claude.py`, `tests/test_relevance.py`, `tests/test_caption_writer.py`

**Interfaces:**
- Consumes: `Candidate`, `Score`, `CATEGORIES` (models); `trim_body` (Task 5).
- Produces:
  - `ClaudeJSON(model, api_key=None, client=None)` with `async ask_json(*, system:str, content:list[dict], schema:dict, effort:str, max_tokens:int=8000) -> dict | None`. It returns `None` on an API error, a stop reason other than `end_turn`, a missing text block or invalid JSON. It also exports `FALLBACK_BETA = "server-side-fallback-2026-07-01"`.
  - `RelevanceChecker(claude, http: httpx.AsyncClient, batch_size=10)` with `async score(candidates) -> list[Score | None] | None`. The list has one entry per candidate; the whole result is `None` only when every batch failed (AI unavailable).
  - `parse_scores(data, count) -> list[Score | None]`, `shrink_to_jpeg(data, max_side=768) -> bytes`, `fetch_preview(http, url) -> bytes | None`, `SCORE_SCHEMA`, `SYSTEM_PROMPT`.
  - `CaptionWriter(claude, examples)` with `async draft(candidate, score) -> str`. It returns `""` on failure and trims to `BODY_LIMIT`. Also `build_system_prompt(examples) -> str`, `CATEGORY_HASHTAGS: dict[str, str]`, `CAPTION_SCHEMA`.
  - `tests.fakes`: `FakeClaudeClient(responses)` (has `.messages.calls`), `claude_reply(payload, stop_reason="end_turn")`, `connection_error()`, `FakeClaudeJSON(replies)` (has `.calls`).

- [ ] **Step 1: Write the test fakes**

`tests/fakes.py`:
```python
"""Fake collaborators for tests: no network, no Telegram, no ffmpeg."""
from __future__ import annotations

import json
from types import SimpleNamespace


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClaudeClient:
    """Stands in for anthropic.AsyncAnthropic (only .beta.messages.create is used)."""

    def __init__(self, responses):
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=self.messages)


def claude_reply(payload, stop_reason: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def connection_error():
    import anthropic

    try:
        import httpx2 as http_lib  # anthropic 1.x is built on httpx2
    except ImportError:
        import httpx as http_lib
    return anthropic.APIConnectionError(request=http_lib.Request("POST", "https://api.anthropic.com/v1/messages"))


class FakeClaudeJSON:
    """Stands in for ClaudeJSON: returns queued dicts (or None) and records calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def ask_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_claude.py`:
```python
from contentbot.ai.claude import FALLBACK_BETA, ClaudeJSON
from tests.fakes import FakeClaudeClient, claude_reply, connection_error

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


async def ask(client):
    claude = ClaudeJSON("claude-opus-5-5", client=client)
    return await claude.ask_json(system="sys", content=[{"type": "text", "text": "hi"}], schema=SCHEMA, effort="low")


async def test_returns_parsed_json_and_sends_expected_request():
    client = FakeClaudeClient([claude_reply({"ok": True})])
    assert await ask(client) == {"ok": True}
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == "sys"
    assert call["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["betas"] == [FALLBACK_BETA]
    assert call["extra_body"] == {"fallbacks": "default"}


async def test_refusal_returns_none():
    assert await ask(FakeClaudeClient([claude_reply({"ok": True}, stop_reason="refusal")])) is None


async def test_invalid_json_returns_none():
    assert await ask(FakeClaudeClient([claude_reply("not json")])) is None


async def test_api_error_returns_none():
    assert await ask(FakeClaudeClient([connection_error()])) is None
```

`tests/test_relevance.py`:
```python
import io

import httpx
from PIL import Image

from contentbot.ai.relevance import RelevanceChecker, parse_scores, shrink_to_jpeg
from contentbot.models import Score
from tests.factories import make_candidate
from tests.fakes import FakeClaudeJSON


def png_bytes(size=(1200, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, "PNG")
    return buffer.getvalue()


def result(index, score, category, branding=False, reason="r"):
    return {"index": index, "score": score, "category": category, "competitor_branding": branding, "reason": reason}


def test_parse_scores_clamps_and_caps_competitors():
    data = {
        "results": [
            result(0, 14, "foam", reason="foam cutting"),
            result(1, 9, "mechanism", branding=True),
            result(2, 7, "spaceships"),
            result(7, 9, "foam"),
            {"index": 3},
        ]
    }
    out = parse_scores(data, 4)
    assert out[0] == Score(10, "foam", False, "foam cutting")
    assert out[1].score == 3 and out[1].competitor_branding
    assert out[2].category == "other"
    assert out[3] is None


def test_shrink_to_jpeg_limits_size():
    small = shrink_to_jpeg(png_bytes(), max_side=768)
    with Image.open(io.BytesIO(small)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 768


async def test_score_sends_image_and_text_blocks(respx_mock):
    respx_mock.get("https://img.example/1.jpg").respond(200, content=png_bytes())
    fake = FakeClaudeJSON([{"results": [result(0, 8, "fabric", reason="fabric roll")]}])
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http).score([make_candidate(thumbnail_url="https://img.example/1.jpg")])
    assert scores == [Score(8, "fabric", False, "fabric roll")]
    call = fake.calls[0]
    assert call["effort"] == "low"
    assert any(b["type"] == "image" and b["source"]["media_type"] == "image/jpeg" for b in call["content"])
    assert call["content"][0]["text"].startswith("[0] platform: youtube")


async def test_missing_preview_still_scores(respx_mock):
    respx_mock.get("https://img.example/1.jpg").respond(404)
    fake = FakeClaudeJSON([{"results": [result(0, 7, "legs")]}])
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http).score([make_candidate(thumbnail_url="https://img.example/1.jpg")])
    assert scores[0].category == "legs"
    assert all(b["type"] == "text" for b in fake.calls[0]["content"])


async def test_all_batches_failing_means_ai_unavailable():
    fake = FakeClaudeJSON([None, None])
    candidates = [make_candidate(thumbnail_url=None), make_candidate(platform_id="v2", thumbnail_url=None)]
    async with httpx.AsyncClient() as http:
        assert await RelevanceChecker(fake, http, batch_size=1).score(candidates) is None


async def test_one_failed_batch_gives_none_for_its_items():
    fake = FakeClaudeJSON([None, {"results": [result(0, 6, "legs")]}])
    candidates = [make_candidate(thumbnail_url=None), make_candidate(platform_id="v2", thumbnail_url=None)]
    async with httpx.AsyncClient() as http:
        scores = await RelevanceChecker(fake, http, batch_size=1).score(candidates)
    assert scores[0] is None and scores[1].score == 6
```

`tests/test_caption_writer.py`:
```python
from contentbot.ai.caption_writer import CaptionWriter, build_system_prompt
from contentbot.models import Score
from contentbot.pipeline.captions import BODY_LIMIT, utf16_len
from tests.factories import make_candidate
from tests.fakes import FakeClaudeJSON


async def test_draft_returns_caption_and_passes_hashtag_and_reason():
    fake = FakeClaudeJSON([{"caption": "#mexanizm Zo'r mexanizm! Bunday mahsulotlarni Evimda topishingiz mumkin!"}])
    writer = CaptionWriter(fake, ["Misol 1", "Misol 2"])
    out = await writer.draft(make_candidate(title="Sofa bed mechanism"), Score(9, "mechanism", False, "sofa bed opening"))
    assert out.startswith("#mexanizm")
    call = fake.calls[0]
    prompt = call["content"][0]["text"]
    assert "#mexanizm" in prompt and "sofa bed opening" in prompt and "Sofa bed mechanism" in prompt
    assert "Misol 2" in call["system"]
    assert call["effort"] == "medium"


async def test_draft_failure_returns_empty_string():
    writer = CaptionWriter(FakeClaudeJSON([None]), ["x"])
    assert await writer.draft(make_candidate(), None) == ""


async def test_long_caption_is_trimmed():
    writer = CaptionWriter(FakeClaudeJSON([{"caption": "Gap. " * 200}]), ["x"])
    out = await writer.draft(make_candidate(), Score(8, "foam", False, "foam"))
    assert 0 < utf16_len(out) <= BODY_LIMIT


def test_system_prompt_rules():
    prompt = build_system_prompt(["Novella ✨"])
    assert "Latin script" in prompt
    assert "NOT filmed at Evim" in prompt
    assert "Never write prices" in prompt
    assert "Novella ✨" in prompt
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_claude.py tests/test_relevance.py tests/test_caption_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.ai.claude'`

- [ ] **Step 4: Write the implementation**

`contentbot/ai/claude.py`:
```python
"""One structured-output call to Claude that returns parsed JSON, or None on any failure."""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeJSON:
    def __init__(self, model: str, api_key: str | None = None, client: Any = None) -> None:
        self.model = model
        self.client = client if client is not None else anthropic.AsyncAnthropic(api_key=api_key)

    async def ask_json(
        self, *, system: str, content: list[dict], schema: dict, effort: str, max_tokens: int = 8000
    ) -> dict | None:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                betas=[FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
            )
        except anthropic.APIError as exc:
            log.warning("Claude request failed: %s", exc)
            return None
        if response.stop_reason != "end_turn":
            log.warning("Claude stopped with %s", response.stop_reason)
            return None
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            log.warning("Claude returned no text block")
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Claude returned invalid JSON")
            return None
        return data if isinstance(data, dict) else None
```

`contentbot/ai/relevance.py`:
```python
"""Ask Claude whether each candidate video fits the Evim channel."""
from __future__ import annotations

import base64
import io
import logging

import httpx
from PIL import Image

from contentbot.ai.claude import ClaudeJSON
from contentbot.models import CATEGORIES, Candidate, Score

log = logging.getLogger(__name__)

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "competitor_branding": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "category", "competitor_branding", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You review short videos for the Telegram channel of Evim, a shop in Tashkent that sells materials and parts for soft-furniture makers: sofa and chair mechanisms, foam, upholstery fabric, leather, furniture legs, tools, clips and small fittings.

For each numbered candidate you get the platform, title, description and hashtags, and usually a preview image right after its text. Score how well the video fits the channel, from 0 to 10:
- 8-10: clearly about soft furniture: the materials above, upholstery work, sofa or chair making in a workshop, mechanisms in action, finished sofas and chairs. Clear and good-looking.
- 5-7: related to soft furniture but weaker: unclear image, mostly a finished interior, little detail.
- 0-4: off-topic, mostly a person talking, memes, dancing, only hard furniture (kitchens, wardrobes), or an ad covered with text.

Set competitor_branding to true when you can see another shop's phone number, logo, website, price list or store name in the image or text. Evim must not advertise other shops.

Pick the closest category. Keep reason under 15 words, in English. Return one result for every candidate index."""


def describe(index: int, c: Candidate) -> str:
    description = " ".join(c.description.split())[:600]
    return f"[{index}] platform: {c.platform}\ntitle: {c.title[:200]}\ndescription: {description}"


def shrink_to_jpeg(data: bytes, max_side: int = 768) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    rgb.thumbnail((max_side, max_side))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return out.getvalue()


async def fetch_preview(http: httpx.AsyncClient, url: str | None, max_side: int = 768) -> bytes | None:
    if not url:
        return None
    try:
        response = await http.get(url, timeout=20)
        response.raise_for_status()
        return shrink_to_jpeg(response.content, max_side)
    except Exception as exc:  # a missing preview only means "judge by the text"
        log.info("Preview download failed for %s: %s", url, exc)
        return None


def parse_scores(data: dict, count: int) -> list[Score | None]:
    out: list[Score | None] = [None] * count
    for item in data.get("results", []):
        try:
            index = int(item["index"])
            if not 0 <= index < count:
                continue
            score = max(0, min(10, int(item["score"])))
            category = item["category"] if item["category"] in CATEGORIES else "other"
            branding = bool(item["competitor_branding"])
            if branding:
                score = min(score, 3)
            out[index] = Score(score, category, branding, str(item.get("reason", ""))[:200])
        except (KeyError, TypeError, ValueError):
            continue
    return out


class RelevanceChecker:
    def __init__(self, claude: ClaudeJSON, http: httpx.AsyncClient, batch_size: int = 10) -> None:
        self.claude = claude
        self.http = http
        self.batch_size = batch_size

    async def score(self, candidates: list[Candidate]) -> list[Score | None] | None:
        """One Score (or None) per candidate; None overall when every AI call failed."""
        if not candidates:
            return []
        results: list[Score | None] = []
        any_answer = False
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            data = await self.claude.ask_json(
                system=SYSTEM_PROMPT, content=await self._content(batch), schema=SCORE_SCHEMA, effort="low"
            )
            if data is None:
                results.extend([None] * len(batch))
                continue
            any_answer = True
            results.extend(parse_scores(data, len(batch)))
        return results if any_answer else None

    async def _content(self, batch: list[Candidate]) -> list[dict]:
        blocks: list[dict] = []
        for index, c in enumerate(batch):
            blocks.append({"type": "text", "text": describe(index, c)})
            preview = await fetch_preview(self.http, c.thumbnail_url)
            if preview:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(preview).decode("ascii"),
                        },
                    }
                )
        blocks.append({"type": "text", "text": f"Score all {len(batch)} candidates (indexes 0 to {len(batch) - 1})."})
        return blocks
```

`contentbot/ai/caption_writer.py`:
```python
"""Ask Claude for a short Uzbek (Latin) caption in the channel's style."""
from __future__ import annotations

from collections.abc import Sequence

from contentbot.ai.claude import ClaudeJSON
from contentbot.models import Candidate, Score
from contentbot.pipeline.captions import trim_body

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": False,
}

CATEGORY_HASHTAGS = {
    "mechanism": "#mexanizm",
    "foam": "#porolon",
    "fabric": "#material",
    "leather": "#charm",
    "legs": "#mebel_oyoqlari",
    "tools": "#asbob",
    "fittings": "#furnitura",
    "upholstery_work": "#obivka",
    "finished_furniture": "#yumshoq_mebel",
    "other": "#evim",
}


def build_system_prompt(examples: Sequence[str]) -> str:
    joined = "\n\n---\n\n".join(examples)
    return f"""You write short captions for Evim's Telegram channel (@evim_uzb). Evim is a shop in Tashkent that sells materials and parts for soft-furniture makers. The captions go under short videos that were found on the internet, so the video was NOT filmed at Evim.

Rules:
- Write in Uzbek, Latin script, in the same style as the examples. Russian product words are fine where the examples use them.
- Start with the category hashtag you are given.
- 1-3 short sentences and 1-3 emojis.
- End with a call to action saying such products can be found at Evim, for example: "Bunday mahsulotlarni Evimda topishingiz mumkin!"
- Never write prices, sizes, model names or brand names, and never name another company or shop.
- Never claim the video was filmed at Evim or shows Evim's own workshop.
- At most 400 characters. Do not add phone numbers or addresses; they are added automatically.

Example captions from the channel:

{joined}"""


class CaptionWriter:
    def __init__(self, claude: ClaudeJSON, examples: Sequence[str]) -> None:
        self.claude = claude
        self.system = build_system_prompt(examples)

    async def draft(self, candidate: Candidate, score: Score | None) -> str:
        category = score.category if score else "other"
        hashtag = CATEGORY_HASHTAGS.get(category, "#evim")
        about = score.reason if score else ""
        description = " ".join(candidate.description.split())[:600]
        prompt = (
            f"Category hashtag: {hashtag}\n"
            f"What the video shows: {about}\n"
            f"Original title: {candidate.title[:200]}\n"
            f"Original description: {description}\n\n"
            "Write the caption."
        )
        data = await self.claude.ask_json(
            system=self.system,
            content=[{"type": "text", "text": prompt}],
            schema=CAPTION_SCHEMA,
            effort="medium",
        )
        if not data:
            return ""
        caption = str(data.get("caption", "")).strip()
        return trim_body(caption) if caption else ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_claude.py tests/test_relevance.py tests/test_caption_writer.py -v`
Expected: 14 passed

- [ ] **Step 6: Commit**

```bash
git add contentbot/ai tests/fakes.py tests/test_claude.py tests/test_relevance.py tests/test_caption_writer.py
git commit -m "feat: add Claude relevance check and caption writer" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Video info and sound rendering with ffmpeg

**Files:**
- Create: `contentbot/pipeline/media.py`
- Create: `tests/media_helpers.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Consumes: `AudioMode` (models).
- Produces:
  - `TELEGRAM_MAX_BYTES = 49 * 1024 * 1024`.
  - `MediaError(Exception)`.
  - `ProbeInfo(duration_s: float, vcodec: str|None, acodec: str|None, size_bytes: int)`.
  - `build_render_args(ffmpeg, src, dest, mode, info, music=None, video_kbps=None) -> list[str]`.
  - `target_video_kbps(duration_s, max_bytes, audio_kbps=128) -> int`, never below 300.
  - `Media(ffmpeg="ffmpeg", ffprobe="ffprobe", max_bytes=TELEGRAM_MAX_BYTES)` with `async probe(path) -> ProbeInfo` and `async render(src, mode, dest, music=None) -> Path`. Every output is H.264 + AAC; it raises `MediaError` on failure or when the file is still too big after compression.

- [ ] **Step 1: Install ffmpeg on the dev machine**

Windows: `winget install --id Gyan.FFmpeg -e`, then close and reopen the terminal and re-activate the venv.
Linux: `sudo apt install ffmpeg`.

Run: `ffmpeg -version` and `ffprobe -version`
Expected: both print a version line. If ffmpeg cannot be installed, continue anyway: the ffmpeg integration tests below are skipped automatically, and the pure tests still run.

- [ ] **Step 2: Write the failing tests**

`tests/media_helpers.py`:
```python
"""Create tiny test videos with ffmpeg (only used when ffmpeg is installed)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def make_clip(path: Path, seconds: int = 3, *, audio: bool = True, vcodec: str = "libx264") -> Path:
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=25"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args.append(str(path))
    subprocess.run(args, check=True)
    return path


def make_tone(path: Path, seconds: int = 1) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def max_volume_db(path: Path) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"max_volume:\s*(-?inf|-?[\d.]+) dB", proc.stderr)
    if not match:
        raise AssertionError(proc.stderr[-500:])
    return float(match.group(1))
```

`tests/test_media.py`:
```python
import pytest

from contentbot.models import AudioMode
from contentbot.pipeline.media import Media, MediaError, ProbeInfo, build_render_args, target_video_kbps
from tests.media_helpers import make_clip, make_tone, max_volume_db, requires_ffmpeg

INFO = ProbeInfo(duration_s=3.0, vcodec="h264", acodec="aac", size_bytes=1000)


def args_for(tmp_path, mode, info=INFO, music=None):
    return build_render_args("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", mode, info, music)


def test_original_with_aac_copies_both_streams(tmp_path):
    args = args_for(tmp_path, AudioMode.ORIGINAL)
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert "anullsrc" not in " ".join(args)


def test_mute_uses_a_real_silent_track(tmp_path):
    joined = " ".join(args_for(tmp_path, AudioMode.MUTE))
    assert "anullsrc=channel_layout=stereo" in joined
    assert "-map 0:v:0 -map 1:a:0" in joined


def test_music_loops_track_and_fades(tmp_path):
    music = tmp_path / "calm.m4a"
    args = args_for(tmp_path, AudioMode.MUSIC, music=music)
    assert args.index("-stream_loop") < args.index(str(music))
    assert "afade=t=out:st=1.50:d=1.5" in args[args.index("-af") + 1]
    assert args[args.index("-t") + 1] == "3.000"


def test_original_without_audio_gets_silent_track(tmp_path):
    info = ProbeInfo(duration_s=3.0, vcodec="h264", acodec=None, size_bytes=1000)
    assert "anullsrc" in " ".join(args_for(tmp_path, AudioMode.ORIGINAL, info))


def test_non_h264_video_is_transcoded(tmp_path):
    info = ProbeInfo(duration_s=3.0, vcodec="mpeg4", acodec="aac", size_bytes=1000)
    args = args_for(tmp_path, AudioMode.ORIGINAL, info)
    assert args[args.index("-c:v") + 1] == "libx264"


def test_target_bitrate_has_a_floor():
    assert target_video_kbps(10_000, 49 * 1024 * 1024) == 300
    assert 3000 < target_video_kbps(60, 49 * 1024 * 1024) < 7000


async def test_missing_ffprobe_binary_raises_media_error(tmp_path):
    with pytest.raises(MediaError, match="not found"):
        await Media(ffprobe="definitely-not-ffprobe").probe(tmp_path / "x.mp4")


@requires_ffmpeg
async def test_render_modes_end_to_end(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4")
    tone = make_tone(tmp_path / "tone.m4a")  # 1 s, shorter than the clip, so it must loop
    media = Media()
    original = await media.render(clip, AudioMode.ORIGINAL, tmp_path / "o.mp4")
    muted = await media.render(clip, AudioMode.MUTE, tmp_path / "m.mp4")
    music = await media.render(clip, AudioMode.MUSIC, tmp_path / "mu.mp4", music=tone)
    for path in (original, muted, music):
        info = await media.probe(path)
        assert info.vcodec == "h264" and info.acodec == "aac"
        assert abs(info.duration_s - 3.0) < 0.3
    assert max_volume_db(muted) < -80
    assert max_volume_db(music) > -30


@requires_ffmpeg
async def test_old_codec_and_silent_input_become_telegram_friendly(tmp_path):
    clip = make_clip(tmp_path / "old.mp4", audio=False, vcodec="mpeg4")
    media = Media()
    info = await media.probe(await media.render(clip, AudioMode.ORIGINAL, tmp_path / "out.mp4"))
    assert info.vcodec == "h264" and info.acodec == "aac"


@requires_ffmpeg
async def test_too_large_even_after_compression_raises(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4")
    with pytest.raises(MediaError, match="too large"):
        await Media(max_bytes=1000).render(clip, AudioMode.ORIGINAL, tmp_path / "o.mp4")
    assert not (tmp_path / "o.mp4").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_media.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.pipeline.media'`

- [ ] **Step 4: Write the implementation**

`contentbot/pipeline/media.py`:
```python
"""ffprobe/ffmpeg: read video info and render the three sound modes for Telegram."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from contentbot.models import AudioMode

TELEGRAM_MAX_BYTES = 49 * 1024 * 1024
SCALE_FILTER = r"scale=w=trunc(min(1080\,iw)/2)*2:h=-2"
AAC_ARGS = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]


class MediaError(Exception):
    """ffmpeg/ffprobe failed or the result cannot be sent to Telegram."""


@dataclass(frozen=True)
class ProbeInfo:
    duration_s: float
    vcodec: str | None
    acodec: str | None
    size_bytes: int


def target_video_kbps(duration_s: float, max_bytes: int, audio_kbps: int = 128) -> int:
    if duration_s <= 0:
        return 300
    total_kbps = max_bytes * 8 / 1000 * 0.92 / duration_s
    return max(300, int(total_kbps - audio_kbps))


def build_render_args(
    ffmpeg: str,
    src: Path,
    dest: Path,
    mode: AudioMode,
    info: ProbeInfo,
    music: Path | None = None,
    video_kbps: int | None = None,
) -> list[str]:
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    audio_filter: str | None = None
    copy_audio = False
    if mode is AudioMode.ORIGINAL and info.acodec is not None:
        audio_input = "0:a:0"
        copy_audio = info.acodec == "aac"
    elif mode is AudioMode.MUSIC:
        if music is None:
            raise MediaError("music mode needs a track")
        args += ["-stream_loop", "-1", "-i", str(music)]
        audio_input = "1:a:0"
        filters = ["afade=t=in:d=0.5"]
        if info.duration_s > 2:
            filters.append(f"afade=t=out:st={info.duration_s - 1.5:.2f}:d=1.5")
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        audio_filter = ",".join(filters)
    else:  # MUTE, or ORIGINAL without an audio stream: a real silent track keeps it a "video"
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_input = "1:a:0"
    args += ["-map", "0:v:0", "-map", audio_input]
    if video_kbps is not None:
        args += [
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
            "-pix_fmt", "yuv420p", "-vf", SCALE_FILTER,
        ]
    elif info.vcodec == "h264":
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-vf", SCALE_FILTER]
    if audio_filter:
        args += ["-af", audio_filter]
    args += ["-c:a", "copy"] if copy_audio else AAC_ARGS
    if info.duration_s > 0:
        args += ["-t", f"{info.duration_s:.3f}"]
    args += ["-shortest", "-movflags", "+faststart", str(dest)]
    return args


class Media:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", max_bytes: int = TELEGRAM_MAX_BYTES) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.max_bytes = max_bytes

    async def probe(self, path: Path) -> ProbeInfo:
        out = await self._run(
            [
                self.ffprobe, "-v", "error",
                "-show_entries", "format=duration,size:stream=codec_type,codec_name",
                "-of", "json", str(path),
            ]
        )
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise MediaError("ffprobe returned invalid JSON") from exc
        streams = data.get("streams") or []
        fmt = data.get("format") or {}

        def codec(kind: str) -> str | None:
            return next((s.get("codec_name") for s in streams if s.get("codec_type") == kind), None)

        try:
            duration = float(fmt.get("duration") or 0)
        except ValueError:
            duration = 0.0
        size = int(fmt.get("size") or 0) or path.stat().st_size
        return ProbeInfo(duration_s=duration, vcodec=codec("video"), acodec=codec("audio"), size_bytes=size)

    async def render(self, src: Path, mode: AudioMode, dest: Path, music: Path | None = None) -> Path:
        info = await self.probe(src)
        if info.vcodec is None:
            raise MediaError("file has no video stream")
        dest.parent.mkdir(parents=True, exist_ok=True)
        await self._run(build_render_args(self.ffmpeg, src, dest, mode, info, music))
        if dest.stat().st_size <= self.max_bytes:
            return dest
        smaller = dest.with_name(dest.stem + "_small.mp4")
        kbps = target_video_kbps(info.duration_s, self.max_bytes)
        await self._run(build_render_args(self.ffmpeg, src, smaller, mode, info, music, video_kbps=kbps))
        smaller.replace(dest)
        if dest.stat().st_size > self.max_bytes:
            dest.unlink(missing_ok=True)
            raise MediaError("video is too large for Telegram even after compression")
        return dest

    async def _run(self, args: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError as exc:
            raise MediaError(f"{args[0]} not found; install ffmpeg") from exc
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise MediaError(f"{Path(args[0]).name} failed: {err.decode(errors='replace')[-500:]}")
        return out.decode(errors="replace")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_media.py -v`
Expected: 10 passed when ffmpeg is installed (otherwise 7 passed, 3 skipped).

- [ ] **Step 6: Commit**

```bash
git add contentbot/pipeline/media.py tests/media_helpers.py tests/test_media.py
git commit -m "feat: render original, silent and music sound with ffmpeg" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Video downloader

**Files:**
- Create: `contentbot/pipeline/downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: `Candidate` (models).
- Produces:
  - `DownloadError(Exception)`, `USER_AGENT: str`, `YTDLP_FORMAT: str`.
  - `run_ytdlp(url, dest, options) -> None`: the real yt-dlp call, run in a thread.
  - `Downloader(http, videos_dir, *, cookies_file=None, ffmpeg_path="ffmpeg", max_bytes=300 MB, ytdlp_fn=None)` with `async fetch(video_id, candidate) -> Path`. The file is saved as `videos_dir / f"{video_id}_src.mp4"`. It tries `media_url` first (unless it is an `.m3u8` playlist) and falls back to yt-dlp on `candidate.url`.

- [ ] **Step 1: Write the failing tests**

`tests/test_downloader.py`:
```python
import httpx
import pytest

from contentbot.pipeline.downloader import DownloadError, Downloader
from tests.factories import make_candidate

VIDEO_URL = "https://cdn.example/v.mp4"


class FakeYtdlp:
    def __init__(self, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    def __call__(self, url, dest, options):
        self.calls.append((url, dest, options))
        if self.fail:
            raise RuntimeError("yt-dlp says no")
        dest.write_bytes(b"from-ytdlp")


async def fetch(tmp_path, candidate, ytdlp, **kwargs):
    async with httpx.AsyncClient() as http:
        return await Downloader(http, tmp_path, ytdlp_fn=ytdlp, **kwargs).fetch(7, candidate)


async def test_direct_media_url_is_downloaded(tmp_path, respx_mock):
    respx_mock.get(VIDEO_URL).respond(200, content=b"video-bytes", headers={"content-type": "video/mp4"})
    ytdlp = FakeYtdlp()
    path = await fetch(tmp_path, make_candidate(platform="instagram", media_url=VIDEO_URL), ytdlp)
    assert path == tmp_path / "7_src.mp4"
    assert path.read_bytes() == b"video-bytes"
    assert ytdlp.calls == []


async def test_expired_signed_url_falls_back_to_ytdlp(tmp_path, respx_mock):
    respx_mock.get(VIDEO_URL).respond(403)
    ytdlp = FakeYtdlp()
    candidate = make_candidate(platform="instagram", media_url=VIDEO_URL, url="https://www.instagram.com/p/C9abc/")
    path = await fetch(tmp_path, candidate, ytdlp)
    assert path.read_bytes() == b"from-ytdlp"
    assert ytdlp.calls[0][0] == "https://www.instagram.com/p/C9abc/"
    assert not (tmp_path / "7_src.part").exists()


async def test_html_instead_of_video_falls_back(tmp_path, respx_mock):
    respx_mock.get(VIDEO_URL).respond(200, text="<html>login</html>", headers={"content-type": "text/html"})
    ytdlp = FakeYtdlp()
    path = await fetch(tmp_path, make_candidate(media_url=VIDEO_URL), ytdlp)
    assert path.read_bytes() == b"from-ytdlp"


async def test_hls_playlist_goes_straight_to_ytdlp(tmp_path):
    ytdlp = FakeYtdlp()
    await fetch(tmp_path, make_candidate(platform="pinterest", media_url="https://v.pinimg.com/x.m3u8"), ytdlp)
    assert len(ytdlp.calls) == 1


async def test_ytdlp_failure_raises_download_error(tmp_path):
    with pytest.raises(DownloadError, match="yt-dlp failed"):
        await fetch(tmp_path, make_candidate(), FakeYtdlp(fail=True))


async def test_ytdlp_options(tmp_path):
    ytdlp = FakeYtdlp()
    await fetch(tmp_path, make_candidate(), ytdlp, cookies_file="/srv/cookies.txt")
    options = ytdlp.calls[0][2]
    assert options["cookiefile"] == "/srv/cookies.txt"
    assert options["outtmpl"].endswith("7_src.%(ext)s")
    assert options["merge_output_format"] == "mp4"
    assert options["noplaylist"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_downloader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.pipeline.downloader'`

- [ ] **Step 3: Write the implementation**

`contentbot/pipeline/downloader.py`:
```python
"""Download a candidate video: the direct media URL first, then yt-dlp."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import httpx

from contentbot.models import Candidate

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
YTDLP_FORMAT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b"


class DownloadError(Exception):
    """The video could not be downloaded."""


def run_ytdlp(url: str, dest: Path, options: dict) -> None:
    import yt_dlp

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])


class Downloader:
    def __init__(
        self,
        http: httpx.AsyncClient,
        videos_dir: Path,
        *,
        cookies_file: str | None = None,
        ffmpeg_path: str = "ffmpeg",
        max_bytes: int = 300 * 1024 * 1024,
        ytdlp_fn: Callable[[str, Path, dict], None] | None = None,
    ) -> None:
        self.http = http
        self.videos_dir = videos_dir
        self.cookies_file = cookies_file
        self.ffmpeg_path = ffmpeg_path
        self.max_bytes = max_bytes
        self._ytdlp = ytdlp_fn or run_ytdlp

    async def fetch(self, video_id: int, candidate: Candidate) -> Path:
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        dest = self.videos_dir / f"{video_id}_src.mp4"
        if candidate.media_url and ".m3u8" not in candidate.media_url:
            try:
                await self._direct(candidate.media_url, dest)
                return dest
            except Exception as exc:  # expired link, login page, network error: try yt-dlp next
                log.info("Direct download failed for %s: %s; trying yt-dlp", candidate.url, exc)
        try:
            await asyncio.to_thread(self._ytdlp, candidate.url, dest, self._ytdlp_options(dest))
        except Exception as exc:
            raise DownloadError(f"yt-dlp failed: {exc}") from exc
        if not dest.exists() or dest.stat().st_size == 0:
            raise DownloadError("yt-dlp produced no file")
        return dest

    async def _direct(self, url: str, dest: Path) -> None:
        part = dest.with_suffix(".part")
        try:
            async with self.http.stream(
                "GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120
            ) as response:
                response.raise_for_status()
                if "text/html" in response.headers.get("content-type", ""):
                    raise DownloadError("got a web page instead of a video")
                size = 0
                with part.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise DownloadError("file too large")
                        fh.write(chunk)
            if size == 0:
                raise DownloadError("empty file")
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)

    def _ytdlp_options(self, dest: Path) -> dict:
        options = {
            "format": YTDLP_FORMAT,
            "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": self.max_bytes,
            "http_headers": {"User-Agent": USER_AGENT},
        }
        if self.cookies_file:
            options["cookiefile"] = self.cookies_file
        if self.ffmpeg_path != "ffmpeg":
            options["ffmpeg_location"] = self.ffmpeg_path
        return options
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_downloader.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/pipeline/downloader.py tests/test_downloader.py
git commit -m "feat: download videos via direct URL with yt-dlp fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Source helpers and YouTube Shorts source

**Files:**
- Create: `contentbot/sources/base.py`, `contentbot/sources/youtube.py`
- Test: `tests/test_sources_base.py`, `tests/test_youtube.py`

**Interfaces:**
- Consumes: `Candidate`, `Keyword` (models).
- Produces:
  - `SourceResult(candidates=[], cost_usd=0.0)`.
  - `Source` Protocol: attributes `name: str`, `timeout_s: float`, `is_paid: bool`; method `async search(keywords) -> SourceResult`.
  - `as_query(text)` strips `#`; `as_hashtag(text)` strips `#` and spaces.
  - `unique(items) -> list[str]`, `as_dict(value) -> dict`, `to_int(value) -> int|None`, `to_float(value) -> float|None`, `parse_datetime(value) -> datetime|None` (ISO, RFC 2822 or epoch; always aware).
  - `YouTubeSource(http, api_key, per_keyword=5)`: `name="youtube"`, `timeout_s=90`, `is_paid=False`.
  - `parse_iso_duration(value) -> float|None`, `parse_video_item(item) -> Candidate|None`, `SEARCH_URL`, `VIDEOS_URL`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sources_base.py`:
```python
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
```

`tests/test_youtube.py`:
```python
import httpx
import pytest

from contentbot.models import Keyword
from contentbot.sources.youtube import SEARCH_URL, VIDEOS_URL, YouTubeSource, parse_iso_duration, parse_video_item


def test_parse_iso_duration():
    assert parse_iso_duration("PT1M5S") == 65.0
    assert parse_iso_duration("PT45S") == 45.0
    assert parse_iso_duration("PT2M") == 120.0
    assert parse_iso_duration("PT1H0M1S") == 3601.0
    assert parse_iso_duration("P0D") == 0.0
    assert parse_iso_duration("PT") is None
    assert parse_iso_duration("garbage") is None
    assert parse_iso_duration("") is None


def test_parse_video_item_skips_missing_id():
    assert parse_video_item({"snippet": {"title": "x"}}) is None


async def test_search_collects_unique_ids_and_details(respx_mock):
    search = respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": {"videoId": "a1"}}, {"id": {"videoId": "b2"}}]}),
            httpx.Response(200, json={"items": [{"id": {"videoId": "b2"}}, {"id": {"kind": "youtube#channel"}}]}),
        ]
    )
    videos = respx_mock.get(VIDEOS_URL).respond(
        200,
        json={
            "items": [
                {
                    "id": "a1",
                    "snippet": {
                        "title": "Sofa mechanism",
                        "description": "demo",
                        "channelTitle": "Maker",
                        "publishedAt": "2026-09-01T10:00:00Z",
                        "tags": ["sofa bed"],
                        "thumbnails": {"high": {"url": "https://i.ytimg.com/a1.jpg"}},
                    },
                    "contentDetails": {"duration": "PT1M5S"},
                    "statistics": {"viewCount": "12345"},
                },
                {"id": "b2", "snippet": {"title": "Foam"}, "contentDetails": {"duration": "PT30S"}, "statistics": {}},
            ]
        },
    )
    keywords = [Keyword(1, "sofa mechanism", "en", "query"), Keyword(2, "#upholstery", "en", "hashtag")]
    async with httpx.AsyncClient() as http:
        result = await YouTubeSource(http, "KEY", per_keyword=5).search(keywords)
    assert [c.platform_id for c in result.candidates] == ["a1", "b2"]
    a1 = result.candidates[0]
    assert a1.duration_s == 65.0 and a1.views == 12345
    assert a1.url == "https://www.youtube.com/shorts/a1"
    assert a1.thumbnail_url == "https://i.ytimg.com/a1.jpg"
    assert "#sofabed" in a1.description and a1.author == "Maker"
    assert result.candidates[1].views is None
    assert result.cost_usd == 0.0
    first, second = search.calls[0].request.url.params, search.calls[1].request.url.params
    assert first["videoDuration"] == "short" and first["type"] == "video" and first["maxResults"] == "5"
    assert second["q"] == "upholstery"
    assert videos.calls[0].request.url.params["id"] == "a1,b2"


async def test_quota_error_raises(respx_mock):
    respx_mock.get(SEARCH_URL).respond(403, json={"error": {"message": "quotaExceeded"}})
    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.HTTPStatusError):
            await YouTubeSource(http, "KEY").search([Keyword(1, "sofa", "en", "query")])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sources_base.py tests/test_youtube.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.sources.base'`

- [ ] **Step 3: Write the implementation**

`contentbot/sources/base.py`:
```python
"""What every video source returns, plus forgiving parsing helpers for scraped data."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from contentbot.models import Candidate, Keyword


@dataclass
class SourceResult:
    candidates: list[Candidate] = field(default_factory=list)
    cost_usd: float = 0.0


class Source(Protocol):
    name: str
    timeout_s: float
    is_paid: bool

    async def search(self, keywords: list[Keyword]) -> SourceResult: ...


def as_query(text: str) -> str:
    return text.strip().lstrip("#").strip()


def as_hashtag(text: str) -> str:
    return text.strip().lstrip("#").replace(" ", "")


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
```

`contentbot/sources/youtube.py`:
```python
"""YouTube Shorts through the official YouTube Data API v3 (free daily quota)."""
from __future__ import annotations

import re

import httpx

from contentbot.models import Candidate, Keyword
from contentbot.sources.base import SourceResult, as_query, parse_datetime, to_int

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_iso_duration(value: str) -> float | None:
    if not value or value in ("P", "PT"):
        return None
    match = _DURATION.match(value)
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def parse_video_item(item: dict) -> Candidate | None:
    video_id = item.get("id")
    if not video_id or not isinstance(video_id, str):
        return None
    snippet = item.get("snippet") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumbnail = next((thumbs[k]["url"] for k in ("high", "medium", "default") if thumbs.get(k, {}).get("url")), None)
    tags = " ".join("#" + str(t).replace(" ", "") for t in (snippet.get("tags") or [])[:15])
    description = f"{snippet.get('description', '')} {tags}".strip()
    return Candidate(
        platform="youtube",
        platform_id=video_id,
        url=f"https://www.youtube.com/shorts/{video_id}",
        thumbnail_url=thumbnail,
        title=str(snippet.get("title", "")),
        description=description,
        author=str(snippet.get("channelTitle", "")),
        duration_s=parse_iso_duration((item.get("contentDetails") or {}).get("duration", "")),
        views=to_int((item.get("statistics") or {}).get("viewCount")),
        published_at=parse_datetime(snippet.get("publishedAt")),
    )


class YouTubeSource:
    name = "youtube"
    timeout_s = 90.0
    is_paid = False

    def __init__(self, http: httpx.AsyncClient, api_key: str, per_keyword: int = 5) -> None:
        self.http = http
        self.api_key = api_key
        self.per_keyword = per_keyword

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        ids: list[str] = []
        for keyword in keywords:
            response = await self.http.get(
                SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": as_query(keyword.text),
                    "type": "video",
                    "videoDuration": "short",
                    "maxResults": self.per_keyword,
                    "key": self.api_key,
                },
            )
            response.raise_for_status()
            for item in response.json().get("items", []):
                video_id = (item.get("id") or {}).get("videoId")
                if video_id and video_id not in ids:
                    ids.append(video_id)
        candidates: list[Candidate] = []
        for start in range(0, len(ids), 50):
            response = await self.http.get(
                VIDEOS_URL,
                params={"part": "snippet,contentDetails,statistics", "id": ",".join(ids[start : start + 50]), "key": self.api_key},
            )
            response.raise_for_status()
            for item in response.json().get("items", []):
                candidate = parse_video_item(item)
                if candidate:
                    candidates.append(candidate)
        return SourceResult(candidates)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sources_base.py tests/test_youtube.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/sources/base.py contentbot/sources/youtube.py tests/test_sources_base.py tests/test_youtube.py
git commit -m "feat: add YouTube Shorts source and parsing helpers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Apify runner with TikTok, Instagram and Pinterest sources

**Files:**
- Create: `contentbot/sources/apify.py`, `contentbot/sources/tiktok.py`, `contentbot/sources/instagram.py`, `contentbot/sources/pinterest.py`
- Test: `tests/test_apify_sources.py`

**Interfaces:**
- Consumes: `ApifyActor` (config); `SourceResult`, `as_query`, `as_hashtag`, `unique`, `as_dict`, `to_int`, `to_float`, `parse_datetime` (sources/base); `Candidate`, `Keyword` (models).
- Produces:
  - `APIFY_BASE`, `ApifyError(Exception)`.
  - `ApifyRunner(http, token)` with `async run(actor_id, run_input, *, max_items, max_charge_usd, timeout_s=280) -> list[dict]`.
  - `charge_cap(actor) -> float` and `estimate_cost(items, actor) -> float`.
  - `TikTokSource(runner, actor)`, `InstagramSource(runner, actor)`, `PinterestSource(runner, actor)`. Each has `name` (`"tiktok"`, `"instagram"`, `"pinterest"`), `timeout_s=330`, `is_paid=True`, `build_input(keywords) -> dict` and `async search(keywords) -> SourceResult`. `cost_usd` is the estimated cost.
  - `parse_tiktok_item`, `parse_instagram_item` and `parse_pinterest_item` (each `dict -> Candidate | None`).

- [ ] **Step 1: Write the failing tests**

`tests/test_apify_sources.py`:
```python
import json

import httpx
import pytest

from contentbot.config import ApifyActor
from contentbot.models import Keyword
from contentbot.sources.apify import APIFY_BASE, ApifyError, ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.instagram import InstagramSource, parse_instagram_item
from contentbot.sources.pinterest import PinterestSource, parse_pinterest_item
from contentbot.sources.tiktok import TikTokSource, parse_tiktok_item

TIKTOK_ACTOR = ApifyActor("clockworks/tiktok-scraper", 1.70, 20)
TIKTOK_URL = f"{APIFY_BASE}/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items"

TIKTOK_ITEM = {
    "id": "7350000000000000001",
    "text": "Divan mexanizmi #sofa",
    "webVideoUrl": "https://www.tiktok.com/@maker/video/7350000000000000001",
    "createTimeISO": "2026-09-30T08:00:00.000Z",
    "playCount": 15200,
    "authorMeta": {"name": "maker"},
    "videoMeta": {"duration": 21, "coverUrl": "https://p16.tiktokcdn.com/cover.jpg"},
}
INSTAGRAM_ITEM = {
    "id": "3400000000000000002",
    "type": "Video",
    "shortCode": "C9abc",
    "caption": "Перетяжка дивана #перетяжкамебели",
    "url": "https://www.instagram.com/p/C9abc/",
    "videoUrl": "https://scontent.cdninstagram.com/v.mp4?sig=1",
    "displayUrl": "https://scontent.cdninstagram.com/p.jpg",
    "videoDuration": 34.5,
    "videoPlayCount": 8800,
    "ownerUsername": "obivka_master",
    "timestamp": "2026-09-29T12:00:00.000Z",
    "productType": "clips",
}
PINTEREST_ITEM = {
    "id": "424605071130957254",
    "title": "Sofa upholstery",
    "description": "step by step",
    "url": "https://www.pinterest.com/pin/424605071130957254/",
    "imageUrl": "https://i.pinimg.com/originals/x.jpg",
    "videoUrl": "https://v1.pinimg.com/videos/mc/720p/x.mp4",
    "isVideo": True,
    "createdAt": "Mon, 29 Sep 2026 10:00:00 +0000",
    "pinner": {"username": "homeideas"},
}
KEYWORDS = [Keyword(1, "sofa mechanism", "en", "query"), Keyword(2, "#перетяжка мебели", "ru", "hashtag")]


def test_parse_tiktok_item():
    c = parse_tiktok_item(TIKTOK_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("tiktok", "7350000000000000001", 15200, 21.0)
    assert c.thumbnail_url == "https://p16.tiktokcdn.com/cover.jpg"
    assert c.author == "maker" and c.media_url is None


def test_parse_tiktok_item_tolerates_odd_types():
    c = parse_tiktok_item({**TIKTOK_ITEM, "playCount": "2300", "videoMeta": "broken", "authorMeta": None})
    assert c.views == 2300 and c.duration_s is None and c.thumbnail_url is None and c.author == ""


def test_parse_tiktok_item_without_id_is_skipped():
    assert parse_tiktok_item({**TIKTOK_ITEM, "id": None}) is None
    assert parse_tiktok_item({"text": "no url"}) is None


def test_parse_instagram_reel():
    c = parse_instagram_item(INSTAGRAM_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("instagram", "3400000000000000002", 8800, 34.5)
    assert c.media_url.startswith("https://scontent") and c.author == "obivka_master"


def test_instagram_photo_post_is_skipped():
    assert parse_instagram_item({**INSTAGRAM_ITEM, "type": "Image", "productType": "feed"}) is None


def test_instagram_uses_ig_play_count_when_needed():
    item = {k: v for k, v in INSTAGRAM_ITEM.items() if k != "videoPlayCount"}
    assert parse_instagram_item({**item, "igPlayCount": 4100}).views == 4100


def test_parse_pinterest_video_pin():
    c = parse_pinterest_item(PINTEREST_ITEM)
    assert (c.platform, c.platform_id, c.views, c.duration_s) == ("pinterest", "424605071130957254", None, None)
    assert c.media_url.endswith(".mp4") and c.author == "homeideas" and c.published_at is not None


def test_pinterest_image_pin_is_skipped():
    assert parse_pinterest_item({**PINTEREST_ITEM, "isVideo": False, "videoUrl": None}) is None


def test_pinterest_pinner_as_plain_string():
    assert parse_pinterest_item({**PINTEREST_ITEM, "pinner": "someone"}).author == "someone"


def test_tiktok_input_splits_queries_and_hashtags():
    data = TikTokSource(None, TIKTOK_ACTOR).build_input(KEYWORDS)
    assert data["searchQueries"] == ["sofa mechanism"]
    assert data["hashtags"] == ["перетяжкамебели"]
    assert data["resultsPerPage"] == 10
    assert data["shouldDownloadVideos"] is False


def test_instagram_input_turns_keywords_into_hashtags():
    data = InstagramSource(None, ApifyActor("apify/instagram-hashtag-scraper", 2.6, 20)).build_input(KEYWORDS)
    assert data == {"hashtags": ["sofamechanism", "перетяжкамебели"], "resultsType": "reels", "resultsLimit": 10}


def test_pinterest_input_uses_plain_queries():
    data = PinterestSource(None, ApifyActor("cirkit/pinterest-pins-scraper", 2.0, 30)).build_input(KEYWORDS)
    assert data == {"searchQueries": ["sofa mechanism", "перетяжка мебели"], "maxResults": 30, "enrichWithDetails": False}


def test_cost_helpers():
    assert charge_cap(ApifyActor("x", 2.0, 30)) == 0.14
    assert estimate_cost(2, TIKTOK_ACTOR) == 0.0034


async def test_runner_calls_run_sync_endpoint(respx_mock):
    route = respx_mock.post(TIKTOK_URL).respond(201, json=[TIKTOK_ITEM, "junk"])
    async with httpx.AsyncClient() as http:
        items = await ApifyRunner(http, "TOKEN").run(
            "clockworks/tiktok-scraper", {"a": 1}, max_items=20, max_charge_usd=0.1
        )
    assert items == [TIKTOK_ITEM]
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer TOKEN"
    assert request.url.params["maxItems"] == "20"
    assert request.url.params["maxTotalChargeUsd"] == "0.10"
    assert json.loads(request.content) == {"a": 1}


async def test_runner_raises_on_http_error(respx_mock):
    respx_mock.post(TIKTOK_URL).respond(402, text="Payment required")
    async with httpx.AsyncClient() as http:
        with pytest.raises(ApifyError, match="402"):
            await ApifyRunner(http, "TOKEN").run("clockworks/tiktok-scraper", {}, max_items=1, max_charge_usd=0.1)


async def test_source_search_estimates_cost(respx_mock):
    respx_mock.post(TIKTOK_URL).respond(201, json=[TIKTOK_ITEM, {**TIKTOK_ITEM, "id": None}])
    async with httpx.AsyncClient() as http:
        result = await TikTokSource(ApifyRunner(http, "T"), TIKTOK_ACTOR).search([Keyword(1, "sofa", "en", "query")])
    assert len(result.candidates) == 1
    assert result.cost_usd == 0.0034
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_apify_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.sources.apify'`

- [ ] **Step 3: Write the implementation**

`contentbot/sources/apify.py`:
```python
"""Run an Apify actor synchronously and return its dataset items."""
from __future__ import annotations

import httpx

from contentbot.config import ApifyActor

APIFY_BASE = "https://api.apify.com/v2"


class ApifyError(Exception):
    """Apify returned an error or unexpected data."""


def charge_cap(actor: ApifyActor) -> float:
    """Hard spending cap for one run: 1.5x the expected cost plus a little for the start fee."""
    return round(actor.max_results * actor.price_per_1000 / 1000 * 1.5 + 0.05, 2)


def estimate_cost(items: int, actor: ApifyActor) -> float:
    return round(items * actor.price_per_1000 / 1000, 4)


class ApifyRunner:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self.http = http
        self.token = token

    async def run(
        self, actor_id: str, run_input: dict, *, max_items: int, max_charge_usd: float, timeout_s: int = 280
    ) -> list[dict]:
        url = f"{APIFY_BASE}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items"
        response = await self.http.post(
            url,
            params={
                "timeout": timeout_s,
                "maxItems": max_items,
                "maxTotalChargeUsd": f"{max_charge_usd:.2f}",
                "format": "json",
                "clean": "true",
            },
            json=run_input,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout_s + 40,
        )
        if response.status_code >= 400:
            raise ApifyError(f"Apify {actor_id} HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if not isinstance(data, list):
            raise ApifyError(f"Apify {actor_id} returned unexpected data")
        return [item for item in data if isinstance(item, dict)]
```

`contentbot/sources/tiktok.py`:
```python
"""TikTok videos through the Apify actor clockworks/tiktok-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import SourceResult, as_dict, as_hashtag, as_query, parse_datetime, to_float, to_int, unique


def parse_tiktok_item(item: dict) -> Candidate | None:
    video_id = item.get("id")
    url = item.get("webVideoUrl")
    if not video_id or not url:
        return None
    meta = as_dict(item.get("videoMeta"))
    return Candidate(
        platform="tiktok",
        platform_id=str(video_id),
        url=str(url),
        media_url=None,
        thumbnail_url=meta.get("coverUrl"),
        title="",
        description=str(item.get("text") or ""),
        author=str(as_dict(item.get("authorMeta")).get("name") or ""),
        duration_s=to_float(meta.get("duration")),
        views=to_int(item.get("playCount")),
        published_at=parse_datetime(item.get("createTimeISO")),
    )


class TikTokSource:
    name = "tiktok"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        queries = unique(as_query(k.text) for k in keywords if k.kind == "query")
        hashtags = unique(as_hashtag(k.text) for k in keywords if k.kind == "hashtag")
        per_term = max(1, self.actor.max_results // max(1, len(queries) + len(hashtags)))
        return {
            "searchQueries": queries,
            "hashtags": hashtags,
            "resultsPerPage": per_term,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = [c for c in (parse_tiktok_item(i) for i in items) if c]
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
```

`contentbot/sources/instagram.py`:
```python
"""Instagram Reels by hashtag through the Apify actor apify/instagram-hashtag-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import SourceResult, as_hashtag, parse_datetime, to_float, to_int, unique


def parse_instagram_item(item: dict) -> Candidate | None:
    is_video = item.get("type") == "Video" or item.get("productType") == "clips"
    post_id = item.get("id") or item.get("shortCode")
    page_url = item.get("url")
    if not (is_video and post_id and page_url):
        return None
    views = to_int(item.get("videoPlayCount")) or to_int(item.get("igPlayCount")) or to_int(item.get("videoViewCount"))
    return Candidate(
        platform="instagram",
        platform_id=str(post_id),
        url=str(page_url),
        media_url=item.get("videoUrl") or None,
        thumbnail_url=item.get("displayUrl") or None,
        title="",
        description=str(item.get("caption") or ""),
        author=str(item.get("ownerUsername") or ""),
        duration_s=to_float(item.get("videoDuration")),
        views=views,
        published_at=parse_datetime(item.get("timestamp")),
    )


class InstagramSource:
    name = "instagram"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        hashtags = unique(as_hashtag(k.text) for k in keywords)
        per_tag = max(1, self.actor.max_results // max(1, len(hashtags)))
        return {"hashtags": hashtags, "resultsType": "reels", "resultsLimit": per_tag}

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = [c for c in (parse_instagram_item(i) for i in items) if c]
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
```

`contentbot/sources/pinterest.py`:
```python
"""Pinterest video pins through the Apify actor cirkit/pinterest-pins-scraper."""
from __future__ import annotations

from contentbot.config import ApifyActor
from contentbot.models import Candidate, Keyword
from contentbot.sources.apify import ApifyRunner, charge_cap, estimate_cost
from contentbot.sources.base import SourceResult, as_query, parse_datetime, unique


def parse_pinterest_item(item: dict) -> Candidate | None:
    if not item.get("isVideo") or not item.get("videoUrl"):
        return None
    pin_id = item.get("id")
    page_url = item.get("url")
    if not pin_id or not page_url:
        return None
    pinner = item.get("pinner")
    if isinstance(pinner, dict):
        author = str(pinner.get("username") or pinner.get("fullName") or "")
    else:
        author = str(pinner or "")
    return Candidate(
        platform="pinterest",
        platform_id=str(pin_id),
        url=str(page_url),
        media_url=str(item["videoUrl"]),
        thumbnail_url=item.get("imageUrl") or None,
        title=str(item.get("title") or ""),
        description=str(item.get("description") or ""),
        author=author,
        duration_s=None,
        views=None,
        published_at=parse_datetime(item.get("createdAt")),
    )


class PinterestSource:
    name = "pinterest"
    timeout_s = 330.0
    is_paid = True

    def __init__(self, runner: ApifyRunner, actor: ApifyActor) -> None:
        self.runner = runner
        self.actor = actor

    def build_input(self, keywords: list[Keyword]) -> dict:
        return {
            "searchQueries": unique(as_query(k.text) for k in keywords),
            "maxResults": self.actor.max_results,
            "enrichWithDetails": False,
        }

    async def search(self, keywords: list[Keyword]) -> SourceResult:
        items = await self.runner.run(
            self.actor.actor_id,
            self.build_input(keywords),
            max_items=self.actor.max_results,
            max_charge_usd=charge_cap(self.actor),
        )
        candidates = [c for c in (parse_pinterest_item(i) for i in items) if c]
        return SourceResult(candidates, estimate_cost(len(items), self.actor))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_apify_sources.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/sources tests/test_apify_sources.py
git commit -m "feat: add Apify runner with TikTok, Instagram and Pinterest sources" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Bot texts, slot planning and keyboards

**Files:**
- Create: `contentbot/texts.py`, `contentbot/scheduling.py`, `contentbot/bot/keyboards.py`
- Test: `tests/test_scheduling.py`, `tests/test_keyboards.py`

**Interfaces:**
- Consumes: `VideoRow` (db), `CATEGORIES` and `PLATFORMS` (models).
- Produces:
  - `contentbot.texts`: every user-visible string (constants listed in the code below) plus `COMMANDS`, `PLATFORM_NAMES` and `CATEGORY_NAMES` dicts.
  - `next_free_slot(now, post_times, taken, tz, horizon_days=60) -> datetime` returns an aware UTC datetime strictly after `now` that is not in `taken`. `when_label(slot, now, tz) -> str` returns `"HH:MM"` for the same Tashkent day and `"DD.MM HH:MM"` otherwise.
  - Keyboards: `NOOP = "noop"`, `VideoCb(action:str, vid:int)` (prefix `"v"`; actions `orig, mute, music, track, edit, ok, no, undo, noop`), `MusicCb(action:str, msg:int)` (prefix `"m"`; actions `add, skip`), `review_keyboard(v)`, `approved_keyboard(v, when)`, `posted_keyboard(v, when, link)`, `status_keyboard(v, label)`, `music_ask_keyboard(audio_message_id)`, `source_button(v)` and `post_link(channel_id, message_id) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scheduling.py`:
```python
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
```

`tests/test_keyboards.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scheduling.py tests/test_keyboards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.scheduling'`

- [ ] **Step 3: Write the implementation**

`contentbot/texts.py`:
```python
"""Every message and button text the bot shows (Uzbek, Latin script). Edit wording here only."""

# Review buttons
BTN_ORIGINAL = "🔊 Asl ovoz"
BTN_MUTE = "🔇 Ovozsiz"
BTN_MUSIC = "🎵 Musiqa"
BTN_EDIT = "✏️ Matnni tahrirlash"
BTN_TRACK = "🔀 Boshqa musiqa"
BTN_APPROVE = "✅ Tasdiqlash"
BTN_REJECT = "❌ Rad etish"
BTN_CANCEL = "↩️ Bekor qilish"
BTN_YES = "Ha"
BTN_NO = "Yo'q"
CHECK = " ✓"
SOURCE = "🔗 Manba: {platform} · {score} · {category}"
NO_SCORE = "AI baholamagan"

# Status labels on review messages
WILL_POST = "✅ {when} da joylanadi"
POSTED = "📢 {when} da joylandi"
EXPIRED = "⌛ Muddati o'tdi"
REJECTED = "❌ Rad etildi"

# Short pop-up answers to button presses
RENDERING = "⏳ Tayyorlanmoqda…"
ALREADY = "Bu video allaqachon ko'rib chiqilgan"
NEED_CAPTION = "✏️ Avval matn yozing"
MUSIC_EMPTY = "🎵 Musiqa kutubxonasi bo'sh"
SOUND_FAILED = "⚠️ Ovozni o'zgartirib bo'lmadi"
APPROVED_TOAST = "✅ Tasdiqlandi"

# Caption editing
EDIT_PROMPT = "✏️ Yangi matnni yuboring (shu xabarga javob qilib)."
EDIT_CURRENT = "Hozirgi matn (bosib nusxa oling):"
TOO_LONG = "⚠️ Matn {n} belgiga uzun"
AI_FAILED_BODY = "✏️ AI ishlamadi, matnni o'zingiz yozing"

# Messages from the daily search and posting
SOURCE_FAILED = "⚠️ Bugun {source} ishlamadi"
ONLY_N = "Bugun faqat {n} ta yaxshi video topildi"
AI_UNAVAILABLE = "⚠️ AI ishlamadi: videolar ko'rishlar soni bo'yicha tanlandi"
RUN_FAILED = "⚠️ Qidiruvda xatolik yuz berdi, loglarni tekshiring"
APIFY_BUDGET = "⚠️ Apify oylik limiti tugadi. TikTok, Instagram va Pinterest keyingi oygacha to'xtatildi"
POST_FAILED = "⚠️ Kanalga joylab bo'lmadi. Keyingi urinish: {when}"

# Commands
SEARCH_STARTED = "🔎 Qidiruv boshlandi"
SEARCH_RUNNING = "Qidiruv allaqachon ishlayapti"
QUEUE_EMPTY = "Navbat bo'sh"
QUEUE_TITLE = "📋 Navbat:"
KW_TITLE = "🔑 Kalit so'zlar:"
KW_ADDED = "✅ Kalit so'z qo'shildi: {text}"
KW_EXISTS = "Bu kalit so'z allaqachon bor"
KW_REMOVED = "🗑 O'chirildi: {text}"
KW_NOT_FOUND = "Bunday kalit so'z topilmadi"
KW_USAGE_ADD = "Masalan: /addkw divan mexanizmi"
KW_USAGE_DEL = "Masalan: /delkw divan mexanizmi"
MUSIC_ASK = "Musiqa kutubxonasiga qo'shilsinmi?"
MUSIC_ADDED = "✅ Qo'shildi: {title}"
MUSIC_TOO_BIG = "⚠️ Fayl juda katta (20 MB gacha bo'lishi kerak)"
NEVER = "hali bo'lmagan"
ERROR_WORD = "xato"
STATUS = (
    "📊 Holat\n"
    "Oxirgi qidiruv: {last_run}\n"
    "Manbalar: {sources}\n"
    "Ko'rib chiqilmoqda: {in_review}\n"
    "Navbatda: {queued}\n"
    "Musiqalar: {tracks}\n"
    "Apify (shu oy): ${spent:.2f} / ${budget:.2f}"
)

COMMANDS = {
    "queue": "Joylanadigan videolar navbati",
    "search": "Hozir qo'shimcha qidiruv",
    "status": "Bot holati",
    "keywords": "Kalit so'zlar ro'yxati",
    "addkw": "Kalit so'z qo'shish",
    "delkw": "Kalit so'zni o'chirish",
}

PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "pinterest": "Pinterest"}

CATEGORY_NAMES = {
    "mechanism": "mexanizm",
    "foam": "porolon",
    "fabric": "mato",
    "leather": "charm",
    "legs": "oyoqlar",
    "tools": "asboblar",
    "fittings": "furnitura",
    "upholstery_work": "obivka",
    "finished_furniture": "tayyor mebel",
    "other": "boshqa",
}
```

`contentbot/scheduling.py`:
```python
"""Posting slot planning in Tashkent time, and short time labels."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_free_slot(
    now: datetime, post_times: Sequence[time], taken: Iterable[datetime], tz: ZoneInfo, horizon_days: int = 60
) -> datetime:
    """Earliest posting time after `now` that no approved video holds yet (returned in UTC)."""
    taken_utc = {t.astimezone(UTC) for t in taken}
    local_now = now.astimezone(tz)
    for offset in range(horizon_days):
        day = local_now.date() + timedelta(days=offset)
        for slot_time in sorted(post_times):
            slot = datetime.combine(day, slot_time, tzinfo=tz)
            if slot > local_now and slot.astimezone(UTC) not in taken_utc:
                return slot.astimezone(UTC)
    raise RuntimeError(f"no free posting slot in the next {horizon_days} days")


def when_label(slot: datetime, now: datetime, tz: ZoneInfo) -> str:
    local = slot.astimezone(tz)
    if local.date() == now.astimezone(tz).date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m %H:%M")
```

`contentbot/bot/keyboards.py`:
```python
"""Inline keyboards and callback data for the review group."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from contentbot import texts
from contentbot.db import VideoRow

NOOP = "noop"


class VideoCb(CallbackData, prefix="v"):
    action: str
    vid: int


class MusicCb(CallbackData, prefix="m"):
    action: str
    msg: int


def _button(text: str, action: str, video_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=VideoCb(action=action, vid=video_id).pack())


def _mark(label: str, active: bool) -> str:
    return label + texts.CHECK if active else label


def source_button(v: VideoRow) -> InlineKeyboardButton:
    score = f"{v.ai_score}/10" if v.ai_score is not None else texts.NO_SCORE
    category = texts.CATEGORY_NAMES.get(v.ai_category or "other", texts.CATEGORY_NAMES["other"])
    platform = texts.PLATFORM_NAMES.get(v.platform, v.platform)
    return InlineKeyboardButton(text=texts.SOURCE.format(platform=platform, score=score, category=category), url=v.url)


def review_keyboard(v: VideoRow) -> InlineKeyboardMarkup:
    mode = v.audio_mode
    second_row = [_button(texts.BTN_EDIT, "edit", v.id)]
    if mode == "music":
        second_row.append(_button(texts.BTN_TRACK, "track", v.id))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(_mark(texts.BTN_ORIGINAL, mode == "original"), "orig", v.id),
                _button(_mark(texts.BTN_MUTE, mode == "mute"), "mute", v.id),
                _button(_mark(texts.BTN_MUSIC, mode == "music"), "music", v.id),
            ],
            second_row,
            [_button(texts.BTN_APPROVE, "ok", v.id), _button(texts.BTN_REJECT, "no", v.id)],
            [source_button(v)],
        ]
    )


def approved_keyboard(v: VideoRow, when: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(texts.WILL_POST.format(when=when), NOOP, v.id), _button(texts.BTN_CANCEL, "undo", v.id)],
            [source_button(v)],
        ]
    )


def posted_keyboard(v: VideoRow, when: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.POSTED.format(when=when), url=link)], [source_button(v)]]
    )


def status_keyboard(v: VideoRow, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(label, NOOP, v.id)], [source_button(v)]])


def music_ask_keyboard(audio_message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_YES, callback_data=MusicCb(action="add", msg=audio_message_id).pack()),
                InlineKeyboardButton(text=texts.BTN_NO, callback_data=MusicCb(action="skip", msg=audio_message_id).pack()),
            ]
        ]
    )


def post_link(channel_id: str, message_id: int) -> str:
    channel_id = str(channel_id)
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"
    return f"https://t.me/c/{channel_id.removeprefix('-100')}/{message_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scheduling.py tests/test_keyboards.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/texts.py contentbot/scheduling.py contentbot/bot/keyboards.py tests/test_scheduling.py tests/test_keyboards.py
git commit -m "feat: add Uzbek bot texts, slot planning and review keyboards" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Discovery and the daily pipeline

**Files:**
- Create: `contentbot/pipeline/discover.py`, `contentbot/pipeline/run.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes:
  - `Database` (Task 3), `Settings` (Task 2), `Source` and `SourceResult` (Task 9).
  - `FilterRules`, `dedupe`, `split`, `order_for_ai`, `Ranked`, `rank`, `CategoryQuota` (Task 4).
  - `texts` (Task 11), `AudioMode`, `Status`, `utc_now` (Task 1).
  - The checker (`score(candidates) -> list[Score|None] | None`), writer (`draft(candidate, score) -> str`), downloader (`fetch(video_id, candidate) -> Path`) and media (`probe(path) -> ProbeInfo`, `render(src, mode, dest, music=None) -> Path`) interfaces from Tasks 6–8.
- Produces:
  - `DiscoverResult(candidates, counts: dict[str,int], errors: dict[str,str], cost_usd: float)` and `async discover(sources, keywords) -> DiscoverResult`.
  - `ReviewSink` Protocol: `async send_candidate(video_id)` and `async notify(text)`.
  - `Pipeline(*, db, settings, sources, checker, writer, downloader, media, sink, dry_run=False, clock=utc_now)` with `.lock: asyncio.Lock`, `.sources` and `async run(trigger="schedule") -> dict`. The returned summary has the keys `trigger, found, errors, apify_cost_usd, keywords, new, checked, selected`, or is `{"skipped": "already running"}`.
  - `PrintSink(db)` with `.sent`, `.notes` and `report() -> str`.
- Each selected video ends up with status `SELECTED`, `caption_body` set, `original_path = data/videos/{id}_src.mp4` and `rendered_path = data/videos/{id}_original.mp4`. After that, `sink.send_candidate(id)` is called; the sink sets `IN_REVIEW`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pipeline.py`:
```python
import asyncio
from datetime import UTC, datetime

from contentbot import texts
from contentbot.db import Database
from contentbot.models import Score, Status
from contentbot.pipeline.discover import discover
from contentbot.pipeline.media import ProbeInfo
from contentbot.pipeline.run import Pipeline, PrintSink
from contentbot.sources.base import SourceResult
from tests.factories import make_candidate, make_settings

NOW = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)  # 07:00 in Tashkent


class FakeSource:
    def __init__(self, name, candidates=(), *, paid=False, error=None, cost=0.0, delay=0.0, timeout_s=5.0):
        self.name = name
        self.timeout_s = timeout_s
        self.is_paid = paid
        self.candidates = list(candidates)
        self.error = error
        self.cost = cost
        self.delay = delay
        self.calls = 0

    async def search(self, keywords):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SourceResult(list(self.candidates), self.cost)


class FakeChecker:
    def __init__(self, scores=None, *, unavailable=False):
        self.scores = scores or {}
        self.unavailable = unavailable
        self.seen = []

    async def score(self, candidates):
        self.seen.extend(c.platform_id for c in candidates)
        if self.unavailable:
            return None
        return [self.scores.get(c.platform_id) for c in candidates]


class FakeWriter:
    async def draft(self, candidate, score):
        return f"#test {candidate.platform_id}"


class FakeDownloader:
    def __init__(self, videos_dir, fail_ids=()):
        self.videos_dir = videos_dir
        self.fail_ids = set(fail_ids)
        self.fetched = []

    async def fetch(self, video_id, candidate):
        self.fetched.append(candidate.platform_id)
        if candidate.platform_id in self.fail_ids:
            raise RuntimeError("blocked")
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        path = self.videos_dir / f"{video_id}_src.mp4"
        path.write_text(candidate.platform_id)
        return path


class PipelineMedia:
    """probe() reports the duration configured for the platform_id written in the file."""

    def __init__(self, durations=None):
        self.durations = durations or {}

    async def probe(self, path):
        return ProbeInfo(self.durations.get(path.read_text(), 30.0), "h264", "aac", 10)

    async def render(self, src, mode, dest, music=None):
        dest.write_bytes(b"rendered")
        return dest


class RecordingSink:
    def __init__(self):
        self.sent = []
        self.notes = []
        self.fail_next = 0

    async def send_candidate(self, video_id):
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("telegram down")
        self.sent.append(video_id)

    async def notify(self, text):
        self.notes.append(text)


def build(tmp_path, sources, checker, *, fail_ids=(), durations=None, dry_run=False, **settings_overrides):
    settings = make_settings(tmp_path, **settings_overrides)
    db = Database(":memory:")
    db.seed_keywords({"en": ["sofa mechanism", "upholstery"]})
    sink = RecordingSink()
    downloader = FakeDownloader(settings.data_dir / "videos", fail_ids)
    pipeline = Pipeline(
        db=db,
        settings=settings,
        sources=sources,
        checker=checker,
        writer=FakeWriter(),
        downloader=downloader,
        media=PipelineMedia(durations),
        sink=sink,
        dry_run=dry_run,
        clock=lambda: NOW,
    )
    return pipeline, db, sink, downloader


def score(value, category):
    return Score(value, category, False, f"{category} video")


def video_by_pid(db, platform_id):
    row = db.conn.execute("SELECT id FROM videos WHERE platform_id = ?", (platform_id,)).fetchone()
    return db.get_video(row[0])


def sent_ids(db, sink):
    return [db.get_video(i).platform_id for i in sink.sent]


async def test_discover_isolates_failures_and_sums_cost():
    good = FakeSource("youtube", [make_candidate(platform_id="a")])
    paid = FakeSource("tiktok", [make_candidate(platform="tiktok", platform_id="t")], paid=True, cost=0.02)
    broken = FakeSource("instagram", error=RuntimeError("blocked"))
    result = await discover([good, paid, broken], [])
    assert [c.platform_id for c in result.candidates] == ["a", "t"]
    assert result.counts == {"youtube": 1, "tiktok": 1}
    assert result.errors["instagram"].startswith("RuntimeError")
    assert result.cost_usd == 0.02


async def test_discover_times_out_slow_sources():
    result = await discover([FakeSource("pinterest", delay=1.0, timeout_s=0.05)], [])
    assert result.errors["pinterest"].startswith("TimeoutError")


async def test_happy_path_respects_score_and_category_cap(tmp_path):
    candidates = [make_candidate(platform_id=p) for p in ("a", "b", "c", "d", "e")]
    checker = FakeChecker(
        {"a": score(9, "mechanism"), "b": score(8, "mechanism"), "c": score(7, "mechanism"), "d": score(6, "foam"), "e": score(3, "fabric")}
    )
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("youtube", candidates)], checker)
    summary = await pipeline.run("schedule")
    assert sent_ids(db, sink) == ["a", "b", "d"]
    a = video_by_pid(db, "a")
    assert a.status is Status.SELECTED
    assert a.caption_body == "#test a"
    assert a.original_path.endswith(f"{a.id}_src.mp4")
    assert a.rendered_path.endswith(f"{a.id}_original.mp4")
    assert (a.ai_score, a.ai_category) == (9, "mechanism")
    assert video_by_pid(db, "c").status is Status.SCORED
    assert texts.ONLY_N.format(n=3) in sink.notes
    assert summary["selected"] == 3 and summary["found"] == {"youtube": 5}


async def test_seen_videos_are_skipped(tmp_path):
    cands = [make_candidate(platform_id="a"), make_candidate(platform_id="b")]
    checker = FakeChecker({"a": score(9, "foam"), "b": score(9, "legs")})
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("youtube", cands)], checker)
    db.insert_video(make_candidate(platform_id="a"), Status.POSTED, NOW)
    await pipeline.run()
    assert checker.seen == ["b"]
    assert sent_ids(db, sink) == ["b"]


async def test_filtered_out_candidates_are_remembered(tmp_path):
    source = FakeSource("youtube", [make_candidate(platform_id="long", duration_s=300)])
    pipeline, db, _, _ = build(tmp_path, [source], FakeChecker())
    await pipeline.run()
    assert video_by_pid(db, "long").status is Status.FILTERED_OUT
    await pipeline.run()
    assert db.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1


async def test_ai_unavailable_ranks_by_views_and_warns(tmp_path):
    cands = [make_candidate(platform_id="few", views=2000), make_candidate(platform_id="many", views=9000)]
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("youtube", cands)], FakeChecker(unavailable=True))
    await pipeline.run()
    assert sent_ids(db, sink) == ["many", "few"]
    assert texts.AI_UNAVAILABLE in sink.notes


async def test_failed_download_takes_the_next_candidate(tmp_path):
    cands = [make_candidate(platform_id="a"), make_candidate(platform_id="b")]
    checker = FakeChecker({"a": score(9, "foam"), "b": score(8, "legs")})
    pipeline, db, sink, _ = build(
        tmp_path, [FakeSource("youtube", cands)], checker, fail_ids={"a"}, candidates_per_day=1
    )
    await pipeline.run()
    assert sent_ids(db, sink) == ["b"]
    failed = video_by_pid(db, "a")
    assert failed.status is Status.FAILED and "blocked" in failed.error


async def test_too_long_after_download_is_dropped(tmp_path):
    cands = [
        make_candidate(platform="pinterest", platform_id="p", duration_s=None, views=None),
        make_candidate(platform_id="b"),
    ]
    checker = FakeChecker({"p": score(9, "fabric"), "b": score(8, "legs")})
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("mixed", cands)], checker, durations={"p": 300.0})
    await pipeline.run()
    assert sent_ids(db, sink) == ["b"]
    p = video_by_pid(db, "p")
    assert p.status is Status.FILTERED_OUT and p.duration_s == 300.0
    assert not (tmp_path / "data" / "videos" / f"{p.id}_src.mp4").exists()


async def test_review_send_failure_skips_only_that_video(tmp_path):
    cands = [make_candidate(platform_id="a"), make_candidate(platform_id="b")]
    checker = FakeChecker({"a": score(9, "foam"), "b": score(8, "legs")})
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("youtube", cands)], checker)
    sink.fail_next = 1
    await pipeline.run()
    assert sent_ids(db, sink) == ["b"]
    failed = video_by_pid(db, "a")
    assert failed.status is Status.FAILED and "telegram down" in failed.error


async def test_source_error_is_reported_and_others_continue(tmp_path):
    sources = [FakeSource("youtube", [make_candidate(platform_id="a")]), FakeSource("instagram", error=RuntimeError("x"))]
    pipeline, db, sink, _ = build(tmp_path, sources, FakeChecker({"a": score(8, "foam")}))
    summary = await pipeline.run()
    assert texts.SOURCE_FAILED.format(source="Instagram") in sink.notes
    assert "instagram" in summary["errors"]
    assert sent_ids(db, sink) == ["a"]


async def test_budget_reached_skips_paid_sources_and_warns_once(tmp_path):
    free = FakeSource("youtube")
    paid = FakeSource("tiktok", paid=True)
    pipeline, db, sink, _ = build(tmp_path, [free, paid], FakeChecker(), apify_monthly_budget_usd=1.0)
    earlier = db.start_run("schedule", NOW)
    db.finish_run(earlier, {"apify_cost_usd": 1.5}, NOW)
    await pipeline.run()
    await pipeline.run()
    assert free.calls == 2 and paid.calls == 0
    assert sink.notes.count(texts.APIFY_BUDGET) == 1


async def test_second_run_while_running_is_skipped(tmp_path):
    slow = FakeSource("youtube", delay=0.2)
    pipeline, *_ = build(tmp_path, [slow], FakeChecker())
    first = asyncio.create_task(pipeline.run())
    await asyncio.sleep(0.05)
    assert await pipeline.run("manual") == {"skipped": "already running"}
    await first
    assert slow.calls == 1


async def test_dry_run_downloads_nothing(tmp_path):
    source = FakeSource("youtube", [make_candidate(platform_id="a")])
    pipeline, db, sink, downloader = build(tmp_path, [source], FakeChecker({"a": score(8, "foam")}), dry_run=True)
    await pipeline.run("dry-run")
    assert downloader.fetched == []
    assert sent_ids(db, sink) == ["a"]


async def test_no_active_keywords_stops_early(tmp_path):
    source = FakeSource("youtube", [make_candidate()])
    pipeline, db, _, _ = build(tmp_path, [source], FakeChecker())
    for keyword in db.list_keywords():
        db.deactivate_keyword(keyword.text)
    summary = await pipeline.run()
    assert source.calls == 0
    assert summary["errors"] == {"keywords": "no active keywords"}


async def test_print_sink_report():
    db = Database(":memory:")
    sink = PrintSink(db)
    video_id = db.insert_video(make_candidate(platform_id="a"), Status.SELECTED, NOW)
    db.update_video(video_id, ai_score=8, ai_category="foam", caption_body="#porolon Zo'r!")
    await sink.send_candidate(video_id)
    await sink.notify("note")
    report = sink.report()
    assert "score=8 foam" in report and "#porolon Zo'r!" in report and "NOTE: note" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.pipeline.discover'`

- [ ] **Step 3: Write the implementation**

`contentbot/pipeline/discover.py`:
```python
"""Run every source at the same time; one broken source never stops the others."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from contentbot.models import Candidate, Keyword
from contentbot.sources.base import Source

log = logging.getLogger(__name__)


@dataclass
class DiscoverResult:
    candidates: list[Candidate] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0


async def discover(sources: Sequence[Source], keywords: list[Keyword]) -> DiscoverResult:
    async def one(source: Source):
        return await asyncio.wait_for(source.search(keywords), timeout=source.timeout_s)

    outcomes = await asyncio.gather(*(one(s) for s in sources), return_exceptions=True)
    result = DiscoverResult()
    for source, outcome in zip(sources, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("Source %s failed: %r", source.name, outcome)
            result.errors[source.name] = f"{type(outcome).__name__}: {outcome}"[:200]
            continue
        result.candidates.extend(outcome.candidates)
        result.counts[source.name] = len(outcome.candidates)
        result.cost_usd += outcome.cost_usd
    result.cost_usd = round(result.cost_usd, 4)
    return result
```

`contentbot/pipeline/run.py`:
```python
"""One full run: search → free filter → AI check → pick → download → caption → review group."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from contentbot import texts
from contentbot.config import Settings
from contentbot.db import Database
from contentbot.models import AudioMode, Status, utc_now
from contentbot.pipeline.discover import discover
from contentbot.pipeline.picker import CategoryQuota, Ranked, rank
from contentbot.pipeline.prefilter import FilterRules, dedupe, order_for_ai, split
from contentbot.sources.base import Source

log = logging.getLogger(__name__)


class ReviewSink(Protocol):
    async def send_candidate(self, video_id: int) -> None: ...

    async def notify(self, text: str) -> None: ...


class PrintSink:
    """Collects what a dry run would have sent to Telegram."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.sent: list[int] = []
        self.notes: list[str] = []

    async def send_candidate(self, video_id: int) -> None:
        self.sent.append(video_id)

    async def notify(self, text: str) -> None:
        self.notes.append(text)

    def report(self) -> str:
        lines: list[str] = []
        for video_id in self.sent:
            v = self.db.get_video(video_id)
            score = v.ai_score if v.ai_score is not None else "-"
            lines.append(f"[{v.platform}] score={score} {v.ai_category or '-'} {v.url}")
            lines.append("    " + v.caption_body.replace("\n", " "))
        lines += [f"NOTE: {note}" for note in self.notes]
        return "\n".join(lines) or "Nothing selected."


class Pipeline:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        sources: Sequence[Source],
        checker: Any,
        writer: Any,
        downloader: Any,
        media: Any,
        sink: ReviewSink,
        dry_run: bool = False,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db = db
        self.settings = settings
        self.sources = list(sources)
        self.checker = checker
        self.writer = writer
        self.downloader = downloader
        self.media = media
        self.sink = sink
        self.dry_run = dry_run
        self.clock = clock
        self.rules = FilterRules.from_settings(settings)
        self.videos_dir = settings.data_dir / "videos"
        self.lock = asyncio.Lock()

    async def run(self, trigger: str = "schedule") -> dict:
        if self.lock.locked():
            return {"skipped": "already running"}
        async with self.lock:
            return await self._run(trigger)

    async def _run(self, trigger: str) -> dict:
        started = self.clock()
        run_id = self.db.start_run(trigger, started)
        summary: dict = {"trigger": trigger, "found": {}, "errors": {}, "apify_cost_usd": 0.0}
        try:
            await self._steps(started, summary)
        except Exception as exc:
            log.exception("Pipeline run failed")
            summary["fatal"] = f"{type(exc).__name__}: {exc}"[:300]
            await self._notify(texts.RUN_FAILED)
        finally:
            self.db.finish_run(run_id, summary, self.clock())
        return summary

    async def _steps(self, now: datetime, summary: dict) -> None:
        s = self.settings
        keywords = self.db.pick_keywords(s.keywords_per_run)
        if not keywords:
            summary["errors"] = {"keywords": "no active keywords"}
            return
        self.db.mark_keywords_used([k.id for k in keywords], now)
        summary["keywords"] = [k.text for k in keywords]

        result = await discover(await self._sources_within_budget(now), keywords)
        summary.update(found=result.counts, errors=result.errors, apify_cost_usd=result.cost_usd)
        for name in result.errors:
            await self._notify(texts.SOURCE_FAILED.format(source=texts.PLATFORM_NAMES.get(name, name)))

        fresh = [c for c in dedupe(result.candidates) if not self.db.video_exists(c.platform, c.platform_id)]
        passed, dropped = split(fresh, self.rules)
        ordered = order_for_ai(passed)
        to_check, overflow = ordered[: s.ai_check_limit], ordered[s.ai_check_limit :]
        for c in dropped + overflow:
            self.db.insert_video(c, Status.FILTERED_OUT, now)
        ids = [self.db.insert_video(c, Status.FOUND, now) for c in to_check]
        summary.update(new=len(fresh), checked=len(to_check))

        scores = await self.checker.score(to_check)
        ai_ok = scores is not None
        items: list[Ranked] = []
        for video_id, candidate, score in zip(ids, to_check, scores if ai_ok else [None] * len(to_check), strict=True):
            if score is not None:
                self.db.update_video(
                    video_id, status=Status.SCORED, ai_score=score.score, ai_category=score.category, ai_reason=score.reason
                )
            items.append(Ranked(video_id, candidate, score))

        quota = CategoryQuota(s.candidates_per_day, s.max_per_category)
        selected: list[Ranked] = []
        for item in rank(items, s.min_ai_score, ai_ok):
            if quota.full:
                break
            category = item.score.category if item.score else None
            if not quota.wants(category):
                continue
            if await self._prepare(item):
                quota.take(category)
                selected.append(item)
        summary["selected"] = len(selected)

        for item in selected:
            body = await self.writer.draft(item.candidate, item.score)
            self.db.update_video(item.video_id, caption_body=body, status=Status.SELECTED)
            try:
                await self.sink.send_candidate(item.video_id)
            except Exception as exc:  # one Telegram failure must not lose the rest of the batch
                log.warning("Sending video %s to review failed: %s", item.video_id, exc)
                self.db.update_video(item.video_id, status=Status.FAILED, error=f"send failed: {exc}"[:300])

        if not ai_ok and to_check:
            await self._notify(texts.AI_UNAVAILABLE)
        if len(selected) < s.candidates_per_day:
            await self._notify(texts.ONLY_N.format(n=len(selected)))

    async def _prepare(self, item: Ranked) -> bool:
        """Download, check the real duration and render a Telegram-ready original."""
        if self.dry_run:
            return True
        video_id = item.video_id
        try:
            source = await self.downloader.fetch(video_id, item.candidate)
            info = await self.media.probe(source)
            if not self.rules.min_duration_s <= info.duration_s <= self.rules.max_duration_s:
                source.unlink(missing_ok=True)
                self.db.update_video(
                    video_id, status=Status.FILTERED_OUT, duration_s=info.duration_s, error="duration out of range"
                )
                return False
            rendered = await self.media.render(source, AudioMode.ORIGINAL, self.videos_dir / f"{video_id}_original.mp4")
        except Exception as exc:  # any download or ffmpeg failure: take the next candidate
            log.warning("Preparing video %s failed: %s", video_id, exc)
            self.db.update_video(video_id, status=Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:300])
            return False
        self.db.update_video(
            video_id,
            original_path=str(source),
            rendered_path=str(rendered),
            audio_mode=AudioMode.ORIGINAL.value,
            duration_s=info.duration_s,
        )
        return True

    async def _sources_within_budget(self, now: datetime) -> list[Source]:
        local = now.astimezone(self.settings.timezone)
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self.db.apify_spend_since(month_start) < self.settings.apify_monthly_budget_usd:
            return list(self.sources)
        month = local.strftime("%Y-%m")
        if self.db.get_meta("apify_budget_warned") != month:
            self.db.set_meta("apify_budget_warned", month)
            await self._notify(texts.APIFY_BUDGET)
        return [s for s in self.sources if not s.is_paid]

    async def _notify(self, text: str) -> None:
        try:
            await self.sink.notify(text)
        except Exception:  # a failed warning message must not stop the run
            log.exception("Could not send notification")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/pipeline/discover.py contentbot/pipeline/run.py tests/test_pipeline.py
git commit -m "feat: add discovery and the daily pipeline run" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Review group service and its Telegram router

**Files:**
- Create: `contentbot/bot/review.py`
- Modify: `tests/fakes.py` (append `FakeBot`, `telegram_error`, `FakeMedia`)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes:
  - Keyboards and `NOOP` (Task 11), `next_free_slot`, `when_label` (Task 11), `texts` (Task 11).
  - `build_caption`, `caption_overflow` (Task 5), `MediaError` (Task 7).
  - `Database`, `VideoRow`, `MusicTrack` (Task 3), `Settings`, `Secrets` (Task 2).
  - The bot object: an aiogram `Bot`, or `FakeBot` in tests. Methods used: `send_video`, `send_message`, `edit_message_media`, `edit_message_caption`, `edit_message_reply_markup`, `delete_message`; all are called with keyword arguments.
- Produces:
  - `Outcome` (StrEnum): `OK, ALREADY, NEED_CAPTION, MUSIC_EMPTY, FAILED`. Also `SOUND_ACTIONS: dict[str, AudioMode]` and `TOASTS: dict[Outcome, str]`.
  - `ReviewService(bot, db, settings, secrets, media, *, clock=utc_now, rng=None)`, which also satisfies `ReviewSink`. Methods:
    - Pipeline sink: `notify(text)`, `send_candidate(video_id)`.
    - Read-only helpers: `preview_caption(v) -> str`, `has_music() -> bool`.
    - Button actions (all async): `set_sound(video_id, action) -> Outcome`, `start_caption_edit(video_id) -> Outcome`, `apply_caption_reply(prompt_message_id, reply_message_id, text) -> Outcome | None` (returns `None` when the reply is not to a caption prompt), `approve`, `undo` and `reject` (each `video_id -> Outcome`).
    - Cleanup: `expire_old(now=None) -> int`.
  - `build_review_router(service, chat_id) -> aiogram.Router`.
  - `tests.fakes.FakeBot`: `.calls` is a list of `(method, kwargs, message_id)`; it also has `.failures: dict[str, int]`, `.named(method) -> list[kwargs]` and `.last_id(method) -> int`. `telegram_error(method)` builds a test error, and `FakeMedia(fail=False)` has `.renders` and `.fail`.

- [ ] **Step 1: Extend the test fakes**

Append to `tests/fakes.py`:
```python
from pathlib import Path  # noqa: E402

from contentbot.pipeline.media import MediaError  # noqa: E402


def telegram_error(method: str = "send_video"):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    return TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message=f"{method} failed")


class FakeBot:
    """Records Bot API calls and returns objects with a message_id, like aiogram does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int | None]] = []
        self.failures: dict[str, int] = {}
        self._next_id = 1000

    async def _call(self, method: str, kwargs: dict):
        if self.failures.get(method, 0) > 0:
            self.failures[method] -= 1
            self.calls.append((method, kwargs, None))
            raise telegram_error(method)
        self._next_id += 1
        self.calls.append((method, kwargs, self._next_id))
        return SimpleNamespace(message_id=self._next_id)

    async def send_video(self, **kwargs):
        return await self._call("send_video", kwargs)

    async def send_message(self, **kwargs):
        return await self._call("send_message", kwargs)

    async def edit_message_media(self, **kwargs):
        return await self._call("edit_message_media", kwargs)

    async def edit_message_caption(self, **kwargs):
        return await self._call("edit_message_caption", kwargs)

    async def edit_message_reply_markup(self, **kwargs):
        return await self._call("edit_message_reply_markup", kwargs)

    async def delete_message(self, **kwargs):
        await self._call("delete_message", kwargs)
        return True

    async def download(self, file, destination=None, **kwargs):
        self.calls.append(("download", {"file": file, "destination": destination}, None))
        Path(destination).write_bytes(b"audio")

    def named(self, method: str) -> list[dict]:
        return [kwargs for name, kwargs, _ in self.calls if name == method]

    def last_id(self, method: str) -> int:
        return [message_id for name, _, message_id in self.calls if name == method][-1]


class FakeMedia:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.renders: list[tuple] = []

    async def render(self, src, mode, dest, music=None):
        self.renders.append((mode, music))
        if self.fail:
            raise MediaError("ffmpeg exploded")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"rendered")
        return dest
```

- [ ] **Step 2: Write the failing tests**

`tests/test_review.py`:
```python
import asyncio
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.review import Outcome, ReviewService
from contentbot.db import Database
from contentbot.models import AudioMode, Status
from tests.factories import make_candidate, make_secrets, make_settings
from tests.fakes import FakeBot, FakeMedia

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent
TEN_AM = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
ONE_PM = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    media = FakeMedia()
    service = ReviewService(bot, db, settings, make_secrets(), media, clock=lambda: NOW, rng=random.Random(1))
    yield SimpleNamespace(settings=settings, db=db, bot=bot, media=media, service=service)
    db.close()


async def sent_video(env, platform_id="v1", body="#mexanizm Zo'r mexanizm!"):
    """A downloaded, captioned video that has been sent to the review group."""
    videos = env.settings.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    video_id = env.db.insert_video(make_candidate(platform_id=platform_id), Status.SELECTED, NOW)
    src = videos / f"{video_id}_src.mp4"
    src.write_bytes(b"src")
    rendered = videos / f"{video_id}_original.mp4"
    rendered.write_bytes(b"orig")
    env.db.update_video(
        video_id, original_path=str(src), rendered_path=str(rendered), caption_body=body, ai_score=8, ai_category="mechanism"
    )
    await env.service.send_candidate(video_id)
    return video_id


def add_tracks(env, count):
    env.settings.music_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        path = env.settings.music_dir / f"t{i}.mp3"
        path.write_bytes(b"mp3")
        env.db.add_track(str(path), f"t{i}", NOW)


async def test_send_candidate_posts_preview_and_marks_in_review(env):
    video_id = await sent_video(env)
    call = env.bot.named("send_video")[0]
    assert call["chat_id"] == -100111
    assert call["caption"].startswith("#mexanizm Zo'r mexanizm!")
    assert call["caption"].endswith(env.settings.footer)
    v = env.db.get_video(video_id)
    assert v.status is Status.IN_REVIEW
    assert v.review_message_id == env.bot.last_id("send_video")
    assert v.review_sent_at == NOW


async def test_empty_caption_preview_asks_a_human(env):
    await sent_video(env, body="")
    assert env.bot.named("send_video")[0]["caption"].startswith(texts.AI_FAILED_BODY)


async def test_mute_replaces_video_in_same_message(env):
    video_id = await sent_video(env)
    old_render = env.db.get_video(video_id).rendered_path
    assert await env.service.set_sound(video_id, "mute") is Outcome.OK
    assert env.media.renders[0][0] is AudioMode.MUTE
    v = env.db.get_video(video_id)
    edit = env.bot.named("edit_message_media")[0]
    assert edit["message_id"] == v.review_message_id
    assert edit["reply_markup"].inline_keyboard[0][1].text == texts.BTN_MUTE + texts.CHECK
    assert v.audio_mode == "mute"
    assert not Path(old_render).exists()
    assert Path(v.original_path).exists()


async def test_render_failure_keeps_previous_version(env):
    video_id = await sent_video(env)
    env.media.fail = True
    assert await env.service.set_sound(video_id, "mute") is Outcome.FAILED
    assert env.db.get_video(video_id).audio_mode == "original"
    assert env.bot.named("edit_message_media") == []


async def test_music_without_tracks_reports_empty(env):
    video_id = await sent_video(env)
    assert env.service.has_music() is False
    assert await env.service.set_sound(video_id, "music") is Outcome.MUSIC_EMPTY
    assert env.media.renders == []


async def test_another_track_picks_a_different_track(env):
    video_id = await sent_video(env)
    add_tracks(env, 3)
    assert await env.service.set_sound(video_id, "music") is Outcome.OK
    first = env.db.get_video(video_id).music_track_id
    assert await env.service.set_sound(video_id, "track") is Outcome.OK
    second = env.db.get_video(video_id).music_track_id
    assert first is not None and second is not None and second != first


async def test_double_approve_gives_one_slot(env):
    video_id = await sent_video(env)
    outcomes = await asyncio.gather(env.service.approve(video_id), env.service.approve(video_id))
    assert sorted(outcomes) == [Outcome.ALREADY, Outcome.OK]
    assert env.db.approved_slots() == [TEN_AM]
    markup = env.bot.named("edit_message_reply_markup")[-1]["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "✅ 10:00 da joylanadi"


async def test_approve_needs_a_caption(env):
    video_id = await sent_video(env, body="")
    assert await env.service.approve(video_id) is Outcome.NEED_CAPTION
    assert env.db.get_video(video_id).status is Status.IN_REVIEW


async def test_cancel_frees_the_slot(env):
    first = await sent_video(env, "a")
    second = await sent_video(env, "b")
    await env.service.approve(first)
    await env.service.approve(second)
    assert env.db.get_video(second).slot_at == ONE_PM
    assert await env.service.undo(first) is Outcome.OK
    undone = env.db.get_video(first)
    assert undone.status is Status.IN_REVIEW and undone.slot_at is None
    third = await sent_video(env, "c")
    await env.service.approve(third)
    assert env.db.get_video(third).slot_at == TEN_AM


async def test_caption_edit_round_trip(env):
    video_id = await sent_video(env)
    assert await env.service.start_caption_edit(video_id) is Outcome.OK
    prompt = env.bot.named("send_message")[-1]
    assert prompt["parse_mode"] == "HTML"
    assert "<code>#mexanizm Zo&#x27;r mexanizm!</code>" in prompt["text"]
    prompt_id = env.bot.last_id("send_message")
    assert await env.service.apply_caption_reply(prompt_id, 555, "Yangi matn!") is Outcome.OK
    assert env.db.get_video(video_id).caption_body == "Yangi matn!"
    assert env.bot.named("edit_message_caption")[0]["caption"].startswith("Yangi matn!")
    assert {kw["message_id"] for kw in env.bot.named("delete_message")} == {prompt_id, 555}
    assert env.db.caption_edit_video(prompt_id) is None


async def test_too_long_caption_is_refused(env):
    video_id = await sent_video(env)
    await env.service.start_caption_edit(video_id)
    prompt_id = env.bot.last_id("send_message")
    assert await env.service.apply_caption_reply(prompt_id, 556, "🛋" * 520) is Outcome.FAILED
    assert env.bot.named("send_message")[-1]["text"].startswith("⚠️ Matn")
    assert env.db.get_video(video_id).caption_body == "#mexanizm Zo'r mexanizm!"
    assert env.db.caption_edit_video(prompt_id) == video_id  # the reviewer can try again


async def test_replies_to_other_messages_are_ignored(env):
    await sent_video(env)
    before = len(env.bot.calls)
    assert await env.service.apply_caption_reply(99999, 1, "salom") is None
    assert len(env.bot.calls) == before


async def test_reject_deletes_message_and_files(env):
    video_id = await sent_video(env)
    v = env.db.get_video(video_id)
    assert await env.service.reject(video_id) is Outcome.OK
    assert env.db.get_video(video_id).status is Status.REJECTED
    assert env.bot.named("delete_message")[0]["message_id"] == v.review_message_id
    assert not Path(v.original_path).exists() and not Path(v.rendered_path).exists()


async def test_expire_old_after_48_hours(env):
    video_id = await sent_video(env)
    assert await env.service.expire_old(NOW + timedelta(hours=47)) == 0
    assert await env.service.expire_old(NOW + timedelta(hours=49)) == 1
    v = env.db.get_video(video_id)
    assert v.status is Status.EXPIRED and not Path(v.rendered_path).exists()
    markup = env.bot.named("edit_message_reply_markup")[-1]["reply_markup"]
    assert markup.inline_keyboard[0][0].text == texts.EXPIRED
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.bot.review'`

- [ ] **Step 4: Write the implementation**

`contentbot/bot/review.py`:
```python
"""Review group: send candidates, change sound, edit captions, approve, reject, expire."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ForceReply, FSInputFile, InputMediaVideo, Message, ReplyParameters

from contentbot import texts
from contentbot.bot.keyboards import NOOP, VideoCb, approved_keyboard, review_keyboard, status_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import Database, MusicTrack, VideoRow
from contentbot.models import AudioMode, Status, utc_now
from contentbot.pipeline.captions import build_caption, caption_overflow
from contentbot.pipeline.media import MediaError
from contentbot.scheduling import next_free_slot, when_label

log = logging.getLogger(__name__)

SOUND_ACTIONS = {
    "orig": AudioMode.ORIGINAL,
    "mute": AudioMode.MUTE,
    "music": AudioMode.MUSIC,
    "track": AudioMode.MUSIC,
}


class Outcome(StrEnum):
    OK = "ok"
    ALREADY = "already"
    NEED_CAPTION = "need_caption"
    MUSIC_EMPTY = "music_empty"
    FAILED = "failed"


TOASTS = {
    Outcome.ALREADY: texts.ALREADY,
    Outcome.NEED_CAPTION: texts.NEED_CAPTION,
    Outcome.MUSIC_EMPTY: texts.MUSIC_EMPTY,
    Outcome.FAILED: texts.SOUND_FAILED,
}


class ReviewService:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        media: Any,
        *,
        clock: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.media = media
        self.chat_id = secrets.review_chat_id
        self.clock = clock
        self.rng = rng or random.Random()
        self.videos_dir = settings.data_dir / "videos"
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- used by the pipeline -------------------------------------------
    async def notify(self, text: str) -> None:
        await self.bot.send_message(chat_id=self.chat_id, text=text)

    async def send_candidate(self, video_id: int) -> None:
        v = self._get(video_id)
        message = await self.bot.send_video(
            chat_id=self.chat_id,
            video=FSInputFile(v.rendered_path),
            caption=self.preview_caption(v),
            reply_markup=review_keyboard(v),
            supports_streaming=True,
        )
        self.db.update_video(
            video_id, status=Status.IN_REVIEW, review_message_id=message.message_id, review_sent_at=self.clock()
        )

    def preview_caption(self, v: VideoRow) -> str:
        return build_caption(v.caption_body or texts.AI_FAILED_BODY, self.settings.footer)

    def has_music(self) -> bool:
        return bool(self._existing_tracks())

    # ---- sound ------------------------------------------------------------
    async def set_sound(self, video_id: int, action: str) -> Outcome:
        mode = SOUND_ACTIONS[action]
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            if action != "track" and v.audio_mode == mode.value:
                return Outcome.OK
            track: MusicTrack | None = None
            if mode is AudioMode.MUSIC:
                track = self._pick_track(exclude_id=v.music_track_id)
                if track is None:
                    return Outcome.MUSIC_EMPTY
            dest = self.videos_dir / f"{v.id}_{mode.value}_{uuid.uuid4().hex[:8]}.mp4"
            try:
                await self.media.render(
                    Path(v.original_path), mode, dest, music=Path(track.file_path) if track else None
                )
            except MediaError as exc:
                log.warning("Sound change failed for video %s: %s", video_id, exc)
                return Outcome.FAILED
            old_render = v.rendered_path
            self.db.update_video(
                v.id, audio_mode=mode.value, music_track_id=track.id if track else None, rendered_path=str(dest)
            )
            if track:
                self.db.mark_track_used(track.id, self.clock())
            v = self._get(v.id)
            await self.bot.edit_message_media(
                chat_id=self.chat_id,
                message_id=v.review_message_id,
                media=InputMediaVideo(media=FSInputFile(dest), caption=self.preview_caption(v), supports_streaming=True),
                reply_markup=review_keyboard(v),
            )
            if old_render and old_render not in (v.original_path, str(dest)):
                Path(old_render).unlink(missing_ok=True)
            return Outcome.OK

    def _existing_tracks(self) -> list[MusicTrack]:
        return [t for t in self.db.list_tracks() if Path(t.file_path).exists()]

    def _pick_track(self, exclude_id: int | None) -> MusicTrack | None:
        tracks = self._existing_tracks()
        if not tracks:
            return None
        used = [t for t in tracks if t.last_used_at is not None]
        last_id = max(used, key=lambda t: t.last_used_at).id if used else None
        pool = (
            [t for t in tracks if t.id not in (exclude_id, last_id)]
            or [t for t in tracks if t.id != exclude_id]
            or tracks
        )
        return self.rng.choice(pool)

    # ---- caption ------------------------------------------------------------
    async def start_caption_edit(self, video_id: int) -> Outcome:
        v = self.db.get_video(video_id)
        if v is None or v.status != Status.IN_REVIEW:
            return Outcome.ALREADY
        text = texts.EDIT_PROMPT
        if v.caption_body:
            text += f"\n\n{texts.EDIT_CURRENT}\n<code>{html.escape(v.caption_body)}</code>"
        prompt = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode="HTML",
            reply_parameters=ReplyParameters(message_id=v.review_message_id),
            reply_markup=ForceReply(),
        )
        self.db.add_caption_edit(prompt.message_id, v.id)
        return Outcome.OK

    async def apply_caption_reply(self, prompt_message_id: int, reply_message_id: int, text: str | None) -> Outcome | None:
        video_id = self.db.caption_edit_video(prompt_message_id)
        if video_id is None or not text or not text.strip():
            return None
        v = self.db.get_video(video_id)
        if v is None or v.status != Status.IN_REVIEW:
            self.db.remove_caption_edit(prompt_message_id)
            return Outcome.ALREADY
        overflow = caption_overflow(text, self.settings.footer)
        if overflow:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=texts.TOO_LONG.format(n=overflow),
                reply_parameters=ReplyParameters(message_id=reply_message_id),
            )
            return Outcome.FAILED
        self.db.update_video(v.id, caption_body=text.strip())
        v = self._get(v.id)
        await self.bot.edit_message_caption(
            chat_id=self.chat_id,
            message_id=v.review_message_id,
            caption=self.preview_caption(v),
            reply_markup=review_keyboard(v),
        )
        self.db.remove_caption_edit(prompt_message_id)
        await self._delete(prompt_message_id)
        await self._delete(reply_message_id)
        return Outcome.OK

    # ---- decisions ----------------------------------------------------------
    async def approve(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            if not v.caption_body.strip():
                return Outcome.NEED_CAPTION
            now = self.clock()
            slot = next_free_slot(now, self.settings.post_times, self.db.approved_slots(), self.settings.timezone)
            self.db.update_video(v.id, status=Status.APPROVED, slot_at=slot)
            v = self._get(v.id)
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id,
                message_id=v.review_message_id,
                reply_markup=approved_keyboard(v, when_label(slot, now, self.settings.timezone)),
            )
            return Outcome.OK

    async def undo(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.APPROVED:
                return Outcome.ALREADY
            self.db.update_video(v.id, status=Status.IN_REVIEW, slot_at=None)
            v = self._get(v.id)
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=review_keyboard(v)
            )
            return Outcome.OK

    async def reject(self, video_id: int) -> Outcome:
        async with self._locks[video_id]:
            v = self.db.get_video(video_id)
            if v is None or v.status != Status.IN_REVIEW:
                return Outcome.ALREADY
            self.db.update_video(v.id, status=Status.REJECTED)
            self._delete_files(v)
            if not await self._delete(v.review_message_id):
                await self._safe(
                    self.bot.edit_message_reply_markup(
                        chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=status_keyboard(v, texts.REJECTED)
                    )
                )
            return Outcome.OK

    async def expire_old(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        cutoff = now - timedelta(hours=self.settings.review_expiry_hours)
        expired = 0
        for v in self.db.review_sent_before(cutoff):
            self.db.update_video(v.id, status=Status.EXPIRED)
            self._delete_files(v)
            await self._safe(
                self.bot.edit_message_reply_markup(
                    chat_id=self.chat_id, message_id=v.review_message_id, reply_markup=status_keyboard(v, texts.EXPIRED)
                )
            )
            expired += 1
        return expired

    # ---- helpers ------------------------------------------------------------
    def _get(self, video_id: int) -> VideoRow:
        v = self.db.get_video(video_id)
        if v is None:
            raise LookupError(f"video {video_id} not found")
        return v

    @staticmethod
    def _delete_files(v: VideoRow) -> None:
        for path in {v.original_path, v.rendered_path}:
            if path:
                Path(path).unlink(missing_ok=True)

    async def _delete(self, message_id: int | None) -> bool:
        if message_id is None:
            return False
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            return True
        except TelegramAPIError as exc:
            log.info("Could not delete message %s: %s", message_id, exc)
            return False

    async def _safe(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except TelegramAPIError as exc:
            log.warning("Telegram call failed: %s", exc)


def build_review_router(service: ReviewService, chat_id: int) -> Router:
    router = Router(name="review")
    router.message.filter(F.chat.id == chat_id)
    router.callback_query.filter(F.message.chat.id == chat_id)

    @router.callback_query(VideoCb.filter())
    async def on_video_button(query: CallbackQuery, callback_data: VideoCb) -> None:
        action, video_id = callback_data.action, callback_data.vid
        if action == NOOP:
            await query.answer()
            return
        if action in SOUND_ACTIONS:
            if SOUND_ACTIONS[action] is AudioMode.MUSIC and not service.has_music():
                await query.answer(texts.MUSIC_EMPTY, show_alert=True)
                return
            await query.answer(texts.RENDERING)
            outcome = await service.set_sound(video_id, action)
            if outcome in (Outcome.FAILED, Outcome.MUSIC_EMPTY):
                await service.notify(TOASTS[outcome])
            return
        handlers = {
            "edit": service.start_caption_edit,
            "ok": service.approve,
            "undo": service.undo,
            "no": service.reject,
        }
        handler = handlers.get(action)
        if handler is None:
            await query.answer()
            return
        outcome = await handler(video_id)
        toast = texts.APPROVED_TOAST if action == "ok" and outcome is Outcome.OK else TOASTS.get(outcome)
        await query.answer(toast)

    @router.message(F.reply_to_message, F.text)
    async def on_reply(message: Message) -> None:
        await service.apply_caption_reply(message.reply_to_message.message_id, message.message_id, message.text)

    return router
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_review.py -v`
Expected: 14 passed

- [ ] **Step 6: Commit**

```bash
git add contentbot/bot/review.py tests/fakes.py tests/test_review.py
git commit -m "feat: add review group service with sound, caption and approval actions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Publisher: posting at slot times

**Files:**
- Create: `contentbot/bot/publisher.py`
- Test: `tests/test_publisher.py`

**Interfaces:**
- Consumes: `Database` (Task 3), `next_free_slot`, `when_label`, `approved_keyboard`, `posted_keyboard`, `post_link`, `texts` (Task 11), `build_caption` (Task 5), and `FakeBot` in tests (Task 13).
- Produces: `Publisher(bot, db, settings, secrets, *, clock=utc_now, retry_delays=(60, 120, 240), sleep=asyncio.sleep)` with:
  - `async publish_due(now=None) -> int`: posts every approved video whose slot has arrived; returns how many were posted.
  - `async replan_missed(now=None) -> int`: moves approved videos whose slot passed more than 5 minutes ago to future free slots.
  - After a successful post: status `POSTED`, `posted_at` and `channel_message_id` are set, the review message shows a link to the post, and the files are deleted.
  - After every attempt fails: the video moves to the next free slot and the review group gets `texts.POST_FAILED`.

- [ ] **Step 1: Write the failing tests**

`tests/test_publisher.py`:
```python
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.publisher import Publisher
from contentbot.db import Database
from contentbot.models import Status
from tests.factories import make_candidate, make_secrets, make_settings
from tests.fakes import FakeBot

NOW = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 10:00 in Tashkent


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    publisher = Publisher(bot, db, settings, make_secrets(), clock=lambda: NOW, retry_delays=(1, 2, 4), sleep=fake_sleep)
    yield SimpleNamespace(settings=settings, db=db, bot=bot, publisher=publisher, sleeps=sleeps)
    db.close()


def approved(env, platform_id, slot):
    videos = env.settings.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    video_id = env.db.insert_video(make_candidate(platform_id=platform_id), Status.APPROVED, NOW)
    src = videos / f"{video_id}_src.mp4"
    src.write_bytes(b"x")
    rendered = videos / f"{video_id}_music.mp4"
    rendered.write_bytes(b"x")
    env.db.update_video(
        video_id,
        slot_at=slot,
        caption_body=f"#test {platform_id}",
        original_path=str(src),
        rendered_path=str(rendered),
        review_message_id=77,
    )
    return video_id


async def test_due_video_is_posted_to_channel(env):
    video_id = approved(env, "a", NOW)
    assert await env.publisher.publish_due() == 1
    call = env.bot.named("send_video")[0]
    assert call["chat_id"] == "@test_channel"
    assert call["caption"] == "#test a\n\n" + env.settings.footer
    v = env.db.get_video(video_id)
    assert v.status is Status.POSTED and v.posted_at == NOW
    assert v.channel_message_id == env.bot.last_id("send_video")
    edit = env.bot.named("edit_message_reply_markup")[0]
    assert edit["message_id"] == 77
    button = edit["reply_markup"].inline_keyboard[0][0]
    assert button.url == f"https://t.me/test_channel/{v.channel_message_id}"
    assert button.text == "📢 10:00 da joylandi"
    assert not Path(v.rendered_path).exists() and not Path(v.original_path).exists()


async def test_future_video_waits(env):
    approved(env, "a", NOW + timedelta(hours=3))
    assert await env.publisher.publish_due() == 0
    assert env.bot.named("send_video") == []


async def test_retry_then_success(env):
    approved(env, "a", NOW)
    env.bot.failures["send_video"] = 1
    assert await env.publisher.publish_due() == 1
    assert len(env.bot.named("send_video")) == 2
    assert env.sleeps == [1]


async def test_all_attempts_fail_moves_to_next_slot(env):
    video_id = approved(env, "a", NOW)
    env.bot.failures["send_video"] = 4
    assert await env.publisher.publish_due() == 0
    assert env.sleeps == [1, 2, 4]
    v = env.db.get_video(video_id)
    assert v.status is Status.APPROVED
    assert v.slot_at == datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 13:00 in Tashkent
    sent_texts = [kw["text"] for kw in env.bot.named("send_message")]
    assert texts.POST_FAILED.format(when="13:00") in sent_texts


async def test_replan_missed_slots_after_downtime(env):
    first = approved(env, "a", datetime(2026, 10, 6, 11, 0, tzinfo=UTC))  # yesterday 16:00
    second = approved(env, "b", datetime(2026, 10, 6, 14, 0, tzinfo=UTC))  # yesterday 19:00
    morning = NOW - timedelta(hours=2)  # 08:00 in Tashkent
    assert await env.publisher.replan_missed(morning) == 2
    assert env.db.get_video(first).slot_at == datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 10:00
    assert env.db.get_video(second).slot_at == datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 13:00
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publisher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.bot.publisher'`

- [ ] **Step 3: Write the implementation**

`contentbot/bot/publisher.py`:
```python
"""Post approved videos to the channel when their slot time arrives."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from contentbot import texts
from contentbot.bot.keyboards import approved_keyboard, post_link, posted_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import Database, VideoRow
from contentbot.models import Status, utc_now
from contentbot.pipeline.captions import build_caption
from contentbot.scheduling import next_free_slot, when_label

log = logging.getLogger(__name__)


class Publisher:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        *,
        clock: Callable[[], datetime] = utc_now,
        retry_delays: Sequence[float] = (60, 120, 240),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.channel_id = secrets.channel_id
        self.review_chat_id = secrets.review_chat_id
        self.clock = clock
        self.retry_delays = tuple(retry_delays)
        self.sleep = sleep

    async def publish_due(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        posted = 0
        for video in self.db.due_for_posting(now):
            if await self._post(video):
                posted += 1
        return posted

    async def replan_missed(self, now: datetime | None = None) -> int:
        """After downtime, move videos whose slot already passed to the next free slots."""
        now = now or self.clock()
        moved = 0
        for video in self.db.due_for_posting(now - timedelta(minutes=5)):
            slot = self._next_slot(now, exclude=video.slot_at)
            self.db.update_video(video.id, slot_at=slot)
            await self._show_slot(video.id, slot, now)
            moved += 1
        return moved

    async def _post(self, video: VideoRow) -> bool:
        caption = build_caption(video.caption_body, self.settings.footer)
        attempts = len(self.retry_delays) + 1
        last_error: Exception | None = None
        message = None
        for attempt in range(attempts):
            try:
                message = await self.bot.send_video(
                    chat_id=self.channel_id,
                    video=FSInputFile(video.rendered_path),
                    caption=caption,
                    supports_streaming=True,
                )
                break
            except (TelegramAPIError, OSError) as exc:
                last_error = exc
                log.warning("Posting video %s failed (attempt %s/%s): %s", video.id, attempt + 1, attempts, exc)
                if attempt < len(self.retry_delays):
                    await self.sleep(self.retry_delays[attempt])
        if message is None:
            now = self.clock()
            slot = self._next_slot(now, exclude=video.slot_at)
            self.db.update_video(video.id, slot_at=slot, error=str(last_error)[:300])
            when = when_label(slot, now, self.settings.timezone)
            await self._safe(self.bot.send_message(chat_id=self.review_chat_id, text=texts.POST_FAILED.format(when=when)))
            await self._show_slot(video.id, slot, now)
            return False
        posted_at = self.clock()
        self.db.update_video(video.id, status=Status.POSTED, posted_at=posted_at, channel_message_id=message.message_id)
        updated = self.db.get_video(video.id)
        link = post_link(self.channel_id, message.message_id)
        when = when_label(posted_at, posted_at, self.settings.timezone)
        if video.review_message_id is not None:
            await self._safe(
                self.bot.edit_message_reply_markup(
                    chat_id=self.review_chat_id,
                    message_id=video.review_message_id,
                    reply_markup=posted_keyboard(updated, when, link),
                )
            )
        for path in {video.original_path, video.rendered_path}:
            if path:
                Path(path).unlink(missing_ok=True)
        return True

    def _next_slot(self, now: datetime, exclude: datetime | None) -> datetime:
        taken = [slot for slot in self.db.approved_slots() if slot != exclude]
        return next_free_slot(now, self.settings.post_times, taken, self.settings.timezone)

    async def _show_slot(self, video_id: int, slot: datetime, now: datetime) -> None:
        video = self.db.get_video(video_id)
        if video is None or video.review_message_id is None:
            return
        when = when_label(slot, now, self.settings.timezone)
        await self._safe(
            self.bot.edit_message_reply_markup(
                chat_id=self.review_chat_id, message_id=video.review_message_id, reply_markup=approved_keyboard(video, when)
            )
        )

    async def _safe(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except TelegramAPIError as exc:
            log.warning("Telegram call failed: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publisher.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add contentbot/bot/publisher.py tests/test_publisher.py
git commit -m "feat: post approved videos at slot times with retries" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Review-group commands and music uploads

**Files:**
- Create: `contentbot/bot/commands.py`
- Modify: `tests/fakes.py` (append `FakePipeline`)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `Database` (`approved_in_order`, `count_with_status`, `last_run`, `apify_spend_since`, keyword and music methods; Task 3), `when_label` (Task 11), `texts`, `MusicCb`, `music_ask_keyboard` (Task 11), `MUSIC_SUFFIXES` (Task 3), and a pipeline object with `.lock` and `async run(trigger)` (Task 12).
- Produces:
  - `detect_language(text) -> "zh"|"ru"|"tr"|"uz"|"en"` and `audio_suffix(file_name, mime_type) -> str`.
  - `CommandService(bot, db, settings, secrets, pipeline, *, clock=utc_now)` with `queue_text(now=None)`, `status_text(now=None)`, `keywords_text()`, `add_keyword(raw)`, `remove_keyword(raw)`, `start_search()` (all `-> str`), `remember_audio(message_id, file_id, title, suffix)`, `async save_audio(message_id) -> str | None` and `forget_audio(message_id)`.
  - `build_commands_router(service, chat_id) -> aiogram.Router`.
  - `tests.fakes.FakePipeline` with `.lock`, `.runs` and `.sources`.

- [ ] **Step 1: Extend the test fakes**

Append to `tests/fakes.py`:
```python
import asyncio  # noqa: E402


class FakePipeline:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.runs: list[str] = []
        self.sources: list = []

    async def run(self, trigger: str = "schedule") -> dict:
        self.runs.append(trigger)
        return {}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_commands.py`:
```python
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from contentbot import texts
from contentbot.bot.commands import CommandService, audio_suffix, detect_language
from contentbot.db import Database
from contentbot.models import Status
from tests.factories import make_secrets, make_settings, make_video
from tests.fakes import FakeBot, FakePipeline

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    bot = FakeBot()
    pipeline = FakePipeline()
    service = CommandService(bot, db, settings, make_secrets(), pipeline, clock=lambda: NOW)
    yield SimpleNamespace(settings=settings, db=db, bot=bot, pipeline=pipeline, service=service)
    db.close()


def test_queue_text_lists_approved_videos_in_order(env):
    assert env.service.queue_text() == texts.QUEUE_EMPTY
    make_video(
        env.db, platform_id="b", status=Status.APPROVED, slot_at=datetime(2026, 10, 8, 5, 0, tzinfo=UTC),
        caption_body="Ertangi video", ai_category="foam",
    )
    make_video(
        env.db, platform_id="a", status=Status.APPROVED, slot_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
        caption_body="Bugungi video", ai_category="mechanism",
    )
    lines = env.service.queue_text().splitlines()
    assert lines == [texts.QUEUE_TITLE, "1. 13:00 · mexanizm · Bugungi video", "2. 08.10 10:00 · porolon · Ertangi video"]


def test_status_text(env):
    run = env.db.start_run("schedule", NOW - timedelta(hours=1))
    env.db.finish_run(
        run, {"found": {"youtube": 25}, "errors": {"instagram": "RuntimeError: x"}, "apify_cost_usd": 1.5}, NOW
    )
    make_video(env.db, platform_id="r", status=Status.IN_REVIEW)
    text = env.service.status_text()
    assert "07.10 07:00" in text
    assert "YouTube: 25" in text and "Instagram: xato" in text
    assert "Ko'rib chiqilmoqda: 1" in text
    assert "$1.50 / $5.00" in text


def test_status_before_first_run(env):
    assert texts.NEVER in env.service.status_text()


def test_keyword_commands(env):
    assert env.service.add_keyword("  ") == texts.KW_USAGE_ADD
    assert env.service.add_keyword(None) == texts.KW_USAGE_ADD
    assert env.service.add_keyword("divan mexanizmi") == texts.KW_ADDED.format(text="divan mexanizmi")
    assert env.service.add_keyword("divan mexanizmi") == texts.KW_EXISTS
    assert "en: divan mexanizmi" in env.service.keywords_text()
    assert env.service.remove_keyword("divan mexanizmi") == texts.KW_REMOVED.format(text="divan mexanizmi")
    assert env.service.remove_keyword("divan mexanizmi") == texts.KW_NOT_FOUND
    assert env.service.remove_keyword("") == texts.KW_USAGE_DEL


def test_detect_language():
    assert detect_language("沙发机构") == "zh"
    assert detect_language("перетяжка") == "ru"
    assert detect_language("koltuk döşeme") == "tr"
    assert detect_language("yog'och oyoq") == "uz"
    assert detect_language("sofa legs") == "en"


def test_audio_suffix():
    assert audio_suffix("calm.M4A", None) == ".m4a"
    assert audio_suffix(None, "audio/mpeg") == ".mp3"
    assert audio_suffix("track.xyz", "audio/ogg") == ".ogg"
    assert audio_suffix(None, None) == ".mp3"


async def test_start_search_runs_pipeline_in_background(env):
    assert env.service.start_search() == texts.SEARCH_STARTED
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert env.pipeline.runs == ["manual"]


async def test_start_search_while_running(env):
    async with env.pipeline.lock:
        assert env.service.start_search() == texts.SEARCH_RUNNING
    assert env.pipeline.runs == []


async def test_saving_uploaded_music(env):
    env.service.remember_audio(42, "file-id-1", "Calm piano", ".mp3")
    assert await env.service.save_audio(42) == texts.MUSIC_ADDED.format(title="Calm piano")
    track = env.db.list_tracks()[0]
    assert track.title == "Calm piano" and track.file_path.endswith(".mp3")
    assert env.bot.named("download")[0]["file"] == "file-id-1"
    assert await env.service.save_audio(42) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contentbot.bot.commands'`

- [ ] **Step 4: Write the implementation**

`contentbot/bot/commands.py`:
```python
"""Review-group commands (/queue /search /status /keywords /addkw /delkw) and music uploads."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from contentbot import texts
from contentbot.bot.keyboards import MusicCb, music_ask_keyboard
from contentbot.config import Secrets, Settings
from contentbot.db import MUSIC_SUFFIXES, Database
from contentbot.models import Status, utc_now
from contentbot.scheduling import when_label

log = logging.getLogger(__name__)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
MIME_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def detect_language(text: str) -> str:
    """Rough label for a new keyword; only used for display and rotation."""
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru"
    if re.search(r"[ğışĞİŞ]", text):
        return "tr"
    if re.search(r"[oOgG][ʻ'’`]", text):
        return "uz"
    return "en"


def audio_suffix(file_name: str | None, mime_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix.lower()
        if suffix in MUSIC_SUFFIXES:
            return suffix
    return MIME_SUFFIXES.get(mime_type or "", ".mp3")


class CommandService:
    def __init__(
        self,
        bot: Any,
        db: Database,
        settings: Settings,
        secrets: Secrets,
        pipeline: Any,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.pipeline = pipeline
        self.clock = clock
        self._pending_audio: dict[int, tuple[str, str, str]] = {}
        self._tasks: set[asyncio.Task] = set()

    def queue_text(self, now: datetime | None = None) -> str:
        now = now or self.clock()
        videos = self.db.approved_in_order()
        if not videos:
            return texts.QUEUE_EMPTY
        lines = [texts.QUEUE_TITLE]
        for number, v in enumerate(videos, 1):
            when = when_label(v.slot_at, now, self.settings.timezone)
            category = texts.CATEGORY_NAMES.get(v.ai_category or "other", texts.CATEGORY_NAMES["other"])
            preview = " ".join(v.caption_body.split())[:40]
            lines.append(f"{number}. {when} · {category} · {preview}")
        return "\n".join(lines)

    def status_text(self, now: datetime | None = None) -> str:
        now = now or self.clock()
        tz = self.settings.timezone
        run = self.db.last_run()
        if run is None:
            last_run, sources = texts.NEVER, "-"
        else:
            last_run = run.started_at.astimezone(tz).strftime("%d.%m %H:%M")
            found = run.summary.get("found", {})
            errors = run.summary.get("errors", {})
            parts = [f"{texts.PLATFORM_NAMES.get(n, n)}: {count}" for n, count in found.items()]
            parts += [f"{texts.PLATFORM_NAMES.get(n, n)}: {texts.ERROR_WORD}" for n in errors]
            sources = " · ".join(parts) or "-"
        month_start = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return texts.STATUS.format(
            last_run=last_run,
            sources=sources,
            in_review=self.db.count_with_status(Status.IN_REVIEW),
            queued=self.db.count_with_status(Status.APPROVED),
            tracks=len(self.db.list_tracks()),
            spent=self.db.apify_spend_since(month_start),
            budget=self.settings.apify_monthly_budget_usd,
        )

    def keywords_text(self) -> str:
        by_language: dict[str, list[str]] = {}
        for keyword in self.db.list_keywords():
            by_language.setdefault(keyword.language, []).append(keyword.text)
        lines = [texts.KW_TITLE] + [f"{lang}: {', '.join(words)}" for lang, words in by_language.items()]
        return "\n".join(lines) if by_language else texts.KW_TITLE + "\n-"

    def add_keyword(self, raw: str | None) -> str:
        text = (raw or "").strip()
        if not text:
            return texts.KW_USAGE_ADD
        if self.db.add_keyword(text, detect_language(text)):
            return texts.KW_ADDED.format(text=text)
        return texts.KW_EXISTS

    def remove_keyword(self, raw: str | None) -> str:
        text = (raw or "").strip()
        if not text:
            return texts.KW_USAGE_DEL
        if self.db.deactivate_keyword(text):
            return texts.KW_REMOVED.format(text=text)
        return texts.KW_NOT_FOUND

    def start_search(self) -> str:
        if self.pipeline.lock.locked():
            return texts.SEARCH_RUNNING
        task = asyncio.create_task(self.pipeline.run("manual"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return texts.SEARCH_STARTED

    def remember_audio(self, message_id: int, file_id: str, title: str, suffix: str) -> None:
        self._pending_audio[message_id] = (file_id, title, suffix)

    def forget_audio(self, message_id: int) -> None:
        self._pending_audio.pop(message_id, None)

    async def save_audio(self, message_id: int) -> str | None:
        pending = self._pending_audio.pop(message_id, None)
        if pending is None:
            return None
        file_id, title, suffix = pending
        self.settings.music_dir.mkdir(parents=True, exist_ok=True)
        dest = self.settings.music_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
        await self.bot.download(file_id, destination=dest)
        self.db.add_track(str(dest.resolve()), title, self.clock())
        return texts.MUSIC_ADDED.format(title=title)


def build_commands_router(service: CommandService, chat_id: int) -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.id == chat_id)
    router.callback_query.filter(F.message.chat.id == chat_id)

    @router.message(Command("queue"))
    async def on_queue(message: Message) -> None:
        await message.answer(service.queue_text())

    @router.message(Command("search"))
    async def on_search(message: Message) -> None:
        await message.answer(service.start_search())

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(service.status_text())

    @router.message(Command("keywords"))
    async def on_keywords(message: Message) -> None:
        await message.answer(service.keywords_text())

    @router.message(Command("addkw"))
    async def on_add_keyword(message: Message, command: CommandObject) -> None:
        await message.answer(service.add_keyword(command.args))

    @router.message(Command("delkw"))
    async def on_remove_keyword(message: Message, command: CommandObject) -> None:
        await message.answer(service.remove_keyword(command.args))

    @router.message(F.audio)
    async def on_audio(message: Message) -> None:
        audio = message.audio
        if audio.file_size and audio.file_size > TELEGRAM_DOWNLOAD_LIMIT:
            await message.reply(texts.MUSIC_TOO_BIG)
            return
        title = audio.title or audio.file_name or "track"
        service.remember_audio(message.message_id, audio.file_id, title, audio_suffix(audio.file_name, audio.mime_type))
        await message.reply(texts.MUSIC_ASK, reply_markup=music_ask_keyboard(message.message_id))

    @router.callback_query(MusicCb.filter())
    async def on_music_choice(query: CallbackQuery, callback_data: MusicCb) -> None:
        if callback_data.action == "add":
            result = await service.save_audio(callback_data.msg)
            await query.message.edit_text(result or texts.ALREADY)
        else:
            service.forget_audio(callback_data.msg)
            await query.message.delete()
        await query.answer()

    return router
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_commands.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add contentbot/bot/commands.py tests/fakes.py tests/test_commands.py
git commit -m "feat: add review-group commands and music uploads" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Entry point: wiring, scheduler, logging, dry run

**Files:**
- Create: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: every service from Tasks 2–15.
- Produces:
  - `main(argv=None) -> int`. It returns 2 on a config error and 0 otherwise; the flags are `--dry-run`, `--settings` and `--env`.
  - `setup_logging(log_dir)`, `make_http() -> httpx.AsyncClient`, `build_sources(http, settings, secrets) -> list`, `build_pipeline(db, settings, secrets, http, sink, *, dry_run=False) -> Pipeline`, `async run_dry(settings, secrets)` and `async run_bot(settings, secrets)`.
  - Scheduler jobs:
    - `search`: cron at `search_time`, Tashkent, misfire grace 1 h.
    - `publish`: every 60 s.
    - `expire`: every hour.
    - All jobs use `coalesce=True` and `max_instances=1`.

- [ ] **Step 1: Write the failing tests**

`tests/test_main.py`:
```python
import logging

import httpx
from aiogram import Bot, Dispatcher

from contentbot.bot.commands import CommandService, build_commands_router
from contentbot.bot.review import ReviewService, build_review_router
from contentbot.db import Database
from contentbot.pipeline.run import PrintSink
from main import build_pipeline, main, setup_logging
from tests.factories import make_secrets, make_settings
from tests.fakes import FakePipeline


def test_config_error_exits_with_code_2(tmp_path, capsys):
    code = main(["--settings", str(tmp_path / "missing.yaml"), "--env", str(tmp_path / "none.env")])
    assert code == 2
    assert "Config error" in capsys.readouterr().err


async def test_build_pipeline_wires_all_sources(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(":memory:")
    async with httpx.AsyncClient() as http:
        pipeline = build_pipeline(db, settings, make_secrets(), http, PrintSink(db))
    assert [s.name for s in pipeline.sources] == ["youtube", "tiktok", "instagram", "pinterest"]
    assert pipeline.dry_run is False


async def test_routers_attach_to_a_dispatcher(tmp_path):
    settings = make_settings(tmp_path)
    secrets = make_secrets()
    db = Database(":memory:")
    bot = Bot(token=secrets.telegram_bot_token)
    try:
        review = ReviewService(bot, db, settings, secrets, media=None)
        commands = CommandService(bot, db, settings, secrets, FakePipeline())
        dispatcher = Dispatcher()
        dispatcher.include_router(build_commands_router(commands, secrets.review_chat_id))
        dispatcher.include_router(build_review_router(review, secrets.review_chat_id))
        assert len(dispatcher.sub_routers) == 2
    finally:
        await bot.session.close()


def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        setup_logging(tmp_path / "logs")
        logging.getLogger("contentbot").info("hello")
        assert (tmp_path / "logs" / "bot.log").exists()
    finally:
        for handler in root.handlers[:]:
            handler.close()
        root.handlers[:] = saved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Write the implementation**

`main.py`:
```python
"""Evim content bot.

Run the bot:   python main.py
Test a search: python main.py --dry-run   (searches, AI-checks and drafts captions; sends nothing)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from contentbot import texts
from contentbot.ai.caption_writer import CaptionWriter
from contentbot.ai.claude import ClaudeJSON
from contentbot.ai.relevance import RelevanceChecker
from contentbot.bot.commands import CommandService, build_commands_router
from contentbot.bot.publisher import Publisher
from contentbot.bot.review import ReviewService, build_review_router
from contentbot.config import ConfigError, Secrets, Settings, load_secrets, load_settings
from contentbot.db import Database
from contentbot.models import utc_now
from contentbot.pipeline.downloader import USER_AGENT, Downloader
from contentbot.pipeline.media import Media
from contentbot.pipeline.run import Pipeline, PrintSink, ReviewSink
from contentbot.sources.apify import ApifyRunner
from contentbot.sources.instagram import InstagramSource
from contentbot.sources.pinterest import PinterestSource
from contentbot.sources.tiktok import TikTokSource
from contentbot.sources.youtube import YouTubeSource

log = logging.getLogger("contentbot")


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [file_handler, console]
    logging.getLogger("httpx").setLevel(logging.WARNING)


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def build_sources(http: httpx.AsyncClient, settings: Settings, secrets: Secrets) -> list:
    runner = ApifyRunner(http, secrets.apify_token)
    actors = settings.apify_actors
    return [
        YouTubeSource(http, secrets.youtube_api_key, settings.youtube_per_keyword),
        TikTokSource(runner, actors["tiktok"]),
        InstagramSource(runner, actors["instagram"]),
        PinterestSource(runner, actors["pinterest"]),
    ]


def build_pipeline(
    db: Database,
    settings: Settings,
    secrets: Secrets,
    http: httpx.AsyncClient,
    sink: ReviewSink,
    *,
    dry_run: bool = False,
) -> Pipeline:
    claude = ClaudeJSON(settings.ai_model, api_key=secrets.anthropic_api_key)
    return Pipeline(
        db=db,
        settings=settings,
        sources=build_sources(http, settings, secrets),
        checker=RelevanceChecker(claude, http),
        writer=CaptionWriter(claude, settings.caption_examples),
        downloader=Downloader(
            http,
            settings.data_dir / "videos",
            cookies_file=settings.ytdlp_cookies_file,
            ffmpeg_path=settings.ffmpeg_path,
        ),
        media=Media(settings.ffmpeg_path, settings.ffprobe_path),
        sink=sink,
        dry_run=dry_run,
    )


async def run_dry(settings: Settings, secrets: Secrets) -> None:
    """Search + AI check + captions on a copy of the database; prints instead of sending."""
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "dry-run.db"
        real_path = settings.data_dir / "bot.db"
        if real_path.exists():
            real = Database(real_path)
            real.backup_to(copy_path)
            real.close()
        db = Database(copy_path)
        try:
            db.seed_keywords(settings.seed_keywords)
            sink = PrintSink(db)
            async with make_http() as http:
                summary = await build_pipeline(db, settings, secrets, http, sink, dry_run=True).run("dry-run")
            print(sink.report())
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        finally:
            db.close()


async def run_bot(settings: Settings, secrets: Secrets) -> None:
    settings.music_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "bot.db")
    db.seed_keywords(settings.seed_keywords)
    log.info("Music tracks added from folder: %s", db.sync_music_dir(settings.music_dir, utc_now()))
    bot = Bot(token=secrets.telegram_bot_token)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    try:
        async with make_http() as http:
            review = ReviewService(bot, db, settings, secrets, Media(settings.ffmpeg_path, settings.ffprobe_path))
            pipeline = build_pipeline(db, settings, secrets, http, review)
            publisher = Publisher(bot, db, settings, secrets)
            commands = CommandService(bot, db, settings, secrets, pipeline)

            dispatcher = Dispatcher()
            dispatcher.include_router(build_commands_router(commands, secrets.review_chat_id))
            dispatcher.include_router(build_review_router(review, secrets.review_chat_id))

            scheduler.add_job(
                pipeline.run,
                CronTrigger(
                    hour=settings.search_time.hour, minute=settings.search_time.minute, timezone=settings.timezone
                ),
                kwargs={"trigger": "schedule"},
                id="search",
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
            )
            scheduler.add_job(publisher.publish_due, "interval", seconds=60, id="publish", coalesce=True, max_instances=1)
            scheduler.add_job(review.expire_old, "interval", hours=1, id="expire", coalesce=True, max_instances=1)

            await publisher.replan_missed()
            await bot.set_my_commands(
                [BotCommand(command=name, description=description) for name, description in texts.COMMANDS.items()],
                scope=BotCommandScopeChat(chat_id=secrets.review_chat_id),
            )
            scheduler.start()
            log.info("Bot started. Daily search at %s (%s)", settings.search_time.strftime("%H:%M"), settings.timezone.key)
            await dispatcher.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evim content bot")
    parser.add_argument("--dry-run", action="store_true", help="search, AI-check and draft captions; send nothing")
    parser.add_argument("--settings", default="settings.yaml")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
        secrets = load_secrets(args.env)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    setup_logging(settings.data_dir / "logs")
    try:
        asyncio.run(run_dry(settings, secrets) if args.dry_run else run_bot(settings, secrets))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: 4 passed

Run: `python main.py --help`
Expected: usage text listing `--dry-run`, `--settings`, `--env`

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: wire bot, scheduler, logging and dry-run entry point" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Setup files, owner guide, full verification

**Files:**
- Create: `.env.example`, `deploy/contentbot.service`, `deploy/update-ytdlp.sh`, `README.md`

**Interfaces:**
- Consumes: the variable names in `contentbot/config.py` `SECRET_VARS`; `main.py` flags.
- Produces: documentation and deployment files only.

- [ ] **Step 1: Write the setup files**

`.env.example`:
```dotenv
# Telegram bot token from @BotFather
TELEGRAM_BOT_TOKEN=
# Private review group id (a negative number, e.g. -1001234567890)
REVIEW_CHAT_ID=
# Where approved videos are posted. Start with your private test channel; later: @evim_uzb
CHANNEL_ID=
# https://console.anthropic.com -> API keys
ANTHROPIC_API_KEY=
# Google Cloud -> "YouTube Data API v3" -> Credentials -> API key
YOUTUBE_API_KEY=
# https://console.apify.com -> Settings -> API & Integrations -> Personal API token
APIFY_TOKEN=
```

`deploy/contentbot.service`:
```ini
# Optional: run the bot as a service that restarts on crash and after reboot.
# 1) Replace REPLACE_WITH_LINUX_USER and /opt/contentbot below.
# 2) sudo cp deploy/contentbot.service /etc/systemd/system/
# 3) sudo systemctl daemon-reload && sudo systemctl enable --now contentbot
[Unit]
Description=Evim content bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=REPLACE_WITH_LINUX_USER
WorkingDirectory=/opt/contentbot
ExecStart=/opt/contentbot/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`deploy/update-ytdlp.sh`:
```sh
#!/bin/sh
# Update yt-dlp (platforms change often). Run weekly from cron, for example:
#   0 4 * * 1 /opt/contentbot/deploy/update-ytdlp.sh >> /opt/contentbot/data/logs/ytdlp-update.log 2>&1
# Restart the bot afterwards so it uses the new version.
cd "$(dirname "$0")/.." && .venv/bin/python -m pip install -U yt-dlp
```

`README.md`:
````markdown
# Evim content bot

Every morning the bot finds short soft-furniture videos (YouTube Shorts, TikTok, Instagram Reels, Pinterest), checks them with Claude AI, writes an Uzbek caption draft and sends 8 of them to your private review group. There you choose the sound, edit the caption and approve. Approved videos are posted to the channel at 10:00, 13:00, 16:00 and 19:00 (Tashkent time).

All settings (times, counts, keywords, footer, AI model) are in `settings.yaml`. All bot texts are in `contentbot/texts.py`.

## 1. Create the Telegram parts

1. In Telegram, open **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Create a **private group** for review. Add the bot and make it an **admin**, so it can see uploaded music and delete messages.
3. Create a **private test channel**. Add the bot as an **admin** with "Post messages".
4. Find the ids: send any message in the review group and one post in the test channel. Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The group id is `chat.id` (a negative number such as `-100…`) → `REVIEW_CHAT_ID`. The channel id (also `-100…`) → `CHANNEL_ID`. Do this before you start the bot.

## 2. Create the API keys

- **Claude:** https://console.anthropic.com → add a payment card → API keys → `ANTHROPIC_API_KEY`.
- **YouTube:** https://console.cloud.google.com → new project → enable "YouTube Data API v3" → Credentials → Create API key → `YOUTUBE_API_KEY`.
- **Apify:** https://apify.com → sign up (free plan, $5 credit every month) → Settings → API & Integrations → `APIFY_TOKEN`.

Copy `.env.example` to `.env` and fill in all six values.

## 3. Add calm music

Put about 10 calm tracks (`.mp3` or `.m4a`) into the `music/` folder. Pixabay Music (https://pixabay.com/music/, search "calm" or "ambient") allows business use without credit. You can also send audio files to the review group later; the bot asks "Musiqa kutubxonasiga qo'shilsinmi?".

## 4. Install on the server (Linux)

```bash
sudo apt install python3 python3-venv ffmpeg
cd /opt/contentbot            # the folder with this project
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python 3.11 or newer is required (`python3 --version`).

## 5. Test without sending anything

```bash
.venv/bin/python main.py --dry-run
```

It prints the videos it would send, with scores and caption drafts. Nothing is posted and nothing is marked as seen.

## 6. Start the bot

```bash
nohup .venv/bin/python main.py > nohup.out 2>&1 &
```

Logs: `data/logs/bot.log`. To stop it: `pkill -f "python main.py"`.

Optional auto-restart: see `deploy/contentbot.service`.

## 7. Weekly maintenance

Platforms change often, so update yt-dlp once a week and restart the bot:

```bash
.venv/bin/python -m pip install -U yt-dlp
```

`deploy/update-ytdlp.sh` has a ready cron line.

## 8. Switch to the real channel

When the test channel looks good: make the bot an admin of **@evim_uzb** with "Post messages", set `CHANNEL_ID=@evim_uzb` in `.env` and restart the bot.

## Review group commands

| Command | What it does |
|---|---|
| `/queue` | Approved videos and their posting times |
| `/search` | Run an extra search now |
| `/status` | Last search, sources, queue, music count, Apify spend this month |
| `/keywords` | All search keywords |
| `/addkw <text>` | Add a keyword (`#word` searches as a hashtag) |
| `/delkw <text>` | Remove a keyword |

## Backup

Copy `data/bot.db`, `settings.yaml`, `.env` and `music/`.

## Costs (estimate)

YouTube API: free. Apify: within the free $5/month at the default limits. Claude (Opus 5.5): about $10–20/month; set `ai.model: claude-sonnet-5-5` in `settings.yaml` for about half.
````

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -v`
Expected: 159 passed (or 156 passed, 3 skipped if ffmpeg is not installed).

- [ ] **Step 3: Commit**

```bash
git add .env.example deploy README.md
git commit -m "docs: add setup guide, env template and deployment files" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Owner checkpoint: live dry run (needs the owner's real keys)**

This step needs the six values from section 1–2 of the README. If `.env` is not filled in yet, stop here and report that the code is complete and the dry run is waiting for the keys.

Run: `python main.py --dry-run`
Expected:
- One block per selected video, each starting with `[youtube]`, `[tiktok]`, `[instagram]` or `[pinterest]` and showing a score, a category, the URL and an Uzbek Latin caption that starts with a hashtag.
- A JSON summary in which `found` has counts for the sources that worked.
- Any source error, such as a wrong actor input or a bad key, appears under `errors`. Fix it before going live.

- [ ] **Step 5: Owner checkpoint: live test in the test channel**

Start the bot with `CHANNEL_ID` set to the test channel, then run `/search` in the review group and check each item:
- [ ] The candidates arrive as videos with a caption, the footer and 4 rows of buttons.
- [ ] 🔇 Ovozsiz and 🎵 Musiqa replace the video in the same message, and you can hear the new sound.
- [ ] ✏️ Matnni tahrirlash → replying with new text updates the caption, and the prompt and your reply disappear.
- [ ] ✅ Tasdiqlash shows "✅ HH:MM da joylanadi"; ↩️ Bekor qilish brings the buttons back.
- [ ] At the slot time the video appears in the test channel, and the review message shows "📢 HH:MM da joylandi" with a link.
- [ ] `/queue`, `/status` and `/keywords` answer in Uzbek.

