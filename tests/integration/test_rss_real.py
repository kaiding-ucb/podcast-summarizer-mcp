"""Real YouTube RSS fetch — proves the no-key feed endpoint still works."""

import pytest

from tests.integration.conftest import CHANNEL_ID
from tools import rss_discovery


@pytest.mark.expensive
def test_real_rss_returns_entries():
    """Forward Guidance ships videos weekly, so we expect ≥1 entry."""
    videos = rss_discovery.get_recent_videos(CHANNEL_ID, max_results=15)
    assert len(videos) >= 1
    # Validate shape on the first entry
    v = videos[0]
    assert v["channel_id"] == CHANNEL_ID
    assert v["video_id"]
    assert v["title"]
    assert v["published_at"]
    assert v["url"].startswith("https://www.youtube.com/watch?v=")


@pytest.mark.expensive
def test_real_rss_entries_sorted_newest_first():
    """RSS entries should arrive in published-desc order (defensive sort
    in parse_feed double-checks)."""
    videos = rss_discovery.get_recent_videos(CHANNEL_ID, max_results=5)
    if len(videos) < 2:
        pytest.skip("not enough recent videos to compare ordering")
    for a, b in zip(videos, videos[1:]):
        assert a["published_at"] >= b["published_at"]
