"""Failing-first unit tests for tools/ytdlp_metadata.

The yt-dlp wrapper replaces the YouTube Data API v3 `videos.list` call
with no-key extraction. Each helper is mocked at the YoutubeDL boundary
so these tests run offline.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools.ytdlp_metadata import (
    YtDlpError,
    extract_video_id,
    get_video_metadata,
    is_livestream,
)


# ---------- extract_video_id (pure helper) ----------


def test_extract_video_id_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=MO9ZTZPUwXY") == "MO9ZTZPUwXY"


def test_extract_video_id_short_url():
    assert extract_video_id("https://youtu.be/MO9ZTZPUwXY") == "MO9ZTZPUwXY"


def test_extract_video_id_with_extra_params():
    assert (
        extract_video_id("https://www.youtube.com/watch?v=MO9ZTZPUwXY&t=120s")
        == "MO9ZTZPUwXY"
    )


def test_extract_video_id_embed_url():
    assert extract_video_id("https://www.youtube.com/embed/MO9ZTZPUwXY") == "MO9ZTZPUwXY"


def test_extract_video_id_passthrough_bare_id():
    assert extract_video_id("MO9ZTZPUwXY") == "MO9ZTZPUwXY"


# ---------- get_video_metadata ----------


def _fake_ydl(info: dict):
    """Build a MagicMock that mimics yt_dlp.YoutubeDL context-manager usage."""
    ydl = MagicMock()
    ydl.extract_info.return_value = info
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    return cm


@patch("tools.ytdlp_metadata.YoutubeDL")
def test_get_video_metadata_returns_normalized_shape(mock_ydl_cls):
    mock_ydl_cls.return_value = _fake_ydl(
        {
            "id": "MO9ZTZPUwXY",
            "title": "The Macro Outlook for 2026",
            "channel": "Forward Guidance",
            "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
            "duration": 3725,
            "upload_date": "20260501",
            "is_live": False,
            "live_status": "not_live",
            "webpage_url": "https://www.youtube.com/watch?v=MO9ZTZPUwXY",
        }
    )
    info = get_video_metadata("https://www.youtube.com/watch?v=MO9ZTZPUwXY")
    assert info == {
        "video_id": "MO9ZTZPUwXY",
        "title": "The Macro Outlook for 2026",
        "channel_name": "Forward Guidance",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "duration": 3725,
        "published_at": "2026-05-01",
        "url": "https://www.youtube.com/watch?v=MO9ZTZPUwXY",
        "is_live": False,
    }


@patch("tools.ytdlp_metadata.YoutubeDL")
def test_get_video_metadata_handles_bare_video_id(mock_ydl_cls):
    mock_ydl_cls.return_value = _fake_ydl(
        {
            "id": "MO9ZTZPUwXY",
            "title": "T",
            "channel": "C",
            "channel_id": "UCx" + "x" * 21,
            "duration": 600,
            "upload_date": "20260101",
            "is_live": False,
            "webpage_url": "https://www.youtube.com/watch?v=MO9ZTZPUwXY",
        }
    )
    info = get_video_metadata("MO9ZTZPUwXY")
    assert info["video_id"] == "MO9ZTZPUwXY"
    # The wrapper should have passed a fully-qualified URL to yt-dlp
    args, _ = mock_ydl_cls.return_value.__enter__.return_value.extract_info.call_args
    assert args[0].startswith("https://www.youtube.com/watch?v=")


@patch("tools.ytdlp_metadata.YoutubeDL")
def test_get_video_metadata_returns_none_for_missing_video(mock_ydl_cls):
    """yt-dlp raises DownloadError on private/deleted videos. Wrapper should
    return None rather than propagating, so callers can handle gracefully."""
    from yt_dlp.utils import DownloadError

    ydl = MagicMock()
    ydl.extract_info.side_effect = DownloadError("Video unavailable")
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    assert get_video_metadata("https://www.youtube.com/watch?v=NOTREALxxx") is None


@patch("tools.ytdlp_metadata.YoutubeDL")
def test_get_video_metadata_propagates_unexpected_errors(mock_ydl_cls):
    """Unexpected errors (network down, yt-dlp internal bug) should surface
    as YtDlpError so the caller can decide whether to retry."""
    ydl = MagicMock()
    ydl.extract_info.side_effect = RuntimeError("internal yt-dlp explosion")
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    with pytest.raises(YtDlpError):
        get_video_metadata("https://www.youtube.com/watch?v=MO9ZTZPUwXY")


@patch("tools.ytdlp_metadata.YoutubeDL")
def test_get_video_metadata_marks_livestream_with_zero_duration(mock_ydl_cls):
    """yt-dlp returns duration=None for live streams; we coerce to 0 to
    match the existing parse_duration("P0D") == 0 contract."""
    mock_ydl_cls.return_value = _fake_ydl(
        {
            "id": "LIVExxxxxxx",
            "title": "Live Now",
            "channel": "Some News",
            "channel_id": "UCx" + "y" * 21,
            "duration": None,
            "upload_date": "20260508",
            "is_live": True,
            "live_status": "is_live",
            "webpage_url": "https://www.youtube.com/watch?v=LIVExxxxxxx",
        }
    )
    info = get_video_metadata("https://www.youtube.com/watch?v=LIVExxxxxxx")
    assert info["duration"] == 0
    assert info["is_live"] is True


# ---------- is_livestream helper ----------


def test_is_livestream_true_for_live():
    assert is_livestream({"is_live": True}) is True


def test_is_livestream_true_for_was_live_with_zero_duration():
    """Some 'was_live' videos still report duration=None; treat as livestream."""
    assert is_livestream({"is_live": False, "live_status": "is_upcoming"}) is True


def test_is_livestream_false_for_normal_video():
    assert is_livestream({"is_live": False, "live_status": "not_live"}) is False
    assert is_livestream({"is_live": False}) is False
