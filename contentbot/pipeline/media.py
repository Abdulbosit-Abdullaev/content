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
