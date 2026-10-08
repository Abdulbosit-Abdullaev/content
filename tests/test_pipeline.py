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


async def test_without_ai_platforms_take_turns_and_captions_are_left_to_people(tmp_path):
    cands = [
        make_candidate(platform_id="y1", views=9000),
        make_candidate(platform_id="y2", views=8000),
        make_candidate(platform="pinterest", platform_id="p1", views=None),
    ]
    pipeline, db, sink, _ = build(tmp_path, [FakeSource("mixed", cands)], None, candidates_per_day=2)
    pipeline.writer = None
    await pipeline.run()
    assert sent_ids(db, sink) == ["y1", "p1"]
    assert video_by_pid(db, "y1").caption_body == ""
    assert texts.AI_UNAVAILABLE not in sink.notes
