"""Real Gemini video analysis — most expensive test in the suite.

Cost: ~$0.10–0.30 per run depending on video length. Forward Guidance
videos are typically 30–60 min so budget accordingly. RUN_EXPENSIVE_TESTS=1
gates this on top of requiring a real GEMINI_API_KEY.
"""

import os

import pytest

from tests.integration.conftest import VIDEO_ID, VIDEO_URL
from tools.gemini_client import GeminiClient


@pytest.fixture
def gm() -> GeminiClient:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    return GeminiClient(key)


@pytest.mark.expensive
def test_real_analyze_video_basic_shape(gm: GeminiClient):
    """Full Gemini analysis should produce a well-structured podcast summary."""
    # Pull the duration first via yt-dlp so timestamp validation works
    from tools import ytdlp_metadata

    meta = ytdlp_metadata.get_video_metadata(VIDEO_URL)
    assert meta is not None
    duration = meta["duration"]

    result = gm.analyze_video(
        video_url=VIDEO_URL,
        video_id=VIDEO_ID,
        video_duration=duration,
        max_retries=2,
    )

    assert result["success"] is True, f"analysis failed: {result.get('error')}"
    assert result["attempts"] >= 1
    text = result["analysis"]
    assert len(text) > 500, "analysis text too short to be a real summary"
    # Output structure (per gemini_client.PROMPT)
    assert "Video Summary" in text or "Summary" in text
    assert "Recommendations" in text or "Section 2" in text or "No explicit" in text
    # URL-rewrite postprocessing should have replaced any hallucinated v= hashes
    if "youtube.com/watch?v=" in text:
        assert VIDEO_ID in text, "expected real video_id to appear in rewritten links"
    # Timestamp validation should be true (Gemini is generally accurate here)
    assert result["timestamps_valid"] is True
