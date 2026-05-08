"""Real yt-dlp metadata fetch — validates the wrapper against live YouTube."""

import pytest

from tests.integration.conftest import VIDEO_ID, VIDEO_URL
from tools import ytdlp_metadata


@pytest.mark.expensive
def test_real_video_metadata():
    info = ytdlp_metadata.get_video_metadata(VIDEO_URL)
    assert info is not None
    assert info["video_id"] == VIDEO_ID
    assert info["title"]
    assert info["channel_id"].startswith("UC")
    assert info["duration"] > 0  # not a livestream
    assert info["published_at"]  # YYYY-MM-DD
    assert info["is_live"] is False


@pytest.mark.expensive
def test_real_missing_video_returns_none():
    """An obviously-bogus 11-char ID should yield None, not raise."""
    info = ytdlp_metadata.get_video_metadata("https://www.youtube.com/watch?v=zzzzzzzzzzz")
    assert info is None
