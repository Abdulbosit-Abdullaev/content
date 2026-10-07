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
