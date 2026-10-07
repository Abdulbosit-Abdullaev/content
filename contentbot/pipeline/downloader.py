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
