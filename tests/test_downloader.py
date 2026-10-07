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
