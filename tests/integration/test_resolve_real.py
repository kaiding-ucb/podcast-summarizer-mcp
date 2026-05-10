"""Real yt-dlp handle/URL resolution — validates against live YouTube."""

import pytest

from tests.integration.conftest import CHANNEL_HANDLE, CHANNEL_ID
from tools import channel_search


@pytest.mark.expensive
def test_real_resolve_handle():
    info = channel_search.resolve_youtube_channel(CHANNEL_HANDLE)
    assert info is not None
    assert info["channel_id"] == CHANNEL_ID
    assert "forward guidance" in info["name"].lower()


@pytest.mark.expensive
def test_real_resolve_full_url():
    info = channel_search.resolve_youtube_channel(
        f"https://www.youtube.com/channel/{CHANNEL_ID}"
    )
    assert info is not None
    assert info["channel_id"] == CHANNEL_ID


@pytest.mark.expensive
def test_real_resolve_nonexistent_returns_none():
    info = channel_search.resolve_youtube_channel("@thischanneldoesnotexist00000")
    assert info is None


@pytest.mark.expensive
def test_real_get_channel_metadata():
    info = channel_search.get_channel_metadata(CHANNEL_ID)
    assert info["channel_id"] == CHANNEL_ID
    assert info["name"]
    # Subscriber count is a number when available; OK if None occasionally
    if info["subscriber_count"] is not None:
        assert info["subscriber_count"] > 0
    # Recent video titles list (may be empty if extract_flat truncated)
    assert isinstance(info["recent_video_titles"], list)
