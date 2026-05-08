"""Real yt-dlp channel search — validates ranking against live YouTube."""

import pytest

from tests.integration.conftest import CHANNEL_ID
from tools import channel_search


@pytest.mark.expensive
def test_real_search_finds_forward_guidance():
    candidates = channel_search.search_youtube_channels("Forward Guidance", max_results=5)
    assert len(candidates) >= 1
    # Top candidate should be the actual Forward Guidance channel
    top = candidates[0]
    assert top["channel_id"] == CHANNEL_ID, (
        f"Expected top candidate to be {CHANNEL_ID}, got {top}"
    )
    assert "forward guidance" in top["name"].lower()


@pytest.mark.expensive
def test_real_search_handles_vague_query():
    """A topic-only query should still return *some* candidates without crashing."""
    candidates = channel_search.search_youtube_channels("macro investing podcast", max_results=3)
    # Don't assert on a specific channel — these are subjective. Just shape.
    for c in candidates:
        assert c["channel_id"].startswith("UC")
        assert c["name"]
        assert isinstance(c["confidence_score"], (int, float))
