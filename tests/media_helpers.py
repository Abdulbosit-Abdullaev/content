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
