import sys
from pathlib import Path

import httpx
import pytest

from contentbot.pipeline.downloader import DownloadError, Downloader, run_ytdlp
from tests.factories import make_candidate

VIDEO_URL = "https://cdn.example/v.mp4"


class FakeYtdlp:
    """Stands in for the yt-dlp process: records the command and writes the output file."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    async def __call__(self, command):
        self.calls.append(command)
        if self.fail:
            raise DownloadError("yt-dlp failed: says no")
        template = command[command.index("-o") + 1]
        Path(template.replace(".%(ext)s", ".mp4")).write_bytes(b"from-ytdlp")


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
    assert ytdlp.calls[0][-1] == "https://www.instagram.com/p/C9abc/"
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


async def test_ytdlp_runs_as_a_separate_process(tmp_path):
    ytdlp = FakeYtdlp()
    await fetch(tmp_path, make_candidate(), ytdlp, cookies_file="/srv/cookies.txt")
    command = ytdlp.calls[0]
    assert command[:3] == [sys.executable, "-m", "yt_dlp"]
    assert command[command.index("--cookies") + 1] == "/srv/cookies.txt"
    assert command[command.index("-o") + 1].endswith("7_src.%(ext)s")
    assert command[command.index("--merge-output-format") + 1] == "mp4"
    assert "--no-playlist" in command


async def test_run_ytdlp_reports_process_errors():
    with pytest.raises(DownloadError, match="boom"):
        await run_ytdlp([sys.executable, "-c", "import sys; sys.exit('boom')"])


async def test_run_ytdlp_stops_a_stuck_process():
    with pytest.raises(DownloadError, match="timed out"):
        await run_ytdlp([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)
