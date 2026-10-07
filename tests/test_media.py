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
