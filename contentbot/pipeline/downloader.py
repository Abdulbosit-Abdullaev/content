"""Download a candidate video: the direct media URL first, then yt-dlp."""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from contentbot.models import Candidate

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
YTDLP_FORMAT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b"
YTDLP_TIMEOUT_S = 600


class DownloadError(Exception):
    """The video could not be downloaded."""


async def run_ytdlp(command: list[str], timeout_s: float = YTDLP_TIMEOUT_S) -> None:
    """Run yt-dlp as its own process, so a weekly `pip install -U yt-dlp` applies without restarting the bot."""
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise DownloadError(f"yt-dlp timed out after {timeout_s:g} s") from None
    if proc.returncode != 0:
        raise DownloadError(f"yt-dlp failed: {err.decode(errors='replace').strip()[-300:]}")


class Downloader:
    def __init__(
        self,
        http: httpx.AsyncClient,
        videos_dir: Path,
        *,
        cookies_file: str | None = None,
        ffmpeg_path: str = "ffmpeg",
        max_bytes: int = 300 * 1024 * 1024,
        ytdlp_fn: Callable[[list[str]], Awaitable[None]] | None = None,
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
            await self._ytdlp(self._ytdlp_command(candidate.url, dest))
        except DownloadError:
            raise
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

    def _ytdlp_command(self, url: str, dest: Path) -> list[str]:
        command = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--quiet", "--no-warnings",
            "-f", YTDLP_FORMAT,
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "--max-filesize", str(self.max_bytes),
            "--add-headers", f"User-Agent:{USER_AGENT}",
            "-o", str(dest.with_suffix("")) + ".%(ext)s",
        ]
        if self.cookies_file:
            command += ["--cookies", self.cookies_file]
        if self.ffmpeg_path != "ffmpeg":
            command += ["--ffmpeg-location", self.ffmpeg_path]
        command.append(url)
        return command
